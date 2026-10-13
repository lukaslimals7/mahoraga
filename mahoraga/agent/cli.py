"""Mahoraga TUI — three regions, stdlib ANSI only:

    ┌─────────────────────────────┐
    │   the wheel (spins while    │  rows 1..wheel_h — never touches below
    │        she works)           │
    ├─────────────────────────────┤
    │   persistent text pane:     │  thinking + answers stream here live,
    │   conversation lives here   │  tool calls, errors, /command output
    ├─────────────────────────────┤
    │ ❯ your prompt               │  bottom row
    └─────────────────────────────┘

The wheel thread owns the top region and (throttled) pane repaints; the main
thread owns the buffer and the input row. Nothing ever clears the whole screen
during work — the pane is persistent.
"""
from __future__ import annotations
import sys, threading, time, json, shutil, textwrap

from ..core.runtime import Mahoraga
from ..core.wheel import _make_frame, HIDE_CURSOR, SHOW_CURSOR, RESET
from ..policy import approval

BOLD = "\033[1m"; DIM = "\033[2m"; CYAN = "\033[36m"; GOLD = "\033[33m"
GREEN = "\033[32m"; RED = "\033[31m"; R0 = "\033[0m"

ALT_ON = "\033[?1049h"
ALT_OFF = "\033[?1049l"


def _at(row: int, col: int = 1) -> str:
    return f"\033[{row};{col}H"


class Screen:
    """Wheel on top, persistent pane in the middle, input on the bottom row."""

    def __init__(self):
        size = shutil.get_terminal_size((100, 34))
        self.cols, self.rows = size.columns, size.lines
        self.compact = self.rows < 30
        self.wheel_h = 2 if self.compact else 21   # frame(19) + title + subtitle
        self.pane_top = self.wheel_h + 1
        self.pane_h = max(3, self.rows - self.wheel_h - 1)
        self.entries: list[tuple[str, str]] = []   # (ansi-color, text)
        self._dirty = threading.Event()
        self._frame = 0
        self._busy = threading.Event()
        self._wheel_stop = threading.Event()
        self._frozen = True

    # ---- lifecycle ----

    def enter(self):
        sys.stdout.write(ALT_ON + HIDE_CURSOR)
        sys.stdout.flush()
        threading.Thread(target=self._wheel_loop, daemon=True).start()
        self._dirty.set()

    def leave(self):
        self._wheel_stop.set()
        sys.stdout.write(R0 + SHOW_CURSOR + ALT_OFF + RESET)
        sys.stdout.flush()

    # ---- painting ----

    def _paint_wheel(self):
        out = []
        if self.compact:
            glyphs = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            spin = glyphs[self._frame % len(glyphs)]
            out.append(_at(1) + f"\033[K {GOLD}{spin} MAHORAGA{R0}")
            out.append(_at(2) + f"\033[K {DIM}{'thinking…' if self._busy.is_set() else 'ready'}{R0}")
        else:
            frame = _make_frame(self._frame, min(self.cols, 112), 19, color=True)
            for i, line in enumerate(frame.split("\n")[:self.wheel_h]):
                out.append(_at(1 + i) + line[: self.cols] + "\033[K")
            for i in range(len(frame.split("\n")), self.wheel_h):
                out.append(_at(1 + i) + "\033[K")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def _wrapped(self):
        """entries -> wrapped display lines [(color, line)]."""
        lines = []
        for color, text in self.entries:
            for para in text.split("\n"):
                if not para:
                    continue
                for w in textwrap.wrap(para, width=self.cols - 2) or [""]:
                    lines.append((color, w))
        return lines

    def _paint_pane(self):
        lines = self._wrapped()[-self.pane_h:]
        out = []
        for i in range(self.pane_h):
            row = self.pane_top + i
            if i < len(lines):
                color, text = lines[i]
                out.append(_at(row) + f"{color}{text}{R0}\033[K")
            else:
                out.append(_at(row) + "\033[K")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def _wheel_loop(self):
        while not self._wheel_stop.is_set():
            if self._busy.is_set():
                self._frame += 1
                self._paint_wheel()
            elif self._frozen:
                self._frozen = False
                self._paint_wheel()
            if self._dirty.is_set():
                self._dirty.clear()
                self._paint_pane()
            time.sleep(0.08)

    # ---- buffer API (main thread) ----

    def say(self, text: str, color: str = ""):
        self.entries.append((color, text))
        self._dirty.set()

    def stream_piece(self, text: str, color: str, key: str):
        """Append to the entry tagged `key` (created on first piece)."""
        idx = self._stream_idx.get(key)
        if idx is None or idx >= len(self.entries):
            self.entries.append((color, ""))
            idx = len(self.entries) - 1
            self._stream_idx[key] = idx
        c, old = self.entries[idx]
        self.entries[idx] = (c, old + text)
        self._dirty.set()

    _stream_idx: dict = {}

    def finish_stream(self):
        self._stream_idx = {}
        self._dirty.set()

    def input_line(self, prompt: str) -> str:
        """Bottom-row prompt. The wheel keeps spinning above it."""
        sys.stdout.write(_at(self.rows) + f"\033[K{prompt}")
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()
        try:
            return input()
        finally:
            sys.stdout.write(HIDE_CURSOR)
            sys.stdout.flush()

    def set_busy(self, busy: bool):
        if busy:
            self._busy.set()
        else:
            self._busy.clear()
            self._frozen = True


