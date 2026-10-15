# Installing Mahoraga — full setup guide

A minimal, local, extensible AI agent in pure Python. **Zero external
dependencies** — no pip install, no venv, no framework. Clone it and run it.

> This guide was executed end-to-end on a fresh clone before shipping:
> all five test gauntlets pass, the TUI boots, and answers stream correctly
> against an OpenAI-compatible endpoint (verified with a mock server in a
> clean `$HOME`). Every command below is real and was run.

---

## 0. Requirements

| thing | why |
|---|---|
| **Python 3.10+** | the code uses `X \| None` annotations and modern generics (verified on 3.14.7) |
| **Linux or macOS** | the TUI uses `termios`/`tty`; on Windows use **WSL** |
| **A real terminal** | Konsole, GNOME Terminal, kitty, Alacritty, iTerm2, Windows Terminal (WSL). Needs truecolor. A piped/redirected shell is not enough for the interactive UI. |
| *(optional)* a model | a local llama.cpp/Ollama server, **or** a cloud API key (DeepSeek/OpenRouter) |

No `pip install`. No build step. If `python3 --version` says 3.10+, you're set.

---

## 1. Clone

```bash
git clone https://github.com/lukaslimals7/mahoraga.git
cd mahoraga
```

(SSH alternative: `git clone git@github.com:lukaslimals7/mahoraga.git`)

---

## 2. Verify the checkout (10 seconds, no model needed)

Run every gauntlet. Expected results, one per line:

```bash
for t in test_allow test_loop test_render test_input test_shell_session; do
  printf '%-22s ' "$t"; python3 tests/$t.py | tail -1
done
```

```
test_allow             all whitelist checks passed
test_loop              === ALL CHECKS PASSED ===
test_render            RENDER GAUNTLET: ALL OK
test_input             INPUT GAUNTLET: ALL OK
test_shell_session     SHELL SESSION: ALL OK
```

If all five pass, the install is sound — the rest is just pointing it at a
model. (These tests cover the renderer, the line editor, the agent loop, the
permission tiers and the persistent shell session; they need no network.)

---

## 3. Give it a brain

Two options. You can configure both and switch at runtime with `/provider`.

### Option A — local model (free, private, offline)

**llama.cpp** (single model):
```bash
llama-server -m /path/to/model.gguf --port 8080 -c 8192
```

**llama.cpp router** (multiple models from a directory):
```bash
llama-server --models /path/to/models --port 8080
```

**Ollama**:
```bash
ollama serve
ollama pull llama3.2
# base_url: http://127.0.0.1:11434/v1
```

### Serving a menu of models — llama.cpp router mode

Router mode puts every preset behind one endpoint. Mahoraga asks the server what
it has (`GET /v1/models`, then the native `/models`, retrying with `?reload=1`
to force a preset re-scan) and lists all of them:

```bash
llama-server --models /path/to/models --port 8080     # or --models-preset presets.ini
# drop a new .gguf into that directory and /model refresh picks it up — no restart
```

In the TUI, `/model` prints a numbered menu of everything the server advertises
and `/model <n>` switches to it — the next request carries that model id and
llama.cpp loads it on demand:

```
●  1  Qwen/Qwen3-1.7B-GGUF:Q8_0
   2  ggml-org/SmolLM3-3B-GGUF:Q4_K_M
   3  openbmb/MiniCPM5-2B-GGUF:Q4_K_M
   ...
pick: /model <n>  ·  /model <name>  ·  /model refresh re-queries
```

- Only one model in the menu? The server probably didn't answer —
  `curl http://127.0.0.1:8080/v1/models` should list them all. Make sure
  `base_url` in the config ends with `/v1`.
- Serving **another machine**: start llama-server with `--host 0.0.0.0` and point
  `base_url` at that machine's LAN address (e.g. `http://192.168.1.20:8080/v1`).
  Bound to the default `127.0.0.1`, it is only reachable from that same machine.
- A provider can also carry a pinned fallback list (`"models": [...]` in the
  config). For local servers the **live server list wins**; the pinned list is
  used only when the server can't be reached.

### Option B — cloud API (DeepSeek, OpenRouter, anything OpenAI-compatible)

