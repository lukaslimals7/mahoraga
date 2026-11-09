#!/usr/bin/env python3
"""The paste-a-key path, end to end.

The bug this pins: a hand-written provider entry LOOKED right — key accepted,
/v1/models listed 84 ids — and every request still answered 404, because the
saved model was one the account could not call. So `connect()` may only save
and activate a model that answered a real completion, and the key must never
reach config.json or the history file.

Run: python3 tests/test_keycmd.py
"""
import http.server
import json
import os
import pathlib
import stat
import sys
import tempfile
import threading
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from mahoraga.core import keys
from mahoraga.core.runtime import Mahoraga
from mahoraga.agent.cli import error_hint, parse_key_arg
from mahoraga.llm import catalog
from mahoraga.llm.openai_compat import LLMError

fails = []


def check(label, cond, extra=""):
    print(("PASS" if cond else "FAIL"), "-", label, "" if cond else extra)
    if not cond:
        fails.append(label)


# ---------------------------------------------------------------- key detection
check("nvapi- identifies NVIDIA", catalog.detect("nvapi-abc123") == ("nvidia", []))
check("sk-or- identifies OpenRouter",
      catalog.detect("sk-or-v1-deadbeef") == ("openrouter", []))
check("sk-proj- identifies OpenAI", catalog.detect("sk-proj-xyz") == ("openai", []))
check("oc_sk- identifies OpenCode",
      catalog.detect("oc_sk_abc123") == ("opencode", []))
check("opencode is a known service", "opencode" in catalog.names())
name, cands = catalog.detect("sk-0123456789")
check("bare sk- is ambiguous, never a guess",
      name is None and "deepseek" in cands and "opencode" in cands,
      f"{name} {cands}")
check("unknown shape → no idea", catalog.detect("totally-not-a-key") == (None, []))
check("empty → no idea", catalog.detect("") == (None, []))
check("embedding ids are not chat models",
      not catalog.is_chat_model("nvidia/nemotron-3-embed-1b")
      and catalog.is_chat_model("nvidia/nemotron-3.5-bolt-e4b"))

# ------------------------------------------------------------------- masking
check("mask keeps the ends", keys.mask("nvapi-abcdef123456") == "nvapi-…3456")
check("mask hides short secrets", set(keys.mask("abc")) == {"•"})

# ------------------------------------------------------------------ .env file
tmp = pathlib.Path(tempfile.mkdtemp(prefix="maho-key-"))
envf = tmp / ".env"
envf.write_text("# my keys\nA=1\nB=2\n")
keys.write_env_var(envf, "B", "changed")
text = envf.read_text()
check("other lines survive a rewrite", "# my keys" in text and "A=1" in text, text)
check("existing key is replaced, not duplicated",
      text.count("B=") == 1 and "B=changed" in text, text)
keys.write_env_var(envf, "C", "3")
check("a new key is appended", "C=3" in envf.read_text())
keys.write_env_var(envf, "B", "again")
check("no duplicate lines after re-set", envf.read_text().count("B=") == 1)
check("env file is owner-only (600)",
      stat.S_IMODE(envf.stat().st_mode) == 0o600,
      oct(stat.S_IMODE(envf.stat().st_mode)))


