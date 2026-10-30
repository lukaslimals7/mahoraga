"""OpenAI-compatible chat client — works with any /v1/chat/completions endpoint:
llama.cpp router, Ollama, vLLM, OpenRouter, DeepSeek, LM Studio, etc.

Streams SSE deltas when asked (the responsive path), and turns ugly HTTP errors
into readable ones with the server's own message.
"""
from __future__ import annotations
import json, threading, urllib.request, urllib.error
from .provider import LLMProvider, ChatResponse, tool_call_from_raw, Cancelled

class LLMError(Exception):
    """A readable provider failure — the server's message, not a traceback.

    Carries `code` (HTTP status or None) and `url` so callers can react to a
    specific failure (a 404 on the wrong path prefix, say) instead of matching
    on text.
    """

    def __init__(self, message: str, code: int | None = None, url: str = ""):
        super().__init__(message)
        self.code = code
        self.url = url


def _extract_ids(data) -> list[str]:
    """Model ids out of every shape servers actually return:
    {"data":[{"id":…}]} (OpenAI) · {"models":[{"name"|"model"|"id":…}]}
    (llama.cpp router) · a bare list of dicts or strings."""
    items = None
    if isinstance(data, dict):
        for key in ("data", "models"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    elif isinstance(data, list):
        items = data
    if not items:
        return []
    out: list[str] = []
    for it in items:
        if isinstance(it, str) and it:
            out.append(it)
        elif isinstance(it, dict):
            for key in ("id", "name", "model"):
                v = it.get(key)
                if isinstance(v, str) and v:
                    out.append(v)
                    break
    seen, uniq = set(), []
    for m in out:
        if m not in seen:
            seen.add(m)
            uniq.append(m)
    return uniq


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
        self._cancel = threading.Event()
        self._active = None          # the live response object, for cancel()

    def cancel(self) -> None:
        """Stop the current stream: flag it, and close the socket so a stalled
        read unblocks instead of waiting for the server to send another byte."""
        self._cancel.set()
        resp, self._active = self._active, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _bases(self) -> list[str]:
        """Candidate API roots: the configured base_url, and the same host with
        the /v1 prefix added or removed. llama.cpp's router answers on
        `/v1/models` while the bare host serves `/models`, and users point
        `base_url` at both spellings — so accept either."""
        b = self.base_url.rstrip("/")
        alt = b[:-3].rstrip("/") if b.endswith("/v1") else b + "/v1"
        return [b] + ([alt] if alt and alt != b else [])

    def _request(self, path: str, body: dict | None = None,
                 timeout: float | None = None, base: str | None = None):
        url = f"{base or self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, headers=self._headers(),
            method="POST" if body is not None else "GET")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout or self.timeout)
            self.base_url = base or self.base_url      # remember what worked
            return resp
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read())
                msg = detail.get("error", {}).get("message") or str(detail)[:200]
            except Exception:
                msg = e.reason or str(e)
            raise LLMError(f"{msg}  (HTTP {e.code} @ {base or self.base_url})",
                           code=e.code, url=url) from None
        except urllib.error.URLError as e:
            raise LLMError(
                f"can't reach {base or self.base_url} — is the server running? "
                f"({e.reason})", url=url) from None
        except TimeoutError:
            raise LLMError(f"timed out talking to {base or self.base_url}",
                           url=url) from None

    def _chat_post(self, body: dict, timeout: float | None = None):
        """POST /chat/completions, tolerating a base_url that omits (or adds)
        the /v1 prefix: a 404 falls through to the alternate root once."""
        last: LLMError | None = None
        for base in self._bases():
            try:
                return self._request("/chat/completions", body,
                                     timeout=timeout, base=base)
            except LLMError as e:
                last = e
                if e.code not in (404, 405):
                    raise
        raise last if last else LLMError("no API root available")

    def _get_json(self, url: str, timeout: float = 15.0):
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def list_models(self) -> list[str]:
        """Ask the server what models it has.

        Tries `/v1/models` (via base_url), then the bare host `/models`, each
        with and without `?reload=1` — llama.cpp's router re-scans its presets
        on reload, which is how a model added to the presets dir shows up
        without a restart. Accepts every response shape (see `_extract_ids`).
        Empty list = server unreachable or nothing advertised."""
        urls = [f"{b}/models" for b in self._bases()]
        for url in urls:
            for suffix in ("", "?reload=1"):
                try:
                    ids = _extract_ids(self._get_json(url + suffix))
                except Exception:
                    ids = []
                if ids:
                    self.base_url = url.rsplit("/models", 1)[0]
                    return ids
        return []

    def reachable(self) -> bool:
        """True if the server answers at all (used for a clear error message)."""
        for base in self._bases():
            try:
                self._get_json(f"{base}/models", timeout=4)
                return True
            except Exception:
                continue
        return False

    def chat(self, messages, tools=None, stream=False,
             on_delta=None, on_reason=None, reasoning: bool = True) -> ChatResponse:
        self._cancel.clear()
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": bool(stream and on_delta),
        }
        if tools:
            body["tools"] = _sanitize_schema(tools)
        if not reasoning:
            # Qwen/llama.cpp templates read this; servers that don't know the
            # field ignore it. Anything that ignores it simply still thinks —
            # the TUI hides the reasoning stream either way.
            body["chat_template_kwargs"] = {"enable_thinking": False}

        if not body["stream"]:
            with self._chat_post(body) as resp:
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
        with self._chat_post(body) as resp:
            self._active = resp
            try:
                for raw in resp:
                    if self._cancel.is_set():
                        raise Cancelled()
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
            except Exception:
                # Dragging the socket out from under a blocking read surfaces as
                # OSError, ValueError or AttributeError depending on how far the
                # reader got. Whatever it is: if the user asked to stop, it was
                # a stop; otherwise it is a real failure.
                if self._cancel.is_set():
                    raise Cancelled() from None
                raise
            finally:
                self._active = None

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
