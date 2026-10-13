"""Human-in-the-loop approval prompt.

Called only for ASK-level shell commands (the executor runs SAFE ones without
asking and refuses BLOCK ones outright). before_prompt/after_prompt hooks let
the TUI park its wheel so the prompt always lands on clean ground.
"""
from __future__ import annotations
import json

before_prompt = None   # optional callable
after_prompt = None


def ask(tool_name: str, args: dict, yolo: bool = False) -> bool:
    """True if the call may proceed. yolo skips prompts (BLOCK still refuses)."""
    if yolo:
        return True
    if before_prompt:
        before_prompt()
    print(f"\n  ⚠ [dangerous?] {tool_name}({json.dumps(args, ensure_ascii=False)[:160]})")
    try:
        ans = input("  Allow? [y/N] ").strip().lower()
    finally:
        if after_prompt:
            after_prompt()
    return ans in ("y", "yes")
