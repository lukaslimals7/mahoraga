"""The prompt must show a BLINKING cursor, and give the terminal its default back.

Showing the cursor (`?25h`) is not enough: terminals keep whatever DECSCUSR they
were last given, so a fresh profile can land on a steady block.
"""
import fcntl, os, pty, select, struct, sys, termios, time

BLINK = b"\x1b[?12h\x1b[5 q"
DEFAULT = b"\x1b[?12l\x1b[0 q"

pid, master = pty.fork()
if pid == 0:
    env = dict(os.environ, COLUMNS="100", LINES="40", TERM="xterm-256color")
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.execvpe(sys.executable, [sys.executable, "-m", "mahoraga"], env)

fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
buf = bytearray()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.05)
        if r:
            try:
                d = os.read(master, 65536)
            except OSError:
                return
            if not d:
                return
            buf.extend(d)


pump(1.6)
assert BLINK in buf, "prompt did not ask for a blinking cursor"
assert b"\x1b[?25h" in buf, "cursor was never shown"

os.write(master, b"/quit\r")
pump(1.0)
assert DEFAULT in buf, "terminal cursor style was not restored on exit"
print("cursor OK: blinking bar at the prompt, default restored on exit")
