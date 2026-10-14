#!/usr/bin/env python3
"""Replay a recorded TUI stream into a screen grid, render as HTML.
Handles SGR truecolor fg/bg (38;2;r;g;b / 48;2;r;g;b) + bold/dim/italic."""
import re, sys

LOG = "/tmp/tui.log"
OUT = "/tmp/tui_render.html"
ROWS, COLS = 60, 160

raw = open(LOG, "rb").read().decode("utf-8", errors="replace")
grid = [[(" ", "#d0cdc6", "#101010", "") for _ in range(COLS)] for _ in range(ROWS)]

fg, bg, style = "#d0cdc6", "#101010", ""
row = col = 1
srow, scol = 1, 1

i, n = 0, len(raw)
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
                if int(params or 0) == 0:
                    for c in range(col, COLS + 1):
                        grid[row - 1][c - 1] = (" ", fg, bg, style)
            elif cmd == "s":
                srow, scol = row, col
            elif cmd == "u":
                row, col = srow, scol
            elif cmd == "m":
                ps = (params or "0").split(";")
                j = 0
                while j < len(ps):
                    p = ps[j]
                    if p == "0":
                        fg, bg, style = "#d0cdc6", "#101010", ""
                    elif p == "1":
                        style = "font-weight:600"
                    elif p == "2":
                        fg = "#8b8880"
                    elif p == "3":
                        style = "font-style:italic"
                    elif p == "31":
                        fg = "#e07a6e"
                    elif p == "32":
                        fg = "#9ed8a8"
                    elif p == "33":
                        fg = "#e8b877"
                    elif p == "36":
                        fg = "#8fd3d3"
                    elif p in ("38", "48") and j + 1 < len(ps) and ps[j + 1] == "2":
                        r_, g_, b_ = (int(x) for x in ps[j + 2:j + 5])
                        colr = f"#{r_:02x}{g_:02x}{b_:02x}"
                        if p == "38":
                            fg = colr
                        else:
                            bg = colr
                        j += 4
                    j += 1
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
            grid[row - 1][col - 1] = (ch, fg, bg, style)
        col += 1
    i += 1

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def span(color, back, st, buf):
    extra = (f"background:{back};" if back != "#101010" else "") + (st + ";" if st else "")
    return f'<span style="color:{color};{extra}">{esc(buf)}</span>'

parts = []
for r in range(ROWS):
    line, cur, buf = "", None, ""
    for c in range(COLS):
        cell = grid[r][c]
        if cell[1:] != (cur[1:] if cur else None):
            if buf:
                line += span(*cur[1:], buf)
            buf = ""
            cur = ("",) + cell[1:]
        buf += cell[0]
    if buf:
        line += span(*cur[1:], buf)
    parts.append(line)

html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body {{ margin:0; background:#101010; }}
pre {{ color:#d0cdc6; background:#101010;
  font: 13px 'DejaVu Sans Mono', monospace; line-height: 1.32;
  padding: 14px 16px; margin:0; white-space: pre; }}
</style></head><body><pre>{''.join(p + chr(10) for p in parts)}</pre></body></html>"""

open(OUT, "w").write(html)
print("rendered", OUT)
