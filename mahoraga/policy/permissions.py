"""Permission policy: run everything, question only danger.

Philosophy: the user is root of his own box. Tools run without friction;
only genuinely dangerous shell commands are blocked or confirmed. Everything
is audited either way (see audit.py).

Whitelist: ~/.mahoraga/allow holds one regex per line describing commands the
user has explicitly blessed. A whitelisted command runs as SAFE (no prompt).
The hard-refusal list (disk/system destruction) can NOT be whitelisted — it is
refused outright. Everything else is fair game.
"""
from __future__ import annotations
import os
import re

SAFE = "safe"      # runs without asking
ASK = "ask"        # dangerous — confirm first
BLOCK = "block"    # too dangerous — refused outright, not whitelistable

ALLOW_PATH = os.path.join(os.path.expanduser("~"), ".mahoraga", "allow")

# hard refusals: storage/death sentences — never whitelistable
_BLOCK_PATTERNS = [
    r"\bmkfs\b", r"\bdd\s+if=", r">\s*/dev/(sd|nvme|hd)",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b",
    r":\(\)\s*\{.*\};\s*:",                       # fork bomb
    r"\bkill\s+-9\s+-1\b",
    r"\brm\s+(-[a-z]*\s+)*-?[rf]{1,2}\s+/\s*$",   # rm -rf /
    r"\brm\s+(-[a-z]*\s+)*-?[rf]{1,2}\s+/\*",
    r"\bmkswap\b", r"\bhdparm\b",
]
# ask-first: recoverable but sharp (confirm once — or whitelist it)
_ASK_PATTERNS = [
    r"\brm\s+-[a-z]*r",                           # any recursive delete
    r"\bsudo\b", r"\bsu\b",
    r"\bchmod\s+-R\b", r"\bchown\s+-R\b",
    r"\bkill\s+-9\b", r"\bkillall\b",
    r"\btruncate\b", r"\bshred\b",
    r">\s*/etc/", r"\bmv\b.*/dev/null",
    r"\bcrontab\b", r"\bsystemctl\b",
]

_BLOCK_RE = [re.compile(p) for p in _BLOCK_PATTERNS]
_ASK_RE = [re.compile(p) for p in _ASK_PATTERNS]

# mtime-cached view of the user whitelist (re-read whenever the file changes)
_allow_cache: dict = {"mtime": None, "rules": []}


def allow_path() -> str:
    return ALLOW_PATH


def _load_allow() -> list:
    try:
        mtime = os.path.getmtime(ALLOW_PATH)
    except OSError:
        _allow_cache.update(mtime=None, rules=[])
        return []
    if _allow_cache["mtime"] != mtime:
        rules = []
        try:
            with open(ALLOW_PATH, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        rules.append(re.compile(line))
                    except re.error:
                        continue
        except OSError:
            rules = []
        _allow_cache.update(mtime=mtime, rules=rules)
    return _allow_cache["rules"]


def allow_match(command: str) -> bool:
    """True if the user has whitelisted this command."""
    return any(rx.search(command) for rx in _load_allow())


def list_allow() -> list:
    """Whitelist rules, in file order."""
    try:
        with open(ALLOW_PATH, "r", encoding="utf-8") as fh:
            return [l.strip() for l in fh
                    if l.strip() and not l.strip().startswith("#")]
    except OSError:
        return []


def add_allow(rule: str) -> bool:
    """Append a rule (regex). False if empty, invalid, or already present."""
    rule = rule.strip()
    if not rule:
        return False
    try:
        re.compile(rule)
    except re.error:
        return False
    if rule in list_allow():
        return False
    os.makedirs(os.path.dirname(ALLOW_PATH), exist_ok=True)
    with open(ALLOW_PATH, "a", encoding="utf-8") as fh:
        fh.write(rule + "\n")
    _allow_cache["mtime"] = None
    return True


def remove_allow(which: str) -> bool:
    """Remove by 1-based index (as shown by /allow) or by exact rule text."""
    rules = list_allow()
    idx = None
    if which.isdigit():
        i = int(which) - 1
        if 0 <= i < len(rules):
            idx = i
    elif which in rules:
        idx = rules.index(which)
    if idx is None:
        return False
    del rules[idx]
    os.makedirs(os.path.dirname(ALLOW_PATH), exist_ok=True)
    with open(ALLOW_PATH, "w", encoding="utf-8") as fh:
        fh.write("# mahoraga allowlist — one regex per line; matches run without asking\n")
        fh.writelines(r + "\n" for r in rules)
    _allow_cache["mtime"] = None
    return True


def shell_risk(command: str) -> str:
    """Classify a shell command: SAFE, ASK, or BLOCK.

    Order: hard refusals (BLOCK) > user whitelist (SAFE) > ask-first (ASK).
    """
    for rx in _BLOCK_RE:
        if rx.search(command):
            return BLOCK
    if allow_match(command):
        return SAFE
    for rx in _ASK_RE:
        if rx.search(command):
            return ASK
    return SAFE


def tier_for(tool_name: str) -> str:
    """Static tier for non-shell tools — all unobstructed (audited instead)."""
    return SAFE
