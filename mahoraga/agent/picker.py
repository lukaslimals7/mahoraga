"""A modal, scrollable picker for the pane region — models and providers.

Why this exists as its own module: the choice lists in Mahoraga come from an API
key (11 callable NVIDIA models out of 68 advertised, say), so they are longer
than one screen and not knowable in advance. Arrow keys plus a windowed list
beat typing `/model 7` and hoping.

Two rules this widget follows, both learned the hard way elsewhere in this TUI:

* it paints ABSOLUTE rows inside the pane, saving and restoring the cursor
  (`\\033[s` … `\\033[u`) so the prompt row is never stolen — the same discipline
  `_paint_pane` uses;
* every row starts with `CSI 2K` (erase the WHOLE line). Clearing to
  end-of-line leaves the previous frame's band visible behind a plain row,
  which reads as overlapping blocks after a scroll or a resize.

The selection maths (`window_slice`) is a pure function, so it is tested
without a terminal.
"""
from __future__ import annotations
import sys

from .lineedit import (BACKSPACE, DOWN, END, ENTER, HOME, IGNORE, INTERRUPT,
                       PGUP, PGDN, TEXT, UP, WHEEL_DOWN, WHEEL_UP,
                       decode_key, read_byte)

BOLD = "\033[1m"
DIM = "\033[2m"
GOLD = "\033[33m"
R0 = "\033[0m"
BG_SEL = "\033[48;2;38;52;72m"      # the same slate band a user message uses


def _at(row: int, col: int = 1) -> str:
    return f"\033[{row};{col}H"


def window_slice(count: int, height: int, pos: int, start: int) -> tuple[int, int]:
    """The slice [start, end) of a list of `count` rows, `height` tall, that
    still shows `pos`. Pure: tested without a terminal.

    Scrolling only when the selection leaves the window is what keeps the list
    from jumping under the reader's eyes on every keypress.
    """
    height = max(1, height)
    start = max(0, min(start, max(0, count - height)))
    if pos < start:
        start = pos
    elif pos >= start + height:
        start = pos - height + 1
    start = max(0, min(start, max(0, count - height)))
    return start, min(count, start + height)


def _fit(text: str, width: int) -> str:
    """Cut to `width` chars, with an ellipsis when it had to cut."""
    text = text or ""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


