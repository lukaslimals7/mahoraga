#!/usr/bin/env python3
"""The permission card and its three answers: session / whitelist / deny.

The key map and the policy effects are pure, so they are tested here without a
terminal. The painting itself is only provable in a pty — see test_key_tui.py.

Run: python3 tests/test_approval.py
"""
import builtins
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from mahoraga.policy import permissions as P
from mahoraga.policy import approval as A
from mahoraga.agent.approve import OPTIONS, handle_key
from mahoraga.agent.lineedit import (DOWN, END, ENTER, HOME, IGNORE, INTERRUPT,
                                     TEXT, UP)

fails = []


def check(label, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), "-", label,
          "" if ok else f"(got {got!r}, want {want!r})")
    if not ok:
        fails.append(label)


# isolate the allowlist and the session grants in a temp file / clean slate
P.ALLOW_PATH = os.path.join(tempfile.mkdtemp(), "allow")
P.clear_session()

# --- the key map is the contract: every answer is reachable -----------------
check("three answers", [o[0] for o in OPTIONS], ["session", "always", "deny"])
check("down moves the selection", handle_key(0, DOWN, ""), (1, None))
check("up clamps at the top", handle_key(0, UP, ""), (0, None))
check("down clamps at the bottom", handle_key(2, DOWN, ""), (2, None))
check("home / end jump", (handle_key(1, HOME, ""), handle_key(1, END, "")),
      ((0, None), (2, None)))
check("enter confirms the selected answer", handle_key(1, ENTER, ""), (1, "always"))
check("y = this session", handle_key(0, TEXT, "y"), (0, "session"))
check("a = whitelist", handle_key(0, TEXT, "a"), (1, "always"))
check("n = deny", handle_key(0, TEXT, "n"), (2, "deny"))
check("1/2/3 jump straight to an answer", handle_key(0, TEXT, "3"), (2, "deny"))
check("uppercase hotkeys work", handle_key(0, TEXT, "A"), (1, "always"))
check("esc refuses (an unanswered agent hangs)", handle_key(1, IGNORE, ""), (1, "deny"))
check("ctrl+c refuses too", handle_key(1, INTERRUPT, ""), (1, "deny"))
check("any other key waits", handle_key(1, TEXT, "x"), (1, None))

# --- ask(): session / whitelist / deny --------------------------------------
check("killall asks by default", P.shell_risk("killall firefox"), P.ASK)
asked = []
A.ui_ask = lambda tool, args: asked.append((tool, args)) or "session"
check("session answer allows the call",
      A.ask("shell", {"command": "killall firefox"}), True)
check("the tool and args were shown to the human",
      asked, [("shell", {"command": "killall firefox"})])
check("session grant is remembered", P.shell_risk("killall firefox"), P.SAFE)
check("a different command still asks", P.shell_risk("killall code"), P.ASK)
check("a session grant is not written to disk", P.list_allow(), [])

A.ui_ask = lambda tool, args: "always"
check("whitelist answer allows the call",
      A.ask("shell", {"command": "killall firefox"}), True)
check("the exact command was whitelisted", P.list_allow(), [r"killall\ firefox"])
check("the whitelisted command is SAFE", P.shell_risk("killall firefox"), P.SAFE)

A.ui_ask = lambda tool, args: "deny"
check("deny refuses the call",
      A.ask("shell", {"command": "killall firefox"}), False)

# --- yolo is untouched: it never opens a prompt -----------------------------
calls = []
A.ui_ask = lambda tool, args: calls.append(1) or "deny"
check("yolo still allows without asking",
      A.ask("shell", {"command": "killall firefox"}, yolo=True), True)
check("yolo never opened the prompt", calls, [])

# --- the TUI hooks still fire (they park the status line) -------------------
seen = []
A.before_prompt = lambda: seen.append("before")
A.after_prompt = lambda: seen.append("after")
A.ask("shell", {"command": "killall firefox"})
check("hooks fired in order", seen, ["before", "after"])
A.before_prompt = A.after_prompt = A.ui_ask = None

# --- stdin fallback (pipes, scripts): a = whitelist, y = once, empty = deny -
real_input = builtins.input
builtins.input = lambda prompt="": "a"
try:
    check("stdin 'a' whitelists",
          A.ask("shell", {"command": "killall code"}), True)
finally:
    builtins.input = real_input
check("stdin whitelist landed on disk", P.list_allow()[-1], r"killall\ code")

builtins.input = lambda prompt="": "y"
try:
    check("stdin 'y' allows once",
          A.ask("shell", {"command": "systemctl status x"}), True)
finally:
    builtins.input = real_input
check("stdin 'y' remembered for the session",
      P.shell_risk("systemctl status x"), P.SAFE)

builtins.input = lambda prompt="": ""
try:
    check("empty stdin answer denies",
          A.ask("shell", {"command": "sudo true"}), False)
finally:
    builtins.input = real_input

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all approval checks passed")
