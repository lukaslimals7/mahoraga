"""Persistent memory files — Mahoraga's always-on layer.

Two plain Markdown files under ~/.mahoraga/:

    memory.md   cross-session facts the agent should always know
    user.md     the user's identity, preferences, style

Both are injected into the system prompt on every turn, so they survive
restarts without the model having to call a tool. They are plain text:
editable by hand, diffable, and movable between machines with export/import
(one JSON bundle, or a directory of the two files).

This is deliberately NOT the SQLite history (that is the conversation log).
Memory is the narrow, high-signal layer that is present in every session.
"""
from __future__ import annotations
import json, pathlib

DATA_DIR = pathlib.Path.home() / ".mahoraga"
FILES = ("memory.md", "user.md")
DEFAULT_BUDGET = 2000          # chars per file that reach the model


class MemoryStore:
    def __init__(self, data_dir=None):
        self.dir = pathlib.Path(data_dir).expanduser() if data_dir else DATA_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---- raw file access ----

    def path(self, name: str) -> pathlib.Path:
        if name not in FILES:
            raise ValueError(f"unknown memory file: {name} (have: {', '.join(FILES)})")
        return self.dir / name

    def read(self, name: str = "memory.md") -> str:
        try:
            return self.path(name).read_text()
        except OSError:
            return ""

    def write(self, name: str, text: str) -> None:
        self.path(name).write_text(text)

    def append(self, name: str, line: str) -> None:
        """Add one line, keeping the file a clean list of facts."""
        line = (line or "").strip()
        if not line:
            raise ValueError("empty memory line")
        body = self.read(name).rstrip("\n")
        self.write(name, (body + "\n" + line if body else line) + "\n")

    # ---- prompt injection ----

    def inject(self, budget: int = DEFAULT_BUDGET) -> str:
        """The block appended to the system prompt. Capped per file so a
        long memory can never crowd out the conversation."""
        out = []
        for name, title in (("memory.md", "PERSISTENT MEMORY"),
                            ("user.md", "USER PROFILE")):
            body = self.read(name).strip()
            if not body:
                continue
            if len(body) > budget:
                body = (body[:budget]
                        + f"\n… [truncated — {len(body)} chars total, "
                          f"edit {self.dir / name}]")
            out.append(f"## {title}\n{body}")
        return "\n\n".join(out)

    # ---- import / export ----

    def export(self, dest) -> str:
        """Write both files to `dest` — a JSON bundle, or a directory.

        A path with no suffix (or an existing directory) is treated as a
        directory; anything else is written as one JSON bundle.
        """
        dest = pathlib.Path(dest).expanduser()
        if dest.is_dir() or not dest.suffix:
            dest.mkdir(parents=True, exist_ok=True)
            for name in FILES:
                (dest / name).write_text(self.read(name))
            return str(dest)
        bundle = {"version": 1, "memory": self.read("memory.md"),
                  "user": self.read("user.md")}
        dest.write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
        return str(dest)

    def import_(self, src, overwrite: bool = True) -> dict:
        """Read a bundle, a directory of the two files, or a single .md."""
        src = pathlib.Path(src).expanduser()
        if not src.exists():
            raise FileNotFoundError(src)
        written = []
        if src.is_dir():
            for name in FILES:
                f = src / name
                if f.is_file():
                    self.write(name, f.read_text())
                    written.append(name)
            return {"written": written, "source": str(src)}
        if src.suffix == ".md":
            self.write("memory.md", src.read_text())
            return {"written": ["memory.md"], "source": str(src)}
        data = json.loads(src.read_text())
        if not isinstance(data, dict):
            raise ValueError("memory bundle must be a JSON object")
        for key, name in (("memory", "memory.md"), ("user", "user.md")):
            val = data.get(key)
            if val is None:
                continue
            if overwrite or not self.read(name).strip():
                self.write(name, val)
                written.append(name)
        return {"written": written, "source": str(src)}
