"""Executor: the single place tool calls actually run (policy-checked).

Shell gets per-command danger classification; everything else runs and is
audited. This is the only path a tool call should take.
"""
from __future__ import annotations
from .registry import Registry
from ..policy import approval, audit, permissions

MAX_RESULT = 12_000  # chars kept per tool result

SHELL_ALIASES = {"shell", "bash", "run_command", "execute_command"}


def execute(reg: Registry, name: str, args: dict, yolo: bool = False) -> str:
    """Classify, (maybe) approve, run, audit, cap."""
    if name in SHELL_ALIASES:
        risk = permissions.shell_risk(str(args.get("command", "")))
        if risk == permissions.BLOCK:
            audit.log(name, args, ok=False)
            return ("[refused] hard-blocked (disk/system destruction) — this one "
                    "cannot be whitelisted. Run it yourself if you truly mean it.")
        if risk == permissions.ASK and not approval.ask(name, args, yolo=yolo):
            audit.log(name, args, ok=False)
            return ("[user denied this tool call — whitelist it with "
                    "/allow <pattern> in the TUI to skip the prompt next time]")
    result = reg.execute(name, args)
    audit.log(name, args, ok='"error"' not in result[:20])
    return result[:MAX_RESULT]
