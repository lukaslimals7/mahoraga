"""Permission policy: run everything, question only danger.

Philosophy: the user is root of his own box. Tools run without friction;
only genuinely dangerous shell commands are blocked or confirmed. Everything
is audited either way (see audit.py).
"""
from __future__ import annotations
import re

SAFE = "safe"      # runs without asking
ASK = "ask"        # dangerous — confirm first
BLOCK = "block"    # too dangerous — refused outright

# hard refusals: storage/death sentences
_BLOCK_PATTERNS = [
    r"\bmkfs\b", r"\bdd\s+if=", r">\s*/dev/(sd|nvme|hd)",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b",
    r":\(\)\s*\{.*\};\s*:",                       # fork bomb
    r"\bkill\s+-9\s+-1\b", r"\bkillall\s+-9\b",
    r"\brm\s+(-[a-z]*\s+)*-?[rf]{1,2}\s+/\s*$",   # rm -rf /
    r"\brm\s+(-[a-z]*\s+)*-?[rf]{1,2}\s+/\*",
    r"\bmkswap\b", r"\bhdparm\b",
]
# ask-first: recoverable but sharp
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


def shell_risk(command: str) -> str:
    """Classify a shell command: SAFE, ASK, or BLOCK."""
    for rx in _BLOCK_RE:
        if rx.search(command):
            return BLOCK
    for rx in _ASK_RE:
        if rx.search(command):
            return ASK
    return SAFE


def tier_for(tool_name: str) -> str:
    """Static tier for non-shell tools — all unobstructed (audited instead)."""
    return SAFE
