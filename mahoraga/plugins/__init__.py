"""Built-in plugins — each registers itself into a Registry."""
from __future__ import annotations
import os, json, re, shlex, subprocess, urllib.request

from ..tools.shell_session import ShellSession

# ONE persistent shell session: cd / export / source venv all stick across
# tool calls (cd x && y also persists — it's a real shell now). SHELL_STATE
# mirrors the session cwd for the status bar and relative-path tools.
_SESSION = ShellSession()
SHELL_STATE = {"cwd": _SESSION.cwd}


def shell_cwd() -> str:
    return SHELL_STATE["cwd"]


def set_shell_cwd(path: str) -> str:
    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(p):
        raise NotADirectoryError(p)
    _SESSION.run(f"cd {shlex.quote(p)}")
    SHELL_STATE["cwd"] = _SESSION.cwd
    return SHELL_STATE["cwd"]


def _resolve(path: str) -> str:
    """Relative paths resolve against the SHELL's cwd, so tools see the same
    directory the shell does."""
    p = os.path.expanduser(path)
    return p if os.path.isabs(p) else os.path.join(SHELL_STATE["cwd"], p)

def register_all(reg, data_dir: str = "~/.mahoraga"):
    d = os.path.expanduser(data_dir)
    os.makedirs(d, exist_ok=True)
    notes_path = os.path.join(d, "notes.md")

    def read_file(path: str, max_lines: int = 400) -> str:
        p = _resolve(path)
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
        root = _resolve(root)
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
        p = _resolve(path)
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
            out, code, cwd = _SESSION.run(command, timeout)
            SHELL_STATE["cwd"] = cwd
            body = out.rstrip()
            tail = f"\n[exit {code}]" if code not in (0, None) else ""
            if not body:
                return f"(no output, exit={code}, cwd={cwd})"
            return body[:8000] + tail
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    shell_schema = {"type": "object", "properties": {
        "command": {"type": "string"},
        "timeout": {"type": "integer", "default": 60}},
        "required": ["command"]}
    reg.register("shell", "Run a bash command. Returns stdout+stderr + exit "
        "code. PERSISTENT SESSION: one long-lived shell — cd, exported vars "
        "and activated venvs STICK across calls (even `cd x && y` moves the "
        "directory for later calls). Rely on it. Non-interactive commands "
        "only; use process_manage for long/interactive work.", shell_schema, shell)
    # aliases: small models invent tool names — meet them halfway
    reg.register("bash", "Run a bash command (persistent shell session).", shell_schema, shell)
    reg.register("run_command", "Run a shell command (persistent shell session).", shell_schema, shell)

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
        p = _resolve(path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
        return f"wrote {len(content)} chars to {p}"

    def edit_file(path: str, find: str, replace: str) -> str:
        p = _resolve(path)
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

    # ---- session history search (the messages table already exists) ----
    def session_search(query: str, limit: int = 10) -> str:
        from ..memory import sqlite as mem
        hits = mem.search_messages(query, limit)
        if not hits:
            return f"no past messages mention {query!r}"
        return "\n".join(f"[{r}] {c[:160]}" for r, c in hits)

    reg.register("session_search", "Search past conversation history by keyword. "
        "Use when the user asks what was said/decided/done earlier.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 10}},
         "required": ["query"]}, session_search)

    # ---- todo list (multi-step work tracking) ----
    def todo_list(action: str, text: str = "", index: int = 0) -> str:
        import json
        from ..memory import sqlite as mem
        todos = json.loads(mem.kv_get("todos", "[]"))
        if action == "add":
            todos.append({"text": text, "done": False})
        elif action == "done" and 0 <= index - 1 < len(todos):
            todos[index - 1]["done"] = True
        elif action == "clear":
            todos = [t for t in todos if not t["done"]]
        mem.kv_set("todos", json.dumps(todos, ensure_ascii=False))
        if not todos:
            return "(todo list empty)"
        return "\n".join(
            f"{i}. [{'x' if t['done'] else ' '}] {t['text']}"
            for i, t in enumerate(todos, 1))

    reg.register("todo_list", "Track multi-step work for this session. "
        "action='add' adds text, 'done' completes item <index> (1-based), "
        "'clear' drops completed items. Listing happens automatically — "
        "the return value is always the current list.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["add", "done", "clear"]},
            "text": {"type": "string", "description": "task text for action=add"},
            "index": {"type": "integer", "description": "1-based item for action=done"}},
         "required": ["action"]}, todo_list)

    # ---- clarify: ask the human instead of guessing ----
    def clarify(question: str, choices: list | None = None) -> str:
        from ..policy import approval
        if approval.before_prompt:
            approval.before_prompt()
        try:
            print(f"\n  ❓ {question}")
            for i, c in enumerate(choices or [], 1):
                print(f"     {i}. {c}")
            return input("  > ").strip()
        finally:
            if approval.after_prompt:
                approval.after_prompt()

    reg.register("clarify", "Ask the user a question and wait for the answer. "
        "Use ONLY when the answer changes what you would do next and cannot be "
        "found or safely defaulted. Optional choices for quick picks.",
        {"type": "object", "properties": {
            "question": {"type": "string"},
            "choices": {"type": "array", "items": {"type": "string"},
                        "description": "optional quick-pick options"}},
         "required": ["question"]}, clarify)

    # ---- background processes ----
    _PROCS: dict[int, dict] = {}

    def process_manage(action: str, command: str = "", pid: int = 0) -> str:
        import subprocess, tempfile, time
        if action == "run":
            log = tempfile.NamedTemporaryFile(
                prefix="mproc_", suffix=".log", delete=False)
            p = subprocess.Popen(command, shell=True, stdout=log,
                                 stderr=subprocess.STDOUT)
            _PROCS[p.pid] = {"cmd": command, "popen": p, "log": log.name,
                             "started": time.time()}
            return f"started pid {p.pid}: {command[:100]}"
        if action == "list":
            if not _PROCS:
                return "(no background processes)"
            out = []
            for pid_, info in _PROCS.items():
                state = "running" if info["popen"].poll() is None \
                    else f"exit {info['popen'].returncode}"
                out.append(f"pid {pid_} [{state}] {info['cmd'][:80]}")
            return "\n".join(out)
        if action == "poll":
            info = _PROCS.get(pid)
            if not info:
                return f"[error] no such pid {pid} (see action=list)"
            code = info["popen"].poll()
            try:
                with open(info["log"], errors="replace") as f:
                    tail = f.read()[-2000:]
            except OSError:
                tail = ""
            state = "running" if code is None else f"exited with code {code}"
            return f"pid {pid} [{state}]\n--- output tail ---\n{tail}"
        if action == "kill":
            info = _PROCS.get(pid)
            if not info:
                return f"[error] no such pid {pid}"
            info["popen"].kill()
            return f"pid {pid} killed"
        return "[error] action must be run|list|poll|kill"

    reg.register("process_manage", "Run and manage BACKGROUND shell processes "
        "(long builds, scans, downloads). action='run' starts one and returns "
        "its pid immediately; 'list' shows all; 'poll' returns status + output "
        "tail; 'kill' stops one. For quick commands use shell instead.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["run", "list", "poll", "kill"]},
            "command": {"type": "string", "description": "for action=run"},
            "pid": {"type": "integer", "description": "for poll/kill"}},
         "required": ["action"]}, process_manage)

    # ---- web search (DuckDuckGo, no API key) ----
    def web_search(query: str, limit: int = 5) -> str:
        import urllib.parse
        UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) mahoraga/1.0"}

        def fetch(req):
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read().decode("utf-8", errors="replace")

        # primary: POST to the html endpoint (GET gets the bot-detection page)
        data = urllib.parse.urlencode({"q": query, "b": ""}).encode()
        page = fetch(urllib.request.Request(
            "https://html.duckduckgo.com/html/", data=data, headers=UA))
        results = []
        for m in re.finditer(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page):
            href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
            results.append((title.strip(), href))
        snippets = [re.sub(r"<[^>]+>", "", s).strip() for s in re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)]

        # fallback: the lite endpoint (different markup)
        if not results:
            page = fetch(urllib.request.Request(
                "https://lite.duckduckgo.com/lite/?"
                + urllib.parse.urlencode({"q": query}), headers=UA))
            for m in re.finditer(
                    r'class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page):
                href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
                results.append((title.strip(), href))
            snippets = [re.sub(r"<[^>]+>", "", s).strip() for s in re.findall(
                r'class="result-snippet"[^>]*>(.*?)</td>', page, re.S)]

        for i, (title, href) in enumerate(results):   # unwrap redirect links
            if "uddg=" in href:
                results[i] = (title, urllib.parse.unquote(
                    href.split("uddg=")[1].split("&")[0]))
        if not results:
            return "no results"
        out = []
        for i, (title, href) in enumerate(results[:limit]):
            snip = snippets[i] if i < len(snippets) else ""
            out.append(f"{i+1}. {title}\n   {href}\n   {snip[:160]}")
        return "\n".join(out)

    reg.register("web_search", "Search the web. Returns top results as "
        "numbered title / url / snippet lines. Use before web_extract.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 5}},
         "required": ["query"]}, web_search)

    # ---- web extract (fetch a page as readable text) ----
    def web_extract(url: str, max_chars: int = 8000) -> str:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) mahoraga/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", errors="replace")
        text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars] + (f"\n... (truncated at {max_chars})" if len(text) > max_chars else "")

    reg.register("web_extract", "Fetch a web page and return its readable "
        "text (HTML stripped). Use after web_search to read a result.",
        {"type": "object", "properties": {
            "url": {"type": "string"},
            "max_chars": {"type": "integer", "default": 8000}},
         "required": ["url"]}, web_extract)
