"""Where API keys live: `~/.mahoraga/.env`, mode 600, never config.json.

Two rules this module exists to keep:

* a secret is written once, atomically, owner-readable only — no window where
  a half-written key file exists;
* the key VALUE never lands in config.json or in any message. Only the env
  var NAME is configuration. `mask()` is the only way a value reaches a screen.
"""
from __future__ import annotations
import os, pathlib


def mask(secret: str) -> str:
    """`nvapi-abc…7f3d` — enough to recognise, not enough to use."""
    s = (secret or "").strip()
    if not s:
        return "(empty)"
    if len(s) <= 8:
        return "•" * len(s)
    return f"{s[:6]}…{s[-4:]}"


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def read_env_var(path, name: str) -> str:
    try:
        return parse_env(pathlib.Path(path).read_text()).get(name, "")
    except OSError:
        return ""


def write_env_var(path, name: str, value: str) -> pathlib.Path:
    """Set NAME=value in the env file, keeping every other line (comments too).

    Rewrites through a 0600 temp file + rename, so a crash mid-write can't
    leave a truncated .env. Duplicate lines for the same name collapse to one.
    """
    name = (name or "").strip()
    if not name or "=" in name:
        raise ValueError(f"invalid env var name: {name!r}")
    p = pathlib.Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = p.read_text().splitlines()
    except OSError:
        lines = []

    kept: list[str] = []
    written = False
    for line in lines:
        stripped = line.strip()
        key = None
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
        if key == name:
            if not written:
                kept.append(f"{name}={value}")
                written = True
            continue                      # drop the stale duplicates
        kept.append(line)
    if not written:
        kept.append(f"{name}={value}")

    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(kept) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return p
