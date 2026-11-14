"""Runtime: wire provider + tools + memory + context into a working agent.

Config holds a small provider registry (local llama.cpp, DeepSeek, MiMo, …)
and an `active` one — switchable at runtime, Hermes-style. API keys are read
from the environment on every request; never stored in config.
"""
from __future__ import annotations
import json, os, pathlib

from .context import Context
from .budget import Budget
from .loop import run_turn
from ..tools.registry import Registry
from ..llm.openai_compat import OpenAICompat
from ..plugins import register_all
from ..memory import sqlite as mem
from ..memory.store import MemoryStore
from ..skills.manager import SkillsManager

CONFIG_PATH = pathlib.Path.home() / ".mahoraga" / "config.json"


def _load_env_files(env_dir: pathlib.Path | None = None):
    """Load KEY=VALUE lines from ~/.mahoraga/.env and ./.env (never overrides
    an already-exported var). This is how API keys reach the providers.

    `env_dir` follows the config file, so a test (or a second profile) can
    point at its own directory instead of the real one.
    """
    for p in ((env_dir or CONFIG_PATH.parent) / ".env", pathlib.Path.cwd() / ".env"):
        try:
            if not p.is_file():
                continue
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip().strip('"').strip("'"),
                                      v.strip().strip('"').strip("'"))
        except OSError:
            pass

DEFAULT_SYSTEM = (
    "You are Mahoraga, a minimal local agent with tools.\n"
    "TOOL DISCIPLINE: use a tool ONLY when the user asks you to do, run, find, "
    "read, write, or change something. For greetings, smalltalk, opinions, or "
    "questions you can answer yourself, reply directly with NO tool calls. "
    "A simple 'hey' deserves a warm sentence, not a filesystem tour.\n"
    "When you do use tools, say briefly what you did. Be concise and honest."
)

DEFAULT_PROVIDERS = {
    "local": {
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "openbmb/MiniCPM5-2B-GGUF:Q4_K_M",
        "api_key_env": "",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "api_key_env": "DEEPSEEK_API_KEY",
        "models": ["deepseek-v4-pro", "deepseek-flash"],
    },
    "xiaomi": {
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "model": "mimo-v2.6-flash",
        "api_key_env": "XIAOMI_API_KEY",
        "models": ["mimo-v2.6-flash", "mimo-v2.6-pro",
                   "mimo-v2.5-pro", "mimo-v2.5"],
    },
    "mimo": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "xiaomi/mimo-v2.6-flash",
        "api_key_env": "OPENROUTER_API_KEY",
        "models": ["xiaomi/mimo-v2.6-flash", "xiaomi/mimo-v2.6-pro",
                   "xiaomi/mimo-v2.6-pro-ultraspeed", "xiaomi/mimo-v2.5-pro",
                   "xiaomi/mimo-v2.5"],
    },
}


