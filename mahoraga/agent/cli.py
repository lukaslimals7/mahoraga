"""Mahoraga TUI — stdlib ANSI only, Mahoraga wheel as the thinking spinner."""
from __future__ import annotations
import sys, threading, itertools

from ..core.runtime import Mahoraga
from ..core.wheel import _make_frame, HIDE_CURSOR, SHOW_CURSOR, CLEAR_SCREEN, RESET, HOME

BOLD = "\033[1m"; DIM = "\033[2m"; CYAN = "\033[36m"; GOLD = "\033[33m"
GREEN = "\033[32m"; RED = "\033[31m"; RESET0 = "\033[0m"

class Spinner(threading.Thread):
    """Spins the wheel in the main thread's place — no cursor flicker."""
    def __init__(self):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self.status = "thinking"

    def run(self):
        f = 0
        sys.stdout.write(HIDE_CURSOR)
        while not self._stop.is_set():
            frame = _make_frame(f, 60, 15, color=True)
            sys.stdout.write(HOME + frame + f"\n  {GOLD}● {self.status}{RESET0}\n")
            sys.stdout.flush()
            f += 1
            time.sleep(0.08)
        sys.stdout.write(CLEAR_SCREEN)
        sys.stdout.flush()

    def stop(self):
        self._stop.set()
        sys.stdout.write(SHOW_CURSOR + RESET)
        sys.stdout.flush()

import time

def banner():
    return f"""{GOLD}{BOLD}
      ██╗  ██╗ █████╗  ██████╗ █████╗ ███╗   ███╗
      ██║  ██║██╔══██╗██╔════╝██╔══██╗████╗ ████║
      ███████║███████║██║     ███████║██╔████╔██║
      ██╔══██║██╔══██║██║     ██╔══██║██╚═██╔╝██║
      ██║  ██║██║  ██║╚██████╗██║  ██║██║ ╚═╝ ██║
      ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝     ╚═╝
    {DIM}the adaptive agent — local · minimal · extensible{RESET0}{RESET0}"""

def cli():
    print(banner())
    print(f"\n  {DIM}type /help for commands · /quit to exit{RESET0}\n")

    agent = Mahoraga(yolo="--yolo" in sys.argv)

    def on_thinking():
        spinner.status = "thinking…"

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
    /tools         list available tools
    /memory        show recent stored messages
    /clear         wipe stored memory
  {DIM}yolo mode: python -m mahoraga --yolo{RESET0}""")
                continue
            elif cmd == "/new":
                agent.new_session()
                print(f"  {DIM}context cleared{RESET0}")
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
