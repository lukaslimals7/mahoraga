"""OpenAI-compatible chat client — works with any /v1/chat/completions endpoint:
OpenRouter, vLLM, llama.cpp server, Ollama, LM Studio, etc."""
from __future__ import annotations
import json, urllib.request, urllib.error
from .provider import LLMProvider, ChatResponse, tool_call_from_raw

class OpenAICompat(LLMProvider):
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str = "", model: str = "",
                 temperature: float = 0.7, max_tokens: int = 4096,
                 timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, messages, tools=None, stream=False) -> ChatResponse:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        choice = data["choices"][0]
        msg = choice.get("message", {})
        usage = data.get("usage", {})
        tcs = [tool_call_from_raw(tc) for tc in msg.get("tool_calls") or []]
        return ChatResponse(
            content=msg.get("content") or "",
            tool_calls=tcs,
            usage=usage,
        )
