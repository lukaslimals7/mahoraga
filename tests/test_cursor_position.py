"""Painting must never leave the cursor somewhere else.

Every region saves the cursor, paints with absolute addressing, and restores.
The slim banner branch used to save but never restore, so at 12 frames/second
the cursor ended up parked under the wheel instead of on the prompt row.
"""
import io, sys
sys.path.insert(0, ".")

from mahoraga.agent.cli import Screen


class Cap:
    def __init__(self):
        self.buf = ""

    def write(self, s):
        self.buf += s

    def flush(self):
        pass


def paint(screen, method: str) -> str:
    cap = Cap()
    old, sys.stdout = sys.stdout, cap
    try:
        getattr(screen, method)()
    finally:
        sys.stdout = old
    return cap.buf


def assert_balanced(buf: str, label: str):
    saves = buf.count("\033[s")
    restores = buf.count("\033[u")
    assert saves == restores, (
        f"{label}: {saves} save(s) but {restores} restore(s) — "
        f"the cursor is left where painting ended")
    assert saves > 0, f"{label}: never saved the cursor"


s = Screen()
s.cols, s.rows = 120, 50
s.compact = False
s.status = "speaking…"

# full banner (idle, 22 rows of art)
s.busy = False
s._ever_busy = False
s.wheel_h = s._wheel_rows()
assert s.wheel_h > 8, s.wheel_h
assert_balanced(paint(s, "_paint_wheel"), "full banner")

# slim banner: the case that broke — busy, sticky, 4 rows
s.busy = True
s._ever_busy = True
s.wheel_h = s._wheel_rows()
assert s.wheel_h < 8, s.wheel_h
assert_balanced(paint(s, "_paint_wheel"), "slim banner (busy)")

# compact terminal (2 rows)
s.compact = True
s.wheel_h = s._wheel_rows()
assert_balanced(paint(s, "_paint_wheel"), "compact terminal")

# the other regions, for the same invariant
s.compact = False
s.wheel_h = s._wheel_rows()
assert_balanced(paint(s, "_paint_pane"), "pane")
assert_balanced(paint(s, "_paint_bottom"), "status bar")

print("cursor restore OK: wheel (full/slim/compact), pane and status bar all balance")
