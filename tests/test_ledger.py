"""docs/REGRESSIONS.md must stay true.

A ledger that names tests which no longer exist is worse than no ledger: it
claims coverage that isn't there. Cheap guard, runs in milliseconds.
"""
import re
from pathlib import Path

ledger = Path("docs/REGRESSIONS.md")
assert ledger.exists(), "docs/REGRESSIONS.md is missing"

text = ledger.read_text()
tests_dir = Path("tests")
existing = {p.name for p in tests_dir.glob("test_*.py")}

named = set(re.findall(r"(test_[a-z_]+\.py)", text))
missing = named - existing
assert not missing, f"ledger names tests that do not exist: {sorted(missing)}"

rows = [l for l in text.splitlines() if l.startswith("| ") and not l.startswith("| #")
        and not l.startswith("|---")]
assert len(rows) >= 10, f"ledger has only {len(rows)} rows — did it get truncated?"

# every row must say what guards it, even if the answer is "manual"
for line in rows:
    cells = [c.strip() for c in line.strip("|").split("|")]
    assert len(cells) >= 5, f"malformed row: {line[:60]}"
    assert cells[-1], f"row has no guard: {cells[0]}"

print(f"ledger OK: {len(rows)} fixes logged, {len(named)} test files referenced, all exist")
