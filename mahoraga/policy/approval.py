"""Human-in-the-loop approval prompt."""
from __future__ import annotations
import json
from .permissions import needs_approval, tier_for

def ask(tool_name: str, args: dict, yolo: bool = False) -> bool:
    """True if the call may proceed. yolo skips prompts but never skips UNSAFE."""
    from .permissions import UNSAFE
    tier = tier_for(tool_name)
    if tier == UNSAFE:
        pass  # always ask — no shortcut
    elif yolo or not needs_approval(tool_name):
        return True
    print(f"\n  ⚠ [{tier.upper()}] {tool_name}({json.dumps(args, ensure_ascii=False)[:160]})")
    return input("  Allow? [y/N] ").strip().lower() in ("y", "yes")
