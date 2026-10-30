"""Mahoraga TUI — three regions, stdlib ANSI only, Pi-clean:

    ┌─────────────────────────────┐
    │   the wheel — spins,        │  rows 1..wheel_h (plain glyphs, exact
    │   always, that's the brand  │  width, never wraps, never scrolls)
    ├─────────────────────────────┤
    │   persistent text pane:     │  conversation, thinking, answers, tools
    │   streams live, never lost  │  — wrapped by VISUAL width, ANSI-safe
    ├─────────────────────────────┤
    │ ❯ your prompt               │  bottom row
    └─────────────────────────────┘

Terminal hardening: autowrap is disabled inside the alt screen, so a too-long
line can only clip at the right edge — it can never wrap, scroll, or shift the
layout. Every painted row ends in \\033[K (clear to end of line).
"""
from __future__ import annotations
import sys, threading, time, json, re, shutil, queue

from ..core.runtime import Mahoraga
from ..llm.provider import Cancelled
from ..core.wheel import _make_frame, HIDE_CURSOR, SHOW_CURSOR, RESET
from ..policy import approval
from .lineedit import LineEditor, complete, set_raw, restore
from .markdown import render as md_render

BOLD = "\033[1m"; DIM = "\033[2m"; CYAN = "\033[36m"; GOLD = "\033[33m"
GREEN = "\033[32m"; RED = "\033[31m"; R0 = "\033[0m"

# block renderer palette (OpenCode-style banded output)
INK = "\033[38;2;236;234;229m"      # reading text
ITAL = "\033[3m"                     # thinking
GRTXT = "\033[38;2;198;222;198m"     # pale green text on tool cards
BG_USER = "\033[48;2;38;52;72m"      # slate-blue user band
BG_TOOL = "\033[48;2;33;48;37m"      # deep-green tool card


def _md(s: str, width: int = 100, tables: bool = True) -> str:
    """Markdown -> clean terminal text (tables become aligned columns).
    `tables=False` for raw program output, where `|` may be real data."""
    return md_render(s, width, tables)

ALT_ON = "\033[?1049h"
ALT_OFF = "\033[?1049l"
NOWRAP = "\033[?7l"      # never autowrap: layout can't be shifted by long lines
WRAP_ON = "\033[?7h"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


_ANSI_FULL = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"            # CSI: colours, cursor moves, clears
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"   # OSC: window titles, hyperlinks
    r"|\x1b[@-Z\\-_]"                       # two-character escapes
)


def _clean(text: str) -> str:
    """Strip escapes and control characters from text we did not author.

    Command output (ls --color, apt, nmap) carries cursor-addressed escapes.
    Printed verbatim into an absolutely-addressed pane they repaint other rows —
    that is the scrambled screen. Nothing we render may carry an escape we did
    not put there ourselves.
    """
    if not text:
        return ""
    t = _ANSI_FULL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return "".join(ch for ch in t if ch in "\n\t" or ord(ch) >= 0x20)


# ---- commands: ONE table, used by completion, /help and the drift test -----
# Completing from a hand-maintained list is how /cd, /pwd, /mouse, /reasoning
# and /wheel ended up invisible to Tab. Add a command here and it is complete
# everywhere; tests/test_commands.py fails if a dispatch branch has no entry.
COMMAND_SPEC: list[tuple[str, str, str]] = [
    ("/help", "", "this"),
    ("/quit", "", "exit"),
    ("/new", "", "fresh context (memory keeps history)"),
    ("/stop", "", "stop the reply in flight + drop the queue"),
    ("/provider", "[name]", "list providers / switch"),
    ("/model", "[n|name|refresh]", "list models (numbered) / switch"),
    ("/tools", "on|off", "list tools / enable or disable them"),
    ("/reasoning", "on|off", "show or hide the model's thinking"),
    ("/wheel", "sticky|auto|full|<rows>", "banner size"),
    ("/allow", "[regex|rm <n>]", "list / whitelist / remove a rule"),
    ("/cd", "<path>", "change the shell working dir (persists)"),
    ("/pwd", "", "where the shell runs"),
    ("/mouse", "on|off", "wheel-scroll the pane (off = native copy/paste)"),
    ("/memory", "", "recent stored messages"),
    ("/clear", "", "wipe stored memory"),
]
# Fixed candidate words per command — data, so it can be tested and cannot drift
# silently the way the old inline branch chain did.
STATIC_ARG_POOL: dict[str, list[str]] = {
    "/reasoning": ["on", "off"],
    "/mouse": ["on", "off"],
    "/tools": ["on", "off"],
    "/wheel": ["sticky", "auto", "full"],
    "/allow": ["rm"],
}
COMMAND_ALIASES = {"/exit": "/quit", "/q": "/quit"}
COMMAND_NAMES = [name for name, _, _ in COMMAND_SPEC] + list(COMMAND_ALIASES)


