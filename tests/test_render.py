import sys, re, subprocess
sys.path.insert(0, ".")
from mahoraga.agent.cli import Screen, vis_len, wrap_ansi
from mahoraga.core.wheel import _make_frame

NL = chr(10)

# 1) wheel frames: plain text, every line fits -> can never wrap
for f in (0, 7, 123):
    frame = _make_frame(f, 100, 19, color=False)
    lines = frame.split(NL)
    bad = [(vis_len(l), l[:20]) for l in lines if vis_len(l) > 100]
    assert not bad, bad
print("wheel lines fit: OK")

# 2) wrap_ansi wraps by VISUAL width (colored text included)
colored = "\033[32m" + "word " * 40 + "\033[0m"
ws = wrap_ansi(colored, 50)
assert all(vis_len(w) <= 50 for w in ws), [vis_len(w) for w in ws]
assert len(ws) > 3
print("wrap_ansi visual width: OK")

# 3) every painted segment: cleared row, fits width
class Cap:
    def __init__(self): self.buf = ""
    def write(self, x): self.buf += x
    def flush(self): pass

s = Screen()
cap = Cap()
old = sys.stdout
sys.stdout = cap
s.say("\033[32m" + "streamed answer " * 30 + "\033[0m")
s._paint_wheel()
s._paint_pane()
sys.stdout = old

assert cap.buf.startswith("\033[s"), "paint must SAVE the cursor first"
assert cap.buf.rstrip().endswith("\033[u"), "paint must RESTORE the cursor last"
segs = re.split(r"\x1b\[\d+;\d+H", cap.buf)[1:]
assert segs, "nothing painted!"
for seg in segs:
    core = seg
    while core.endswith("\033[u") or core.endswith("\033[s"):   # drop cursor markers
        core = core[:-3]
    body = core.replace("\033[K", "")
    assert vis_len(body) <= s.cols - 1, f"row overflow: {vis_len(body)} > {s.cols-1}"
    assert core.endswith("\033[K"), "row not cleared to EOL"
print(f"paint segments: {len(segs)} rows, all fit + cleared: OK")

# 4) full app boots and exits cleanly on EOF
p = subprocess.run([sys.executable, "-m", "mahoraga"], input=b"",
                   capture_output=True, timeout=40, cwd=".")
assert p.returncode == 0, p.stderr[-400:]
print("boot + clean exit: OK")
print("RENDER GAUNTLET: ALL OK")
