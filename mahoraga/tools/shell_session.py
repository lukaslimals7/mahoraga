"""Persistent shell session — the real fix for "cd doesn't stick".

A long-lived bash process behind the shell tool: `cd`, `export`, `source
venv/bin/activate`, shell functions — everything persists across tool calls,
exactly like a terminal. Every command's exit code and cwd come back via a
per-session random-token sentinel line, so command output can never fake it.

I/O note: read the pipe with raw os.read + an explicit line buffer. Mixing
select() with buffered readline() deadlocks — readline() swallows multiple
lines into Python's buffer and select() then never fires.

Non-interactive commands only (an interactive program would eat the
sentinel); use process_manage for long-running or interactive work.
"""
from __future__ import annotations
import os, re, select, secrets, subprocess, time

_SENTINEL_RE = r"^__MRG_{tok}_(\d+)_(.*)$"


class ShellSession:
    def __init__(self, cwd: str | None = None):
        self.cwd = cwd or os.path.expanduser("~")
        self.proc: subprocess.Popen | None = None
        self.token = secrets.token_hex(6)
        self._buf = b""

    def _spawn(self):
        self.token = secrets.token_hex(6)
        self._buf = b""
        self.proc = subprocess.Popen(
            ["bash", "--noprofile", "--norc"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=self.cwd, bufsize=0)
        # stderr into the same pipe; no prompt garbage into captured output
        self._write("exec 2>&1\nunset PS1 PROMPT_COMMAND\n")

    def _write(self, s: str):
        self.proc.stdin.write(s.encode())
        self.proc.stdin.flush()

    def _kill(self):
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc = None

    def run(self, command: str, timeout: int = 60) -> tuple[str, int, str]:
        """Run one command in the persistent session.
        Returns (output, exit_code, cwd_after)."""
        if self.proc is None or self.proc.poll() is not None:
            self._spawn()
        deadline = time.time() + timeout
        pat = re.compile(_SENTINEL_RE.format(tok=self.token))
        try:
            self._write(command.rstrip() + "\n")
            self._write(f"printf '\\n__MRG_{self.token}_%s_%s\\n' \"$?\" \"$PWD\"\n")
        except (BrokenPipeError, OSError):
            self._kill()
            return "[shell session died — it restarts on the next call]", -1, self.cwd

        out: list[str] = []
        fd = self.proc.stdout.fileno()
        while True:
            # drain complete lines already buffered
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace")
                m = pat.match(line)
                if m:
                    code, pwd = int(m.group(1)), m.group(2)
                    self.cwd = pwd
                    return "\n".join(out), code, pwd
                out.append(line)
            remaining = deadline - time.time()
            if remaining <= 0:
                self._kill()
                out.append(f"[timeout after {timeout}s — shell restarted]")
                return "\n".join(out), -1, self.cwd
            r, _, _ = select.select([fd], [], [], min(remaining, 1.0))
            if not r:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                self._kill()
                out.append("[shell session ended unexpectedly]")
                return "\n".join(out), -1, self.cwd
            self._buf += chunk