def complete_line(buf: str, cur: int, names: list[str], arg_pool) -> tuple | None:
    """Tab completion for one prompt line.

    Before the first space: command names. After it: whatever `arg_pool(cmd)`
    returns. Pure, so tests drive it without a terminal.
    """
    try:
        head = buf[:cur]
        if not head.startswith("/"):
            return None
        if " " not in head:
            return complete(buf, cur, names, 0)
        cmd, _, rest = head.partition(" ")
        if cmd == "/allow" and rest.startswith("rm "):
            return complete(buf, cur, arg_pool("/allow rm"), len("/allow rm "))
        pool = arg_pool(cmd)
        if not pool:
            return None
        return complete(buf, cur, pool, len(cmd) + 1)
    except Exception:
        return None      # Tab doing nothing beats Tab killing the prompt


def vis_len(s: str) -> int:
    return len(_ANSI.sub("", s))


def wrap_ansi(line: str, width: int) -> list[str]:
    """Wrap on spaces by VISUAL width; escape codes don't count toward length."""
    out, cur = [], ""
    for word in line.split(" "):
        cand = f"{cur} {word}" if cur else word
        if vis_len(cand) > width and cur:
            out.append(cur)
            cur = word
        else:
            cur = cand
    out.append(cur)
    return out or [""]


# Aspect ratio of the art. The shape comes from w/h (rx = w*0.285, ry = h*0.245)
# and terminal cells are ~2:1 tall, so the original 112x19 (5.9:1) rendered as a
# flattened pancake. 4.0 keeps a visible rim and still fills the top band.
_WHEEL_ASPECT = 4.0


def _wheel_height(rows: int, compact: bool) -> int:
    """Height of the wheel region (title line + art rows).

    Compact terminals get the 2-line spinner. Otherwise the wheel takes about a
    third of the height in stable 2-row steps — so it grows with the window
    instead of being pinned at 21 — clamped to a floor of 9 (the art itself
    needs 8 rows) and a ceiling of 28 so the transcript keeps most of the screen.
    """
    if compact:
        return 2
    # 21 is the size the banner was designed at — never shrink below it (that's
    # what made it look squashed); grow gently on tall terminals, cap at 28.
    return max(21, min(28, int(rows * 0.45)))


def _at(row: int, col: int = 1) -> str:
    return f"\033[{row};{col}H"


