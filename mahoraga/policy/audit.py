"""Append-only audit log: every tool call recorded locally as JSONL."""
from __future__ import annotations
import json, time, pathlib

DEFAULT = pathlib.Path.home() / ".mahoraga" / "audit.jsonl"

def log(tool: str, args: dict, ok: bool, path: str | pathlib.Path = DEFAULT):
    p = pathlib.Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps({"ts": time.time(), "tool": tool, "ok": ok}) + "\n")
