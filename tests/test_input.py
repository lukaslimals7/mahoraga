"""Unit tests for the line editor's pure logic: key decoding, line state,
tab completion. No tty needed — keys are fed through a fake byte reader."""
import sys
sys.path.insert(0, ".")
from mahoraga.agent.lineedit import (
    decode_key, LineState, complete, read_byte,
    UP, DOWN, RIGHT, LEFT, HOME, END, DELETE, BACKSPACE, TAB, ENTER,
    EOF, INTERRUPT, KILL_END, KILL_START, WORD_BACK, TEXT, IGNORE,
)


def reader_for(data: bytes):
    q = list(data)
    return lambda: bytes([q.pop(0)]) if q else b""


def keys(data: bytes):
    """Feed a whole byte stream through decode_key, collect actions."""
    r = reader_for(data)
    out = []
    while True:
        a, t = decode_key(r)
        if a == EOF:
            break
        out.append((a, t))
    return out


# ---- key decoding ----
got = keys(b"\x1b[A\x1b[B\x1b[C\x1b[D")
assert got == [(UP, ""), (DOWN, ""), (RIGHT, ""), (LEFT, "")], got
got = keys(b"\x1b[H\x1b[F\x1b[3~\x1b[1~\x1b[4~")
assert got == [(HOME, ""), (END, ""), (DELETE, ""), (HOME, ""), (END, "")], got
got = keys(b"\x1bOA\x1bOB")           # application-cursor-key variants
assert got == [(UP, ""), (DOWN, "")], got
got = keys(b"\r\n\x7f\t")
assert got == [(ENTER, ""), (ENTER, ""), (BACKSPACE, ""), (TAB, "")], got
got = keys(b"\x01\x05\x0b\x15\x17\x03")
assert got == [(HOME, ""), (END, ""), (KILL_END, ""), (KILL_START, ""),
               (WORD_BACK, ""), (INTERRUPT, "")], got
got = keys(b"hello")
assert got == [(TEXT, "h"), (TEXT, "e"), (TEXT, "l"), (TEXT, "l"), (TEXT, "o")], got
got = keys("é".encode())              # multibyte utf-8 lands as ONE text key
assert got == [(TEXT, "é")], got
got = keys(b"\x1b")                    # lone ESC -> ignored, no crash
assert got == [(IGNORE, "")], got
print("key decoding: OK")

# ---- line state ----
s = LineState()
for ch in "hello":
    s.insert(ch)
assert (s.buf, s.cur) == ("hello", 5)
s.left(); s.left(); s.insert("X")
assert (s.buf, s.cur) == ("helXlo", 4), (s.buf, s.cur)
s.backspace()
assert (s.buf, s.cur) == ("hello", 3), (s.buf, s.cur)
s.home(); s.delete()
assert (s.buf, s.cur) == ("ello", 0)
s.end(); s.kill_start()
assert (s.buf, s.cur) == ("", 0)
s.insert("rm -rf /tmp"); s.word_back()
assert s.cur == 7, s.cur            # "/tmp" is one word (no spaces)
s.kill_end()
assert s.buf == "rm -rf "
print("line editing: OK")

# ---- history navigation ----
s = LineState(["first", "second", "third"])
s.insert("draft")
s.history_prev()                      # -> third, draft stashed
assert s.buf == "third"
s.history_prev(); s.history_prev()
assert s.buf == "first"
s.history_prev()                      # clamps at oldest
assert s.buf == "first"
s.history_next(); s.history_next(); s.history_next()
assert s.buf == "draft", s.buf        # back to the stashed draft
s.insert("!")
assert s.buf == "draft!"
print("history navigation: OK")

# ---- completion ----
pool = ["/provider", "/model", "/memory"]
b, c, m = complete("/pro", 4, pool, 0)
assert (b, c) == ("/provider", 9) and m == ["/provider"], (b, c, m)
b, c, m = complete("/m", 2, pool, 0)
assert b == "/m" or b == "/me" or b == "/mo", b   # common prefix of /model,/memory
assert sorted(m) == ["/memory", "/model"], m      # several matches reported
b, c, m = complete("/provider de", 12,
                   ["deepseek", "mimo", "local"], 10)
assert (b, c) == ("/provider deepseek", 18), (b, c)
b, c, m = complete("/nope", 5, pool, 0)
assert (b, c, m) == ("/nope", 5, []), (b, c, m)
print("tab completion: OK")

print("INPUT GAUNTLET: ALL OK")
