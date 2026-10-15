#!/usr/bin/env python3
"""Model-discovery + picker gauntlet.

Covers the real-world failure: a llama.cpp router with 7 presets, where the
client showed only the one model hardcoded in the config. Discovery must
succeed against every shape and path the server family actually serves.

Run: python3 tests/test_models.py
"""
import http.server
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mahoraga.llm.openai_compat import OpenAICompat, _extract_ids
from mahoraga.core.runtime import Mahoraga

fails = []


def check(label, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", label, "" if cond else extra)
    if not cond:
        fails.append(label)


PRESETS = ["Qwen/Qwen3-1.7B-GGUF:Q8_0",
           "bartowski/Qwen_Qwen3.5-2B-GGUF:Q4_K_M",
           "empero-ai/Qwen3.8-4B-Distill-GGUF:Q4_K_M",
           "ggml-org/SmolLM3-3B-GGUF:Q4_K_M",
           "katanemo/Arch-Function-3B.gguf:Q4_K_M",
           "openbmb/MiniCPM5-2B-GGUF:Q4_K_M",
           "seanpoyner/smolcode-coder-bsd-3b-tools:Q4_K_M"]


# ---------- response-shape parsing ----------
check("OpenAI shape", _extract_ids({"data": [{"id": "a"}, {"id": "b"}]}) == ["a", "b"])
check("llama.cpp router shape ({models:[{name}]})",
      _extract_ids({"models": [{"name": "a"}, {"model": "b"}]}) == ["a", "b"])
check("bare list of dicts", _extract_ids([{"id": "a"}, {"model": "b"}]) == ["a", "b"])
check("bare list of strings", _extract_ids(["a", "b"]) == ["a", "b"])
check("dupes collapse, order kept", _extract_ids(["a", "b", "a"]) == ["a", "b"])
check("junk yields nothing", _extract_ids({"nope": 1}) == [])
check("None yields nothing", _extract_ids(None) == [])


# ---------- HTTP scenarios ----------
class Handler(http.server.BaseHTTPRequestHandler):
    MODE = "openai"

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path
        if Handler.MODE == "openai":
            if path.startswith("/v1/models"):
                self._send({"object": "list",
                            "data": [{"id": m, "owned_by": "llamacpp"} for m in PRESETS]})
            else:
                self._send({}, 404)
        elif Handler.MODE == "root_only":
            if path.startswith("/models"):          # native path, no /v1
                self._send({"models": [{"name": m} for m in PRESETS]})
            else:
                self._send({}, 404)
        elif Handler.MODE == "reload_only":
            if path.startswith("/v1/models") and "reload=1" in path:
                self._send({"data": [{"id": m} for m in PRESETS]})
            elif path.startswith("/v1/models"):
                self._send({"data": []})            # empty until reload re-scans
            else:
                self._send({}, 404)

    def log_message(self, *a):
        pass


def serve(mode, port):
    Handler.MODE = mode
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


srv_a = serve("openai", 8151)
srv_b = serve("root_only", 8152)
srv_c = serve("reload_only", 8153)

p_openai = OpenAICompat("http://127.0.0.1:8151/v1", model=PRESETS[0])
check("all 7 presets discovered", p_openai.list_models() == PRESETS,
      str(p_openai.list_models()))
check("reachable() true", p_openai.reachable() is True)

p_root = OpenAICompat("http://127.0.0.1:8152/v1", model="x")
check("falls back to root /models", p_root.list_models() == PRESETS)

p_reload = OpenAICompat("http://127.0.0.1:8153/v1", model="x")
check("retries with ?reload=1", p_reload.list_models() == PRESETS)

p_dead = OpenAICompat("http://127.0.0.1:8159/v1", model="x")
check("dead server: empty list", p_dead.list_models() == [])
check("dead server: reachable() false", p_dead.reachable() is False)


# ---------- config wiring: server wins for local, curated is the fallback ----
def make_agent(port, provider_overrides, active="local"):
    tmp = tempfile.mkdtemp()
    cfg = {"temperature": 0.7, "max_tokens": 128, "context_window": 2048,
           "providers": {"local": {"base_url": f"http://127.0.0.1:{port}/v1",
                                   "model": PRESETS[0], "api_key_env": "",
                                   **provider_overrides},
                         "cloud": {"base_url": "https://x.invalid/v1",
                                   "model": "m1", "api_key_env": "NOPE_KEY",
                                   "models": ["m1", "m2"]}},
           "active": active}
    path = os.path.join(tmp, "config.json")
    with open(path, "w") as fh:
        json.dump(cfg, fh)
    return Mahoraga(config_path=path)


a = make_agent(8151, {})
check("local: server catalog used", a.available_models() == PRESETS)
check("local: no curated needed", len(a.available_models()) == 7)

a2 = make_agent(8159, {"models": ["only-in-config"]})     # server down
check("local: curated is the fallback when server is down",
      a2.available_models() == ["only-in-config"])

a3 = make_agent(8151, {"models": ["stale-list"]})         # server up
check("local: live server list beats a stale config list",
      a3.available_models() == PRESETS)

a4 = make_agent(8151, {}, active="cloud")
check("cloud: curated list, no network call", a4.available_models() == ["m1", "m2"])

# ---------- the picker ----------
check("pick by number", a.resolve_model("3") == PRESETS[2])
check("pick last by number", a.resolve_model("7") == PRESETS[6])
check("bad number is None", a.resolve_model("99") is None)
check("name passes through", a.resolve_model("ggml-org/SmolLM3-3B-GGUF:Q4_K_M")
      == "ggml-org/SmolLM3-3B-GGUF:Q4_K_M")
check("empty is None", a.resolve_model("") is None)

switched = a.set_model(a.resolve_model("5"))
check("switch by index persists", switched == PRESETS[4]
      and json.load(open(a.cfg_path))["providers"]["local"]["model"] == PRESETS[4])

for s in (srv_a, srv_b, srv_c):
    s.shutdown()

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("MODEL DISCOVERY GAUNTLET: ALL OK")
