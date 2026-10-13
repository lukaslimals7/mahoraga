"""Mahoraga TUI — three regions, stdlib ANSI only, Pi-clean:

    ┌─────────────────────────────┐
    │   the wheel — spins,        │  rows 1..wheel_h (plain glyphs, exact
    │   always, that's the brand  │  width, never wraps, never scrolls)
    ├─────────────────────────────┤
    │   persistent text pane:     │  conversation, thinking, answers, tools
    │   streams live, never lost  │  — wrapped by VISUAL width, ANSI-safe
    ├─────────────────────────────┤
    │ ❯ your prompt               │  bottom row
    └─────────────────────────────┘

Terminal hardening: autowrap is disabled inside the alt screen, so a too-long
line can only clip at the right edge — it can never wrap, scroll, or shift the
layout. Every painted row ends in \\033[K (clear to end of line).
"""
from __future__ import annotations
import sys, threading, time, json, re, shutil

from ..core.runtime import Mahoraga
from ..core.wheel import _make_frame, HIDE_CURSOR, SHOW_CURSOR, RESET
from ..policy import approval

BOLD = "\033[1m"; DIM = "\033[2m"; CYAN = "\033[36m"; GOLD = "\033[33m"
GREEN = "\033[32m"; RED = "\033[31m"; R0 = "\033[0m"

ALT_ON = "\033[?1049h"
ALT_OFF = "\033[?1049l"
NOWRAP = "\033[?7l"      # never autowrap: layout can't be shifted by long lines
WRAP_ON = "\033[?7h"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def vis_len(s: str) -> int:
    return len(_ANSI.sub("", s))


def wrap_ansi(line: str, width: int) -> list[str]:
    """Wrap on spaces by VISUAL width; escape codes don't count toward length."""
    out, cur = [], ""
    for word in line.split(" "):
        cand = f"{cur} {word}" if cur else word
        if vis_len(cand) > width and cur:
            out.append(cur)
            cur = word
        else:
            cur = cand
    out.append(cur)
    return out or [""]


def _at(row: int, col: int = 1) -> str:
    return f"\033[{row};{col}H"


class Screen:
    """Wheel on top, persistent pane in the middle, input on the bottom row."""

    def __init__(self):
        size = shutil.get_terminal_size((110, 38))
        self.cols, self.rows = size.columns, size.lines
        self.compact = self.rows < 30
        self.wheel_h = 2 if self.compact else 21      # frame 19 + title + subtitle
        self.pane_top = self.wheel_h + 1
        self.pane_h = max(3, self.rows - self.wheel_h - 1)
        self.entries: list[tuple[str, str]] = []      # (ansi-color, text)
        self._stream_idx: dict[str, int] = {}
        self._dirty = threading.Event()
        self._wheel_stop = threading.Event()
        self._frame = 0
        self.status = "ready"

    # ---- lifecycle ----

    def enter(self):
        sys.stdout.write(ALT_ON + NOWRAP + HIDE_CURSOR)
        sys.stdout.flush()
        threading.Thread(target=self._loop, daemon=True).start()

    def leave(self):
        self._wheel_stop.set()
        sys.stdout.write(R0 + SHOW_CURSOR + WRAP_ON + ALT_OFF + RESET)
        sys.stdout.flush()

    # ---- painting ----

    def _paint_wheel(self):
        out = ["\033[s"]          # save cursor: painting must never steal it
        if self.compact:
            glyphs = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            g = glyphs[self._frame % len(glyphs)]
            out.append(_at(1) + f" {GOLD}{g} MAHORAGA{R0}\033[K")
            out.append(_at(2) + f" {DIM}{self.status}{R0}\033[K")
        else:
            # plain glyphs (no ANSI inside lines) + truncate by chars = safe
            frame = _make_frame(self._frame, min(self.cols - 1, 112), 19, color=False)
            lines = frame.split("\n")[: self.wheel_h - 1]
            for i, line in enumerate(lines):
                out.append(_at(1 + i) + line[: self.cols - 1] + "\033[K")
            for i in range(len(lines), self.wheel_h - 1):
                out.append(_at(1 + i) + "\033[K")
            out.append(_at(self.wheel_h) + f" {GOLD}● {self.status}{R0}\033[K")
        out.append("\033[u")      # restore cursor to wherever the user is typing
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def _wrapped(self) -> list[tuple[str, str]]:
        lines = []
        for color, text in self.entries:
            for para in text.split("\n"):
                if not para:
                    continue
                for w in wrap_ansi(para, self.cols - 2):
                    lines.append((color, w))
        return lines

    def _paint_pane(self):
        lines = self._wrapped()[-self.pane_h:]
        out = ["\033[s"]
        for i in range(self.pane_h):
            row = self.pane_top + i
            if i < len(lines):
                color, text = lines[i]
                out.append(_at(row) + f"{color}{text}{R0}\033[K")
            else:
                out.append(_at(row) + "\033[K")
        out.append("\033[u")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def _loop(self):
        """Owns the top region always; repaints the pane when dirty."""
        while not self._wheel_stop.is_set():
            self._frame += 1                 # the wheel never stops turning
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

    def finish_stream(self):
        self._stream_idx = {}
        self._dirty.set()

    def input_line(self, prompt: str) -> str:
        """Bottom-row prompt. The wheel keeps spinning above it."""
        sys.stdout.write(_at(self.rows) + f"\033[K{prompt}")
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()
        try:
            line = input()
        finally:
            sys.stdout.write(HIDE_CURSOR)
            sys.stdout.flush()
        # Enter at the bottom row scrolls the terminal up one line — repaint
        # every region at absolute positions so the layout self-heals instantly.
        self._paint_wheel()
        self._paint_pane()
        return line

    def kick(self):
        self._dirty.set()


def cli():
    screen = Screen()
    screen.enter()
    agent = Mahoraga(yolo="--yolo" in sys.argv)

    screen.say(f"{GOLD}{BOLD}MAHORAGA{R0} — the adaptive agent · local · minimal · extensible")
    screen.say(f"{DIM}/help for commands · /quit to exit{R0}")
    screen.say("")

    # dangerous-command prompts land on the bottom row; status says so
    approval.before_prompt = lambda: setattr(screen, "status", "waiting for you…")
    def _post_prompt():
        screen.status = "working…"
        screen.kick()
    approval.after_prompt = _post_prompt

    def on_tool(name, args):
        args_str = json.dumps(args, ensure_ascii=False)[:90]
        screen.status = f"{name}…"
        screen.say(f"{CYAN}⚙ {name}({args_str}){R0}")
        screen.kick()

    def stream_reply(text):
        screen.say(f"{GOLD}❯ {text}{R0}")
        screen.status = "thinking…"
        screen.finish_stream()
        screen.kick()

        def on_delta(piece):
            screen.status = "speaking…"
            screen.stream_piece(piece, GREEN, "answer")

        def on_reason(piece):
            screen.status = "thinking…"
            screen.stream_piece(piece, DIM, "think")

        try:
            answer = agent.send(text, on_delta=on_delta, on_reason=on_reason,
                                on_tool=on_tool)
        except Exception as e:
            screen.status = "ready"
            screen.finish_stream()
            screen.say(f"{RED}✗ {e}{R0}")
            screen.say("")
            screen.kick()
            return
        screen.status = "ready"
        screen.finish_stream()
        if not answer:
            screen.say(f"{GREEN}(no text){R0}")
        screen.say("")
        screen.kick()

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
            screen.kick()
            continue

        stream_reply(line)


if __name__ == "__main__":
    cli()
