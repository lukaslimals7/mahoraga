"""Readline-style line editing for the TUI prompt — stdlib only.

Python's input() has no escape-sequence handling: arrow keys leak as ^[[A and
there is no history or completion. This module reads keys itself in cbreak mode
(no echo — we paint the prompt row), decodes common sequences, edits a line
buffer with a cursor, and navigates a history list.

Pure logic (decode_key, LineState) is separated from I/O (LineEditor) so tests
can drive it with a fake byte reader.
"""
from __future__ import annotations
import codecs, os, select, sys, termios, tty

# decoded key actions
UP, DOWN, RIGHT, LEFT, HOME, END = "up", "down", "right", "left", "home", "end"
DELETE, BACKSPACE, TAB, ENTER = "delete", "backspace", "tab", "enter"
EOF, INTERRUPT, IGNORE = "eof", "interrupt", "ignore"
KILL_END, KILL_START, WORD_BACK = "kill_end", "kill_start", "word_back"
WHEEL_UP, WHEEL_DOWN = "wheel_up", "wheel_down"
PGUP, PGDN = "pgup", "pgdn"
EXPAND = "expand"
TEXT = "text"

_CSI_FINAL = {"A": UP, "B": DOWN, "C": RIGHT, "D": LEFT, "H": HOME, "F": END}
_CSI_TILDE = {"1": HOME, "3": DELETE, "4": END, "5": PGUP, "6": PGDN,
              "7": HOME, "8": END}
_MOUSE_BTN = {64: WHEEL_UP, 65: WHEEL_DOWN}   # SGR wheel buttons


def read_byte(fd: int, timeout: float | None = None) -> bytes:
    if timeout is not None:
        r, _, _ = select.select([fd], [], [], timeout)
        if not r:
            return b""
    try:
        return os.read(fd, 1)
    except OSError:
        return b""


def decode_key(reader) -> tuple[str, str]:
    """Pull one keypress out of `reader` (callable -> one byte, b'' on timeout/EOF).
    Returns (action, text). Escape sequences are resolved in full; a lone ESC
    (reader times out mid-sequence) is ignored."""
    b = reader()
    if not b:
        return EOF, ""
    if b == b"\x1b":
        seq = b""
        while len(seq) < 16:
            nb = reader()
            if not nb:
                return IGNORE, ""     # bare ESC / incomplete sequence
            seq += nb
            # any CSI/SS3 final byte ends the sequence (0x40-0x7E); params
            # like '<', ';', digits are below that range and keep it open
            if len(seq) > 1 and 0x40 <= seq[-1] <= 0x7e:
                break
        s = seq.decode("latin-1")
        if s.startswith("[<") and s[-1] in "Mm":      # SGR mouse report
            try:
                btn = int(s[2:].split(";")[0])
            except ValueError:
                return IGNORE, ""
            return _MOUSE_BTN.get(btn, IGNORE), ""   # clicks/drags ignored
        if s[:1] in ("[", "O") and s[-1] in _CSI_FINAL:
            return _CSI_FINAL[s[-1]], ""
        if s[:1] in ("[", "O") and s[-1] == "~":
            return _CSI_TILDE.get(s[1:-1].split(";")[0], IGNORE), ""
        return IGNORE, ""
    if b in (b"\r", b"\n"):
        return ENTER, ""
    if b in (b"\x7f", b"\x08"):
        return BACKSPACE, ""
    if b == b"\t":
        return TAB, ""
    if b == b"\x03":
        return INTERRUPT, ""
    if b == b"\x04":
        return EOF, ""                # caller decides: delete vs exit
    if b == b"\x01":
        return HOME, ""
    if b == b"\x05":
        return END, ""
    if b == b"\x0b":
        return KILL_END, ""
    if b == b"\x15":
        return KILL_START, ""
    if b == b"\x17":
        return WORD_BACK, ""
    if b == b"\x0f":
        return EXPAND, ""
    if b[0] < 0x20:
        return IGNORE, ""
    # utf-8: this byte plus any continuation bytes
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    out = dec.decode(b)
    while dec.getstate()[0]:
        nb = reader()
        if not nb:
            break
        out += dec.decode(nb)
    return (TEXT, out) if out else (IGNORE, "")


class LineState:
    """The editable line: buffer + cursor + history navigation. Pure logic."""

    def __init__(self, history: list[str] | None = None):
        self.buf = ""
        self.cur = 0
        self.hist = history if history is not None else []
        self._hist_idx: int | None = None   # None = editing a fresh line
        self._stash = ""

    def insert(self, text: str):
        self.buf = self.buf[: self.cur] + text + self.buf[self.cur:]
        self.cur += len(text)
        self._hist_idx = None

    def backspace(self):
        if self.cur > 0:
            self.buf = self.buf[: self.cur - 1] + self.buf[self.cur:]
            self.cur -= 1

    def delete(self):
        self.buf = self.buf[: self.cur] + self.buf[self.cur + 1:]

    def left(self):
        self.cur = max(0, self.cur - 1)

    def right(self):
        self.cur = min(len(self.buf), self.cur + 1)

    def home(self):
        self.cur = 0

    def end(self):
        self.cur = len(self.buf)

    def kill_end(self):
        self.buf = self.buf[: self.cur]

    def kill_start(self):
        self.buf = self.buf[self.cur:]
        self.cur = 0

    def word_back(self):
        i = self.cur
        while i > 0 and self.buf[i - 1] == " ":
            i -= 1
        while i > 0 and self.buf[i - 1] != " ":
            i -= 1
        self.cur = i

    def history_prev(self):
        if not self.hist:
            return
        if self._hist_idx is None:
            self._stash = self.buf
            self._hist_idx = len(self.hist) - 1
        elif self._hist_idx > 0:
            self._hist_idx -= 1
        self.buf = self.hist[self._hist_idx]
        self.cur = len(self.buf)

    def history_next(self):
        if self._hist_idx is None:
            return
        self._hist_idx += 1
        if self._hist_idx >= len(self.hist):
            self._hist_idx = None
            self.buf = self._stash
        else:
            self.buf = self.hist[self._hist_idx]
        self.cur = len(self.buf)