class Screen:
    """Wheel on top, persistent pane in the middle, input on the bottom row."""

    def __init__(self):
        size = shutil.get_terminal_size((110, 38))
        self.cols, self.rows = size.columns, size.lines
        self.compact = self.rows < 30
        # bottom block: rule (rows-2) + input (rows-1) + status bar (rows)
        self.status_row = self.rows
        self.input_row = self.rows - 1
        self.sep_row = self.rows - 2
        self.busy = False               # True while the model works
        self._ever_busy = False         # has a turn run yet? (sticky collapse)
        # "sticky" = shrink at the first reply and stay small until restart,
        # "auto"   = shrink while streaming, grow back when idle,
        # "full"   = always the big banner, or an int = that many rows.
        self.wheel_setting = "sticky"
        self.wheel_h = 2 if self.compact else _wheel_height(self.rows, self.compact)
        self.pane_top = self.wheel_h + 1
        self.pane_h = max(3, self.sep_row - 1 - self.wheel_h)
        self.entries: list[dict] = []    # blocks: user/think/answer/note/tool
        self._stream_idx: dict[str, int] = {}
        self.expand_tools = False        # Ctrl+O toggles full tool output
        self._pending_tools: list[int] = []
        self._dirty = threading.Event()
        self._wheel_stop = threading.Event()
        self._lock = threading.RLock()      # guards entries vs the render thread
        self.queued = 0                     # messages waiting behind the current turn
        self._frame = 0
        self._saved_tty = None
        self.mouse = False              # /mouse on enables wheel + tracking
        self.scroll_off = 0             # rows scrolled back from the tail; 0 = follow
        self._last_total = None         # row count at the previous paint (anchoring)
        self.status = "ready"
        self.status_left_extra = ""     # cwd (branch), set by cli()
        self.status_right_extra = ""    # (provider) model, set by cli()
        self.resize_pending = threading.Event()
        self._input_snapshot = ("", "", 0)   # prompt, buf, cur for repaints

    # ---- lifecycle ----

    def enter(self):
        # alt screen + no autowrap + tty raw for the app's whole life.
        # Mouse reporting is OPT-IN (self.mouse, /mouse on): tracking steals
        # the terminal's own text selection, so it stays off by default and
        # copy/paste works natively. PgUp/PgDn scroll without it.
        sys.stdout.write(ALT_ON + NOWRAP + HIDE_CURSOR + "\033[?1007l")
        if self.mouse:
            sys.stdout.write("\033[?1000h\033[?1006h")
        sys.stdout.flush()
        if sys.stdin.isatty():
            self._saved_tty = set_raw(sys.stdin.fileno())
        threading.Thread(target=self._loop, daemon=True).start()

    def set_mouse(self, on: bool):
        self.mouse = on
        sys.stdout.write("\033[?1000h\033[?1006h" if on
                         else "\033[?1006l\033[?1000l")
        sys.stdout.flush()
        self._dirty.set()

    def leave(self):
        self._wheel_stop.set()
        if self._saved_tty is not None:
            restore(sys.stdin.fileno(), self._saved_tty)
        sys.stdout.write("\033[?1006l\033[?1000l"
                         + R0 + SHOW_CURSOR + WRAP_ON + ALT_OFF + RESET)
        sys.stdout.flush()

    # ---- painting ----

    def _paint_wheel(self):
        out = ["\033[s"]          # save cursor: painting must never steal it
        if self.compact or self.wheel_h < 8:
            glyphs = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            g = glyphs[self._frame % len(glyphs)]
            out.append(_at(1) + f" {GOLD}{g} MAHORAGA{R0}\033[K")
            if self.wheel_h >= 2:
                label = self.status + ("  ·  ctrl+c stops" if self.busy else "")
                out.append(_at(2) + f" {DIM}{label}{R0}\033[K")
            for r in range(3, self.wheel_h + 1):
                out.append(_at(r) + "\033[2K")
        else:
            # The art's shape comes from the w/h ratio (rx = w*0.285, ry = h*0.245),
            # so shrinking height alone squashes the wheel. Keep the designed
            # proportion — 112 cols x 19 rows — and scale both axes together.
            art_h = max(8, self.wheel_h - 2)          # -1 title, -1 region line
            art_w = max(28, min(self.cols - 1, int(art_h * _WHEEL_ASPECT)))
            pad = max(0, (self.cols - 1 - art_w) // 2)
            frame = _make_frame(self._frame, art_w, art_h, color=False)
            lines = frame.split("\n")[: self.wheel_h - 1]
            for i, line in enumerate(lines):
                out.append(_at(1 + i) + (" " * pad) + line[: self.cols - 1] + "\033[K")
            for i in range(len(lines), self.wheel_h - 1):
                out.append(_at(1 + i) + "\033[K")
            out.append("\033[u")      # restore cursor to wherever the user is typing
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def _wrap_plain(self, text: str, width: int, style: str = "") -> list[str]:
        """Wrap clean text and re-apply the style to every visual row."""
        out = []
        for para in text.split("\n"):
            if not para:
                continue
            for w in wrap_ansi(para, width):
                out.append(style + w if style else w)
        return out

    def _wrapped(self) -> list[tuple[str, str]]:
        with self._lock:
            return self._wrapped_locked()

    def _wrapped_locked(self) -> list[tuple[str, str]]:
        """Render blocks to rows of (bg, styled_text): user = slate band,
        tools = green cards ($ cmd · output · Took Ns), thinking = dim italic,
        answers = plain ink."""
        W = self.cols - 2
        rows: list[tuple[str, str]] = []
        for e in self.entries:
            k = e.get("kind", "note")
            txt = _clean(e.get("text", ""))
            if k == "note":
                color = e.get("color", "")
                rows += [("", (color + w) if color else w)
                         for w in self._wrap_plain(txt, W)]
            elif k == "user":
                rows.append(("", ""))
                rows += [(BG_USER, w) for w in
                         self._wrap_plain(txt, self.cols - 4, INK)]
                rows.append(("", ""))
            elif k == "think":
                rows += [("", w) for w in
                         self._wrap_plain(_md(txt, W), W, DIM + ITAL)]
                rows.append(("", ""))
            elif k == "answer":
                rows += [("", w) for w in
                         self._wrap_plain(_md(txt, W), W, INK)]
                rows.append(("", ""))
            elif k == "tool":
                rows.append(("", ""))
                meta = f" {DIM}({_clean(str(e['meta']))})" if e.get("meta") else ""
                rows.append((BG_TOOL,
                             f"{GRTXT}$ {_md(_clean(str(e['head'])), tables=False)}{meta}"))
                rows.append((BG_TOOL, ""))
                if e.get("out") is None:
                    body = [f"{DIM}running…"]
                else:
                    out_lines = _md(_clean(str(e["out"])), self.cols - 4,
                                    tables=False).rstrip().splitlines() or ["(no output)"]
                    if len(out_lines) > 14 and not self.expand_tools:
                        out_lines = [f"{DIM}… ({len(out_lines) - 14} earlier "
                                     f"lines, ctrl+o to expand)"] + out_lines[-14:]
                    body = [GRTXT + l for l in out_lines]
                    if e.get("dur") is not None:
                        body.append(f"{DIM}Took {e['dur']:.1f}s")
                for b in body:
                    rows += [(BG_TOOL, w) for w in
                             self._wrap_plain(b, self.cols - 4)]
                rows.append((BG_TOOL, ""))
        return rows

    def _paint_pane(self):
        all_lines = self._wrapped()
        total = len(all_lines)
        max_off = max(0, total - self.pane_h)
        # While the reader is scrolled up, new rows land at the bottom. Grow the
        # offset by the same amount so the view holds still on the lines being
        # read — an absolute index drifts while streaming and jumps on resize.
        if (self.scroll_off > 0 and self._last_total is not None
                and total > self._last_total):
            self.scroll_off = min(max_off, self.scroll_off + (total - self._last_total))
        self._last_total = total
        self.scroll_off = max(0, min(self.scroll_off, max_off))
        start = max(0, total - self.pane_h - self.scroll_off)
        lines = all_lines[start:start + self.pane_h]
        out = ["\033[s"]
        for i in range(self.pane_h):
            row = self.pane_top + i
            # Erase the WHOLE line (2K) before drawing: a band from the previous
            # frame otherwise survives behind a now-plain row, which reads as
            # overlapping blocks after a scroll or a resize.
            cell = [_at(row) + R0 + "\033[2K"]
            if i < len(lines):
                bg, text = lines[i]
                if bg:   # band rows: fill the full width with the background
                    pad = " " * max(0, self.cols - 1 - vis_len(text))
                    cell.append(bg + text + pad + R0 + "\033[K")
                else:
                    cell.append(f"{text}{R0}\033[K")
            out.append("".join(cell))
        out.append("\033[u")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def _paint_bottom(self):
        """Rule + status bar (the interactive footer): cwd (branch) left,
        (provider) model • state right. Painted every frame."""
        out = ["\033[s"]
        out.append(_at(self.sep_row) + f"{DIM}{'─' * (self.cols - 1)}{R0}\033[K")
        if self.busy:
            glyphs = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            g = glyphs[self._frame % len(glyphs)]
            extra = f" · +{self.queued} queued" if self.queued else ""
            state = f"{GOLD}{g} {self.status}{extra} · /stop clears{R0}"
            plain = f"{self.status}{extra} · /stop clears"
        elif self.scroll_off > 0:
            state = f"{GOLD}scrolled up · PgDn to follow{R0}"
            plain = "scrolled up · PgDn to follow"
        else:
            state = f"{DIM}{self.status}{R0}"
            plain = self.status
        left = f" {DIM}{self.status_left_extra}{R0}" if self.status_left_extra else ""
        right_plain = f"{self.status_right_extra} • {plain}" if self.status_right_extra else plain
        right = f"{GOLD}{self.status_right_extra}{R0} {DIM}•{R0} {state}" \
            if self.status_right_extra else state
        x = self.cols - 1 - vis_len(right_plain)
        out.append(_at(self.status_row) + left + "\033[K")
        out.append(_at(self.status_row, max(2, x)) + right + "\033[K")
        out.append("\033[u")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def _wheel_rows(self) -> int:
        """How many rows the banner takes.

        sticky — collapses on the first reply and stays collapsed for the rest of
                 the session (it comes back when Mahoraga is restarted)
        auto   — collapses only while a reply is streaming
        full   — always the full banner
        int    — exactly that many rows (2..28)
        """
        if self.compact:
            return 2
        full = _wheel_height(self.rows, self.compact)
        setting = self.wheel_setting
        if isinstance(setting, int):
            return max(2, min(28, setting))
        if setting == "full":
            return full
        if setting == "auto":
            return 4 if self.busy else full
        # sticky (default): once the first reply starts, stay small
        return 4 if (self.busy or self._ever_busy) else full

    def set_wheel(self, setting) -> str:
        """Change the banner size now, without waiting for a turn."""
        self.wheel_setting = setting
        self.resize_pending.set()
        self._dirty.set()
        return self.wheel_label()

    def wheel_label(self) -> str:
        s = self.wheel_setting
        if isinstance(s, int):
            return f"{s} rows"
        return {"sticky": "sticky (stays small after the first reply)",
                "auto": "auto (grows back when the reply ends)",
                "full": "full banner"}.get(s, str(s))

    def set_busy(self, busy: bool) -> None:
        """Busy changes the layout (the wheel shrinks). The relayout itself is
        handed to the render thread — only it paints stdout."""
        if busy == self.busy:
            return
        self.busy = busy
        if busy:
            self._ever_busy = True
        self.resize_pending.set()
        self._dirty.set()

    def relayout(self):
        """Terminal resized: recompute every region and repaint from scratch.
        Without this, regions keep their old row numbers and text lands in
        the wrong places."""
        size = shutil.get_terminal_size((110, 38))
        self.cols, self.rows = size.columns, size.lines
        self.compact = self.rows < 30
        self.status_row = self.rows
        self.input_row = self.rows - 1
        self.sep_row = self.rows - 2
        self.wheel_h = self._wheel_rows()
        self.pane_top = self.wheel_h + 1
        self.pane_h = max(3, self.sep_row - 1 - self.wheel_h)
        max_off = max(0, len(self._wrapped()) - self.pane_h)
        self.scroll_off = min(self.scroll_off, max_off)   # keep the reader's place
        self._last_total = None
        sys.stdout.write("\033[2J")        # wipe, then repaint absolutely
        self._dirty.set()
        self._paint_wheel()
        self._paint_pane()
        self._paint_bottom()
        self._redraw_input(*self._input_snapshot)

    def _loop(self):
        """Owns the top region always; repaints the pane when dirty."""
        while not self._wheel_stop.is_set():
            if self.resize_pending.is_set():
                self.resize_pending.clear()
                self.relayout()
            self._frame += 1                 # the wheel never stops turning
            self._paint_wheel()
            if self._dirty.is_set():
                self._dirty.clear()
                self._paint_pane()
            self._paint_bottom()
            time.sleep(0.08)

    # ---- buffer API (main thread) ----

    def say(self, text: str, color: str = ""):
        """Plain note rows (help text, errors, /command output)."""
        with self._lock:
            self.entries.append({"kind": "note", "text": text, "color": color})
        self._dirty.set()

    def user_msg(self, text: str):
        with self._lock:
            self.entries.append({"kind": "user", "text": text})
        self._dirty.set()

    def tool_call(self, head: str, meta: str = "") -> int:
        with self._lock:
            # The model's next sentence belongs AFTER this card. Without this
            # reset the old stream index is reused and the answer is appended to
            # the block above the command — the "output on top of commands" bug.
            self._stream_idx = {}
            self.entries.append({"kind": "tool", "head": head, "meta": meta,
                                 "out": None, "dur": None})
            idx = len(self.entries) - 1
            self._pending_tools.append(idx)
        self._dirty.set()
        return idx

    def tool_result(self, out: str, dur: float):
        with self._lock:
            if self._pending_tools:
                self.entries[self._pending_tools.pop(0)].update(out=out, dur=dur)
        self._dirty.set()

    def toggle_tools(self):
        self.expand_tools = not self.expand_tools
        self._dirty.set()

    def stream_piece(self, text: str, color: str, key: str):
        """Append to the block tagged `key` (think/answer, created on first
        piece)."""
        with self._lock:
            idx = self._stream_idx.get(key)
            if idx is None or idx >= len(self.entries):
                kind = key if key in ("think", "answer") else "note"
                self.entries.append({"kind": kind, "text": "", "color": color})
                idx = len(self.entries) - 1
                self._stream_idx[key] = idx
            self.entries[idx]["text"] += text
        self._dirty.set()

    def finish_stream(self):
        self._stream_idx = {}
        self._dirty.set()

    def _redraw_input(self, prompt: str, buf: str, cur: int):
        """Paint the input row with the live buffer; horizontal scroll when
        the line is longer than the row (nowrap clips, it never wraps)."""
        self._input_snapshot = (prompt, buf, cur)
        avail = self.cols - vis_len(prompt) - 1
        start = max(0, cur - avail)
        shown = buf[start: start + max(1, avail)]
        sys.stdout.write(_at(self.input_row) + prompt + shown + "\033[K"
                         + _at(self.input_row, vis_len(prompt) + (cur - start) + 1))
        sys.stdout.flush()

    def input_line(self, prompt: str, history: list | None = None,
                   completer=None, on_options=None, on_expand=None,
                   on_interrupt=None) -> str:
        """Bottom-row prompt with line editing: arrows, history (UP/DOWN),
        tab completion. The wheel keeps spinning above it."""
        editor = LineEditor(self._redraw_input, history=history,
                            completer=completer, on_options=on_options,
                            on_scroll=self.scroll_by,
                            on_follow=self.scroll_follow,
                            on_expand=on_expand,
                            on_interrupt=on_interrupt)
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()
        try:
            line = editor.read(prompt)
        finally:
            sys.stdout.write(HIDE_CURSOR)
            sys.stdout.flush()
        # repaint every region at absolute positions — layout self-heals
        self._paint_wheel()
        self._paint_pane()
        return line

    def scroll_by(self, step: int):
        """Pane scrollback: +step = newer, -step = older. Reaching the tail
        (offset 0) re-enables live follow."""
        total = len(self._wrapped())
        max_off = max(0, total - self.pane_h)
        self.scroll_off = max(0, min(self.scroll_off - step, max_off))
        self._last_total = total
        self._dirty.set()

    def scroll_follow(self):
        self.scroll_off = 0
        self._dirty.set()

    def kick(self):
        self._dirty.set()


class Turns:
    """Runs agent turns off the input thread, so the prompt stays live while the
    model works. Messages typed in the meantime are queued and run in order.

    `submit` returns the message's position in line (1 = starts immediately),
    counted explicitly rather than from qsize(), which races with the worker.
    """

    def __init__(self, run, on_change=None):
        self._run = run
        self._on_change = on_change or (lambda n: None)
        self._q = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()
        self._queued = 0                 # submitted but not yet started

    def busy(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def pending(self) -> int:
        with self._lock:
            return self._queued

    def submit(self, text: str) -> int:
        with self._lock:
            self._queued += 1
            pos = self._queued
            self._q.put(text)
            start = not self.busy()
            if start:
                self._thread = threading.Thread(target=self._worker, daemon=True)
                self._thread.start()
        self._on_change(self.pending())
        return pos

    def clear(self) -> int:
        n = 0
        while True:
            try:
                self._q.get_nowait()
                n += 1
            except queue.Empty:
                break
        with self._lock:
            self._queued = max(0, self._queued - n)
        self._on_change(self.pending())
        return n

    def _worker(self):
        while True:
            try:
                text = self._q.get_nowait()
            except queue.Empty:
                self._on_change(self.pending())
                return
            with self._lock:
                self._queued = max(0, self._queued - 1)
            self._on_change(self.pending())
            try:
                self._run(text)
            except Exception:                 # never kill the worker loop
                # print() here would corrupt the absolutely-addressed screen;
                # run_turn already reports the failure into the pane
                pass


def cli():
    agent = Mahoraga(yolo="--yolo" in sys.argv)
    screen = Screen()
    screen.mouse = bool(agent.cfg.get("mouse", False))   # /mouse pref persists
    screen.enter()
    try:
        import signal
        if hasattr(signal, "SIGWINCH"):
            signal.signal(signal.SIGWINCH,
                          lambda *_: screen.resize_pending.set())
    except (ValueError, OSError):
        pass

    screen.say(f"{GOLD}{BOLD}MAHORAGA{R0} — the adaptive agent · local · minimal · extensible")
    screen.say(f"{DIM}/help for commands · /quit to exit{R0}")
    screen.say("")

    # dangerous-command prompts land on the bottom row; status says so
    approval.before_prompt = lambda: setattr(screen, "status", "waiting for you…")
    def _post_prompt():
        screen.status = "working…"
        screen.kick()
    approval.after_prompt = _post_prompt

    # ---- input history (persisted) + tab completion ----
    import pathlib
    hist_path = pathlib.Path.home() / ".mahoraga" / "history"
    try:
        history = [l for l in hist_path.read_text().splitlines() if l.strip()][-500:]
    except OSError:
        history = []

    COMMANDS = ["/help", "/quit", "/exit", "/q", "/new", "/provider",
                "/model", "/tools", "/allow", "/memory", "/clear", "/stop"]
    model_cache = {"t": 0.0, "list": []}

    def arg_pool(cmd: str) -> list[str]:
        """Candidate words for a command's first argument. Never touches the
        network — completion runs on the input thread and must not freeze it."""
        static = STATIC_ARG_POOL.get(cmd)
        if static:
            return list(static)
        if cmd == "/provider":
            return agent.provider_names()
        if cmd == "/model":
            if time.time() - model_cache["t"] > 30:
                model_cache["list"] = agent.model_names_cached()
                model_cache["t"] = time.time()
            return model_cache["list"]
        if cmd == "/allow rm":
            from ..policy import permissions as _perm
            return [str(i) for i in range(1, len(_perm.list_allow()) + 1)]
        return []

    def completer(buf, cur):
        """Tab completion. Wrapped so a bad command can never break the editor."""
        try:
            return complete_line(buf, cur, COMMAND_NAMES, arg_pool)
        except Exception:
            return None

    _shown = {"key": None}

    def on_options(matches):
        """Show the candidates in columns, once per distinct set."""
        key = tuple(matches)
        if _shown["key"] == key:
            return
        _shown["key"] = key
        cols = 6 if len(matches) > 8 else len(matches)
        width = max(len(m) for m in matches) + 2
        for i in range(0, len(matches), cols):
            row = "".join(f"{m:<{width}}" for m in matches[i:i + cols]).rstrip()
            screen.say(f"  {DIM}{row}{R0}")
        screen.kick()

    # ---- status bar contents: cwd (branch) + (provider) model ----
    import subprocess as _sp, time as _t
    _branch_cache = {"t": 0.0, "cwd": "", "name": ""}

    def _git_branch(cwd):
        if _t.time() - _branch_cache["t"] > 3 or _branch_cache["cwd"] != cwd:
            _branch_cache.update(t=_t.time(), cwd=cwd)
            try:
                r = _sp.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=cwd, capture_output=True, text=True, timeout=1)
                _branch_cache["name"] = r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                _branch_cache["name"] = ""
        return _branch_cache["name"]

    def refresh_statusbar():
        from ..plugins import shell_cwd
        cwd = shell_cwd()
        short = cwd.replace(str(pathlib.Path.home()), "~", 1)
        branch = _git_branch(cwd)
        screen.status_left_extra = f"{short} ({branch})" if branch else short
        extra = "" if agent.cfg.get("reasoning", True) else " · no-think"
        screen.status_right_extra = f"({agent.active_name()}) {agent.current_model()}{extra}"

    def on_tool(name, args):
        if name in ("shell", "bash", "run_command"):
            head = str(args.get("command", ""))
            meta = f"timeout {args.get('timeout', 60)}s"
        else:
            head = f"{name} " + json.dumps(args, ensure_ascii=False)[:70]
            meta = ""
        screen.status = f"{name}…"
        screen.tool_call(head, meta)
        screen.kick()

    def on_result(name, args, result, dur):
        screen.tool_result(result, dur)
        screen.kick()

    def run_turn(text):
        screen.user_msg(text)
        screen.status = "thinking…"
        screen.finish_stream()
        screen.set_busy(True)          # shrinks the wheel, gives the rows to the pane
        screen.kick()

        def on_thinking():
            # every model iteration is a fresh block: the second round's
            # reasoning must not append to the first round's reasoning
            screen.finish_stream()

        def on_delta(piece):
            screen.status = "speaking…"
            screen.stream_piece(piece, "", "answer")

        def on_reason(piece):
            screen.status = "thinking…"
            screen.stream_piece(piece, "", "think")

        try:
            answer = agent.send(text, on_thinking=on_thinking,
                                on_delta=on_delta, on_reason=on_reason,
                                on_tool=on_tool, on_result=on_result)
        except (KeyboardInterrupt, Cancelled):
            screen.set_busy(False)
            screen.status = "stopped"
            screen.finish_stream()
            screen.say(f"{DIM}⏹ stopped — kept whatever streamed so far{R0}")
            screen.say("")
            screen.kick()
            return
        except Exception as e:
            screen.set_busy(False)
            screen.status = "ready"
            screen.finish_stream()
            screen.say(f"{RED}✗ {e}{R0}")
            screen.say("")
            screen.kick()
            return
        screen.set_busy(False)
        screen.status = "ready"
        screen.finish_stream()
        if not answer:
            screen.say(f"{GREEN}(no text){R0}")
        screen.say("")
        screen.kick()

    # turns run on a worker thread so you can keep typing (and queue) while
    # the model is working
    turns = Turns(run_turn, on_change=lambda n: setattr(screen, "queued", n))

    def handle_interrupt() -> bool:
        """Ctrl+C. Streaming → stop the reply and keep what arrived.
        Idle → let the editor clear the line (returns False)."""
        if turns.busy():
            agent.cancel()
            screen.status = "stopping…"
            screen.kick()
            return True
        return False

    while True:
        refresh_statusbar()
        try:
            line = screen.input_line(f"{GOLD}❯ {R0}", history=history,
                                     completer=completer,
                                     on_options=on_options,
                                     on_expand=screen.toggle_tools,
                                     on_interrupt=handle_interrupt).strip()
        except KeyboardInterrupt:
            # The tty keeps ISIG on, so ^C arrives as SIGINT here rather than as
            # a byte the editor can decode. Route it through the same handler:
            # streaming → stop the reply; idle → cancel the line.
            if handle_interrupt():
                continue
            screen.say(f"{DIM}^C{R0}")
            screen.kick()
            continue
        except EOFError:
            screen.leave()
            print("bye.")
            break
        if not line:
            continue
        history.append(line)
        try:
            hist_path.write_text("\n".join(history[-500:]) + "\n")
        except OSError:
            pass

        if line.startswith("/"):
            cmd = line.split()[0]
            arg = line.split(maxsplit=1)[1].strip() if len(line.split(maxsplit=1)) > 1 else ""
            if cmd in ("/quit", "/exit", "/q"):
                screen.leave()
                print("bye.")
                break
            elif cmd == "/help":
                screen.say(f"{BOLD}commands{R0}")
                for name, hint, desc in COMMAND_SPEC:
                    left = f"{name} {hint}".strip()
                    screen.say(f"  {left:<30}{desc}")
                screen.say(f"  {DIM}aliases: /exit, /q{R0}")
                screen.say(f"{DIM}type while it works — messages queue · ^C stops the reply · "
                           f"Ctrl+O expands tool output · PgUp/PgDn scrolls · "
                           f"yolo: python -m mahoraga --yolo{R0}")
            elif cmd == "/new":
                agent.new_session()
                screen.say(f"{DIM}context cleared{R0}")
            elif cmd == "/provider":
                if arg:
                    try:
                        name = agent.switch_provider(arg)
                        screen.say(f"{DIM}provider -> {name} ({agent.current_model()}){R0}")
                        refresh_statusbar()
                        if not agent.provider_key_ok():
                            screen.say(f"{RED}⚠ {agent.active_cfg().get('api_key_env')} not set in env{R0}")
                    except ValueError as e:
                        screen.say(f"{RED}{e}{R0}")
                else:
                    cur = agent.active_name()
                    for n in agent.provider_names():
                        mark = f"{GOLD}●{R0}" if n == cur else " "
                        screen.say(f" {mark} {BOLD}{n}{R0} · {DIM}{agent.cfg['providers'][n].get('model','')}{R0}")
            elif cmd == "/model":
                if arg in ("refresh", "list"):
                    arg = ""
                if arg:
                    picked = agent.resolve_model(arg)
                    if not picked:
                        screen.say(f"{RED}no model #{arg}{R0} — /model lists them")
                    else:
                        screen.say(f"{DIM}model -> {agent.set_model(picked)}{R0}")
                        refresh_statusbar()
                else:
                    cur = agent.current_model()
                    models = agent.available_models()
                    pcfg = agent.active_cfg()
                    screen.say(f"{BOLD}current:{R0} {cur}  {DIM}({agent.active_name()}){R0}")
                    for i, m in enumerate(models[:30], 1):
                        mark = f"{GOLD}●{R0}" if m == cur else " "
                        screen.say(f" {mark} {DIM}{i:>2}{R0}  {m}")
                    if len(models) == 1 and not pcfg.get("api_key_env"):
                        if not getattr(agent.provider, "reachable", lambda: True)():
                            screen.say(f"{RED}can't reach {pcfg.get('base_url','')}{R0} "
                                       f"— is the model server running?")
                        else:
                            screen.say(f"{DIM}server listed one model — add more via "
                                       f"llama.cpp presets, or set \"models\": [...] "
                                       f"in ~/.mahoraga/config.json{R0}")
                    screen.say(f"{DIM}pick: /model <n>  ·  /model <name>  ·  "
                               f"/model refresh re-queries{R0}")
            elif cmd == "/tools":
                if arg in ("off", "on"):
                    agent.tools_enabled = arg == "on"
                    screen.say(f"{DIM}tools {'enabled' if agent.tools_enabled else 'disabled'}{R0}")
                else:
                    for name in sorted(agent.registry.tools):
                        screen.say(f"  {CYAN}•{R0} {name}")
                    screen.say(f"{DIM}tools are {'on' if agent.tools_enabled else 'off'} — /tools off|on{R0}")
            elif cmd == "/cd":
                from ..plugins import set_shell_cwd, shell_cwd
                try:
                    screen.say(f"{DIM}shell cwd -> {set_shell_cwd(arg) if arg else shell_cwd()}{R0}")
                except (NotADirectoryError, FileNotFoundError):
                    screen.say(f"{RED}not a directory: {arg}{R0}")
            elif cmd == "/mouse":
                on = arg != "off"
                screen.set_mouse(on)
                agent.cfg["mouse"] = on
                agent._save_config()
                screen.say(f"{DIM}mouse {'on — wheel scrolls the pane, Shift+drag selects' if on else 'off — native select/copy works, PgUp/PgDn scroll'}{R0}")
            elif cmd == "/wheel":
                w = arg.lower().strip()
                if not w:
                    screen.say(f"{DIM}banner: {screen.wheel_label()}{R0}")
                    screen.say(f"{DIM}/wheel sticky  shrink on the first reply, stay small "
                               f"(back on restart){R0}")
                    screen.say(f"{DIM}/wheel auto    shrink only while a reply streams{R0}")
                    screen.say(f"{DIM}/wheel full    always the big banner{R0}")
                    screen.say(f"{DIM}/wheel 6       exactly that many rows (2-28){R0}")
                elif w in ("sticky", "auto", "full"):
                    screen.say(f"{DIM}banner: {screen.set_wheel(w)}{R0}")
                elif w.isdigit() and 2 <= int(w) <= 28:
                    screen.say(f"{DIM}banner: {screen.set_wheel(int(w))}{R0}")
                else:
                    screen.say(f"{RED}usage: /wheel sticky|auto|full|<2-28>{R0}")
                screen.kick()
            elif cmd == "/stop":
                dropped = turns.clear()
                was_running = turns.busy()
                if was_running:
                    agent.cancel()
                    screen.status = "stopping…"
                bits = []
                if was_running:
                    bits.append("stopping the reply in flight")
                bits.append(f"cleared {dropped} queued message(s)" if dropped else "queue was empty")
                screen.say(f"{DIM}{' · '.join(bits)}{R0}")
                screen.kick()
            elif cmd == "/reasoning":
                want = arg.lower()
                if want not in ("on", "off"):
                    state = "on" if agent.cfg.get("reasoning", True) else "off"
                    screen.say(f"{DIM}reasoning is {state} — /reasoning on|off{R0}")
                else:
                    on = want == "on"
                    agent.set_reasoning(on)
                    screen.say(f"{DIM}reasoning {want} — "
                               f"{'thinking is shown and requested' if on else 'thinking hidden and the model is asked to skip it'}{R0}")
                refresh_statusbar()
                screen.kick()
            elif cmd == "/pwd":
                from ..plugins import shell_cwd
                screen.say(f"{DIM}shell cwd: {shell_cwd()}{R0}")
            elif cmd == "/allow":
                from ..policy import permissions as _perm
                if not arg:
                    rules = _perm.list_allow()
                    if not rules:
                        screen.say(f"{DIM}allowlist is empty — /allow <regex> blesses a "
                                   f"command\n  file: {_perm.allow_path()}{R0}")
                    else:
                        screen.say(f"{BOLD}allowlist{R0} {DIM}{_perm.allow_path()}{R0}")
                        for i, r in enumerate(rules, 1):
                            screen.say(f"  {GOLD}{i}{R0}  {r}")
                        screen.say(f"{DIM}remove one: /allow rm <n>{R0}")
                elif arg.startswith("rm "):
                    which = arg[3:].strip()
                    ok = _perm.remove_allow(which)
                    screen.say(f"{DIM}{'removed: ' + which if ok else 'not found: ' + which}{R0}")
                else:
                    ok = _perm.add_allow(arg)
                    screen.say(f"{DIM}{'whitelisted: ' + arg if ok else 'already whitelisted or invalid regex'}{R0}")
            elif cmd == "/memory":
                from ..memory import sqlite as mem
                for role, content in mem.load_recent(10):
                    tag = f"{GOLD}you{R0}" if role == "user" else f"{CYAN}mahoraga{R0}"
                    screen.say(f"{tag}: {content[:90]}")
            elif cmd == "/clear":
                from ..memory import sqlite as mem
                mem.clear_messages()
                screen.say(f"{DIM}memory wiped{R0}")
            else:
                screen.say(f"{DIM}unknown command: {cmd}{R0}")
            screen.kick()
            continue

        if turns.busy():
            n = turns.submit(line)
            screen.say(f"{DIM}↳ queued #{n} — starts when the current reply finishes{R0}")
            screen.kick()
        else:
            turns.submit(line)


if __name__ == "__main__":
    cli()
