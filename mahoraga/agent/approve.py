"""The interactive permission card — the popup for a tool call that wants to run.

Why this exists: the old prompt was `print` + `input()` on the *worker* thread,
which cannot work in this TUI twice over. The tty is in cbreak+noecho, so
input() echoes nothing and hands back raw bytes; and the render thread keeps
repainting the pane behind it. It also could only offer y/n, not "this session"
vs "whitelist forever".

So the card runs on the INPUT thread: the line editor polls for pending
approvals (see LineEditor's `poll`/`on_approval`), the worker thread blocks in
Screen.request_approval until the human answers, and the card paints absolutely
into the pane exactly the way the pickers do — save/restore the cursor, every
row starts with CSI 2K, and Screen.modal keeps the render thread out meanwhile.

Answers (see OPTIONS):
  session  allow this exact command for the rest of the process
  always   append it to ~/.mahoraga/allow
  deny     refuse — also what Esc and ^C do, because an unanswered agent hangs

The key mapping is a pure function, so it is tested without a terminal.
"""
from __future__ import annotations
import json
import re
import sys

from .lineedit import (DOWN, END, ENTER, HOME, IGNORE, INTERRUPT, PGUP, PGDN,
                       TEXT, UP, WHEEL_DOWN, WHEEL_UP, decode_key, read_byte)

BOLD = "\033[1m"
DIM = "\033[2m"
GOLD = "\033[33m"
R0 = "\033[0m"
GRTXT = "\033[38;2;198;222;198m"     # pale green, same as tool cards
BG_SEL = "\033[48;2;38;52;72m"      # the slate band a user message uses

# (value, label, hotkey) — value is what Screen.request_approval returns
OPTIONS = [
    ("session", "Allow this for this session", "y"),
    ("always", "Allow this to whitelist", "a"),
    ("deny", "Deny", "n"),
]

_ANSI = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"            # CSI: colours, cursor moves, clears
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"   # OSC: window titles, hyperlinks
    r"|\x1b[@-Z\\-_]"                       # two-character escapes
)


def _clean(text: str) -> str:
    """Strip escapes/control chars from text we did not author (same reason as
    Screen._clean: a stray CSI inside an absolutely-addressed pane repaints
    other rows)."""
    if not text:
        return ""
    t = _ANSI.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return "".join(ch for ch in t if ch in "\n\t" or ord(ch) >= 0x20)


def _vis(text: str) -> int:
    return len(_ANSI.sub("", text))


def _wrap(text: str, width: int) -> list[str]:
    """Word-wrap by visual width; escape codes don't count toward length."""
    out, cur = [], ""
    for word in (text or "").split(" "):
        cand = f"{cur} {word}" if cur else word
        if _vis(cand) > width and cur:
            out.append(cur)
            cur = word
        else:
            cur = cand
    out.append(cur)
    return out or [""]


def handle_key(pos: int, action: str, text: str) -> tuple[int, str | None]:
    """One keypress on the card → (new_pos, choice|None). Pure, no tty.

    choice is "session" | "always" | "deny", or None to keep waiting.
    """
    n = len(OPTIONS)
    if action in (UP, WHEEL_UP):
        return max(0, pos - 1), None
    if action in (DOWN, WHEEL_DOWN):
        return min(n - 1, pos + 1), None
    if action == HOME:
        return 0, None
    if action == END:
        return n - 1, None
    if action == PGUP:
        return max(0, pos - 3), None
    if action == PGDN:
        return min(n - 1, pos + 3), None
    if action == ENTER:
        return pos, OPTIONS[pos][0]
    if action in (INTERRUPT, IGNORE):     # ^C and Esc both refuse
        return pos, "deny"
    if action == TEXT:
        t = text.strip().lower()
        if t in ("1", "2", "3"):
            return int(t) - 1, OPTIONS[int(t) - 1][0]
        for i, (_val, _label, hotkey) in enumerate(OPTIONS):
            if t == hotkey:
                return i, _val
    return pos, None


def _at(row: int, col: int = 1) -> str:
    return f"\033[{row};{col}H"


class ApprovalDialog:
    """One pending tool call, rendered as a card over the pane."""

    def __init__(self, screen, tool_name: str, args: dict):
        self.s = screen
        self.tool = tool_name
        self.args = args or {}
        self.pos = 0

    def command(self) -> str:
        if self.tool in ("shell", "bash", "run_command", "execute_command"):
            return str(self.args.get("command", ""))
        return ""

    def _rows(self) -> list[tuple[str, str]]:
        """(styled_text, background) per card row, top to bottom."""
        width = max(10, self.s.cols - 4)
        cmd = self.command()
        body = _clean(f"$ {cmd}" if cmd
                      else json.dumps(self.args, ensure_ascii=False))
        lines: list[str] = []
        for para in body.splitlines() or [""]:
            lines += _wrap(para, width)
        truncated = len(lines) > 5
        lines = lines[:5]

        rows = [(f" {BOLD}{GOLD}\u26a0 tool call needs permission{R0}", ""),
                (f" {DIM}{self.tool}{R0}", "")]
        rows += [(f" {GRTXT}{l}{R0}", "") for l in lines]
        if truncated:
            rows.append((f" {DIM}\u2026{R0}", ""))
        rows.append(("", ""))
        for i, (_val, label, hotkey) in enumerate(OPTIONS):
            sel = i == self.pos
            mark = "\u276f" if sel else " "
            rows.append((f" {mark} {BOLD if sel else ''}{label}{R0}"
                         f"   {DIM}[{hotkey}]{R0}",
                         BG_SEL if sel else ""))
        rows.append(("", ""))
        rows.append((f" {DIM}\u2191\u2193 move \u00b7 enter confirm \u00b7 "
                     f"esc / ^C deny{R0}", ""))
        return rows

    def paint(self) -> None:
        s = self.s
        rows = self._rows()[:max(1, s.pane_h)]
        out = ["\033[s"]          # save the cursor: never steal the prompt row
        for i in range(max(1, s.pane_h)):
            y = s.pane_top + i
            text, bg = rows[i] if i < len(rows) else ("", "")
            cell = [_at(y) + R0 + "\033[2K"]
            if bg:
                cell.append(bg + text + " " * max(0, s.cols - 1 - _vis(text))
                             + R0 + "\033[K")
            else:
                cell.append(text + "\033[K")
            out.append("".join(cell))
        out.append("\033[u")      # hand the cursor back
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def run(self) -> str:
        """Block on keys until an answer. Returns "session"|"always"|"deny"."""
        s = self.s
        s.modal = True             # the render thread must not paint under us
        s.status = "allow? \u2191\u2193 choose \u00b7 enter confirm \u00b7 esc deny"
        try:
            self.paint()
            fd = sys.stdin.fileno()
            while True:
                try:
                    first = read_byte(fd)
                except (OSError, ValueError, KeyboardInterrupt):
                    return "deny"
                if not first:
                    return "deny"                  # stdin closed: refuse
                queue = [first]

                def qr():                          # 0.05s deadline for sequences
                    return queue.pop(0) if queue else read_byte(fd, 0.05)

                try:
                    action, text = decode_key(qr)
                except (OSError, ValueError, KeyboardInterrupt):
                    return "deny"
                self.pos, choice = handle_key(self.pos, action, text)
                if choice:
                    return choice
                self.paint()
        finally:
            s.modal = False
            s._last_total = None                   # re-anchor the pane at the tail
            s.kick()
            s._paint_pane()
