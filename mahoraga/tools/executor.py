"""Executor: the single place tool calls actually run (policy-checked)."""
from __future__ import annotations
import json
from .registry import Registry
from ..policy import approval, audit

MAX_RESULT = 12_000  # chars kept per tool result

def execute(reg: Registry, name: str, args: dict, yolo: bool = False) -> str:
    """Approve, run, audit, cap. The only path a tool call should take."""
    if not approval.ask(name, args, yolo=yolo):
        return "[user denied this tool call]"
    result = reg.execute(name, args)
    audit.log(name, args, ok='"error"' not in result[:20])
    return result[:MAX_RESULT]
