# Mahoraga — the adaptive agent

A minimal, local, extensible AI agent in **pure Python**. Zero dependencies.

```
You type → the model thinks → it calls a tool → policy checks it → the result goes
back → it thinks again → until it has an answer.
```

That's the whole design. The core loop is ~50 lines; everything else is the parts that make that loop pleasant and safe to leave running on your machine.

---

## Why this one

- **No dependencies.** Standard library only. No pip, no venv, no framework drift. Python 3.10+ and a real terminal.
- **Local-first.** Any OpenAI-compatible endpoint — llama.cpp, Ollama, vLLM, LM Studio, or a cloud API if you insist.
- **The policy is readable.** Three tiers, one file, ~200 lines. You can audit the thing that decides whether `rm -rf` runs, instead of trusting a framework's abstraction.

---

## Features

### Interaction
- **Full-screen TUI** with three live regions: a turning dharma-wheel spinner, a scrollable transcript, a prompt, and a status bar.
- **Streaming output** — thinking and answer both stream token by token, and you can tell them apart (dim italic vs plain).
- **Tool cards** — every call renders as a block: the command, its output, and how long it took. Long output is truncated with a marker; `Ctrl+O` expands it.
- **Queue messages while it works** — the prompt never locks. Type while the model is replying and your message is queued (`↳ queued #1`), runs next, in order. The status bar shows `+N queued`.
- **Markdown that respects a terminal** — headings cleaned, tables rendered as real box-drawing tables instead of raw pipes.
- **Scrollback** — `PgUp`/`PgDn` through history. The view holds its place when new text arrives and
  survives a resize; reaching the bottom re-enables live follow.
- **Stop a reply** — `Ctrl+C` mid-stream ends generation and keeps whatever arrived. `/stop` does the
  same and clears the queue.
- **Reasoning on/off** — `/reasoning off` hides the thinking block and asks the backend to skip it;
  the setting persists in `config.json`.
- **The wheel yields to the transcript** — while a reply streams the banner collapses to two lines so
  the pane gets the rows, then returns when it's done.
- **Copy/paste friendly** — `/mouse off` restores native terminal selection; `/mouse on` gives the wheel-scroll behaviour back.
- **Persistent history + tab completion** — arrow through 500 previous prompts; `Tab` completes commands, provider names, model numbers and allowlist indices.
- **Resize-safe** — every region recomputes on `SIGWINCH` instead of smearing.

### The agent loop
- Multi-turn tool calling until the model produces a final answer.
- **Budget caps** on turns, tokens and wall-clock time, so a confused model can't loop forever.
- **Streaming SSE parsing** with tool-call reassembly (fragments stitched back into valid arguments).
- **Ctrl+C** clears the line without killing the session; an interrupted turn keeps whatever already streamed.

### Policy & safety
- **Three tiers:** `SAFE` (runs), `ASK` (prompt), `BLOCK` (refused, not whitelistable).
- **Per-command classification** for shell — `rm -r`, `sudo`, `chmod -R`, `killall`, `systemctl`, `shred`, writes to `/etc/` ask; `mkfs`, `dd if=`, `shutdown`, fork bombs are refused outright.
- **Allowlist file** (`~/.mahoraga/allow`, regex per line) consulted *before* the ASK tier, so blessed commands never nag again. Editable live.
- **Approval prompt** with `y` / `N` / `a` (always).
- **Append-only audit log** (`~/.mahoraga/audit.jsonl`) — every call, allowed or denied, with args and outcome.
- **`--yolo`** skips ASK prompts. It does **not** skip BLOCK: disk-destruction commands are still refused.

### Tools (20 built in)
- Shell: `shell`, `bash`, `run_command` (persistent session — `cd` sticks), `process_manage` (background jobs)
- Files: `read_file`, `write_file`, `edit_file`, `list_dir`, `search_files`
- Web: `http_get`, `web_search`, `web_extract`
- Memory: `remember`, `recall`, `session_search`
- Notes: `get_notes`, `set_notes`
- Utility: `todo_list`, `clarify`, `estimate_tokens`

### Providers & models
- Any `/v1/chat/completions` endpoint: llama.cpp server, Ollama, vLLM, LM Studio, OpenRouter, OpenAI, DeepSeek.
- **llama.cpp router discovery** — walks `/v1/models`, `/models`, `?reload=1`, and every payload shape, so a multi-model router shows its whole menu.
- **Numbered `/model` picker** with live switching, plus `/model refresh` to re-query.
- **`/provider`** to switch endpoints mid-session; keys come from env vars named in the config, never from argv.

### Memory
- Every turn persisted to SQLite (`~/.mahoraga/memory.db`), last 30 replayed on launch.
- **WAL mode + thread-local connections**, so background turns and the UI can read/write at once.
- `/new` clears context without deleting history; `/memory` shows recent turns; `/clear` wipes the store.

### Extensibility
- Register a tool from a plain function — the JSON schema is derived from the signature.
- Three ways in: inline, inside `plugins/__init__.py`, or your own module layered on top.

---

## Quick start

```bash
cp examples/config.example.json ~/.mahoraga/config.json
# point base_url at your model server, then:
python3 -m mahoraga
```

