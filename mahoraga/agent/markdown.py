"""Markdown → terminal renderer (stdlib only).

Models answer in markdown; a terminal that prints raw markdown looks broken
(`| Folder | Size |` and `**bold**` and backticks are noise on screen). This
module turns the markdown the model *writes* into the text a human *reads*:

- tables  → aligned columns, bold header, rule, numeric right-alignment;
            shrunk and ellipsized to fit the pane instead of wrapping
- headings → bold
- bullets  → •
- quotes   → │
- rules    → ────
- `code`   → dim, **bold** → bold, *italic* → italic, [t](u) → t (u)

`render(text, width, tables=True)` is the entry point. Set `tables=False` for
raw program output (tool results), where `|` may be real data.
"""
from __future__ import annotations
import re
import unicodedata

BOLD = "\033[1m"
DIM = "\033[2m"
ITAL = "\033[3m"
R0 = "\033[0m"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _w(s: str) -> int:
    """Visual width: ANSI escapes don't count, CJK/emoji count double."""
    s = _ANSI.sub("", s)
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in s)


def _is_sep_row(line: str) -> bool:
    body = line.strip().strip("|").replace(" ", "")
    return bool(body) and set(body) <= set("-:|")


def _is_row(line: str) -> bool:
    s = line.strip()
    return s.count("|") >= 2 and (s.startswith("|") or s.endswith("|"))


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


_NUM = re.compile(r"[<>~]?\s*[\d][\d.,:/\-]*\s*%?\s*[KMGTPE]?i?B?\s*$", re.I)


def _is_num(cell: str) -> bool:
    """Numbers, sizes and percentages right-align; words left-align."""
    return bool(cell) and bool(_NUM.fullmatch(cell))


def _plain(s: str) -> str:
    """Strip inline markdown without styling (for table cells + measuring)."""
    s = re.sub(r"!?\[([^\]]*)\]\(([^)]*)\)", r"\1", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", s)
    return s.replace("`", "").strip()


def _inline(s: str) -> str:
    """Inline markdown → ANSI (bold / italic / dim code / plain links)."""
    s = re.sub(r"!?\[([^\]]*)\]\(([^)]*)\)", lambda m: f"{m.group(1)} ({m.group(2)})"
               if m.group(2).startswith("http") else m.group(1), s)
    s = re.sub(r"\*\*(.+?)\*\*", lambda m: f"{BOLD}{m.group(1)}{R0}", s)
    s = re.sub(r"__(.+?)__", lambda m: f"{BOLD}{m.group(1)}{R0}", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: f"{ITAL}{m.group(1)}{R0}", s)
    s = re.sub(r"`([^`]+)`", lambda m: f"{DIM}{m.group(1)}{R0}", s)
    return s.rstrip()


def _render_table(rows: list[str], width: int) -> list[str]:
    """Aligned columns: bold header, ─── rule, numeric columns right-aligned."""
    body = [r for r in rows if not _is_sep_row(r)] or rows
    grid = [_cells(r) for r in body]
    ncols = max(len(r) for r in grid)
    grid = [r + [""] * (ncols - len(r)) for r in grid]
    header, data = grid[0], grid[1:]

    cols = [[_plain(r[c]) for r in data] for c in range(ncols)]
    w = []
    for c in range(ncols):
        natural = max([_w(header[c])] + [_w(v) for v in cols[c]] or [1])
        w.append(min(natural, 30))
    right = [bool(cols[c]) and all(_is_num(v) for v in cols[c]) and any(cols[c])
             for c in range(ncols)]

    total = sum(w) + 2 * (ncols - 1)
    while total > width and max(w) > 6:          # shrink the widest column
        i = w.index(max(w))
        w[i] -= 1
        total -= 1

    def cell(text: str, c: int) -> str:
        t = _plain(text)
        if _w(t) > w[c]:
            t = t[: max(1, w[c] - 1)] + "…"
        t = t + " " * max(0, w[c] - _w(t))
        return t if right[c] else t

    def line_of(cells_: list[str], bold: bool) -> str:
        parts = []
        for c, raw in enumerate(cells_):
            t = _plain(raw)
            if _w(t) > w[c]:
                t = t[: max(1, w[c] - 1)] + "…"
            pad = " " * max(0, w[c] - _w(t))
            parts.append(pad + t if right[c] else t + pad)
        row = "  ".join(parts).rstrip()
        return f"{BOLD}{row}{R0}" if bold else row

    out = [line_of(header, True), f"{DIM}{'─' * min(total, width)}{R0}"]
    out += [line_of(r, False) for r in data]
    return out


def render(text: str, width: int = 100, tables: bool = True) -> str:
    """Markdown → clean terminal text."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if tables and _is_row(line):
            block = []
            while i < len(lines) and _is_row(lines[i]):
                block.append(lines[i])
                i += 1
            has_sep = any(_is_sep_row(b) for b in block)
            if len(block) >= 2 and (has_sep or all(_is_row(b) for b in block)):
                out.extend(_render_table(block, max(20, width)))
            else:
                out.extend(_inline(l) for l in block)
            continue

        s = line.rstrip()
        if re.fullmatch(r"\s*([-*_])\1{2,}\s*", s or "x"):
            out.append(f"{DIM}{'─' * min(max(10, width), 60)}{R0}")
        elif m := re.match(r"^(#{1,6})\s+(.*)$", s):
            out.append(f"{BOLD}{_inline(m.group(2))}{R0}")
        elif m := re.match(r"^(\s*)[-*+]\s+(.*)$", s):
            out.append(f"{m.group(1)}• {_inline(m.group(2))}")
        elif m := re.match(r"^(\s*)>\s?(.*)$", s):
            out.append(f"{DIM}{m.group(1)}│ {_plain(m.group(2))}{R0}")
        else:
            out.append(_inline(s))
        i += 1
    return "\n".join(out)
