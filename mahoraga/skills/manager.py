"""Skills — plain Markdown instructions the agent loads on demand.

A skill is a folder containing a SKILL.md:

    ---
    name: my-skill
    description: Use when the user asks for X.
    tags: [a, b]
    ---

    # Body
    The instructions the model follows.

Skills live in two places, user skills winning on a name clash:

    ~/.mahoraga/skills/<name>/SKILL.md   (yours — import, edit, delete)
    <package>/skills/<name>/SKILL.md     (bundled with Mahoraga)

The list is injected into the system prompt; the model calls `load_skill`
to read a body in full, and `/skill <name>` drops one into the session.
Everything is stdlib: the frontmatter parser reads `key: value` lines and
`[a, b]` lists, which covers what a skill needs without a YAML dependency.
"""
from __future__ import annotations
import pathlib, shutil
from urllib.parse import urlparse
from urllib.request import Request, urlopen

USER_DIR = pathlib.Path.home() / ".mahoraga" / "skills"
BUNDLED_DIR = pathlib.Path(__file__).resolve().parent.parent / "skills"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split `---` delimited frontmatter from the body. Stdlib only."""
    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key, val = key.strip(), val.strip()
                if val.startswith("[") and val.endswith("]"):
                    val = [v.strip() for v in val[1:-1].split(",") if v.strip()]
                meta[key] = val
            body = parts[2].strip()
    return meta, body


class SkillsManager:
    def __init__(self, user_dir=None, bundled_dir=None):
        self.user_dir = (pathlib.Path(user_dir).expanduser() if user_dir
                         else USER_DIR)
        self.bundled_dir = pathlib.Path(bundled_dir) if bundled_dir else BUNDLED_DIR
        self.user_dir.mkdir(parents=True, exist_ok=True)

    # ---- discovery ----

    def _dirs(self) -> list[pathlib.Path]:
        """Bundled first, user second: a user skill with the same name wins."""
        return [self.bundled_dir, self.user_dir]

    def all(self) -> list[dict]:
        """Every skill. User skills are read last so they override bundled
        ones with the same name."""
        out: dict[str, dict] = {}
        for d in self._dirs():
            if not d.is_dir():
                continue
            for child in sorted(d.iterdir()):
                f = child / "SKILL.md"
                if not f.is_file():
                    continue
                meta, body = parse_frontmatter(f.read_text(errors="replace"))
                name = meta.get("name") or child.name
                out[name] = {
                    "name": name,
                    "description": meta.get("description", ""),
                    "tags": meta.get("tags", []),
                    "body": body,
                    "path": str(child),
                    "bundled": d == self.bundled_dir,
                }
        return list(out.values())

    def names(self) -> list[str]:
        return [s["name"] for s in self.all()]

    def get(self, name: str) -> dict | None:
        for s in self.all():
            if s["name"] == name:
                return s
        return None

    # ---- prompt injection ----

    def inject(self) -> str:
        skills = self.all()
        if not skills:
            return ""
        lines = ["## AVAILABLE SKILLS",
                 "When one matches the task, call the load_skill tool with its "
                 "name to read its full instructions."]
        for s in skills:
            lines.append(f"- {s['name']} — {s['description']}")
        return "\n".join(lines)

    # ---- install / remove ----

    @staticmethod
    def _skill_name(text: str) -> str | None:
        meta, _ = parse_frontmatter(text)
        name = meta.get("name")
        return str(name) if name else None

    def install(self, src) -> dict:
        """Copy a skill folder, a single SKILL.md, or a URL into the user dir.

        The skill's own `name:` frontmatter wins over the folder name, so
        importing a folder called `to-install` that declares `name: gamma`
        lands as `~/.mahoraga/skills/gamma/`.
        """
        src = str(src).strip()
        if src.startswith(("http://", "https://")):
            return self.install_url(src)
        p = pathlib.Path(src).expanduser()
        if not p.exists():
            raise FileNotFoundError(p)
        if p.is_file():
            name = self._skill_name(p.read_text(errors="replace")) or p.stem
            dest = self.user_dir / name
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest / "SKILL.md")
        else:
            dest = self.user_dir / p.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(p, dest)
            f = dest / "SKILL.md"
            declared = (self._skill_name(f.read_text(errors="replace"))
                        if f.is_file() else None)
            name = declared or p.name
            if declared and declared != p.name:
                new = self.user_dir / declared
                if new.exists():
                    shutil.rmtree(new)
                shutil.move(str(dest), str(new))
                dest = new
        return {"name": name, "path": str(dest)}

    def install_url(self, url: str) -> dict:
        req = Request(url, headers={"User-Agent": "mahoraga/1.0"})
        with urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
        name = (self._skill_name(text)
                or pathlib.Path(urlparse(url).path).stem)
        dest = self.user_dir / name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(text)
        return {"name": name, "path": str(dest)}

    def remove(self, name: str) -> bool:
        """Remove a USER skill. Bundled skills are not deletable."""
        dest = self.user_dir / name
        if dest.is_dir():
            shutil.rmtree(dest)
            return True
        return False
