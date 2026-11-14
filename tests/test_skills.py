"""Skills: frontmatter, discovery, install/remove, and the prompt wiring.

Run: python3 tests/test_skills.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mahoraga.skills.manager import SkillsManager, parse_frontmatter
from mahoraga.core.runtime import Mahoraga

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


# ---- frontmatter ----
meta, body = parse_frontmatter(
    "---\nname: my-skill\ndescription: Use when testing.\ntags: [a, b]\n---\n\n# Body\nhi\n")
check("name parsed", meta.get("name"), "my-skill")
check("description parsed", meta.get("description"), "Use when testing.")
check("tag list parsed", meta.get("tags"), ["a", "b"])
check("body is separated", body, "# Body\nhi")

meta2, body2 = parse_frontmatter("# No frontmatter\njust text")
check("missing frontmatter is tolerated", (meta2, body2), ({}, "# No frontmatter\njust text"))

# ---- discovery ----
tmp = tempfile.mkdtemp()
user_dir = os.path.join(tmp, "skills")
bundled_dir = os.path.join(tmp, "bundled")
os.makedirs(os.path.join(user_dir, "alpha"))
os.makedirs(os.path.join(bundled_dir, "beta"))
Path(user_dir, "alpha", "SKILL.md").write_text(
    "---\nname: alpha\ndescription: first skill\n---\n\nalpha body")
Path(bundled_dir, "beta", "SKILL.md").write_text(
    "---\nname: beta\ndescription: second skill\n---\n\nbeta body")

sm = SkillsManager(user_dir=user_dir, bundled_dir=bundled_dir)
check("both dirs discovered", sorted(sm.names()), ["alpha", "beta"])
check("get returns the body", sm.get("alpha")["body"], "alpha body")
check("bundled flag set", sm.get("beta")["bundled"], True)
check("user flag set", sm.get("alpha")["bundled"], False)
check("unknown skill is None", sm.get("nope"), None)

# a user skill with the same name wins over the bundled one
os.makedirs(os.path.join(user_dir, "beta"))
Path(user_dir, "beta", "SKILL.md").write_text(
    "---\nname: beta\ndescription: overridden\n---\n\nuser beta body")
check("user skill overrides bundled", sm.get("beta")["body"], "user beta body")
check("override is not bundled", sm.get("beta")["bundled"], False)

# ---- prompt injection ----
block = sm.inject()
check("inject lists the skill", "alpha" in block and "first skill" in block, True)
check("inject tells the model how to load", "load_skill" in block, True)

# ---- install / remove ----
src_dir = os.path.join(tmp, "to-install")
os.makedirs(src_dir)
Path(src_dir, "SKILL.md").write_text(
    "---\nname: gamma\ndescription: installed from a folder\n---\n\ngamma body")
rep = sm.install(src_dir)
check("install from a folder", rep["name"], "gamma")
check("installed skill is discoverable", "gamma" in sm.names(), True)

src_file = os.path.join(tmp, "delta-SKILL.md")
Path(src_file).write_text(
    "---\nname: delta\ndescription: installed from a file\n---\n\ndelta body")
check("install from a single file", sm.install(src_file)["name"], "delta")

check("remove works", sm.remove("gamma"), True)
check("removed skill is gone", "gamma" in sm.names(), False)
check("user copy of an overridden skill is removable", sm.remove("beta"), True)
check("removing a missing skill fails", sm.remove("nope"), False)
os.makedirs(os.path.join(bundled_dir, "bundled-only"))
Path(bundled_dir, "bundled-only", "SKILL.md").write_text(
    "---\nname: bundled-only\ndescription: ships with mahoraga\n---\n\nbody")
check("bundled skills are not removable", sm.remove("bundled-only"), False)
check("bundled skill survives", "bundled-only" in sm.names(), True)

# ---- runtime wiring ----
cfg_dir = tempfile.mkdtemp()
skills_dir = os.path.join(cfg_dir, "skills", "demo")
os.makedirs(skills_dir)
Path(skills_dir, "SKILL.md").write_text(
    "---\nname: demo\ndescription: a demo skill\n---\n\ndo the demo thing")
Path(cfg_dir, "memory.md").write_text("Lukas is a security engineer")
Path(cfg_dir, "user.md").write_text("Prefers concise answers")
cfg = {"temperature": 0.7, "max_tokens": 256, "context_window": 2048,
       "providers": {"local": {"base_url": "http://127.0.0.1:1/v1",
                               "model": "m", "api_key_env": ""}},
       "active": "local"}
cfg_path = os.path.join(cfg_dir, "config.json")
Path(cfg_path).write_text(json.dumps(cfg))

agent = Mahoraga(config_path=cfg_path)
prompt = agent.ctx.system_prompt
check("memory is injected into the prompt", "Lukas is a security engineer" in prompt, True)
check("user profile is injected", "Prefers concise answers" in prompt, True)
check("skill list is injected", "demo" in prompt and "a demo skill" in prompt, True)

check("load_skill tool is registered", "load_skill" in agent.registry.tools, True)
check("memory_read tool is registered", "memory_read" in agent.registry.tools, True)
check("load_skill returns the body",
      agent.registry.execute("load_skill", {"name": "demo"}), "do the demo thing")
check("load_skill on a missing skill errors",
      "error" in agent.registry.execute("load_skill", {"name": "nope"}), True)

# remember writes to the file the prompt is built from
agent.registry.execute("remember", {"fact": "Lukas ships on Fridays"})
check("remember writes memory.md",
      "Lukas ships on Fridays" in agent.memory.read("memory.md"), True)
check("recall finds it",
      "Lukas ships on Fridays" in agent.registry.execute("recall", {}), True)

# /skill drops the body into the live context
agent.skill_load("demo")
check("skill_load adds to the context",
      any("do the demo thing" in (m.get("content") or "")
          for m in agent.ctx.messages), True)

# editing memory refreshes the prompt without a restart
agent.memory.append("memory.md", "a brand new fact")
agent.refresh_system()
check("refresh_system picks up edits", "a brand new fact" in agent.ctx.system_prompt, True)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all skill checks passed")
