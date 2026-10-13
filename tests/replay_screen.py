#!/usr/bin/env python3
"""Replay a recorded TUI stream into a screen grid, render as HTML."""
import re, sys

LOG = "/tmp/tui.log"
OUT = "/tmp/tui_render.html"
ROWS, COLS = 42, 150

raw = open(LOG, "rb").read().decode("utf-8", errors="replace")
# drop script(1) header/footer noise lines
grid = [[(" ", "#d0cdc6") for _ in range(COLS)] for _ in range(ROWS)]

COLOR = {
    "0": "#d0cdc6", "1": "#ffffff", "2": "#8b8880",
    "31": "#e07a6e", "32": "#9ed8a8", "33": "#e8b877",
    "36": "#8fd3d3", "90": "#8b8880",
}
fg = "#d0cdc6"
row = col = 1
srow, scol = 1, 1

i = 0
n = len(raw)
while i < n:
    ch = raw[i]
    if ch == "\x1b":
        m = re.match(r"\x1b\[([0-9;?]*)([a-zA-Z])", raw[i:])
        if m:
            params, cmd = m.group(1), m.group(2)
            if cmd == "H":
                nums = params.split(";")
                row = int(nums[0] or 1); col = int(nums[1] or 1) if len(nums) > 1 else 1
            elif cmd == "K":
                mode = int(params or 0)
                if mode == 0:
                    for c in range(col, COLS + 1):
                        grid[row - 1][c - 1] = (" ", fg)
            elif cmd == "s":
                srow, scol = row, col
            elif cmd == "u":
                row, col = srow, scol
            elif cmd == "m":
                for p in (params or "0").split(";"):
                    if p in COLOR:
                        fg = COLOR[p]
            i += m.end()
            continue
        i += 1
        continue
    if ch == "\r":
        col = 1
    elif ch == "\n":
        row = min(row + 1, ROWS)
    elif ch == "\t":
        col = min(col + 4, COLS + 1)
    elif ch >= " ":
        if 1 <= row <= ROWS and 1 <= col <= COLS:
            grid[row - 1][col - 1] = (ch, fg)
        col += 1
    i += 1

# render
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

parts = []
for r in range(ROWS):
    line = ""
    cur = None
    buf = ""
    for c in range(COLS):
        ch, color = grid[r][c]
        if color != cur:
            if buf:
                parts.append(f'<span style="color:{cur}">{esc(buf)}</span>') if False else None
                line += f'<span style="color:{cur}">{esc(buf)}</span>'
            buf = ""
            cur = color
        buf += ch
    if buf:
        line += f'<span style="color:{cur}">{esc(buf)}</span>'
    parts.append(line)

html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body {{ margin:0; background:#101010; }}
pre {{ color:#d0cdc6; background:#101010;
  font: 13px 'DejaVu Sans Mono', monospace; line-height: 1.28;
  padding: 14px 16px; margin:0; white-space: pre; }}
</style></head><body><pre>{''.join(p + chr(10) for p in parts)}</pre></body></html>"""

open(OUT, "w").write(html)
print("rendered", OUT)
