"""Persistent shell session tests — the 'cd must stick across calls' contract."""
import sys
sys.path.insert(0, ".")
from mahoraga.tools.shell_session import ShellSession

s = ShellSession("/tmp")

# 1) THE complaint: bare cd persists
out, code, cwd = s.run("cd /etc")
assert code == 0 and cwd == "/etc", (code, cwd)
out, code, cwd = s.run("pwd")
assert out.strip() == "/etc", out
print("bare cd persists: OK")

# 2) THE bug he hit: chained cd also persists now
s.run("cd /tmp && echo in-tmp")
out, code, cwd = s.run("pwd")
assert out.strip() == "/tmp" and cwd == "/tmp", (out, cwd)
print("chained cd persists: OK")

# 3) exports stick
s.run("export MRG_TEST=hello-42")
out, _, _ = s.run("echo $MRG_TEST")
assert out.strip() == "hello-42", out
print("export persists: OK")

# 4) exit codes propagate
_, code, _ = s.run("false")
assert code == 1, code
_, code, _ = s.run("true")
assert code == 0, code
print("exit codes: OK")

# 5) stderr is captured
out, _, _ = s.run("echo to-stderr 1>&2")
assert "to-stderr" in out, out
print("stderr capture: OK")

# 6) output AND code in one call (subshell — a bare `exit` kills the session,
#    which is correct behavior for a persistent shell, not a bug)
out, code, cwd = s.run("echo one; echo two; (exit 3)")
assert "one" in out and "two" in out and code == 3, (out, code)
print("output + code: OK")

# 7) timeout kills and the NEXT call works (session restarts)
out, code, _ = s.run("sleep 30", timeout=1)
assert code == -1 and "timeout" in out, (code, out)
out, code, _ = s.run("echo revived")
assert out.strip() == "revived" and code == 0, (out, code)
print("timeout + recovery: OK")

# 8) session crash (exit) recovers
s.run("exit 0")
out, code, _ = s.run("echo back")
assert out.strip() == "back", out
print("crash recovery: OK")

print("SHELL SESSION: ALL OK")