**Full setup, model wiring, config reference and troubleshooting → [INSTALL.md](INSTALL.md).**

---

## Commands

| Command | What it does |
|---|---|
| `/help` | everything below, in the app |
| `/model` | numbered list of models; `/model <n>` switches; `/model refresh` re-queries |
| `/provider` | list or switch providers |
| `/tools` | list tools; `/tools off\|on` disable them for a turn |
| `/allow` | list allowlist rules; `/allow <regex>` add; `/allow rm <n>` remove |
| `/stop` | stop the reply in flight and drop the queue |
| `/reasoning on\|off` | show/hide the model's thinking (persisted) |
| `/new` | fresh context, history kept |
| `/memory` | recent stored turns |
| `/clear` | wipe stored memory |
| `/cd`, `/pwd` | move the persistent shell's working directory |
| `/mouse on\|off` | wheel-scroll mode vs native selection |
| `/quit` | exit |

**Keys:** `Enter` send · `↑`/`↓` history · `Tab` complete · `PgUp`/`PgDn` scroll ·
`Ctrl+O` expand tool output · `Ctrl+C` stop the reply (or clear an idle line) · `Ctrl+D` quit.

---

## Policy in detail

| Tier | Examples | Behaviour |
|---|---|---|
| `SAFE` | `read_file`, `list_dir`, `search_files`, `http_get` | runs, audited |
| `ASK` | `rm -r`, `sudo`, `systemctl`, `killall`, `chmod -R`, writes to `/etc/` | prompts `Allow? [y/N/a]` |
| `BLOCK` | `mkfs`, `dd if=`, `> /dev/sdX`, `shutdown`, `reboot`, fork bombs | refused; cannot be whitelisted |

Whitelisting, three ways:

```bash
/allow pkill -f firefox      # in the TUI — regex, one per line
/allow rm 2                  # remove rule 2
# or answer "a" at a prompt, or edit ~/.mahoraga/allow directly (applies live)
```

---

## Adding a tool

```python
def my_tool(query: str, limit: int = 10) -> str:
    """Search something custom."""
    return f"found {limit} results for {query}"

from mahoraga.tools.registry import Registry
from mahoraga.tools.schema import auto_schema

reg = Registry()
desc, schema = auto_schema(my_tool)
reg.register("my_tool", desc, schema, my_tool)
```

Or drop it inside `register_all()` in `mahoraga/plugins/__init__.py`, or in your own module and call it after `register_all(reg)`.

---

## Architecture

```
mahoraga/
├── core/
│   ├── loop.py          ← the agent loop (50 lines)
│   ├── runtime.py       ← provider + tools + memory wired together
│   ├── context.py       ← history, token estimation, truncation
│   ├── budget.py        ← turn/token/time caps
│   └── wheel.py         ← the spinner
├── llm/
│   ├── provider.py      ← interface
│   ├── openai_compat.py ← any /v1 endpoint + router discovery
│   ├── ollama.py        ← thin adapter
│   └── llama.py         ← thin adapter
├── tools/
│   ├── registry.py      ← register / describe / execute
│   ├── schema.py        ← JSON schema from signatures
│   └── executor.py      ← classify → approve → run → audit → cap
├── plugins/__init__.py  ← 20 built-in tools
├── policy/
│   ├── permissions.py   ← SAFE / ASK / BLOCK + allowlist
│   ├── approval.py      ← the human-in-the-loop prompt
│   └── audit.py         ← append-only JSONL
├── memory/sqlite.py     ← WAL, thread-local connections
└── agent/
    ├── cli.py           ← the TUI (ANSI, stdlib only)
    ├── lineedit.py      ← raw-mode line editor
    └── __main__.py      ← entry point
```

---

## Config

`~/.mahoraga/config.json`:

```json
{
  "active": "local",
  "providers": {
    "local": { "base_url": "http://127.0.0.1:8080/v1", "model": "qwen3-4b", "api_key_env": "" },
    "cloud": { "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
               "api_key_env": "DEEPSEEK_API_KEY", "models": ["deepseek-chat", "deepseek-reasoner"] }
  },
  "temperature": 0.7,
  "max_tokens": 4096,
  "context_window": 8192
}
```

Files it keeps:

| Path | Contents |
|---|---|
| `~/.mahoraga/config.json` | providers, models, limits |
| `~/.mahoraga/memory.db` | conversation history (SQLite, WAL) |
| `~/.mahoraga/audit.jsonl` | every tool call and its outcome |
| `~/.mahoraga/allow` | regex whitelist |
| `~/.mahoraga/history` | prompt history for arrow-up |

---

## What it deliberately isn't

Honest limits, so you know what you're adopting:

- **No MCP client (yet).** Tools are Python functions registered in-process.
- **No image/vision input.** Text in, text out.
- **No multi-agent orchestration.** One loop, one session — the swarm experiments are separate projects.
- **No remote sandbox.** Tools run as you, on your machine. The policy layer is the guardrail, not a container.
- **Cancelling is best-effort at the socket.** `Ctrl+C` closes the stream and the worker stops within a
  chunk or two; a request the server has already buffered may still cost a few tokens.
- **POSIX-first.** Built and tested on Linux; Windows is untested.

---

## License

MIT — fork it, break it, make it yours.
