"""Built-in plugins — each registers itself into a Registry."""
from __future__ import annotations
import os, json, subprocess, urllib.request

def register_all(reg, data_dir: str = "~/.mahoraga"):
    d = os.path.expanduser(data_dir)
    os.makedirs(d, exist_ok=True)
    notes_path = os.path.join(d, "notes.md")

    def read_file(path: str, max_lines: int = 400) -> str:
        p = os.path.expanduser(path)
        if not os.path.isfile(p):
            return json.dumps({"error": f"not found: {p}"})
        with open(p, "r", errors="replace") as f:
            lines = f.readlines()
        body = "".join(lines[:max_lines])
        if len(lines) > max_lines:
            body += f"\n... ({len(lines)-max_lines} more lines)"
        return body

    reg.register("read_file", "Read a local file. Returns up to max_lines lines.",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "absolute or ~ path"},
            "max_lines": {"type": "integer", "default": 400}},
         "required": ["path"]}, read_file)

    def search_files(query: str, root: str = ".") -> str:
        root = os.path.expanduser(root)
        hits = []
        for dirpath, _, files in os.walk(root):
            if any(x in dirpath for x in (".git", "node_modules", "__pycache__")):
                continue
            for fn in files:
                fp = os.path.join(dirpath, fn)
                try:
                    with open(fp, "r", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            if query.lower() in line.lower():
                                hits.append(f"{fp}:{i}: {line.strip()[:120]}")
                                if len(hits) >= 40:
                                    return "\n".join(hits)
                except Exception:
                    pass
        return "\n".join(hits) if hits else "no matches"

    reg.register("search_files", "Grep-like file search. Returns matching lines with file:line prefix.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "root": {"type": "string", "default": "."}},
         "required": ["query"]}, search_files)

    def list_dir(path: str = ".") -> str:
        p = os.path.expanduser(path)
        try:
            entries = sorted(os.listdir(p))
            return "\n".join(entries) if entries else "(empty)"
        except PermissionError:
            return json.dumps({"error": "permission denied"})
        except FileNotFoundError:
            return json.dumps({"error": f"not found: {p}"})

    reg.register("list_dir", "List directory contents.",
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}},
        list_dir)

    def shell(command: str, timeout: int = 60) -> str:
        try:
            r = subprocess.run(command, shell=True, capture_output=True,
                               text=True, timeout=timeout)
            out = (r.stdout + r.stderr).rstrip()
            tail = f"\n[exit {r.returncode}]" if r.returncode else ""
            return (out[:8000] + tail) if out else f"(no output, exit={r.returncode})"
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"timeout after {timeout}s"})
        except PermissionError as e:
            return json.dumps({"error": f"permission denied: {e}"})
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    shell_schema = {"type": "object", "properties": {
        "command": {"type": "string"},
        "timeout": {"type": "integer", "default": 60}},
        "required": ["command"]}
    reg.register("shell", "Run a bash command. Returns stdout+stderr + exit code.", shell_schema, shell)
    # aliases: small models invent tool names — meet them halfway
    reg.register("bash", "Run a bash command.", shell_schema, shell)
    reg.register("run_command", "Run a shell command.", shell_schema, shell)

    def http_get(url: str, max_bytes: int = 16000) -> str:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mahoraga/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read(max_bytes).decode("utf-8", errors="replace")
        except Exception as e:
            return json.dumps({"error": str(e)})

    reg.register("http_get", "Fetch a URL via GET. Returns response body (max 16KB).",
        {"type": "object", "properties": {
            "url": {"type": "string"}},
         "required": ["url"]}, http_get)

    def write_file(path: str, content: str) -> str:
        p = os.path.expanduser(path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
        return f"wrote {len(content)} chars to {p}"

    def edit_file(path: str, find: str, replace: str) -> str:
        p = os.path.expanduser(path)
        try:
            with open(p) as f:
                text = f.read()
        except FileNotFoundError:
            return json.dumps({"error": f"not found: {p}"})
        n = text.count(find)
        if n == 0:
            return json.dumps({"error": "find string not present"})
        with open(p, "w") as f:
            f.write(text.replace(find, replace))
        return f"replaced {n} occurrence(s) in {p}"

    reg.register("write_file", "Create or overwrite a file with content.",
        {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}},
         "required": ["path", "content"]}, write_file)

    reg.register("edit_file", "Find/replace inside an existing file (all occurrences).",
        {"type": "object", "properties": {
            "path": {"type": "string"},
            "find": {"type": "string"},
            "replace": {"type": "string"}},
         "required": ["path", "find", "replace"]}, edit_file)

    def get_notes() -> str:
        try:
            with open(notes_path) as f:
                return f.read()
        except FileNotFoundError:
            return "(no notes yet)"

    def set_notes(content: str) -> str:
        with open(notes_path, "w") as f:
            f.write(content)
        return f"saved {len(content)} chars to notes"

    reg.register("get_notes", "Read persistent notes file.",
        {"type": "object", "properties": {}}, get_notes)
    reg.register("set_notes", "Overwrite the persistent notes file.",
        {"type": "object", "properties": {"content": {"type": "string"}},
         "required": ["content"]}, set_notes)

    def estimate_tokens(text: str) -> str:
        return f"~{len(text)//3} tokens ({len(text)} chars)"

    reg.register("estimate_tokens", "Rough token estimate for a string.",
        {"type": "object", "properties": {"text": {"type": "string"}},
         "required": ["text"]}, estimate_tokens)

    # ---- long-term memory over the kv table (survives restarts) ----
    def remember(fact: str) -> str:
        import json
        from ..memory import sqlite as mem
        facts = json.loads(mem.kv_get("facts", "[]"))
        facts.append(fact)
        mem.kv_set("facts", json.dumps(facts, ensure_ascii=False))
        return f"remembered. {len(facts)} fact(s) stored."

    reg.register("remember", "Store an important fact about the user or project "
        "for future sessions. Use when the user says to remember something.",
        {"type": "object", "properties": {
            "fact": {"type": "string", "description": "one self-contained sentence"}},
         "required": ["fact"]}, remember)

    def recall(query: str = "") -> str:
        import json
        from ..memory import sqlite as mem
        facts = json.loads(mem.kv_get("facts", "[]"))
        hits = [f for f in facts if query.lower() in f.lower()] if query else facts
        return "\n".join(f"- {f}" for f in hits[-20:]) or "no memories yet"

    reg.register("recall", "Search stored memories. Call when the user asks "
        "what you remember, or when a question depends on past context.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "keyword filter; empty returns everything"}},
         "required": []}, recall)
