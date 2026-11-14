"""Persistent memory files: read/write/append, prompt injection, import/export.

Run: python3 tests/test_memory_store.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mahoraga.memory.store import MemoryStore

fails = []


def check(label, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), "-", label,
          "" if ok else f"(got {got!r}, want {want!r})")
    if not ok:
        fails.append(label)


def raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


tmp = tempfile.mkdtemp()
st = MemoryStore(tmp)

# ---- raw file access ----
check("missing file reads empty", st.read("memory.md"), "")
check("unknown file is rejected", raises(lambda: st.path("nope.md")), True)

st.append("memory.md", "Lukas builds Gyaanix")
check("append creates the file", st.read("memory.md"), "Lukas builds Gyaanix\n")
st.append("memory.md", "Lukas runs on coffee")
check("append adds a line", st.read("memory.md"),
      "Lukas builds Gyaanix\nLukas runs on coffee\n")
check("blank append is rejected", raises(lambda: st.append("memory.md", "   ")), True)
st.append("user.md", "Co-founder, infrastructure & security")
check("user.md is separate", st.read("user.md"), "Co-founder, infrastructure & security\n")

# ---- prompt injection ----
st.write("memory.md", "fact one\nfact two")
st.write("user.md", "Lukas — security engineer")
block = st.inject()
check("inject carries both titles",
      "## PERSISTENT MEMORY" in block and "## USER PROFILE" in block, True)
check("inject carries the content",
      "fact one" in block and "Lukas — security engineer" in block, True)

# the budget is what keeps a big memory from crowding out the conversation
st.write("memory.md", "x" * 5000)
short = st.inject(budget=100)
check("budget truncates the block", len(short) < 600, True)
check("truncation is marked", "truncated" in short, True)
check("budget leaves user.md intact", "Lukas — security engineer" in short, True)

# ---- export / import ----
st.write("memory.md", "alpha\nbeta")
st.write("user.md", "gamma")

bundle = os.path.join(tmp, "bundle.json")
st.export(bundle)
check("bundle is JSON", json.loads(Path(bundle).read_text())["memory"], "alpha\nbeta")

st2 = MemoryStore(tempfile.mkdtemp())
rep = st2.import_(bundle)
check("import wrote both files", sorted(rep["written"]), ["memory.md", "user.md"])
check("import restored memory", st2.read("memory.md"), "alpha\nbeta")
check("import restored user", st2.read("user.md"), "gamma")

# a suffix-less path is a directory
outdir = os.path.join(tmp, "backup")
st.export(outdir)
check("dir export wrote memory.md", Path(outdir, "memory.md").read_text(), "alpha\nbeta")
st3 = MemoryStore(tempfile.mkdtemp())
check("dir import wrote both", sorted(st3.import_(outdir)["written"]),
      ["memory.md", "user.md"])

# a single .md imports as memory.md
single = os.path.join(tmp, "solo.md")
Path(single).write_text("just one file")
st4 = MemoryStore(tempfile.mkdtemp())
check("single .md import", st4.import_(single)["written"], ["memory.md"])
check("single .md content", st4.read("memory.md"), "just one file")

# ---- failures ----
check("missing import is rejected",
      raises(lambda: st.import_(os.path.join(tmp, "nope.json"))), True)
bad = os.path.join(tmp, "bad.json")
Path(bad).write_text("[1, 2, 3]")
check("non-object bundle is rejected", raises(lambda: st.import_(bad)), True)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all memory-store checks passed")
