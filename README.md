# Mahoraga — the adaptive agent

A minimal, local, extensible AI agent in pure Python. Zero external dependencies.

## What it is

Mahoraga is a lightweight agent loop: **you send a message → the LLM thinks →
if it wants a tool, it calls one → you approve → results go back → it thinks again →
until it has a real answer.** That's the whole thing. Everything else is decoration.
 
Mahoraga has ~30. The core loop is 50 lines.

## Quick start

```bash
# 1. copy the example config
cp examples/config.example.json ~/.mahoraga/config.json
# edit it: point base_url at your local LLM (Ollama, llama.cpp, vLLM, OpenRouter…)

# 2. run
python3 -m mahoraga

# 3. yolo mode (skip approval prompts)
python3 -m mahoraga --yolo
```

**New here? → [INSTALL.md](INSTALL.md)** — the full setup guide (requirements,
model wiring, config reference, first-run walkthrough, whitelisting,
troubleshooting). No `pip install`: Python 3.10+ and a real terminal is all it
needs.

## Architecture

```
mahoraga/
├── core/
│   ├── loop.py        ← the agent loop (50 lines, the beating heart)
│   ├── runtime.py     ← wires provider + tools + memory into a working agent
│   ├── context.py     ← message history, token estimation, truncation
│   ├── budget.py      ← turn/token/time budget — stops runaway loops
│   └── wheel.py       ← Mahoraga dharma wheel animation (the spinner)
├── llm/
│   ├── provider.py    ← abstract provider interface
│   ├── openai_compat.py ← works with any /v1/chat/completions endpoint
│   ├── ollama.py      ← Ollama adapter (thin)
│   └── llama.py       ← llama.cpp server adapter (thin)
├── tools/
│   ├── registry.py    ← register, describe, execute tools
│   ├── schema.py      ← auto-derive JSON schema from Python signatures
│   └── executor.py    ← the safe path: approve → run → audit → cap
├── plugins/
│   └── __init__.py    ← 8 built-in tools (read, search, shell, http, notes…)
├── policy/
│   ├── permissions.py ← SAFE / ASK / BLOCK + user whitelist (~/.mahoraga/allow)
│   ├── approval.py    ← human-in-the-loop prompt
│   └── audit.py       ← append-only JSONL audit log
├── memory/
│   └── sqlite.py      ← every turn persisted, restored on next launch
└── agent/
    ├── cli.py         ← the TUI (ANSI, stdlib-only, wheel as spinner)
    └── __main__.py    ← entry point
```

## Policy & whitelisting commands

Philosophy: **run everything, question only danger.** Ordinary shell commands and
all file operations run without friction; only genuinely dangerous commands ask.

- `ASK` tier (prompts `Allow? [y/N/a]`): `rm -r`, `sudo`, `chmod -R`, `kill -9`,
  `killall`, `systemctl`, `shred`, writes to `/etc/`, …
- `BLOCK` tier (refused outright, **cannot be whitelisted** — disk/system
  destruction only): `mkfs`, `dd if=`, `> /dev/sdX`, `shutdown`/`reboot`,
  `kill -9 -1`, fork bombs, `rm -rf /`

Three ways to whitelist a command so it never asks again:

```bash
# 1. in the TUI — bless a pattern (regex, one per line)
/allow pkill -f firefox
/allow killall firefox
/allow                  # list rules (with numbers)
/allow rm 2             # remove rule 2

# 2. at a danger prompt, answer "a" — whitelists that exact command

# 3. edit the file directly (applied live, no restart)
$EDITOR ~/.mahoraga/allow
```

## How to add a tool

Three ways, from simplest to most control:

### 1. One function, done

```python
# in a plugin file or your own script
def my_tool(query: str, limit: int = 10) -> str:
    """Search something custom."""
    return f"found {limit} results for {query}"

from mahoraga.tools.registry import Registry
from mahoraga.tools.schema import auto_schema

reg = Registry()
desc, schema = auto_schema(my_tool)
reg.register("my_tool", desc, schema, my_tool)
```

### 2. In a plugin file

Add a function inside `register_all()` in `mahoraga/plugins/__init__.py`:

```python
def my_tool(query: str) -> str:
    return f"custom: {query}"

reg.register("my_tool", "Does something custom.",
    {"type": "object", "properties": {"query": {"type": "string"}},
     "required": ["query"]}, my_tool)
```

### 3. External plugin (your own file)

```python
from mahoraga.tools.registry import Registry
from mahoraga.plugins import register_all

def my_tool(query: str) -> str:
    return f"custom: {query}"

def register(reg: Registry):
    reg.register("my_tool", "Custom search.",
        {"type": "object", "properties": {"query": {"type": "string"}},
         "required": ["query"]}, my_tool)

# in your own script
reg = Registry()
register_all(reg)
register(reg)  # add your own on top
```

## How to swap LLM providers

Edit `~/.mahoraga/config.json`:

```json
{
  "base_url": "http://localhost:11434/v1",   // Ollama
  "model": "llama3.2"
}
```

Works with any OpenAI-compatible `/v1/chat/completions` endpoint:
- Ollama (`http://localhost:11434/v1`)
- llama.cpp server (`http://localhost:8080/v1`)
- vLLM, OpenRouter, LM Studio, etc.
- OpenAI itself (set `api_key_env` and export the key)

## Memory

Every turn is stored in `~/.mahoraga/memory.db` (SQLite). On next launch,
Mahoraga replays the last 30 messages into its context. `new_session()` clears
the in-memory context but never deletes the database.

## Policy tiers

| Tier | Tools | Behavior |
|------|-------|----------|
| `safe` | read_file, list_dir, search_files, http_get… | auto-approved |
| `write` | shell, set_notes, write_file… | asks before running |
| `unsafe` | unknown tools, rm -rf… | always asks, even in yolo |

Every tool call is appended to `~/.mahoraga/audit.jsonl`.

## License

MIT — fork it, break it, make it yours.
