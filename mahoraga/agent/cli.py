"""Mahoraga TUI — stdlib ANSI only, the Mahoraga wheel is the banner AND the spinner."""
from __future__ import annotations
import sys, threading, time

from ..core.runtime import Mahoraga
from ..core.wheel import (
    animate_mahoraga_wheel, _make_frame,
    HIDE_CURSOR, SHOW_CURSOR, CLEAR_SCREEN, HOME, RESET,
)

BOLD = "\033[1m"; DIM = "\033[2m"; CYAN = "\033[36m"; GOLD = "\033[33m"
GREEN = "\033[32m"; RED = "\033[31m"; RESET0 = "\033[0m"

def banner():
    """His wheel IS the banner — spin it, then step aside."""
    try:
        animate_mahoraga_wheel(seconds=2.8, fps=12)
    except KeyboardInterrupt:
        pass
    print(f"\n  {GOLD}{BOLD}MAHORAGA{RESET0}{DIM} — the adaptive agent · local · minimal · extensible{RESET0}")
    print(f"  {DIM}type /help for commands · /quit to exit{RESET0}\n")


class Spinner(threading.Thread):
    """The wheel keeps turning while she thinks."""
    def __init__(self, width: int = 60, height: int = 19):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self.status = "thinking…"
        self.width = width
        self.height = height

    def run(self):
        sys.stdout.write(HIDE_CURSOR + CLEAR_SCREEN)
        f = 0
        while not self._stop.is_set():
            frame = _make_frame(f, self.width, self.height, color=True)
            sys.stdout.write(HOME + frame + f"\n  {GOLD}● {self.status}{RESET0}")
            sys.stdout.flush()
            f += 1
            time.sleep(0.08)
        sys.stdout.write(CLEAR_SCREEN + SHOW_CURSOR + RESET)
        sys.stdout.flush()

    def stop(self):
        self._stop.set()
        self.join(timeout=1.5)


def cli():
    banner()
    agent = Mahoraga(yolo="--yolo" in sys.argv)

    def on_thinking():
        pass

    def on_tool(name, args):
        import json as _j
        args_str = _j.dumps(args, ensure_ascii=False)[:80]
        print(f"  {CYAN}⚙ {name}({args_str}){RESET0}")

    while True:
        try:
            line = input(f"{GOLD}❯ {RESET0}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {DIM}bye.{RESET0}")
            break
        if not line:
            continue

        if line.startswith("/"):
            cmd = line.split()[0]
            if cmd in ("/quit", "/exit", "/q"):
                print(f"  {DIM}bye.{RESET0}")
                break
            elif cmd == "/help":
                print(f"""  {BOLD}commands{RESET0}
    /help          show this
    /quit          exit
    /new           start fresh context (memory keeps history)
    /model         show current + available models
    /model <name>  switch model (persists to config)
    /tools         list available tools
    /memory        show recent stored messages
    /clear         wipe stored memory
  {DIM}yolo mode: python -m mahoraga --yolo{RESET0}""")
                continue
            elif cmd == "/new":
                agent.new_session()
                print(f"  {DIM}context cleared{RESET0}")
                continue
            elif cmd == "/model":
                parts = line.split(maxsplit=1)
                if len(parts) == 2 and parts[1].strip():
                    name = agent.set_model(parts[1].strip())
                    print(f"  {DIM}model -> {name}{RESET0}")
                else:
                    cur = agent.current_model()
                    print(f"  {BOLD}current:{RESET0} {cur}")
                    models = agent.available_models()
                    if models:
                        print(f"  {BOLD}available:{RESET0}")
                        for m in models:
                            mark = f" {GOLD}●{RESET0}" if m == cur else "  "
                            print(f"  {mark} {m}")
                    else:
                        print(f"  {DIM}(server didn't list models — /model <name> still works){RESET0}")
                continue
            elif cmd == "/tools":
                for name in sorted(agent.registry.tools):
                    print(f"  {CYAN}•{RESET0} {name}")
                continue
            elif cmd == "/memory":
                from ..memory import sqlite as mem
                for role, content in mem.load_recent(10):
                    tag = f"{GOLD}you{RESET0}" if role == "user" else f"{CYAN}mira{RESET0}"
                    print(f"  {tag}: {content[:80]}")
                continue
            elif cmd == "/clear":
                from ..memory import sqlite as mem
                mem.clear_messages()
                print(f"  {DIM}memory wiped{RESET0}")
                continue
            else:
                print(f"  {DIM}unknown command: {cmd}{RESET0}")
                continue

        spinner = Spinner()
        spinner.start()
        try:
            answer = agent.send(line, on_thinking=on_thinking, on_tool=on_tool)
        except Exception as e:
            spinner.stop()
            print(f"  {RED}error: {e}{RESET0}")
            continue
        spinner.stop()
        print(f"  {GREEN}{answer}{RESET0}\n")

if __name__ == "__main__":
    cli()
