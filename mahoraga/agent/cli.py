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
        self.wheel_h = 2 if self.compact else 21      # full wheel: title + 19 art rows
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
        self.busy = False               # True while the model works
        self.mouse = False              # /mouse on enables wheel + tracking
        self.scroll_top: int | None = None   # first visible pane line; None = follow tail
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
        if self.compact:
            glyphs = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            g = glyphs[self._frame % len(glyphs)]
            out.append(_at(1) + f" {GOLD}{g} MAHORAGA{R0}\033[K")
            out.append(_at(2) + f" {DIM}{self.status}{R0}\033[K")
        else:
            # full wheel: title + 19 art rows, plain glyphs, exact width
            frame = _make_frame(self._frame, min(self.cols - 1, 112), 19, color=False)
            lines = frame.split("\n")[: self.wheel_h - 1]
            for i, line in enumerate(lines):
                out.append(_at(1 + i) + line[: self.cols - 1] + "\033[K")
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
            if k == "note":
                color = e.get("color", "")
                rows += [("", (color + w) if color else w)
                         for w in self._wrap_plain(e["text"], W)]
            elif k == "user":
                rows.append(("", ""))
                rows += [(BG_USER, w) for w in
                         self._wrap_plain(e["text"], self.cols - 4, INK)]
                rows.append(("", ""))
            elif k == "think":
                rows += [("", w) for w in
                         self._wrap_plain(_md(e["text"], W), W, DIM + ITAL)]
                rows.append(("", ""))
            elif k == "answer":
                rows += [("", w) for w in
                         self._wrap_plain(_md(e["text"], W), W, INK)]
                rows.append(("", ""))
            elif k == "tool":
                rows.append(("", ""))
                meta = f" {DIM}({e['meta']})" if e.get("meta") else ""
                rows.append((BG_TOOL, f"{GRTXT}$ {_md(e['head'], tables=False)}{meta}"))
                rows.append((BG_TOOL, ""))
                if e.get("out") is None:
                    body = [f"{DIM}running…"]
                else:
                    out_lines = _md(e["out"], self.cols - 4,
                                    tables=False).splitlines() or ["(no output)"]
                    if len(out_lines) > 10 and not self.expand_tools:
                        out_lines = [f"{DIM}… ({len(out_lines) - 10} earlier "
                                     f"lines, ctrl+o to expand)"] + out_lines[-10:]
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
        max_top = max(0, len(all_lines) - self.pane_h)
        start = max_top if self.scroll_top is None \
            else max(0, min(self.scroll_top, max_top))
        lines = all_lines[start:start + self.pane_h]
        out = ["\033[s"]
        for i in range(self.pane_h):
            row = self.pane_top + i
            if i < len(lines):
                bg, text = lines[i]
                if bg:   # band rows: fill the full width with the background
                    pad = " " * max(0, self.cols - 1 - vis_len(text))
                    out.append(_at(row) + bg + text + pad + R0 + "\033[K")
                else:
                    out.append(_at(row) + f"{text}{R0}\033[K")
            else:
                out.append(_at(row) + "\033[K")
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
        elif self.scroll_top is not None:
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
        self.wheel_h = 2 if self.compact else 21
        self.pane_top = self.wheel_h + 1
        self.pane_h = max(3, self.sep_row - 1 - self.wheel_h)
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
                   completer=None, on_options=None, on_expand=None) -> str:
        """Bottom-row prompt with line editing: arrows, history (UP/DOWN),
        tab completion. The wheel keeps spinning above it."""
        editor = LineEditor(self._redraw_input, history=history,
                            completer=completer, on_options=on_options,
                            on_scroll=self.scroll_by,
                            on_follow=self.scroll_follow,
                            on_expand=on_expand)
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
        """Pane scrollback: +step = newer, -step = older. Reaching the bottom
        re-enables live follow."""
        total = len(self._wrapped())
        max_top = max(0, total - self.pane_h)
        top = max_top if self.scroll_top is None else self.scroll_top
        top = max(0, min(top + step, max_top))
        self.scroll_top = None if top >= max_top else top
        self._dirty.set()

    def scroll_follow(self):
        self.scroll_top = None
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
            except Exception as e:            # never kill the worker loop
                print(f"\n[turn error] {e}")


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

    def completer(buf, cur):
        head = buf[:cur]
        if head.startswith("/model "):
            if time.time() - model_cache["t"] > 30:
                model_cache["list"] = agent.available_models()
                model_cache["t"] = time.time()
            pool = model_cache["list"]
        elif head.startswith("/provider "):
            pool = agent.provider_names()
        elif head.startswith("/tools "):
            pool = ["off", "on"]
        elif head.startswith("/allow rm "):
            from ..policy import permissions as _perm
            pool = [str(i) for i in range(1, len(_perm.list_allow()) + 1)]
        elif head.startswith("/allow "):
            pool = ["rm"]
        elif " " not in head and head.startswith("/"):
            pool = COMMANDS
        else:
            return None
        pre, _, _word = head.partition(" ")
        return complete(buf, cur, pool, len(pre) + 1 if " " in head else 0)

    def on_options(matches):
        screen.say(f"{DIM}{'  '.join(matches[:15])}{R0}")
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
        screen.status_right_extra = f"({agent.active_name()}) {agent.current_model()}"

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
        screen.kick()

        def on_delta(piece):
            screen.status = "speaking…"
            screen.stream_piece(piece, "", "answer")

        def on_reason(piece):
            screen.status = "thinking…"
            screen.stream_piece(piece, "", "think")

        screen.busy = True
        try:
            answer = agent.send(text, on_delta=on_delta, on_reason=on_reason,
                                on_tool=on_tool, on_result=on_result)
        except KeyboardInterrupt:
            screen.busy = False
            screen.status = "stopped"
            screen.finish_stream()
            screen.say(f"{DIM}⏹ stopped — kept whatever streamed so far{R0}")
            screen.say("")
            screen.kick()
            return
        except Exception as e:
            screen.busy = False
            screen.status = "ready"
            screen.finish_stream()
            screen.say(f"{RED}✗ {e}{R0}")
            screen.say("")
            screen.kick()
            return
        screen.busy = False
        screen.status = "ready"
        screen.finish_stream()
        if not answer:
            screen.say(f"{GREEN}(no text){R0}")
        screen.say("")
        screen.kick()

    # turns run on a worker thread so you can keep typing (and queue) while
    # the model is working
    turns = Turns(run_turn, on_change=lambda n: setattr(screen, "queued", n))

    while True:
        refresh_statusbar()
        try:
            line = screen.input_line(f"{GOLD}❯ {R0}", history=history,
                                     completer=completer,
                                     on_options=on_options,
                                     on_expand=screen.toggle_tools).strip()
        except KeyboardInterrupt:
            # ^C at the prompt: cancel the line, never the session
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
                for h in ["/help            this", "/quit            exit",
                          "/stop            drop queued messages (in-flight finishes)",
                          "/new             fresh context (memory keeps history)",
                          "/provider        list providers", "/provider <n>    switch provider",
                          "/model           list models (numbered)", "/model <n|name>  switch model",
                          "/model refresh   re-query the server's model list",
                          "/tools           list tools", "/tools off|on    disable/enable tools",
                          "/allow           list whitelisted commands",
                          "/allow <regex>   whitelist a command (runs without asking)",
                          "/allow rm <n>    remove a whitelist rule",
                          "/cd <path>       change the shell working dir (persists)",
                          "/pwd             where the shell runs",
                          "/mouse on|off    wheel-scroll the pane (off = native copy/paste)",
                          "/memory          recent stored messages",
                          "/clear           wipe stored memory"]:
                    screen.say(f"  {h}")
                screen.say(f"{DIM}type while it works — messages queue · ^C clears the line · "
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
            elif cmd == "/stop":
                dropped = turns.clear()
                screen.say(f"{DIM}{'cleared ' + str(dropped) + ' queued message(s)' if dropped else 'queue is empty — the reply in flight will finish'}{R0}")
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
