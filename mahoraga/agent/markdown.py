"""Markdown → terminal renderer (stdlib only).

Models answer in markdown; a terminal that prints raw markdown looks broken
(`| Folder | Size |` and `**bold**` and backticks are noise on screen). This
module turns the markdown the model *writes* into the text a human *reads*:

- tables  → aligned columns, bold header, rule, numeric right-alignment;
            columns share the pane width and cells wrap — nothing is ellipsized
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


def _chunk(s: str, width: int) -> list[str]:
    """Hard-break a string into pieces of at most `width` visual columns."""
    out: list[str] = []
    cur = ""
    w = 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width and cur:
            out.append(cur)
            cur, w = "", 0
        cur += ch
        w += cw
    out.append(cur)
    return out or [""]


def _wrap_cell(text: str, width: int) -> list[str]:
    """Wrap one cell to `width` visual columns: word-wise, hard-breaking a word
    that is wider than the column on its own.

    A cell carries the table's meaning (a "Summary" column is the whole point),
    so it wraps onto extra rows instead of being cut with an ellipsis.
    """
    width = max(1, width)
    lines: list[str] = []
    cur = ""
    for word in text.split():
        for piece in (_chunk(word, width) if _w(word) > width else [word]):
            cand = f"{cur} {piece}" if cur else piece
            if _w(cand) > width and cur:
                lines.append(cur)
                cur = piece
            else:
                cur = cand
    lines.append(cur)
    return lines or [""]


def _render_table(rows: list[str], width: int) -> list[str]:
    """Aligned columns: bold header, ─── rule, numeric columns right-aligned.

    A column takes the width its content needs — the shrink loop still fits the
    table to the pane — and cells wrap inside their column, so no information is
    dropped.
    """
    body = [r for r in rows if not _is_sep_row(r)] or rows
    grid = [_cells(r) for r in body]
    ncols = max(len(r) for r in grid)
    grid = [r + [""] * (ncols - len(r)) for r in grid]
    header, data = grid[0], grid[1:]

    cols = [[_plain(r[c]) for r in data] for c in range(ncols)]
    w = []
    for c in range(ncols):
        natural = max([_w(header[c])] + [_w(v) for v in cols[c]] or [1])
        w.append(natural)
    right = [bool(cols[c]) and all(_is_num(v) for v in cols[c]) and any(cols[c])
             for c in range(ncols)]

    total = sum(w) + 2 * (ncols - 1)
    # Shrink the widest column until the table fits. Cells wrap, so a narrow
    # column costs height, not information — overflowing the pane would clip
    # whole columns off the right edge, which is the same loss by another name.
    while total > width and max(w) > 1:
        i = w.index(max(w))
        w[i] -= 1
        total -= 1

    def row_lines(cells_: list[str], bold: bool) -> list[str]:
        """One logical row → as many physical rows as its tallest cell needs."""
        wrapped = [_wrap_cell(_plain(cells_[c]), w[c]) for c in range(ncols)]
        out = []
        for i in range(max(len(x) for x in wrapped)):
            parts = []
            for c in range(ncols):
                t = wrapped[c][i] if i < len(wrapped[c]) else ""
                pad = " " * max(0, w[c] - _w(t))
                parts.append(pad + t if right[c] else t + pad)
            row = "  ".join(parts).rstrip()
            out.append(f"{BOLD}{row}{R0}" if bold else row)
        return out

    out = row_lines(header, True)
    out.append(f"{DIM}{'─' * min(total, width)}{R0}")
    for r in data:
        out += row_lines(r, False)
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
                out.extend(_render_table(block, width))
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
