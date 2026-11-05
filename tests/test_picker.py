#!/usr/bin/env python3
"""The picker's list maths, without a terminal.

`/model` and `/provider` are now modal scrolling lists, so the arithmetic that
decides WHICH rows are visible (and keeps the cursor row on screen) is the part
that must not drift. Painting and key reading are covered in test_picker_tui.py.

Run: python3 tests/test_picker.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from mahoraga.agent.picker import Picker, window_slice, _fit

fails = []


def check(label, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", label, "" if cond else extra)
    if not cond:
        fails.append(label)


# ------------------------------------------------------------ window_slice
check("a short list shows everything", window_slice(3, 10, 0, 0) == (0, 3))
check("long list starts at the top", window_slice(100, 10, 0, 0) == (0, 10))
check("selection inside the window does not scroll",
      window_slice(100, 10, 5, 0) == (0, 10))
check("selection below the window scrolls by one",
      window_slice(100, 10, 10, 0) == (1, 11))
check("selection above the window scrolls up to it",
      window_slice(100, 10, 2, 40) == (2, 12))
check("the window never runs past the end",
      window_slice(12, 10, 11, 0) == (2, 12))
check("a stale offset is clamped into range",
      window_slice(12, 10, 11, 999) == (2, 12))
check("height 0 still yields one row", window_slice(5, 0, 4, 0) == (4, 5))
check("empty list", window_slice(0, 10, 0, 0) == (0, 0))

# ------------------------------------------------------------------ _fit
check("short text is untouched", _fit("abc", 10) == "abc")
check("long text is cut with an ellipsis", _fit("abcdefghij", 5) == "abcd…" and len(_fit("abcdefghij", 5)) == 5)
check("zero width gives nothing", _fit("abc", 0) == "")

# ------------------------------------------------------- picker list state
class FakeScreen:
    pane_top = 1
    pane_h = 8
    cols = 100


def picker(labels, marks=()):
    items = [{"label": l, "value": l, "mark": l in marks} for l in labels]
    return Picker(FakeScreen(), "models", items, footer="hint")


p = picker(["a-model", "b-model", "nemotron-3.5", "c-model"], marks=["b-model"])
sel = p.selected()
check("starts on the marked (current) item", bool(sel) and sel["value"] == "b-model")
check("item rows = pane minus header/footer", p.height() == 6)

p.filter = "nem"
check("typing filters the list", [p.items[i]["label"] for i in p.visible()] == ["nemotron-3.5"])
check("filter is case-insensitive", (lambda q: (setattr(q, "filter", "NEM"), q.visible())[1])(picker(["x", "nemotron"])) == [1])

p2 = picker(["alpha", "beta"])
p2.filter = "zzz"
check("a filter that matches nothing selects nothing", p2.selected() is None)

# painting must be row-absolute, erase whole lines, and mark the cursor row
buf = io.StringIO()
real = sys.stdout
try:
    sys.stdout = buf
    picker(["one", "two", "three"]).paint()
finally:
    sys.stdout = real
out = buf.getvalue()
check("paints with absolute addressing", "\033[2;1H" in out or "\033[1;1H" in out)
check("every row erases the whole line first",
      out.count("\033[2K") >= 3, str(out.count("\033[2K")))
check("the cursor row is marked", "❯" in out)
check("saves and restores the cursor", out.startswith("\033[s") and out.endswith("\033[u"))

# the modal must fit the pane even when there are far more items than rows
p3 = picker([f"model-{i}" for i in range(200)])
p3.pos = 150
buf = io.StringIO()
try:
    sys.stdout = buf
    p3.paint()
finally:
    sys.stdout = real
painted = buf.getvalue()
check("only pane-sized rows are painted", painted.count("\033[2K") <= FakeScreen.pane_h + 2,
      str(painted.count("\033[2K")))
check("the selected row is on screen", "model-150" in painted)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("picker maths OK")
