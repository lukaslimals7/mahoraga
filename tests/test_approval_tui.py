#!/usr/bin/env python3
"""The interactive permission card, through the REAL app in a pty.

Unit tests can prove the key map and the policy effects; they cannot prove the
card opens, that it is readable, or that a keypress actually reaches it. This
drives `python -m mahoraga` against a fake OpenAI-compatible server that answers
with a shell tool call, then presses the keys and asserts on the captured bytes
and on ~/.mahoraga/allow.

Answers exercised: DOWN+ENTER (whitelist), y (this session), n (deny),
Esc (deny) — plus a second run with --yolo proving the card never opens there.

Run: python3 tests/test_approval_tui.py      (~40 s, spawns python -m mahoraga)
"""
import fcntl, http.server, json, os, pathlib, pty, re, signal, struct, sys
import tempfile, termios, threading, time

ROOT = str(pathlib.Path(__file__).resolve().parents[1])
UP, DOWN, ENTER, ESC = b"\x1b[A", b"\x1b[B", b"\r", b"\x1b"
FINAL = "ALLDONE"          # the fake model's final answer — proves the turn ended

# odd POST = return a tool call, even POST = return the final answer
STATE = {"n": 0}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, payload, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _sse(self, events):
        body = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
        body += b"data: [DONE]\n\n"
        self._send(body, "text/event-stream")

    def do_GET(self):
        if url_ok(self.path):
            return self._send(json.dumps({"data": [{"id": "m1"}]}).encode())
        self._send(json.dumps({"error": {"message": "nope"}}).encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        user = ""
        for m in body.get("messages") or []:
            if m.get("role") == "user":
                user = m.get("content") or ""
        STATE["n"] += 1
        want_tool = STATE["n"] % 2 == 1
        args = json.dumps({"command": user.strip() or "true"})
        if body.get("stream"):
            if want_tool:
                return self._sse([{"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "call_1", "type": "function",
                     "function": {"name": "shell", "arguments": args}}]}}]}])
            return self._sse([{"choices": [{"delta": {"content": FINAL}}]}])
        if want_tool:
            return self._send(json.dumps({"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "shell",
                                             "arguments": args}}]}}]}).encode())
        self._send(json.dumps({"choices": [{"message": {"role": "assistant",
                                                        "content": FINAL}}]}).encode())


def url_ok(path):
    return path.rstrip("/").endswith("/models")


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]


def make_home():
    home = pathlib.Path(tempfile.mkdtemp(prefix="maho-apr-"))
    cfg = home / ".mahoraga"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({
        "active": "fake",
        "providers": {
            "fake": {"base_url": f"http://127.0.0.1:{port}/v1", "model": "m1",
                     "api_key_env": ""},
        },
        "temperature": 0.7, "max_tokens": 256, "context_window": 4096,
        "mouse": False, "wheel": "full",
    }, indent=2))
    return home


class Session:
    def __init__(self, home, args=()):
        self.home = home
        self.out = bytearray()
        env = {**os.environ, "HOME": str(home), "TERM": "xterm-256color",
               "COLUMNS": "120", "LINES": "40", "PYTHONPATH": ROOT}
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execvpe(sys.executable,
                       [sys.executable, "-m", "mahoraga", *args], env)
            os._exit(127)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

    def drain(self, seconds=0.5):
        end = time.time() + seconds
        while time.time() < end:
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.out.extend(chunk)

    def text(self):
        raw = self.out.decode("utf-8", "replace")
        clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", raw)
        return re.sub(r"\x1b[()][A-Z0-9]", "", clean)

    def wait_for(self, needle, timeout=12.0):
        end = time.time() + timeout
        while time.time() < end:
            self.drain(0.2)
            if needle in self.text():
                return True
        return False

    def send(self, line, wait=1.0):
        os.write(self.fd, line.encode() + b"\r")
        self.drain(wait)

    def key(self, seq, wait=0.8):
        os.write(self.fd, seq)
        self.drain(wait)

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass
        for _ in range(30):
            try:
                pid, _ = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                return
            if pid:
                return
            time.sleep(0.1)
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except OSError:
            pass


fails = []


def check(label, ok):
    print(("PASS" if ok else "FAIL"), "-", label)
    if not ok:
        fails.append(label)


# ---- run 1: the card, and every answer ------------------------------------
home = make_home()
allow = home / ".mahoraga" / "allow"
STATE["n"] = 0
s = Session(home)
s.drain(3.0)
check("app booted", "MAHORAGA" in s.text())

s.send("killall firefox")
check("the card opened", s.wait_for("tool call needs permission"))
card = s.text()
check("the card names the command", "$ killall firefox" in card)
check("option: this session", "Allow this for this session" in card)
check("option: whitelist", "Allow this to whitelist" in card)
check("option: deny", "Deny" in card)
check("the card shows the keys", "enter confirm" in card)

s.key(DOWN)                       # -> "Allow this to whitelist"
s.key(ENTER, 2.0)
check("whitelist answer ran the tool", s.wait_for(FINAL))
check("whitelist answer wrote the rule", allow.exists()
      and "killall\\ firefox" in allow.read_text())

s.send("killall apache2")
check("a new command asks again", s.wait_for("tool call needs permission"))
s.key(b"y", 2.0)                  # -> "Allow this for this session"
check("session answer ran the tool", s.wait_for(FINAL))
check("session answer wrote nothing to disk",
      not allow.exists() or "apache2" not in allow.read_text())

s.send("killall nginx")
check("another command asks again", s.wait_for("tool call needs permission"))
s.key(b"n", 2.0)                  # -> "Deny"
check("deny refused the call", s.wait_for("user denied this tool call"))

s.send("killall memcached")
check("and asks once more", s.wait_for("tool call needs permission"))
s.key(ESC, 2.0)                   # bare Esc refuses too
check("esc refused the call", s.wait_for("user denied this tool call"))

s.send("killall varnish")
check("and asks yet again", s.wait_for("tool call needs permission"))
s.key(b"\x03", 2.0)               # ^C is SIGINT here, not a 0x03 byte
check("ctrl+c on the card refused the call",
      s.wait_for("user denied this tool call"))

s.send("/quit", 1.0)
s.close()

# ---- run 2: --yolo must never open the card -------------------------------
home2 = make_home()
allow2 = home2 / ".mahoraga" / "allow"
STATE["n"] = 0
s2 = Session(home2, args=("--yolo",))
s2.drain(3.0)
s2.send("killall firefox")
check("yolo ran the tool with no card", s2.wait_for(FINAL))
check("yolo never opened the card",
      "tool call needs permission" not in s2.text())
check("yolo wrote no whitelist rule", not allow2.exists())
s2.send("/quit", 1.0)
s2.close()
srv.shutdown()

print("\n--- last screen lines ---")
print("\n".join(l for l in s.text().splitlines() if l.strip())[-900:])
print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("interactive permission card OK")
