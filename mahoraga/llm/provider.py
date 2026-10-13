"""LLM provider abstraction. One interface, any OpenAI-compatible backend."""
from __future__ import annotations
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             stream: bool = False) -> dict:
        """Return {content, tool_calls, usage} — never raises on tool calls."""
        ...

class ChatResponse:
    def __init__(self, content: str = "", tool_calls: list[dict] | None = None,
                 usage: dict | None = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage or {}

def tool_call_from_raw(tc: dict) -> dict:
    """Normalize an OpenAI tool_call to a plain dict."""
    fn = tc.get("function", {})
    import json
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except Exception:
        args = {"_raw": fn.get("arguments", "")}
    return {"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args}