class Mahoraga:
    def __init__(self, yolo: bool = False, config_path: str | None = None):
        self.yolo = yolo
        self.cfg_path = pathlib.Path(config_path).expanduser() if config_path else CONFIG_PATH
        self.env_path = self.cfg_path.parent / ".env"
        _load_env_files(self.cfg_path.parent)
        self.cfg = self._load_config()
        self.provider = self._make_provider(self.active_name(), self.active_cfg())
        self.registry = Registry()
        mem.init()
        self.memory = MemoryStore(self.cfg_path.parent)
        self.skills = SkillsManager(user_dir=self.cfg_path.parent / "skills")
        register_all(self.registry, data_dir=str(self.cfg_path.parent),
                     memory=self.memory, skills=self.skills)
        self.ctx = Context(
            system_prompt=self._system_prompt(),
            max_tokens=self.cfg.get("context_window", 8192),
        )
        for role, content in mem.load_recent(30):
            if role == "user":
                self.ctx.add_user(content)
            elif role == "assistant":
                self.ctx.add_assistant(content)
        self.turn_count = 0
        self.tools_enabled = True

    # ---- config ----

    def _load_config(self) -> dict:
        p = self.cfg_path
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            example = {
                "active": "local",
                "providers": DEFAULT_PROVIDERS,
                "temperature": 0.7,
                "max_tokens": 4096,
                "context_window": 8192,
                "system": DEFAULT_SYSTEM,
            }
            p.write_text(json.dumps(example, indent=2))
            print(f"[mahoraga] created config at {p} — edit it, then relaunch.")
        cfg = json.loads(p.read_text())
        # migrate the old single-provider shape into the registry
        if "providers" not in cfg:
            flat = {k: cfg.pop(k) for k in
                    ("base_url", "model", "api_key_env") if k in cfg}
            cfg["providers"] = {"local": {**DEFAULT_PROVIDERS["local"], **flat}}
            cfg["active"] = "local"
        # cloud presets always available (never clobber the user's edits);
        # new default KEYS (like models) land on existing entries too
        for name, pcfg in DEFAULT_PROVIDERS.items():
            entry = cfg["providers"].setdefault(name, {})
            for k, v in pcfg.items():
                entry.setdefault(k, v)
        if cfg.get("active") not in cfg["providers"]:
            cfg["active"] = "local"
        p.write_text(json.dumps(cfg, indent=2))
        return cfg

    def _save_config(self):
        self.cfg_path.write_text(json.dumps(self.cfg, indent=2))

    def _make_provider(self, name: str, pcfg: dict) -> OpenAICompat:
        key_env = pcfg.get("api_key_env", "")
        key = os.environ.get(key_env, "") if key_env else ""
        return OpenAICompat(
            base_url=pcfg["base_url"],
            api_key=key,
            model=pcfg.get("model", ""),
            temperature=self.cfg.get("temperature", 0.7),
            max_tokens=self.cfg.get("max_tokens", 4096),
        )

    # ---- provider registry ----

    def active_name(self) -> str:
        return self.cfg.get("active") or next(iter(self.cfg["providers"]))

    def active_cfg(self) -> dict:
        return self.cfg["providers"][self.active_name()]

    def provider_names(self) -> list[str]:
        return list(self.cfg["providers"].keys())

    def switch_provider(self, name: str) -> str:
        if name not in self.cfg["providers"]:
            raise ValueError(f"unknown provider: {name} "
                             f"(have: {', '.join(self.provider_names())})")
        self.cfg["active"] = name
        self._save_config()
        self.provider = self._make_provider(name, self.cfg["providers"][name])
        return name

    def provider_key_ok(self) -> bool:
        """False when this provider wants a key and the env var is empty."""
        env = self.active_cfg().get("api_key_env", "")
        return not env or bool(os.environ.get(env))

    # ---- adding providers: the /key paste flow ----
    #
    # A pasted key must become a WORKING provider, not just a config entry.
    # Writing JSON is the easy half. The trap is the model: NVIDIA and
    # OpenRouter list models a key cannot call (HTTP 404 "Function … Not found
    # for account"), so a saved model that "looks fine" answers 404 forever.
    # Every model is therefore proven with one real one-token completion, and
    # only a proven one is saved and switched to.

    def set_key(self, env_name: str, secret: str, persist: bool = True) -> str:
        """Export the key for this process and store it in .env (mode 600)."""
        from .keys import write_env_var
        env_name = (env_name or "").strip()
        secret = (secret or "").strip()
        if not env_name:
            raise ValueError("this provider has no api_key_env — "
                             "/provider add <name> <base_url> <model> <ENV_VAR>")
        if not secret:
            raise ValueError("empty key")
        os.environ[env_name] = secret
        if persist:
            write_env_var(self.env_path, env_name, secret)
        return env_name

    @staticmethod
    def _default_env_name(name: str) -> str:
        return f"{name.upper().replace('-', '_').replace('.', '_')}_API_KEY"

    def add_provider(self, name: str, base_url: str, model: str = "",
                     api_key_env: str = "", models=None,
                     activate: bool = False) -> dict:
        """Create or amend a provider from explicit values (/provider add)."""
        from ..llm import catalog
        name = (name or "").strip().lower()
        base_url = (base_url or "").strip().rstrip("/")
        if not name or not all(c.isalnum() or c in "-_." for c in name):
            raise ValueError("provider name: letters, digits, - _ . only")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        preset = catalog.get(name) or {}
        entry = dict(self.cfg["providers"].get(name, {}))
        entry["base_url"] = base_url
        entry["model"] = (model or entry.get("model") or "").strip()
        entry["api_key_env"] = ((api_key_env or entry.get("api_key_env")
                                 or preset.get("api_key_env")
                                 or self._default_env_name(name)).strip())
        curated = [m for m in (models or preset.get("models") or []) if m]
        if curated:
            entry["models"] = list(dict.fromkeys(curated))
        self.cfg["providers"][name] = entry
        self._save_config()
        if activate:
            self._activate(name)
        return entry

    def remove_provider(self, name: str) -> str:
        """Drop a provider from config.json (the key stays in .env)."""
        name = (name or "").strip().lower()
        if name not in self.cfg["providers"]:
            raise ValueError(f"no provider '{name}'")
        if len(self.cfg["providers"]) < 2:
            raise ValueError("can't remove the only provider")
        del self.cfg["providers"][name]
        if self.cfg.get("active") == name:
            self.cfg["active"] = ("local" if "local" in self.cfg["providers"]
                                  else next(iter(self.cfg["providers"])))
            self.provider = self._make_provider(self.active_name(), self.active_cfg())
        self._save_config()
        return self.active_name()

    def _activate(self, name: str):
        self.cfg["active"] = name
        self._save_config()
        self.provider = self._make_provider(name, self.cfg["providers"][name])

    def _ping_batch(self, client: OpenAICompat, models: list[str],
                    timeout: float) -> list[tuple[str, int | None, str]]:
        from concurrent.futures import ThreadPoolExecutor
        if not models:
            return []
        workers = max(1, min(8, len(models)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            out = list(ex.map(lambda m: (m, *client.ping(m, timeout=timeout)), models))
        return out

    def probe(self, name: str, on_progress=None, budget: int = 72,
              batch: int = 8, timeout: float = 8.0, full: bool = False,
              retry_timeouts: float = 0.0) -> dict:
        """Find out which models this key can actually call.

        Config-listed models are tried first (an existing choice is kept when
        it still answers). If none do and the platform's listing can be lying,
        the advertised chat models are scanned in parallel batches until one
        answers — the only way to discover a working model on an account where
        12 of 68 are granted. `full=True` scans everything instead of stopping
        at the first answer, which is how `/model refresh` builds an honest
        model list.
        """
        from ..llm import catalog
        if name not in self.cfg["providers"]:
            raise ValueError(f"no provider '{name}'")
        pcfg = self.cfg["providers"][name]
        env = pcfg.get("api_key_env", "")
        client = OpenAICompat(base_url=pcfg["base_url"],
                              api_key=os.environ.get(env, "") if env else "",
                              model=pcfg.get("model", ""))
        advertised = [m for m in client.list_models() if catalog.is_chat_model(m)]
        preset = catalog.get(name) or {}

        first: list[str] = []

        def add(m):
            if isinstance(m, str) and m and m not in first:
                first.append(m)

        add(pcfg.get("model", ""))
        for m in (pcfg.get("models") or []):
            add(m)
        for m in (preset.get("models") or []):
            add(m)

        tried = 0
        working: list[str] = []
        failures: list[dict] = []

        def record(results):
            nonlocal tried
            for m, code, detail in results:
                tried += 1
                if code == 200:
                    if m not in working:
                        working.append(m)
                else:
                    failures.append({"model": m, "code": code, "detail": detail})

        # a "proven" model from a previous run is still just a claim: re-check
        rest: list[str] = []
        if full:
            rest = list(dict.fromkeys(list(first) + advertised))
            first_round: list[str] = []
        else:
            first_round = first[:8]
            rest = [m for m in advertised if m not in first]

        record(self._ping_batch(client, first_round, timeout))
        if full or not working:
            auth_bad = any(f["code"] in (401, 403) for f in failures)
            if not auth_bad:
                for start in range(0, min(len(rest), budget), batch):
                    chunk = rest[start:start + batch]
                    if on_progress:
                        on_progress(min(start + batch, budget), min(len(rest), budget))
                    results = self._ping_batch(client, chunk, timeout)
                    record(results)
                    if working and not full:
                        break
                    if {c for _, c, _ in results} <= {401, 403}:
                        break

        # a slow model is not a dead one: big ones take >8 s to warm up, so a
        # full scan gives the timeouts one more, patient chance
        if retry_timeouts:
            slow = [f["model"] for f in failures if f["code"] is None]
            for start in range(0, len(slow), max(1, batch // 2)):
                chunk = slow[start:start + max(1, batch // 2)]
                for m, code, _detail in self._ping_batch(client, chunk, retry_timeouts):
                    tried += 1
                    if code == 200 and m not in working:
                        working.append(m)

        return {"provider": name, "advertised": advertised, "tried": tried,
                "working": working, "failed": failures,
                "list_error": getattr(client, "list_error", "")}

    def verify_models(self, name: str | None = None, on_progress=None) -> dict:
        """Full scan of a provider's listing; keep only models that answer.

        This is what makes `/model` trustworthy on a platform that advertises
        models it will not serve: the stored list becomes "proven callable".
        """
        name = name or self.active_name()
        result = self.probe(name, on_progress=on_progress, budget=200, full=True,
                            retry_timeouts=30.0)
        entry = self.cfg["providers"][name]
        dead = {f["model"] for f in result["failed"] if f["code"] in (400, 404, 410)}
        if result["working"]:
            entry["models"] = list(dict.fromkeys(result["working"]))[:40]
            if entry.get("model") in dead or not entry.get("model"):
                entry["model"] = result["working"][0]
                if name == self.active_name():
                    self.provider.model = entry["model"]
        else:
            entry["models"] = [m for m in (entry.get("models") or []) if m not in dead]
        self._save_config()
        result["dead"] = sorted(dead)
        result["model"] = entry.get("model", "")
        return result

    def connect(self, secret: str, provider: str | None = None,
                probe: bool = True, on_progress=None) -> dict:
        """`/key <paste>` — turn a pasted key into a working provider.

        Writes the provider block to config.json, the secret to .env, then
        proves a model before switching to it. Returns a report for the UI;
        raises ValueError with an actionable message when it can't proceed.
        """
        from ..llm import catalog
        from .keys import mask
        secret = (secret or "").strip()
        if not secret:
            raise ValueError("no key given — /key <paste> or /key <provider> <paste>")

        name = (provider or "").strip().lower()
        if not name:
            name, candidates = catalog.detect(secret)
            if not name and candidates:
                raise ValueError("that key shape is shared — say which service: "
                                 + " · ".join(f"/key {c} <key>" for c in candidates))
        if not name:
            raise ValueError("can't tell which service that key is for — "
                             "/key <provider> <key>   (known: "
                             + ", ".join(catalog.names()) + ")")

        preset = catalog.get(name) or {}
        current = dict(self.cfg["providers"].get(name, {}))
        base_url = (current.get("base_url") or preset.get("base_url") or "").rstrip("/")
        if not base_url:
            raise ValueError(f"'{name}' isn't in the catalog — "
                             f"/provider add {name} <base_url> <model> <ENV_VAR> first")
        env = (current.get("api_key_env") or preset.get("api_key_env")
               or self._default_env_name(name))

        models = list(dict.fromkeys((current.get("models") or [])
                                    + (preset.get("models") or [])))
        entry = {"base_url": base_url,
                 "model": current.get("model") or "",
                 "api_key_env": env}
        if models:
            entry["models"] = models
        self.cfg["providers"][name] = entry
        self.set_key(env, secret)
        self._save_config()

        report = {"provider": name, "base_url": base_url, "env": env,
                  "key": mask(secret), "advertised": [], "working": [],
                  "model": entry["model"], "activated": False, "detail": ""}
        if not probe:
            return report

        result = self.probe(name, on_progress=on_progress)
        report["advertised"] = result["advertised"]
        report["working"] = result["working"]
        if result["working"]:
            chosen = result["working"][0]
            dead = {f["model"] for f in result["failed"]
                    if f["code"] in (400, 404, 410)}
            entry["model"] = chosen
            entry["models"] = list(dict.fromkeys(
                result["working"]
                + [m for m in entry.get("models") or [] if m not in dead]))[:40]
            report["model"] = chosen
            report["dead"] = sorted(dead)
            self._activate(name)
            report["activated"] = True
        else:
            report["detail"] = self._probe_failure_text(result)
        self._save_config()
        return report

    @staticmethod
    def _probe_failure_text(result: dict) -> str:
        failed = result.get("failed") or []
        codes = [f.get("code") for f in failed]
        if 401 in codes or 403 in codes:
            return ("the service rejected the key (HTTP 401/403) — wrong, expired, "
                    "or scoped to another project")
        if not result.get("advertised") and not failed:
            err = result.get("list_error") or ""
            if "401" in err or "403" in err:
                return ("the service rejected the key (HTTP 401/403) — wrong, "
                        "expired, or scoped to another project")
            if err:
                return f"could not read the model list — {err[:110]}"
            return "no models to try — check base_url and the provider name"
        if failed:
            first = failed[0]
            return (f"none of the {len(failed)} models tried answered "
                    f"(last: HTTP {first.get('code')} — {str(first.get('detail'))[:90]})")
        return "no models advertised by the server"

    def probe_report_text(self, report: dict) -> str:
        """One-line-ish summary of a /key run, for any front-end."""
        if report.get("activated"):
            return (f"{report['provider']} ready · {report['model']} · "
                    f"{len(report['working'])} model(s) proven · "
                    f"listed {len(report['advertised'])}")
        return f"{report['provider']} saved but not switched — {report['detail']}"

    # ---- models ----

    def current_model(self) -> str:
        return self.provider.model

    def available_models(self) -> list[str]:
        """Models worth listing for the ACTIVE provider: the curated `models`
        list from config, else the local server's own catalog, else just the
        current model. Never dump a platform catalog — OpenRouter's /models
        returns 400+ models the user's key has nothing to do with."""
        pcfg = self.active_cfg()
        curated = list(pcfg.get("models") or [])
        if not pcfg.get("api_key_env"):        # local server: ask the server first
            remote = self.provider.list_models()
            if remote:
                return remote
            return curated or [self.provider.model]
        return curated or [self.provider.model]

    def model_names_cached(self) -> list[str]:
        """Model names for tab-completion: config + current only, no network.
        Completion runs on the input thread; a discovery call here would freeze
        the prompt for as long as the server takes to answer."""
        pcfg = self.active_cfg()
        names = [m for m in (pcfg.get("models") or []) if isinstance(m, str)]
        cur = pcfg.get("model") or getattr(self.provider, "model", "")
        if cur and cur not in names:
            names.insert(0, cur)
        return names

    def resolve_model(self, arg: str) -> str | None:
        """`/model <arg>` → a model id. A number picks from the current list."""
        arg = (arg or "").strip()
        if not arg:
            return None
        if arg.isdigit():
            models = self.available_models()
            i = int(arg) - 1
            return models[i] if 0 <= i < len(models) else None
        return arg

    def set_model(self, name: str) -> str:
        """Switch the active provider's model at runtime and persist it."""
        self.provider.model = name
        self.cfg["providers"][self.active_name()]["model"] = name
        self._save_config()
        return name

    # ---- chat ----

    def set_reasoning(self, on: bool) -> bool:
        """Turn the model's thinking block on/off (persisted in config.json)."""
        self.cfg["reasoning"] = bool(on)
        self._save_config()
        return bool(on)

    def cancel(self) -> None:
        """Stop the reply being generated right now (Ctrl+C / /stop)."""
        try:
            self.provider.cancel()
        except Exception:
            pass

    def send(self, text: str, on_thinking=None, on_tool=None,
             on_delta=None, on_reason=None, on_result=None) -> str:
        self.turn_count += 1
        budget = Budget(max_turns=self.cfg.get("max_turns", 20),
                        max_tokens=self.cfg.get("budget_tokens", 32_000))
        reasoning = bool(self.cfg.get("reasoning", True))
        answer = run_turn(text, self.ctx, self.provider, self.registry, budget,
                          yolo=self.yolo, on_thinking=on_thinking,
                          on_tool=on_tool, on_delta=on_delta,
                          on_reason=on_reason if reasoning else None,
                          tools_on=self.tools_enabled, on_result=on_result,
                          reasoning=reasoning)
        mem.save_message("user", text)
        mem.save_message("assistant", answer)
        return answer

    def new_session(self):
        """Forget the in-memory context (memory db keeps history)."""
        self.ctx.messages.clear()

    # ---- system prompt: base + memory + skills ----

    def _system_prompt(self) -> str:
        """The base prompt plus the always-on memory files and skill list.

        Rebuilt on demand (`refresh_system`) so editing memory.md or
        importing a skill takes effect without a restart.
        """
        system = self.cfg.get("system", DEFAULT_SYSTEM)
        mem_block = self.memory.inject(self.cfg.get("memory_budget", 2000))
        if mem_block:
            system += "\n\n" + mem_block
        skill_block = self.skills.inject()
        if skill_block:
            system += "\n\n" + skill_block
        return system

    def refresh_system(self) -> None:
        """Re-read memory + skills into the system prompt."""
        self.ctx.system_prompt = self._system_prompt()

    # ---- memory files ----

    def memory_show(self) -> str:
        """Both memory files, for /memory show."""
        out = []
        for name, title in (("memory.md", "PERSISTENT MEMORY"),
                            ("user.md", "USER PROFILE")):
            body = self.memory.read(name).strip()
            out.append(f"{name} ({len(body)} chars)\n{body or '(empty)'}")
        return "\n\n".join(out)

    def memory_export(self, path: str) -> str:
        return self.memory.export(path)

    def memory_import(self, path: str) -> dict:
        rep = self.memory.import_(path)
        self.refresh_system()
        return rep

    # ---- skills ----

    def skills_list(self) -> list[dict]:
        return self.skills.all()

    def skills_show(self, name: str) -> str:
        s = self.skills.get(name)
        if not s:
            raise ValueError(f"no skill '{name}' — /skills lists them")
        return f"{s['name']} — {s['description']}\n\n{s['body']}"

    def skills_install(self, src: str) -> dict:
        rep = self.skills.install(src)
        self.refresh_system()
        return rep

    def skills_remove(self, name: str) -> bool:
        ok = self.skills.remove(name)
        if ok:
            self.refresh_system()
        return ok

    def skill_load(self, name: str) -> str:
        """Drop a skill body into the live context (the /skill command)."""
        s = self.skills.get(name)
        if not s:
            raise ValueError(f"no skill '{name}' — /skills lists them")
        self.ctx.add_user(f"[loaded skill: {s['name']}]\n{s['body']}")
        return s["body"]
