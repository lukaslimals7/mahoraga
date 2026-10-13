"""OpenAI-compatible chat client — works with any /v1/chat/completions endpoint:
llama.cpp router, Ollama, vLLM, OpenRouter, DeepSeek, LM Studio, etc.

Streams SSE deltas when asked (the responsive path), and turns ugly HTTP errors
into readable ones with the server's own message.
"""
from __future__ import annotations
import json, urllib.request, urllib.error
from .provider import LLMProvider, ChatResponse, tool_call_from_raw

class LLMError(Exception):
    """A readable provider failure — the server's message, not a traceback."""


def _sanitize_schema(obj):
    """Drop `additionalProperties` recursively: DeepSeek 400s on it; OpenAI
    tolerates its absence just fine."""
    if isinstance(obj, dict):
        return {k: _sanitize_schema(v)
                for k, v in obj.items() if k != "additionalProperties"}
    if isinstance(obj, list):
        return [_sanitize_schema(v) for v in obj]
    return obj


class OpenAICompat(LLMProvider):
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str = "", model: str = "",
                 temperature: float = 0.7, max_tokens: int = 4096,
                 timeout: float = 300.0):
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

    def _request(self, path: str, body: dict | None = None, timeout: float | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data,
            headers=self._headers(), method="POST" if body is not None else "GET")
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read())
                msg = detail.get("error", {}).get("message") or str(detail)[:200]
            except Exception:
                msg = e.reason or str(e)
            raise LLMError(f"{msg}  (HTTP {e.code} @ {self.base_url})") from None
        except urllib.error.URLError as e:
            raise LLMError(
                f"can't reach {self.base_url} — is the server running? ({e.reason})"
            ) from None
        except TimeoutError:
            raise LLMError(f"timed out talking to {self.base_url}") from None

    def list_models(self) -> list[str]:
        """Ask the server what models it has (OpenAI-compatible GET /models)."""
        try:
            with self._request("/models", timeout=15) as resp:
                data = json.loads(resp.read())
            return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        except Exception:
            return []

    def chat(self, messages, tools=None, stream=False,
             on_delta=None, on_reason=None) -> ChatResponse:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": bool(stream and on_delta),
        }
        if tools:
            body["tools"] = _sanitize_schema(tools)

        if not body["stream"]:
            with self._request("/chat/completions", body) as resp:
                data = json.loads(resp.read())
            msg = data["choices"][0].get("message", {})
            tcs = [tool_call_from_raw(tc) for tc in msg.get("tool_calls") or []]
            return ChatResponse(
                content=msg.get("content") or "",
                thinking=msg.get("reasoning_content") or "",
                tool_calls=tcs,
                usage=data.get("usage", {}),
            )

        # ---- SSE streaming: deltas as they land, tool_calls reassembled ----
        content_parts, think_parts = [], []
        tc_frag: dict[int, dict] = {}
        usage = {}
        with self._request("/chat/completions", body) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except Exception:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}

                think = delta.get("reasoning_content")
                if think:
                    think_parts.append(think)
                    if on_reason:
                        on_reason(think)

                piece = delta.get("content")
                if piece:
                    content_parts.append(piece)
                    on_delta(piece)

                for tcf in delta.get("tool_calls") or []:
                    idx = tcf.get("index", 0)
                    slot = tc_frag.setdefault(idx, {"id": "", "name": "", "args": ""})
                    if tcf.get("id"):
                        slot["id"] = tcf["id"]
                    fn = tcf.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]

        tool_calls = []
        for idx in sorted(tc_frag):
            slot = tc_frag[idx]
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["args"] or "{}")
            except Exception:
                args = {"_raw": slot["args"]}
            tool_calls.append({"id": slot["id"] or f"call_{idx}",
                               "name": slot["name"], "arguments": args})

        return ChatResponse(
            content="".join(content_parts),
            thinking="".join(think_parts),
            tool_calls=tool_calls,
            usage=usage,
        )
