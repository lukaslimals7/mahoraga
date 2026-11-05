#!/usr/bin/env python3
"""The /key flow through the REAL TUI, in a pty, with an isolated HOME.

WHY: the model layer is unit-tested in test_keycmd.py, but the prompt, the
report lines and the status line only exist in the terminal — and that is where
the bugs were: a bare `/key` raised IndexError and took the whole app down, and
`/key <configured-provider> <key>` silently ignored the provider name because
only catalog names were recognised.

Asserted on the captured bytes: a proven model is chosen, the raw secret never
reaches config.json or the prompt history, and a rejected key is saved but
never activated.

Run: python3 tests/test_key_tui.py      (~30 s, spawns python -m mahoraga)
"""
import fcntl, http.server, json, os, pathlib, pty, re, shutil, struct, sys, tempfile, termios, threading, time
from urllib.parse import urlsplit

ROOT = str(pathlib.Path(__file__).resolve().parents[1])
HOME = pathlib.Path(tempfile.mkdtemp(prefix="maho-tui-"))
GOOD = ["good-model-1", "good-model-2"]
ADVERTISED = ["dead-1", "good-model-1", "x-embed-1b", "dead-2", "good-model-2"]


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _s(self, code, payload):
        b = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if urlsplit(self.path).path.rstrip("/").endswith("/models"):
            return self._s(200, {"data": [{"id": i} for i in ADVERTISED]})
        self._s(404, {"error": {"message": "nope"}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if body.get("model") in GOOD:
            return self._s(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
        self._s(404, {"detail": f"Function '{body.get('model')}': Not found for account 'x'"})


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

shutil.rmtree(HOME, ignore_errors=True)
cfg_dir = HOME / ".mahoraga"
cfg_dir.mkdir(parents=True)
(cfg_dir / "config.json").write_text(json.dumps({
    "active": "fakeprov",
    "providers": {
        "local": {"base_url": "http://127.0.0.1:9/v1", "model": "x", "api_key_env": ""},
        "fakeprov": {"base_url": f"http://127.0.0.1:{port}/v1", "model": "dead-1",
                     "api_key_env": "FAKE_API_KEY", "models": ["dead-1"]},
    },
    "temperature": 0.7, "max_tokens": 256, "context_window": 4096,
    "mouse": False, "wheel": "full",
}, indent=2))

env = {**os.environ, "HOME": str(HOME), "TERM": "xterm-256color", "COLUMNS": "120", "LINES": "40"}
pid, fd = pty.fork()
if pid == 0:
    os.execvpe(sys.executable, [sys.executable, "-m", "mahoraga"], {**env, "PYTHONPATH": ROOT})
    os._exit(127)

fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
out = bytearray()


def drain(seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        out.extend(chunk)


def send(line, wait=1.2):
    os.write(fd, line.encode() + b"\r")
    drain(wait)


drain(3.0)                                   # banner + first paint
send("/provider", 1.5)                       # list providers
send("/key", 1.2)                            # usage help
send("/key fakeprov secret-abc-123", 8.0)    # the paste flow (probe runs)
send("/model", 3.0)                          # list proven models
send("/key cerebras csk-not-a-real-key", 12.0)   # provider that rejects: saved, not switched
send("/quit", 1.5)

try:
    os.close(fd)
except OSError:
    pass
try:
    os.waitpid(pid, 0)
except ChildProcessError:
    pass
srv.shutdown()

raw = out.decode("utf-8", "replace")
clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", raw)      # drop CSI for readable asserts
clean = re.sub(r"\x1b[()][A-Z0-9]", "", clean)

checks = [
    ("app booted", "MAHORAGA" in clean),
    ("/provider lists seeded providers", "fakeprov" in clean),
    ("/key bare shows usage", "paste a key" in clean),
    ("/key proves a model", "proven 2" in clean),
    ("/key activates fakeprov", re.search(r"fakeprov ready|✓ fakeprov", clean) is not None),
    ("report shows the key masked", "secret\u2026-123" in clean),
    ("/model shows the proven model", "good-model-1" in clean),
    ("dead model is gone from /model list", "dead-1" not in clean.split("current:")[-1]),
    ("rejected key is not activated", "saved but not switched" in clean),
    ("no traceback", "Traceback" not in clean),
]
fails = [n for n, ok in checks if not ok]
for n, ok in checks:
    print(("PASS" if ok else "FAIL"), "-", n)

# the key must be in the isolated .env, and nowhere else
envtext = (cfg_dir / ".env").read_text() if (cfg_dir / ".env").exists() else ""
hist = (cfg_dir / "history").read_text() if (cfg_dir / "history").exists() else ""
cfgtext = (cfg_dir / "config.json").read_text()
extra = [
    ("key stored in .env", "FAKE_API_KEY=secret-abc-123" in envtext),
    ("key absent from config.json", "secret-abc-123" not in cfgtext),
    ("key absent from prompt history", "secret-abc-123" not in hist),
]
for n, ok in extra:
    print(("PASS" if ok else "FAIL"), "-", n)
    if not ok:
        fails.append(n)

print("\n--- last screen lines ---")
print("\n".join(l for l in clean.splitlines() if l.strip())[-1200:])
print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("TUI /key flow OK")