# ------------------------------------------------- a fake provider that lies
GOOD = ["good-model-1", "good-model-2"]
ADVERTISED = ["dead-model-1", "good-model-1", "nvidia/x-embed-1b",
              "dead-model-2", "good-model-2", "dead-model-3"]


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _rejected(self) -> bool:
        """A key the service refuses — proves a bad key is never activated."""
        return "nope" in (self.headers.get("Authorization") or "")

    def do_GET(self):
        if self._rejected():
            return self._send(401, {"error": {"message": "invalid api key"}})
        if urlsplit(self.path).path.rstrip("/").endswith("/models"):
            return self._send(200, {"data": [{"id": m} for m in ADVERTISED]})
        self._send(404, {"error": {"message": "no such path"}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self._rejected():
            return self._send(401, {"error": {"message": "invalid api key"}})
        model = body.get("model", "")
        if model in GOOD:
            return self._send(200, {"choices": [
                {"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        # exactly what NVIDIA says for a model the account isn't granted
        self._send(404, {"status": 404, "title": "Not Found",
                         "detail": f"Function '{model}': Not found for account 'abc'"})


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}/v1"

cfg_path = tmp / "config.json"
agent = Mahoraga(config_path=str(cfg_path))
agent.add_provider("fakeprov", base, "", "FAKE_API_KEY")
cfg_before = json.loads(cfg_path.read_text())
check("provider add writes the entry",
      cfg_before["providers"]["fakeprov"]["base_url"] == base)
check("provider add guesses the env var name",
      cfg_before["providers"]["fakeprov"]["api_key_env"] == "FAKE_API_KEY")

SECRET = "sk-test-SECRET-value-42"
report = agent.connect(SECRET, "fakeprov")

check("connect activates a provider only after a model answers",
      report["activated"] is True, json.dumps(report, default=str)[:300])
check("connect picked a model that really answered",
      report["model"] in GOOD, report["model"])
check("key was exported to this process",
      os.environ.get("FAKE_API_KEY") == SECRET)
check("key landed in .env",
      keys.read_env_var(tmp / ".env", "FAKE_API_KEY") == SECRET)
check("key is NOT in config.json", SECRET not in cfg_path.read_text())

saved = json.loads(cfg_path.read_text())["providers"]["fakeprov"]
check("active provider switched in config", json.loads(cfg_path.read_text())["active"] == "fakeprov")
check("only proven models are stored", "dead-model-1" not in saved.get("models", []),
      str(saved.get("models")))
check("embedding models are not stored", "nvidia/x-embed-1b" not in saved.get("models", []))
check("the agent's live provider uses the proven model",
      agent.provider.model == report["model"], agent.provider.model)

# the trap: a saved model the account can't call must be replaced, not kept
agent.cfg["providers"]["fakeprov"]["model"] = "dead-model-1"
agent.cfg["providers"]["fakeprov"]["models"] = ["dead-model-1"]
agent._save_config()
agent.provider.model = "dead-model-1"
again = agent.connect(SECRET, "fakeprov")
check("a dead saved model is replaced by a proven one",
      again["model"] in GOOD and "dead-model-1" in (again.get("dead") or []),
      json.dumps(again, default=str)[:300])

# verify_models keeps only what answers
res = agent.verify_models("fakeprov")
check("verify keeps the callable models only",
      sorted(res["working"]) == sorted(GOOD), str(res["working"]))
check("verify reports what it dropped", "dead-model-1" in res["dead"])
check("verify dropped the dead ids from config",
      "dead-model-1" not in json.loads(cfg_path.read_text())["providers"]["fakeprov"]["models"])

# ------------------------------------------------------------- bad-key path
bad = Mahoraga(config_path=str(tmp / "cfg2.json"))
bad.add_provider("reject", base, "", "REJECT_API_KEY")
bad.set_key("REJECT_API_KEY", "nope")
bad.cfg["providers"]["reject"]["model"] = "dead-model-1"
bad._save_config()
rep = bad.connect("nope", "reject")
check("a key that no model answers for is saved but NOT switched",
      rep["activated"] is False and rep["detail"], json.dumps(rep, default=str)[:200])

# --------------------------------------------------- prompt argument parsing
# a bare /key used to crash the whole TUI (empty split → IndexError)
check("bare /key is (None, '')", parse_key_arg("") == (None, ""))
check("whitespace-only /key is (None, '')", parse_key_arg("   ") == (None, ""))
check("one word is the key alone", parse_key_arg("sk-abc") == (None, "sk-abc"))
check("two words: provider + key", parse_key_arg("nvidia nvapi-1") == ("nvidia", "nvapi-1"))
check("a configured provider name also parses",
      parse_key_arg("mything secret") == ("mything", "secret"))

# --------------------------------------------------------- error messages
hint = error_hint
check("404 'not found for account' explains the real cause",
      "granted" in hint(LLMError("Function 'x': Not found for account 'y'", code=404)))
check("410 explains a retired model",
      "retired" in hint(LLMError("model has reached its end of life", code=410)))
check("401 points at /key",
      "/key" in hint(LLMError("invalid api key", code=401)))


def raises_value(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    return ""


msg = raises_value(lambda: Mahoraga(config_path=str(tmp / "cfg3.json")).connect("abc", "nosuch"))
check("unknown provider name → actionable error", "/provider add" in msg, msg)
msg = raises_value(lambda: agent.connect("sk-ambiguous-looking", None))
check("ambiguous key shape → asks which service",
      "say which service" in msg or "can't tell which service" in msg, msg)

srv.shutdown()

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all key-flow checks passed")
