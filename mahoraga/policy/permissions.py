"""Permission tiers: which tools need a human."""
from __future__ import annotations

SAFE = "safe"      # read-only → auto-approve
WRITE = "write"    # mutates something → confirm
UNSAFE = "unsafe"  # dangerous → confirm (never auto, even in some yolo modes)

SAFE_TOOLS = {"read_file", "search_files", "list_dir", "get_notes",
              "http_get", "estimate_tokens"}
WRITE_TOOLS = {"write_file", "set_notes", "http_post", "shell"}

def tier_for(tool_name: str) -> str:
    if tool_name in SAFE_TOOLS:
        return SAFE
    if tool_name in WRITE_TOOLS:
        return WRITE
    return UNSAFE

def needs_approval(tool_name: str) -> bool:
    return tier_for(tool_name) != SAFE
