"""Human-in-the-loop approval prompt.

Called only for ASK-level shell commands (the executor runs SAFE ones without
asking and refuses BLOCK ones outright). before_prompt/after_prompt hooks let
the TUI park its wheel so the prompt always lands on clean ground.

Answering "a" (always) appends the exact command to ~/.mahoraga/allow, so it
never asks again. The file is plain regex, one rule per line.
"""
from __future__ import annotations
import json
import re

from . import permissions

before_prompt = None   # optional callable
after_prompt = None


def ask(tool_name: str, args: dict, yolo: bool = False) -> bool:
    """True if the call may proceed. yolo skips prompts (BLOCK still refuses)."""
    if yolo:
        return True
    if before_prompt:
        before_prompt()
    cmd = str(args.get("command", "")) if tool_name in (
        "shell", "bash", "run_command", "execute_command") else ""
    print(f"\n  \u26a0 [dangerous?] {tool_name}({json.dumps(args, ensure_ascii=False)[:160]})")
    if cmd:
        print(f"  (a = always allow this exact command -> {permissions.allow_path()})")
    try:
        ans = input("  Allow? [y/N/a] ").strip().lower()
    finally:
        if after_prompt:
            after_prompt()
    if ans in ("a", "always"):
        if cmd:
            ok = permissions.add_allow(re.escape(cmd))
            print(f"  \u2713 {'whitelisted — it will never ask again' if ok else 'already whitelisted'}")
            return True
        return False
    return ans in ("y", "yes")
