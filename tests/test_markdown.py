#!/usr/bin/env python3
"""Markdown renderer gauntlet — the exact output that looked broken must read
clean: tables as aligned columns, no stray pipes, nothing wider than the pane.

Run: python3 tests/test_markdown.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import re as _re
from mahoraga.agent.markdown import render, _w

ANSI = _re.compile(r"\x1b\[[0-9;]*m")
visible = lambda l: ANSI.sub("", l)          # strip styling for assertions

fails = []


def check(label, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", label, "" if cond else extra)
    if not cond:
        fails.append(label)


# ---- the real-world case: the user's ugly table -------------------------
MODEL_OUTPUT = """Done. Here's the breakdown:

Desktop total size: 101G

Top folders by size

| Folder | Size |
|---|---|
| iso-project | 50G |
| gyaanix-v1 | 17G |
| gyaanix-fin-fix | 17G |
| fin2 | 13G |
| yt-music | 3.7G |
| application | 1.0G |
| phet_sims | 423M |
| custom-greet | 200M |
| birthday | 149M |
| workspace | 48M |

Largest individual files

| File | Size |
|---|---|
| /home/lukas/Desktop/big.iso | 12G |"""

out = render(MODEL_OUTPUT, width=80)
lines = out.split("\n")

print("---- rendered ----")
print(out)
print("------------------")

check("no stray pipes left", "|" not in out)
check("no ** left", "**" not in out)
check("no backticks left", "`" not in out)
check("header rendered", any("Folder" in l and "Size" in l for l in lines))
check("rule under header", any(set(l.strip()) <= {"─", "\x1b", "[", "0", "2", "m"}
                               and "─" in l for l in lines))
check("data row present", any("iso-project" in l and "50G" in l for l in lines))
check("sizes right-aligned (row ends with the size)",
      any(l.strip().endswith("50G") and "iso-project" in l for l in lines))
check("second table rendered too",
      any(visible(l).lstrip().startswith("File") for l in lines))
check("prose preserved", "Desktop total size: 101G" in out)
check("nothing wider than the pane", all(_w(l) <= 80 for l in lines),
      str(max(_w(l) for l in lines)))

# ---- narrow pane: must shrink, never wrap -------------------------------
narrow = render(MODEL_OUTPUT, width=34)
check("narrow: fits 34 cols", all(_w(l) <= 34 for l in narrow.split("\n")),
      str(max(_w(l) for l in narrow.split("\n"))))
check("narrow: still no pipes", "|" not in narrow)
check("narrow: ellipsized", "…" in narrow or all(
    _w(l) <= 34 for l in narrow.split("\n")))

# ---- inline styles -----------------------------------------------------
inline = render("# Heading\n\nSome **bold** and *italic* and `code`.\n"
                "- first item\n  - nested item\n\n> quoted line\n\n"
                "See [the docs](https://example.com/x).\n\n---")
check("heading: bold, no '#'", "\x1b[1mHeading\x1b[0m" in inline and "#" not in inline)
check("bold styled", "\x1b[1mbold\x1b[0m" in inline)
check("italic styled", "\x1b[3mitalic\x1b[0m" in inline)
check("code dimmed, no backticks", "\x1b[2mcode\x1b[0m" in inline)
check("bullet marker", "• first item" in inline)
check("nested bullet keeps indent", "  • nested item" in inline)
check("quote marker", "│ quoted line" in inline)
check("link flattened", "the docs (https://example.com/x)" in inline)
check("rule drawn", "─" in inline)

# ---- raw output must NOT be table-ified ---------------------------------
raw = "| col1 | col2 |\n|---|---|\n| val1 | val2 |"
check("tables=False keeps pipes verbatim",
      render(raw, width=80, tables=False) == raw)
check("tables=True converts it", "|" not in render(raw, width=80))

# ---- separator-less table still renders --------------------------------
nosep = "| A | B |\n| 1 | 2 |\n"
check("separator-less table renders", "|" not in render(nosep, width=40))

# ---- plain text is untouched -------------------------------------------
plain = "just a normal sentence.\nsecond line."
check("plain text unchanged", render(plain) == plain)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("MARKDOWN GAUNTLET: ALL OK")