class Picker:
    """Modal list: ↑/↓ (or wheel) move, PgUp/PgDn jump, Home/End ends,
    Enter selects, Esc or q cancels, typing filters.

    items:   [{"label": str, "meta": str, "value": any, "mark": bool}]
    refresh: optional callable(note: callable(str)) -> new items, bound to `v`,
             so "verify what this key can actually call" is a keystroke.
    """

    def __init__(self, screen, title: str, items: list[dict],
                 footer: str = "", refresh=None):
        self.s = screen
        self.title = title
        self.items = list(items)
        self.footer = footer
        self.refresh = refresh
        self.filter = ""
        self.note = ""
        self.pos = 0
        self.off = 0
        for i, it in enumerate(self.items):
            if it.get("mark"):
                self.pos = i
                break

    # ---- state ----

    def visible(self) -> list[int]:
        f = self.filter.lower()
        if not f:
            return list(range(len(self.items)))
        return [i for i, it in enumerate(self.items)
                if f in str(it.get("label", "")).lower()]

    def height(self) -> int:
        """Rows available for items: the pane minus header and footer."""
        return max(1, self.s.pane_h - 2)

    def selected(self):
        idx = self.visible()
        if not idx:
            return None
        self.pos = max(0, min(self.pos, len(idx) - 1))
        return self.items[idx[self.pos]]

    # ---- painting ----

    def paint(self) -> None:
        s = self.s
        idx = self.visible()
        self.pos = max(0, min(self.pos, len(idx) - 1)) if idx else 0
        start, end = window_slice(len(idx), self.height(), self.pos, self.off)
        self.off = start
        width = max(10, s.cols - 2)
        # size the label column to the longest label, so the meta column gets
        # whatever room is left instead of a fixed 24 chars that truncates
        # "nvidia/nemotron-3.5-super-vl-120b-a12b · key ✓ · 11 models"
        labels = [str(self.items[i].get("label", "")) for i in idx]
        label_w = min(max((len(x) for x in labels), default=8), max(8, width - 30))
        meta_w = max(0, width - label_w - 6)

        out = ["\033[s"]
        head = f" {BOLD}{self.title}{R0}"
        if self.filter:
            head += f"  {GOLD}/{self.filter}{R0}"
        count = (f"{len(idx)} of {len(self.items)}" if self.filter
                 else f"{len(self.items)}")
        out.append(_at(s.pane_top) + R0 + "\033[2K" + head + "\033[K")
        out.append(_at(s.pane_top, max(2, s.cols - 1 - len(count)))
                   + f"{DIM}{count}{R0}")

        for row in range(self.height()):
            y = s.pane_top + 1 + row
            cell = [_at(y) + R0 + "\033[2K"]
            i = start + row
            if i < end:
                it = self.items[idx[i]]
                chosen = i == self.pos
                label = _fit(str(it.get("label", "")), label_w)
                meta = _fit(str(it.get("meta") or ""), meta_w)
                pad = " " * max(1, label_w - len(label) + 2) if meta else ""
                plain = f"{'❯ ' if chosen else '  '}{'● ' if it.get('mark') else '  '}{label}{pad}{meta}"
                styled = (f"{'❯ ' if chosen else '  '}{'● ' if it.get('mark') else '  '}"
                          f"{label}{pad}{DIM}{meta}{R0 if meta else ''}")
                if chosen:
                    fill = " " * max(0, s.cols - 1 - len(plain))
                    cell.append(f"{BG_SEL}{styled}{fill}{R0}\033[K")
                else:
                    cell.append(f"{styled}\033[K")
            out.append("".join(cell))

        hint = self.note or self.footer
        out.append(_at(s.pane_top + s.pane_h - 1) + R0 + "\033[2K "
                   + f"{DIM}{_fit(hint, width)}{R0}\033[K")
        out.append("\033[u")          # hand the cursor back to the prompt row
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    # ---- input ----

    def run(self):
        """Block on keys until a choice or a cancel; return the chosen value."""
        self.paint()
        fd = sys.stdin.fileno()
        while True:
            try:
                first = read_byte(fd)
            except (OSError, ValueError):
                return None
            if not first:
                return None                        # stdin closed: cancel
            queue = [first]

            def qr():
                return queue.pop(0) if queue else read_byte(fd, 0.05)

            action, text = decode_key(qr)
            n = len(self.visible())

            if action in (UP, WHEEL_UP):
                self.pos -= 1
                self.note = ""
            elif action in (DOWN, WHEEL_DOWN):
                self.pos += 1
                self.note = ""
            elif action == PGUP:
                self.pos -= self.height()
                self.note = ""
            elif action == PGDN:
                self.pos += self.height()
                self.note = ""
            elif action == HOME:
                self.pos = 0
            elif action == END:
                self.pos = n - 1
            elif action == ENTER:
                item = self.selected()
                return item.get("value") if item else None
            elif action == INTERRUPT:
                return None
            elif action == BACKSPACE:
                self.filter = self.filter[:-1]
                self.pos = self.off = 0
                self.note = ""
            elif action == TEXT:
                if text.lower() == "q" and not self.filter:
                    return None
                if text.lower() == "v" and self.refresh and not self.filter:
                    self._do_refresh()
                    continue
                self.filter += text
                self.pos = self.off = 0
                self.note = ""
            elif action == IGNORE:
                return None                        # bare Esc
            self.pos = max(0, min(self.pos, max(0, n - 1)))
            self.paint()

    def _do_refresh(self) -> None:
        """Re-populate from the source (`v`) without leaving the picker."""
        def note(msg):
            self.note = msg
            self.paint()
        refresh = self.refresh
        if refresh is None:
            return
        note("verifying — one real call per model…")
        try:
            items = refresh(note)
        except Exception as e:                     # never kill the picker
            self.note = f"refresh failed: {e}"
            self.paint()
            return
        if not items:
            self.note = "nothing came back — is the key set?"
            self.paint()
            return
        keep = self.selected()
        self.items = list(items)
        self.pos = self.off = 0
        for i, it in enumerate(self.items):
            if keep and it.get("value") == keep.get("value"):
                self.pos = i
                break
        self.note = f"{len(self.items)} item(s)"
        self.paint()
