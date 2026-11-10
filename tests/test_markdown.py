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
check("narrow: wraps instead of ellipsizing",
      "…" not in narrow and all(_w(l) <= 34 for l in narrow.split("\n")))

# ---- a wide content column must wrap, never be ellipsized ---------------
# The regression: a "Summary" column was capped at 30 chars and cut with an
# ellipsis, so the very information the model was asked for never showed.
summary_md = """| Component | Summary |
|---|---|
| api-gateway | Routes incoming requests to backend services, applies rate limits, and terminates TLS |
| auth-service | Issues and validates JWT tokens, handles refresh, and stores sessions in Redis |
"""
sout = render(summary_md, width=110)
check("summary: no ellipsis", "…" not in sout)
check("summary: every word survives",
      all(w in sout for w in ("Routes", "rate", "limits", "terminates",
                              "refresh", "Redis")), "")
check("summary: fits the pane", all(_w(l) <= 110 for l in sout.split("\n")),
      str(max(_w(l) for l in sout.split("\n"))))

# narrow pane: the same column wraps to the pane instead of being cut
narrow_sum = render(summary_md, width=40)
check("summary narrow: no ellipsis", "…" not in narrow_sum)
check("summary narrow: fits 40 cols",
      all(_w(l) <= 40 for l in narrow_sum.split("\n")),
      str(max(_w(l) for l in narrow_sum.split("\n"))))

# continuation lines line up under their own column
nlines = [visible(l) for l in narrow_sum.split("\n")]
i = next(i for i, l in enumerate(nlines) if l.strip().startswith("api-gateway"))
col = nlines[i].index("Routes")
check("summary: wrapped line aligns under its column",
      nlines[i + 1].startswith(" " * col), repr(nlines[i + 1]))

# ---- a table can never be wider than the pane --------------------------
# More columns than fit: they wrap narrow rather than clipping off the edge.
wide_md = ("| " + " | ".join(f"column{i}" for i in range(12)) + " |\n"
           + "|" + "---|" * 12 + "\n"
           + "| " + " | ".join("some long-ish value" for _ in range(12)) + " |")
wide = render(wide_md, width=60)
check("wide table: fits the pane", all(_w(l) <= 60 for l in wide.split("\n")),
      str(max(_w(l) for l in wide.split("\n"))))
check("wide table: no ellipsis", "…" not in wide)

# ---- render() must never return a table wider than the width it was given -
# (a `max(20, width)` floor used to break the contract below 20 columns)
narrow_table = "| Folder | Size |\n|---|---|\n| iso-project | 50G |\n| gyaanix-v1 | 17G |"
for w in (8, 12, 16, 19, 20, 40):
    r = render(narrow_table, width=w)
    check(f"table width {w}: nothing wider than requested",
          all(_w(l) <= w for l in r.split("\n")),
          str(max(_w(l) for l in r.split("\n"))))

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
