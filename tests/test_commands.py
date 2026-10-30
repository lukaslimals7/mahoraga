"""The command table, the dispatcher and Tab completion must not drift apart.

The bug this exists for: COMMANDS was a hand-maintained list used only for
completion, so /cd, /pwd, /mouse, /reasoning and /wheel were invisible to Tab
while working fine when typed in full.
"""
import re, sys
from pathlib import Path

sys.path.insert(0, ".")

from mahoraga.agent.cli import (COMMAND_ALIASES, COMMAND_NAMES, COMMAND_SPEC,
                                STATIC_ARG_POOL, complete_line)

SRC = Path("mahoraga/agent/cli.py").read_text()

# ---- 1. every dispatched command is documented, and vice versa -------------
dispatched = set(re.findall(r'cmd == "([^"]+)"', SRC))
for group in re.findall(r'cmd in \(([^)]+)\)', SRC):
    dispatched |= set(re.findall(r'"([^"]+)"', group))
# /allow rm is an argument form, not a command
dispatched.discard("/allow rm")

documented = {name for name, _, _ in COMMAND_SPEC} | set(COMMAND_ALIASES)

undocumented = dispatched - documented
missing_branch = documented - dispatched
assert not undocumented, f"dispatched but missing from COMMAND_SPEC: {sorted(undocumented)}"
assert not missing_branch, f"in COMMAND_SPEC but nothing handles it: {sorted(missing_branch)}"

# ---- 2. the table is sane --------------------------------------------------
names = [n for n, _, _ in COMMAND_SPEC]
assert len(names) == len(set(names)), "duplicate command in the table"
assert all(n.startswith("/") for n in names)
assert all(desc for _, _, desc in COMMAND_SPEC), "every command needs a one-line description"
assert set(STATIC_ARG_POOL) <= set(names), f"arg pool for unknown command: {set(STATIC_ARG_POOL) - set(names)}"

# commands whose argument is free text / discovered at runtime
DYNAMIC = {"/provider", "/model", "/allow", "/cd"}
for name, hint, _ in COMMAND_SPEC:
    if hint and name not in DYNAMIC:
        assert name in STATIC_ARG_POOL, f"{name} declares an argument but offers no candidates"

# ---- 3. completion behaviour ----------------------------------------------
def pool_for(cmd):
    return STATIC_ARG_POOL.get(cmd, [])

# names before the first space
# "/mo" is deliberately ambiguous (/model, /mouse) and must NOT guess
for prefix, expected in (("/", None), ("/mod", "/model"), ("/mo", None),
                         ("/reason", "/reasoning"), ("/wh", "/wheel"), ("/c", None)):
    buf, cur, matches = complete_line(prefix, len(prefix), COMMAND_NAMES, pool_for)
    if expected:
        assert buf == expected and cur == len(expected), (prefix, buf, expected)
    else:
        assert matches, f"{prefix!r} matched nothing"

# the two commands that used to be invisible to Tab
assert "/reasoning" in COMMAND_NAMES and "/wheel" in COMMAND_NAMES

# arguments after the space
buf, cur, m = complete_line("/wheel ", 7, COMMAND_NAMES, pool_for)
assert len(m) == 3 and buf == "/wheel ", (buf, m)
buf, cur, m = complete_line("/wheel s", 8, COMMAND_NAMES, pool_for)
assert buf == "/wheel sticky", buf
# "o" is ambiguous (on / off): extend nothing, offer both
buf, cur, m = complete_line("/reasoning o", 12, COMMAND_NAMES, pool_for)
assert buf == "/reasoning o" and m == ["on", "off"], (buf, m)
buf, cur, m = complete_line("/wheel st", 9, COMMAND_NAMES, pool_for)
assert buf == "/wheel sticky", buf
buf, cur, m = complete_line("/tools of", 9, COMMAND_NAMES, pool_for)
assert buf == "/tools off", buf

# free text does not pretend to complete
assert complete_line("hello", 5, COMMAND_NAMES, pool_for) is None
assert complete_line("", 0, COMMAND_NAMES, pool_for) is None

# a bad pool must not raise out of completion
def broken(cmd):
    raise RuntimeError("boom")
try:
    complete_line("/wheel ", 7, COMMAND_NAMES, broken)
except Exception as e:
    raise AssertionError(f"completion must swallow pool errors: {e}")

print(f"commands OK: {len(COMMAND_SPEC)} documented, {len(dispatched)} dispatched, "
      f"{len(STATIC_ARG_POOL)} with argument completion")
