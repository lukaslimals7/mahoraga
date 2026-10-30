"""Ctrl+C must actually stop a reply mid-stream, not wait for the server.

Spins a real HTTP server that dribbles SSE chunks slowly, starts a chat in a
thread, cancels it, and asserts the stream ended early with Cancelled. Then
checks that turning reasoning off actually reaches the request body.
"""
import json, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, ".")
from mahoraga.llm.openai_compat import OpenAICompat
from mahoraga.llm.provider import Cancelled

SENT = []
SEEN_BODIES = []


class Slow(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", 0)))
        try:
            SEEN_BODIES.append(json.loads(raw or b"{}"))
        except Exception:
            SEEN_BODIES.append({})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for i in range(60):                 # ~6 seconds of dribble
                chunk = {"choices": [{"delta": {"content": f"word{i} "}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.1)
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


srv = HTTPServer(("127.0.0.1", 0), Slow)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

# short timeout: a hung request must fail the test, not stall it for minutes
p = OpenAICompat(f"http://127.0.0.1:{port}/v1", model="m", timeout=10)


def on_delta(piece):
    SENT.append(piece)


raised = []


def run():
    try:
        p.chat([{"role": "user", "content": "hi"}], stream=True, on_delta=on_delta)
        raised.append(None)
    except Cancelled:
        raised.append("cancelled")
    except Exception as e:
        raised.append(repr(e))


# ---- 1. cancel stops the stream mid-flight ---------------------------------
t = threading.Thread(target=run)
t.start()
time.sleep(1.0)                 # let ~10 chunks through
t0 = time.time()
p.cancel()
t.join(timeout=5)
elapsed = time.time() - t0

assert not t.is_alive(), "cancel() did not unblock the read"
assert raised == ["cancelled"], raised
assert 0 < len(SENT) < 60, f"expected a partial stream, got {len(SENT)} pieces"
assert "".join(SENT).startswith("word0"), "".join(SENT)[:40]
assert elapsed < 2.0, f"cancel took {elapsed:.1f}s to take effect"
print(f"cancel stopped the stream after {len(SENT)} pieces in {elapsed*1000:.0f} ms — OK")

# ---- 2. /reasoning off reaches the request body ----------------------------
SEEN_BODIES.clear()
tok = threading.Thread(target=lambda: p.chat(
    [{"role": "user", "content": "hi"}], reasoning=False))
tok.start()
time.sleep(0.6)
p.cancel()
tok.join(timeout=5)
body = SEEN_BODIES[-1]
assert body.get("chat_template_kwargs") == {"enable_thinking": False}, body

SEEN_BODIES.clear()
tok = threading.Thread(target=lambda: p.chat(
    [{"role": "user", "content": "hi"}], reasoning=True))
tok.start()
time.sleep(0.6)
p.cancel()
tok.join(timeout=5)
assert "chat_template_kwargs" not in SEEN_BODIES[-1], "reasoning=on must not send the disable flag"
print("reasoning switch OK (disable flag sent only when off)")

srv.shutdown()