def complete(buf: str, cur: int, pool: list[str], word_start: int
             ) -> tuple[str, int, list[str]]:
    """Complete the word ending at `cur` from `pool`.
    Returns (new_buf, new_cur, all_matches). Matches the common prefix;
    caller decides what to do when several matches remain."""
    word = buf[word_start:cur]
    matches = [p for p in pool if p.startswith(word)]
    if not matches:
        return buf, cur, []
    common = os.path.commonprefix(matches)
    new_buf = buf[:word_start] + common + buf[cur:]
    return new_buf, word_start + len(common), matches


def set_raw(fd: int) -> list:
    """Switch the tty to cbreak+noecho for the WHOLE app lifetime and keep the
    saved attrs for restore(). Echo is the app's job (we paint the prompt row);
    leaving the tty in echo mode is what made wheel/arrow bytes print as raw
    ^[[A garbage while the model was generating."""
    saved = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~termios.ECHO                       # lflag: no echo, keep ISIG
    termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
    return saved


def restore(fd: int, saved: list):
    termios.tcsetattr(fd, termios.TCSADRAIN, saved)


class LineEditor:
    """Owns the bottom-row prompt while reading a line: cbreak mode, self-echo,
    history via UP/DOWN, completion via TAB (through a completer callback).

    completer(buf, cur) -> (new_buf, new_cur, matches) | None
    on_options(matches) -> None   (e.g. print them into the pane)
    """

    def __init__(self, redraw, history: list[str] | None = None,
                 completer=None, on_options=None,
                 on_scroll=None, on_follow=None, on_expand=None,
                 on_interrupt=None):
        self.redraw = redraw          # (prompt, buf, cur) -> paints the row
        self.state = LineState(history)
        self.completer = completer
        self.on_options = on_options
        self.on_scroll = on_scroll    # (step) -> None; + = newer, - = older
        self.on_follow = on_follow    # () -> None; jump back to the live tail
        self.on_expand = on_expand    # () -> None; toggle tool-output expand
        # () -> bool; True = it handled the ^C (stopped a reply), False = clear line
        self.on_interrupt = on_interrupt

    def read(self, prompt: str) -> str:
        """Edit one line. Tty is expected in cbreak+noecho (set_raw by the app).
        Ctrl+C clears the line and keeps the session alive; Ctrl+D / /quit exit."""
        stdin = sys.stdin
        if not stdin.isatty():
            # non-tty (tests, pipes): fall back to plain input
            return input(prompt)
        self._loop(stdin.fileno(), prompt)
        return self.state.buf

    def _loop(self, fd: int, prompt: str):
        st = self.state
        self.redraw(prompt, st.buf, st.cur)
        while True:
            first = read_byte(fd)                 # blocks until a keypress
            if not first:
                raise EOFError                    # stdin closed -> clean exit
            queue = [first]
            def qr():                             # 0.05s deadline for sequences
                return queue.pop(0) if queue else read_byte(fd, 0.05)
            action, text = decode_key(qr)
            if action in (WHEEL_UP, PGUP):
                if self.on_scroll:
                    self.on_scroll(-4 if action == WHEEL_UP else -12)
                continue
            if action in (WHEEL_DOWN, PGDN):
                if self.on_scroll:
                    self.on_scroll(4 if action == WHEEL_DOWN else 12)
                continue
            if action == EXPAND:
                if self.on_expand:
                    self.on_expand()
                self.redraw(prompt, st.buf, st.cur)
                continue
            if action == TEXT:
                st.insert(text)
                if self.on_follow:
                    self.on_follow()
            elif action == ENTER:
                return
            elif action == BACKSPACE:
                st.backspace()
            elif action == DELETE:
                st.delete()
            elif action == TAB:
                if self.completer:
                    res = self.completer(st.buf, st.cur)
                    if res:
                        st.buf, st.cur, matches = res
                        if self.on_options and len(matches) > 1:
                            self.on_options(matches)
            elif action == UP:
                st.history_prev()
            elif action == DOWN:
                st.history_next()
            elif action == RIGHT:
                st.right()
            elif action == LEFT:
                st.left()
            elif action == HOME:
                st.home()
            elif action == END:
                st.end()
            elif action == KILL_END:
                st.kill_end()
            elif action == KILL_START:
                st.kill_start()
            elif action == WORD_BACK:
                st.word_back()
            elif action == INTERRUPT:
                if self.on_interrupt and self.on_interrupt():
                    # stopped a running reply — leave the half-typed line intact
                    self.redraw(prompt, st.buf, st.cur)
                    continue
                st.buf, st.cur = "", 0        # ^C at rest: cancel the line
                st._hist_idx = None
                self.redraw(prompt, st.buf, st.cur)
                continue
            elif action == EOF:
                if not st.buf:
                    raise EOFError
                st.delete()           # ctrl-d on non-empty: delete forward
            self.redraw(prompt, st.buf, st.cur)
