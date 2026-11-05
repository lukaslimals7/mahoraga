#!/usr/bin/env python3
"""The /key flow AND the scrolling pickers, through the REAL TUI in a pty.

WHY this is not a unit test: the prompt, the modal list and the status line only
exist in a terminal. Bugs found here were invisible to unit tests — a bare
`/key` raised IndexError and took the app down, `/key <configured provider>
<key>` ignored the provider name, and a modal can swallow or leak keys.

Asserted on the captured bytes: arrows move and Enter picks, typing filters the
list, a proven model is chosen, the raw secret never reaches config.json or the
prompt history, and a rejected key is saved but never activated.

Run: python3 tests/test_key_tui.py      (~35 s, spawns python -m mahoraga)
"""
import fcntl, http.server, json, os, pathlib, pty, re, shutil, struct, sys, tempfile, termios, threading, time
from urllib.parse import urlsplit

ROOT = str(pathlib.Path(__file__).resolve().parents[1])
HOME = pathlib.Path(tempfile.mkdtemp(prefix="maho-tui-"))
GOOD = ["good-model-1", "good-model-2"]
ADVERTISED = ["dead-1", "good-model-1", "x-embed-1b", "dead-2", "good-model-2"]
UP, DOWN, ENTER = b"\x1b[A", b"\x1b[B", b"\r"


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

    def _rejected(self):
        return "not-a-real" in (self.headers.get("Authorization") or "")

    def do_GET(self):
        if self._rejected():
            return self._s(401, {"error": {"message": "invalid api key"}})
        if urlsplit(self.path).path.rstrip("/").endswith("/models"):
            return self._s(200, {"data": [{"id": i} for i in ADVERTISED]})
        self._s(404, {"error": {"message": "nope"}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self._rejected():
            return self._s(401, {"error": {"message": "invalid api key"}})
        if body.get("model") in GOOD:
            return self._s(200, {"choices": [{"message": {"role": "assistant",
                                                          "content": "ok"}}]})
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

env = {**os.environ, "HOME": str(HOME), "TERM": "xterm-256color",
       "COLUMNS": "120", "LINES": "40", "PYTHONPATH": ROOT}
pid, fd = pty.fork()
if pid == 0:
    os.execvpe(sys.executable, [sys.executable, "-m", "mahoraga"], env)
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


def key(seq, wait=0.7):
    os.write(fd, seq)
    drain(wait)


drain(3.0)                                        # banner + first paint
send("/provider", 1.5)                            # ── provider PICKER opens
key(UP, 0.8)                                      # move off the marked row
key(ENTER, 1.5)                                   # …and switch to it
send("/provider fakeprov", 1.5)                   # typed form still works
send("/key", 1.2)                                 # bare /key → usage
send("/key fakeprov secret-abc-123", 8.0)         # paste flow (probe runs)
send("/model", 1.5)                               # ── model PICKER (2 items)
key(DOWN, 0.8)
key(ENTER, 1.5)
send("/model", 1.5)                               # ── picker again, filtered
key(b"1", 0.8)                                    # typing narrows the list
key(ENTER, 1.5)
send("/key cerebras csk-not-a-real-key", 14.0)    # rejected: saved, not switched
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
    ("/provider opened the picker", "providers ·" in clean and "↑↓ move" in clean),
    ("arrow + enter switched provider", "provider -> local" in clean),
    ("typed /provider still works", "provider -> fakeprov" in clean),
    ("/key bare shows usage", "paste a key" in clean),
    ("/key proves a model", "proven 2" in clean),
    ("/key activates fakeprov", "✓ fakeprov" in clean),
    ("report shows the key masked", "secret\u2026-123" in clean),
    ("/model opened a picker", "models · fakeprov" in clean),
    ("arrow + enter changed the model", "model -> good-model-2" in clean),
    ("typing filtered the list", "1 of 2" in clean and "/1" in clean),
    ("filtered enter chose it", "model -> good-model-1" in clean),
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
print("\n".join(l for l in clean.splitlines() if l.strip())[-1400:])
print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("TUI pickers + /key flow OK")
