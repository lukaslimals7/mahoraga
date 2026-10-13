"""Event types emitted by the agent loop. Pure data — no logic."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AgentEvent:
    type: str          # "assistant_delta" | "tool_call" | "tool_result" | "done" | "error" | "thinking"
    data: Any = None
    index: int = 0

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass
class ToolResult:
    call_id: str
    name: str
    result: str
    ok: bool = True
    error: str | None = None