Put keys in `~/.mahoraga/.env` — they are read from the environment on every
request and are **never** stored in the config file or the repo (`.env` is
gitignored):

```bash
mkdir -p ~/.mahoraga && chmod 700 ~/.mahoraga
cat > ~/.mahoraga/.env <<'EOF'
DEEPSEEK_API_KEY=sk-your-key-here
OPENROUTER_API_KEY=sk-or-your-key-here
EOF
chmod 600 ~/.mahoraga/.env
```

---

## 4. Configure

Config lives at **`~/.mahoraga/config.json`**. On the very first run the app
creates a default one and prints
`[mahoraga] created config at … — edit it, then relaunch.` Edit it and relaunch.

A complete multi-provider example (local + DeepSeek + OpenRouter):

```json
{
  "temperature": 0.7,
  "max_tokens": 4096,
  "context_window": 8192,
  "system": "You are Mahoraga, a minimal local agent. You have tools. Use them when they help; answer directly when they don't. Be concise and honest about what you did.",
  "providers": {
    "local": {
      "base_url": "http://127.0.0.1:8080/v1",
      "model": "your-model-id",
      "api_key_env": ""
    },
    "deepseek": {
      "base_url": "https://api.deepseek.com",
      "model": "deepseek-flash",
      "api_key_env": "DEEPSEEK_API_KEY",
      "models": ["deepseek-v4-pro", "deepseek-flash"]
    },
    "openrouter": {
      "base_url": "https://openrouter.ai/api/v1",
      "model": "xiaomi/mimo-v2.6-flash",
      "api_key_env": "OPENROUTER_API_KEY",
      "models": ["xiaomi/mimo-v2.6-flash", "xiaomi/mimo-v2.6-pro"]
    }
  },
  "active": "local",
  "mouse": false
}
```

Key points:

- `api_key_env: ""` means "no key needed" → for local servers. The app then
  asks the server for its model list.
- `models` is an optional **curated** list. Without it, `/model` shows whatever
  the server advertises — for OpenRouter that's the whole 400+ model catalog,
  which is noise. Curate it.
- `base_url` for OpenAI-compatible servers should include `/v1` (the client
  appends `/chat/completions`). DeepSeek is the exception: `https://api.deepseek.com`.
- `mouse: false` is deliberate — it keeps native terminal select/copy/paste
  working. `/mouse on` swaps to wheel-scrolling of the pane.
- `system` holds the TOOL DISCIPLINE paragraph that stops small models from
  calling tools on a plain "hey". Keep it.

---

## 5. Run

```bash
cd mahoraga                 # run from the repo root (the package lives here)
python3 -m mahoraga         # normal: confirms only genuinely dangerous commands
python3 -m mahoraga --yolo  # no confirmations (the BLOCK list still refuses)
```

There is no `--help`; the TUI is the interface. `python3 -m mahoraga` starts it.

Handy alias:

```bash
echo "alias mahoraga='cd ~/mahoraga && python3 -m mahoraga'" >> ~/.bashrc
```

### What you should see

- **Top:** the animated dharma wheel, spinning continuously.
- **Middle:** a persistent pane where thinking streams (dim italic), tool calls
  appear as green cards (`$ cmd (timeout Ns)` → output → `Took 1.2s`), and
  answers stream as plain text. User messages are slate-blue bands.
- **Bottom:** the prompt row and a status bar: `cwd (git-branch) | provider ·
  model · state`.

### First five things to type

```
hey                     → a normal reply, NO tool calls (this is the health check)
list the files here     → a green tool card with real output
/help                   → all commands
/provider               → list providers, /provider deepseek to switch
/model                  → numbered menu of models, /model <n> to pick one
```

### Essential keys

| key | does |
|---|---|
| `Ctrl+C` at the prompt | clears the line (never quits) |
| `Ctrl+C` mid-reply | stops the reply, keeps the partial answer, session survives |
| `Ctrl+D` or `/quit` | exit |
| `↑` / `↓` | prompt history (persisted to `~/.mahoraga/history`) |
| `Tab` | complete commands / providers / models |
| `Ctrl+O` | expand/collapse long tool output |
| `PgUp` / `PgDn` | scroll the pane (typing jumps back to live) |
| `a` at a danger prompt | whitelist that exact command forever |