def cli():
    screen = Screen()
    screen.enter()
    agent = Mahoraga(yolo="--yolo" in sys.argv)

    screen.say(f"{GOLD}{BOLD}MAHORAGA{R0} — the adaptive agent · local · minimal · extensible")
    screen.say(f"{DIM}type /help for commands · /quit to exit{R0}")
    screen.say("")

    # dangerous-command prompts land on the bottom row, wheel frozen
    approval.before_prompt = lambda: screen.set_busy(False)
    approval.after_prompt = lambda: screen.set_dirty() if hasattr(screen, "set_dirty") else screen._dirty.set()

    def on_tool(name, args):
        args_str = json.dumps(args, ensure_ascii=False)[:90]
        screen.say(f"{CYAN}⚙ {name}({args_str}){R0}")
        screen._paint_pane()

    def stream_reply(text):
        screen.say(f"{GOLD}❯ {text}{R0}")
        screen.set_busy(True)
        screen.finish_stream()
        screen._paint_pane()

        def on_delta(piece):
            screen.stream_piece(piece, GREEN, "answer")

        def on_reason(piece):
            screen.stream_piece(piece, DIM, "think")

        try:
            answer = agent.send(text, on_delta=on_delta, on_reason=on_reason,
                                on_tool=on_tool)
        except Exception as e:
            screen.set_busy(False)
            screen.finish_stream()
            screen.say(f"{RED}✗ {e}{R0}")
            screen._paint_pane()
            return
        screen.set_busy(False)
        screen.finish_stream()
        if not answer:
            screen.say(f"{GREEN}(no text){R0}")
        screen.say("")
        screen._paint_pane()

    while True:
        try:
            line = screen.input_line(f"{GOLD}❯ {R0}").strip()
        except (EOFError, KeyboardInterrupt):
            screen.leave()
            print("bye.")
            break
        if not line:
            continue

        if line.startswith("/"):
            cmd = line.split()[0]
            arg = line.split(maxsplit=1)[1].strip() if len(line.split(maxsplit=1)) > 1 else ""
            if cmd in ("/quit", "/exit", "/q"):
                screen.leave()
                print("bye.")
                break
            elif cmd == "/help":
                screen.say(f"{BOLD}commands{R0}")
                for h in ["/help            this", "/quit            exit",
                          "/new             fresh context (memory keeps history)",
                          "/provider        list providers", "/provider <n>    switch provider",
                          "/model           list models", "/model <name>    switch model",
                          "/tools           list tools", "/tools off|on    disable/enable tools",
                          "/memory          recent stored messages",
                          "/clear           wipe stored memory"]:
                    screen.say(f"  {h}")
                screen.say(f"{DIM}yolo: python -m mahoraga --yolo{R0}")
            elif cmd == "/new":
                agent.new_session()
                screen.say(f"{DIM}context cleared{R0}")
            elif cmd == "/provider":
                if arg:
                    try:
                        name = agent.switch_provider(arg)
                        screen.say(f"{DIM}provider -> {name} ({agent.current_model()}){R0}")
                        if not agent.provider_key_ok():
                            screen.say(f"{RED}⚠ {agent.active_cfg().get('api_key_env')} not set in env{R0}")
                    except ValueError as e:
                        screen.say(f"{RED}{e}{R0}")
                else:
                    cur = agent.active_name()
                    for n in agent.provider_names():
                        mark = f"{GOLD}●{R0}" if n == cur else " "
                        screen.say(f" {mark} {BOLD}{n}{R0} · {DIM}{agent.cfg['providers'][n].get('model','')}{R0}")
            elif cmd == "/model":
                if arg:
                    screen.say(f"{DIM}model -> {agent.set_model(arg)}{R0}")
                else:
                    cur = agent.current_model()
                    screen.say(f"{BOLD}current:{R0} {cur}")
                    models = agent.available_models()
                    if models:
                        for m in models[:30]:
                            screen.say(f"  {GOLD}●{R0} {m}" if m == cur else f"    {m}")
                    else:
                        screen.say(f"{DIM}(server didn't list models — /model <name> still works){R0}")
            elif cmd == "/tools":
                if arg in ("off", "on"):
                    agent.tools_enabled = arg == "on"
                    screen.say(f"{DIM}tools {'enabled' if agent.tools_enabled else 'disabled'}{R0}")
                else:
                    for name in sorted(agent.registry.tools):
                        screen.say(f"  {CYAN}•{R0} {name}")
                    screen.say(f"{DIM}tools are {'on' if agent.tools_enabled else 'off'} — /tools off|on{R0}")
            elif cmd == "/memory":
                from ..memory import sqlite as mem
                for role, content in mem.load_recent(10):
                    tag = f"{GOLD}you{R0}" if role == "user" else f"{CYAN}mira{R0}"
                    screen.say(f"{tag}: {content[:90]}")
            elif cmd == "/clear":
                from ..memory import sqlite as mem
                mem.clear_messages()
                screen.say(f"{DIM}memory wiped{R0}")
            else:
                screen.say(f"{DIM}unknown command: {cmd}{R0}")
            screen._paint_pane()
            continue

        stream_reply(line)


if __name__ == "__main__":
    cli()
