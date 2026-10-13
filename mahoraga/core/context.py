"""Conversation context: message history + token estimation + truncation."""
from __future__ import annotations
import json, re

class Context:
    def __init__(self, system_prompt: str = "", max_tokens: int = 8192):
        self.system_prompt = system_prompt
        self.messages: list[dict] = []
        self.max_tokens = max_tokens

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str):
        self.messages.append({"role": "assistant", "content": text})

    def add_tool_result(self, call_id: str, content: str):
        self.messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": content,
        })

    def add_assistant_tool_calls(self, tool_calls: list[dict]):
        self.messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": tool_calls,
        })

    def build_payload(self, tools: list[dict] | None = None) -> list[dict]:
        payload = []
        if self.system_prompt:
            payload.append({"role": "system", "content": self.system_prompt})
        payload.extend(self.messages)
        return payload

    def estimate_tokens(self) -> int:
        total = 0
        for m in self.messages:
            c = m.get("content") or ""
            total += len(c) // 3 + 4
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    total += len(tc["function"]["arguments"]) // 3 + 8
        return total

    def trim(self):
        """Drop oldest messages (after system) when over budget."""
        while self.estimate_tokens() > self.max_tokens and len(self.messages) > 2:
            self.messages.pop(0)
