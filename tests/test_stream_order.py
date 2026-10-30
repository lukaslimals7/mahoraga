"""Block order must be: input → reasoning → answer → tool card → reasoning → answer.

The regression this pins: after a tool call the stream index was reused, so the
model's next sentence was appended to the answer block ABOVE the command card —
the transcript read "final output on top of commands ran".
"""
import sys
sys.path.insert(0, ".")

from mahoraga.agent.cli import Screen, _clean

s = Screen()

# turn 1: think, answer, then the model asks for a tool
s.user_msg("what is in this dir?")
s.stream_piece("thinking one", "", "think")
s.stream_piece("answer one", "", "answer")
s.tool_call("ls -la", "timeout 60s")
s.tool_result("total 8\ndrwxr-xr-x 2 lukas lukas", 0.2)

kinds = [e["kind"] for e in s.entries]
assert kinds == ["user", "think", "answer", "tool"], kinds

# turn 1 continues: the next iteration must open NEW blocks after the card
s.finish_stream()                      # what run_turn's on_thinking does
s.stream_piece("thinking two", "", "think")
s.stream_piece("answer two", "", "answer")

kinds = [e["kind"] for e in s.entries]
assert kinds == ["user", "think", "answer", "tool", "think", "answer"], kinds

# and the text must be in the right block, not merged into the first answer
assert s.entries[2]["text"] == "answer one"
assert s.entries[5]["text"] == "answer two"

# the rendered rows must show the second answer BELOW the tool card
rows = [t for _, t in s._wrapped()]
joined = "\n".join(rows)
pos_card = joined.index("$ ls -la")
pos_answer2 = joined.index("answer two")
pos_answer1 = joined.index("answer one")
assert pos_answer1 < pos_card < pos_answer2, (pos_answer1, pos_card, pos_answer2)

# ---- escape sanitising: command output may not move the cursor -------------
assert "\x1b" not in _clean("\x1b[31mred\x1b[0m text")
assert _clean("\x1b[2J\x1b[1;1Hcleared") == "cleared"
assert _clean("a\rb\r\nc") == "a\nb\nc"
assert _clean("tab\there ok") == "tab\there ok"
s.tool_call("printf '\\033[2Jpwned'", "")
s.tool_result("\x1b[2J\x1b[1;1H\x1b[31mred\x1b[0m output", 0.1)
render = "\n".join(t for _, t in s._wrapped())
assert "\x1b[2J" not in render and "\x1b[1;1H" not in render, "escape leaked into the pane"

# ---- the wheel gives rows back while busy ----------------------------------
s.rows, s.cols = 50, 120
s.busy = False
idle_rows = s._wheel_rows()
s.busy = True
busy_rows = s._wheel_rows()
assert busy_rows < idle_rows, (busy_rows, idle_rows)
assert busy_rows == 4, busy_rows
print(f"stream order + sanitising + wheel shrink OK (wheel {idle_rows} -> {busy_rows} rows)")
