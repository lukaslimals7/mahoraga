"""Human-in-the-loop approval prompt.

Called only for ASK-level shell commands (the executor runs SAFE ones without
asking and refuses BLOCK ones outright). before_prompt/after_prompt hooks let
the TUI park its status line so the prompt always lands on clean ground.

Three answers, and they are not the same thing:

  session  run this command, and don't ask for this exact command again until
           Mahoraga exits (in-memory grant, see permissions.allow_session)
  always   append the exact command to ~/.mahoraga/allow — never asks again
  deny     refuse the call; the model is told so and can pick another way

The TUI installs `ui_ask` (Screen.request_approval), which opens the interactive
card in agent/approve.py and blocks the calling worker thread until the human
answers. Everywhere else — pipes, tests, scripts — a plain stdin prompt is used.
`--yolo` never reaches this module's prompt at all: ask() returns immediately.
"""
from __future__ import annotations
import json
import re

from . import permissions

before_prompt = None   # optional callable
after_prompt = None
ui_ask = None          # optional callable(tool_name, args) -> "session"|"always"|"deny"

SESSION, ALWAYS, DENY = "session", "always", "deny"

SHELL_TOOLS = ("shell", "bash", "run_command", "execute_command")


def command_of(tool_name: str, args: dict) -> str:
    """The shell command a tool call would run, or "" for non-shell tools."""
    if tool_name in SHELL_TOOLS:
        return str((args or {}).get("command", ""))
    return ""


def ask(tool_name: str, args: dict, yolo: bool = False) -> bool:
    """True if the call may proceed. yolo skips prompts (BLOCK still refuses)."""
    if yolo:
        return True
    if before_prompt:
        before_prompt()
    try:
        choice = ui_ask(tool_name, args) if ui_ask else _ask_stdin(tool_name, args)
    finally:
        if after_prompt:
            after_prompt()
    cmd = command_of(tool_name, args)
    if choice == ALWAYS:
        # an invalid/duplicate rule still means the command is allowed
        if cmd:
            permissions.add_allow(re.escape(cmd))
        return True
    if choice == SESSION:
        return permissions.allow_session(cmd) if cmd else False
    return False


def _ask_stdin(tool_name: str, args: dict) -> str:
    """Fallback prompt for non-TUI runs (pipes, tests): y / a / n on one line."""
    cmd = command_of(tool_name, args)
    print(f"\n  \u26a0 [dangerous?] {tool_name}({json.dumps(args, ensure_ascii=False)[:160]})")
    if cmd:
        print(f"  (y = allow once this session \u00b7 a = always whitelist -> "
              f"{permissions.allow_path()} \u00b7 n = deny)")
    try:
        ans = input("  Allow? [y/a/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return DENY
    if ans in ("a", "always", "w", "whitelist"):
        return ALWAYS
    if ans in ("y", "yes", "s", "session", "once"):
        return SESSION
    return DENY
