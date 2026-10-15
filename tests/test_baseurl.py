#!/usr/bin/env python3
"""base_url tolerance gauntlet.

The router answers on `http://127.0.0.1:8080/v1/models` while users naturally
point `base_url` at `http://127.0.0.1:8080`. Both spellings must work — for
discovery *and* for chat — instead of failing with a confusing 404.

Run: python3 tests/test_baseurl.py
"""
import http.server
import json
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mahoraga.llm.openai_compat import OpenAICompat, LLMError

fails = []
PRESETS = ["Qwen/Qwen3-1.7B-GGUF:Q8_0", "ggml-org/SmolLM3-3B-GGUF:Q4_K_M"]
HITS = []          # (method, path) actually served


def check(label, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", label, "" if cond else extra)
    if not cond:
        fails.append(label)


class RouterOnly(http.server.BaseHTTPRequestHandler):
    """Strictly the /v1 API — anything without the prefix is a 404."""
    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        HITS.append(("GET", self.path))
        if self.path.startswith("/v1/models"):
            self._send({"object": "list", "data": [{"id": m} for m in PRESETS]})
        else:
            self._send({"error": {"message": "not found"}}, 404)

    def do_POST(self):
        HITS.append(("POST", self.path))
        if not self.path.startswith("/v1/chat/completions"):
            self._send({"error": {"message": "use /v1/chat/completions"}}, 404)
            return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i, w in enumerate(["hello", "from", "router"]):
                chunk = {"choices": [{"delta": {"content": ("" if i == 0 else " ") + w}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self._send({"choices": [{"message": {"content": "hello from router"}}]})

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8171), RouterOnly)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# ---- bare host base_url (how a user writes it: no /v1) ----
p = OpenAICompat("http://127.0.0.1:8171", model=PRESETS[0])
check("bare base_url: discovery finds /v1/models", p.list_models() == PRESETS)
check("bare base_url: api root adopted", p.base_url == "http://127.0.0.1:8171/v1")

p2 = OpenAICompat("http://127.0.0.1:8171", model=PRESETS[0])
out = p2.chat([{"role": "user", "content": "hi"}], stream=False)
check("bare base_url: chat works (404 retried on /v1)", out.content == "hello from router")

p3 = OpenAICompat("http://127.0.0.1:8171", model=PRESETS[0])
pieces = []
out3 = p3.chat([{"role": "user", "content": "hi"}], stream=True,
               on_delta=pieces.append)
check("bare base_url: streaming works", out3.content == "hello from router"
      and "".join(pieces) == "hello from router")

# ---- /v1 base_url keeps working (the original spelling) ----
HITS.clear()
p4 = OpenAICompat("http://127.0.0.1:8171/v1", model=PRESETS[0])
check("/v1 base_url: discovery still works", p4.list_models() == PRESETS)
out4 = p4.chat([{"role": "user", "content": "hi"}], stream=False)
check("/v1 base_url: chat works", out4.content == "hello from router")
check("no wasted retry on the correct prefix",
      not any(h == ("POST", "/chat/completions") for h in HITS), str(HITS))

# ---- _bases() shape ----
check("_bases: bare → [bare, bare/v1]",
      OpenAICompat("http://h:1")._bases() == ["http://h:1", "http://h:1/v1"])
check("_bases: /v1 → [/v1, bare]",
      OpenAICompat("http://h:1/v1")._bases() == ["http://h:1/v1", "http://h:1"])
check("_bases: trailing slash normalised",
      OpenAICompat("http://h:1/v1/")._bases() == ["http://h:1/v1", "http://h:1"])

# ---- errors stay informative ----
p5 = OpenAICompat("http://127.0.0.1:8172", model="x", timeout=2)
try:
    p5.chat([{"role": "user", "content": "hi"}])
    check("dead server raises LLMError", False)
except LLMError as e:
    check("dead server raises LLMError", True)
    check("message names the address", "8172" in str(e), str(e))
    check("code is None for a connection failure", e.code is None)

p6 = OpenAICompat("http://127.0.0.1:8171", model="x")
try:
    p6._request("/definitely-not-a-route", {}, base="http://127.0.0.1:8171")
    check("404 raises LLMError with code 404", False)
except LLMError as e:
    check("404 raises LLMError with code 404", e.code == 404, str(e.code))
    check("error carries the url", e.url.endswith("/definitely-not-a-route"))

srv.shutdown()

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("BASE_URL TOLERANCE GAUNTLET: ALL OK")
