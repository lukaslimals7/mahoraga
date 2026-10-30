"""Scrolling must hold still.

Two regressions this pins:
  * new rows arriving while scrolled up moved the view (absolute row index)
  * a resize rewrapped everything and dropped the reader somewhere else
"""
import io, re, sys
sys.path.insert(0, ".")

from mahoraga.agent.cli import Screen

# the pane paints to sys.stdout; keep the test output readable
_real_stdout = sys.stdout
sys.stdout = io.StringIO()


class Cap:
    def __init__(self):
        self.buf = ""

    def write(self, s):
        self.buf += s

    def flush(self):
        pass


def first_visible(s: Screen) -> str:
    """Paint the pane into a buffer and return the top row's text."""
    cap = Cap()
    old, sys.stdout = sys.stdout, cap
    try:
        s._paint_pane()
    finally:
        sys.stdout = old
    rows = re.split(r"\x1b\[\d+;\d+H", cap.buf)[1:]
    for r in rows:
        clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", r).rstrip()
        if clean:
            return clean
    return ""


s = Screen()
s.cols, s.rows = 100, 40
s.compact = False
s.pane_h = 12
s.pane_top = 2

for i in range(40):
    s.say(f"line{i} — some transcript content that wraps the row")

s._paint_pane()
assert s.scroll_off == 0, "expected live follow at the tail"

# scroll up, then let new rows arrive: the view must not move
s.scroll_by(-5)
top_before = first_visible(s)

for i in range(40, 55):
    s.say(f"line{i} — streamed while the reader was scrolled up")
s._paint_pane()
top_after = first_visible(s)

assert top_before == top_after, f"view drifted while streaming:\n  before: {top_before!r}\n  after:  {top_after!r}"

# a resize rewraps every row; the reader should stay near the same content
s.cols = 60                      # narrower -> more wrapped rows
s.rows = 44
s.pane_h = 14
s.pane_top = 2
s._paint_pane()
top_resized = first_visible(s)
assert top_resized.startswith("line"), top_resized
# it may land a row or two off (rewrapping changes row counts) but not jump home
mb = re.search(r"line(\d+)", top_before)
mr = re.search(r"line(\d+)", top_resized)
assert mb and mr, (top_before, top_resized)
moved = abs(int(mb.group(1)) - int(mr.group(1)))
assert moved <= 8, f"resize jumped {moved} lines"

# PgDn back to the tail re-enables follow
s.scroll_follow()
s._paint_pane()
assert s.scroll_off == 0
tail_rows = s._wrapped()
assert "line54" in "\n".join(t for _, t in tail_rows[-6:]), "follow did not return to the newest line"
sys.stdout = _real_stdout
print(f"scroll anchoring OK (held on {top_before!r} while 15 rows arrived; resize moved {moved} lines)")