---

## 6. Files it creates

Everything lives in `~/.mahoraga/`:

| file | what |
|---|---|
| `config.json` | settings + providers + `active` + `mouse` |
| `.env` | *(yours, optional)* API keys, `chmod 600`, gitignored |
| `allow` | command whitelist — one regex per line (created on first `/allow`) |
| `memory.db` | SQLite: every turn persisted, restored on next launch |
| `history` | prompt history (last 500) |
| `audit.log` | append-only JSONL of tool executions (created once a tool runs) |

Nothing outside `~/.mahoraga/` and the repo directory is touched.

---

## 7. Whitelisting your own commands

Philosophy: **run everything, question only danger.**

- `ASK` tier → prompts `Allow? [y/N/a]` (e.g. `rm -r`, `sudo`, `killall`,
  `systemctl`, `chmod -R`).
- `BLOCK` tier → refused outright and **cannot be whitelisted**: `mkfs`,
  `dd if=`, `> /dev/sdX`, `shutdown`/`reboot`, `kill -9 -1`, fork bombs,
  `rm -rf /`. Disk/system destruction only.

Three ways to whitelist, all persistent:

```
/allow pkill -f firefox     # in the TUI (regex)
/allow                      # list rules, numbered
/allow rm 2                 # remove rule 2
```

- Press **`a`** at a danger prompt → whitelists that exact command forever.
- Or edit `~/.mahoraga/allow` directly; changes apply live (no restart).

---

## 8. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `[mahoraga] created config at … relaunch` | first run | edit the config, relaunch |
| `can't reach http://127.0.0.1:8080 — is the server running?` | model server down, or `base_url` missing `/v1` | start llama.cpp/Ollama; check the URL ends in `/v1` (DeepSeek excepted) |
| `HTTP 400` from a cloud provider on every tool call | tool schema the provider rejects | already handled for DeepSeek (`additionalProperties` is stripped) — if another provider 400s, read the error text: the client surfaces the server's own message |
| `⚠ DEEPSEEK_API_KEY not set in env` | missing `.env` | create `~/.mahoraga/.env` (step 3) or export the var |
| text garbled, no colors, layout odd | terminal lacks truecolor, or output is piped | use a real terminal emulator; don't pipe the TUI |
| can't select/copy text | mouse is off by default (by design) | leave it off and select natively, or `/mouse on` to scroll with the wheel (then use Shift+drag to select) |
| `^[[A` appears when pressing arrows | not a TTY (or an ancient copy) | run it in a real terminal; pull latest |
| tools fire on a plain "hey" | the TOOL DISCIPLINE paragraph was removed from `system` | restore it (step 4) |
| `… (N earlier lines, ctrl+o to expand)` | long tool output is collapsed by design | press `Ctrl+O` |
| only the model from my config shows up locally | the server didn't answer `/v1/models` (down, wrong port, `base_url` missing `/v1`, or it's single-model mode) | `curl http://127.0.0.1:8080/v1/models`; fix `base_url`; `/model refresh`; or pin `"models": [...]` on the provider |
| syntax errors on launch | Python < 3.10 | upgrade Python |

---

## 9. Where to go next

- **Add a tool:** `registry.register(name, description, schema, fn)` — see
  README "How to add a tool". Schemas can be auto-derived from signatures.
- **Add a provider:** any OpenAI-compatible endpoint is a config entry
  (`base_url` + `model` + `api_key_env`). No code needed.
- **Change policy:** regex tiers in `mahoraga/policy/permissions.py`
  (BLOCK/ASK); the whitelist is the user-facing lever.
- **The wheel:** `mahoraga/core/wheel.py` — pure-ASCII halftone, dependency-free.
- **Tests:** run all five gauntlets before committing; they're the regression net.

## 10. Does it need the internet?

No. Local models run fully offline. Only two tools reach out — `web_search`
and `web_extract` (DuckDuckGo) — and cloud providers obviously need their API.
Everything else (files, shell, memory, audit) is local. No telemetry, no
accounts, no gateway.
