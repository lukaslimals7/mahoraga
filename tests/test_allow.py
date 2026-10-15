#!/usr/bin/env python3
"""Whitelist gauntlet: tiers, allow-downgrade, BLOCK immunity, add/remove.

Run: python3 tests/test_allow.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mahoraga.policy import permissions as P

fails = []


def check(label, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), "-", label,
          "" if ok else f"(got {got!r}, want {want!r})")
    if not ok:
        fails.append(label)


# isolate the allowlist in a temp file so the real one is untouched
P.ALLOW_PATH = os.path.join(tempfile.mkdtemp(), "allow")

# --- tier classification ---
check("plain command is SAFE", P.shell_risk("ls -la"), P.SAFE)
check("pkill by name is SAFE", P.shell_risk("pkill firefox"), P.SAFE)
check("killall asks (no longer hard-blocked)", P.shell_risk("killall firefox"), P.ASK)
check("killall -9 asks", P.shell_risk("killall -9 firefox"), P.ASK)
check("kill -9 pid asks", P.shell_risk("kill -9 4242"), P.ASK)
check("rm -rf /tmp/x asks", P.shell_risk("rm -rf /tmp/x"), P.ASK)
check("sudo asks", P.shell_risk("sudo apt update"), P.ASK)

# --- hard refusals stay hard ---
check("rm -rf / is BLOCK", P.shell_risk("rm -rf /"), P.BLOCK)
check("mkfs is BLOCK", P.shell_risk("mkfs.ext4 /dev/sda1"), P.BLOCK)
check("shutdown is BLOCK", P.shell_risk("shutdown -h now"), P.BLOCK)
check("fork bomb is BLOCK", P.shell_risk(":(){ :|:& };:"), P.BLOCK)

# --- allowlist: downgrade ASK -> SAFE ---
check("add returns True", P.add_allow(r"killall -9 firefox"), True)
check("whitelisted cmd is now SAFE", P.shell_risk("killall -9 firefox"), P.SAFE)
check("other killall still asks", P.shell_risk("killall firefox"), P.ASK)
check("duplicate add returns False", P.add_allow(r"killall -9 firefox"), False)

# regex rules work
check("add regex", P.add_allow(r"^pkill\s+-f\s+chrome"), True)
check("regex match is SAFE", P.shell_risk("pkill -f chrome"), P.SAFE)
check("regex non-match still SAFE-by-default", P.shell_risk("pkill -f code"), P.SAFE)

# --- BLOCK cannot be whitelisted ---
P.add_allow(r"rm -rf /")
check("BLOCK beats the allowlist", P.shell_risk("rm -rf /"), P.BLOCK)
P.remove_allow(r"rm -rf /")

# --- invalid input is rejected ---
check("empty rule rejected", P.add_allow("   "), False)
check("invalid regex rejected", P.add_allow(r"a("), False)

# --- list / remove ---
rules = P.list_allow()
check("two rules listed", len(rules), 2)
check("remove by index", P.remove_allow("1"), True)
check("removed rule asks again", P.shell_risk("killall -9 firefox"), P.ASK)
check("one rule left", len(P.list_allow()), 1)
check("remove by text", P.remove_allow(r"^pkill\s+-f\s+chrome"), True)
check("empty after removals", P.list_allow(), [])
check("remove missing returns False", P.remove_allow("9"), False)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all whitelist checks passed")
