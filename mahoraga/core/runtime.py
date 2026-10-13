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

CONFIG_PATH = pathlib.Path.home() / ".mahoraga" / "config.json"

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
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "mimo": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "xiaomi/mimo-v2.6-flash",
        "api_key_env": "OPENROUTER_API_KEY",
    },
}


class Mahoraga:
    def __init__(self, yolo: bool = False, config_path: str | None = None):
        self.yolo = yolo
        self.cfg_path = pathlib.Path(config_path).expanduser() if config_path else CONFIG_PATH
        self.cfg = self._load_config()
        self.provider = self._make_provider(self.active_name(), self.active_cfg())
        self.registry = Registry()
        register_all(self.registry, data_dir=str(CONFIG_PATH.parent))
        mem.init()
        self.ctx = Context(
            system_prompt=self.cfg.get("system", DEFAULT_SYSTEM),
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
        # cloud presets always available (never clobber the user's edits)
        for name, pcfg in DEFAULT_PROVIDERS.items():
            cfg["providers"].setdefault(name, dict(pcfg))
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

    # ---- models ----

    def current_model(self) -> str:
        return self.provider.model

    def available_models(self) -> list[str]:
        return self.provider.list_models()

    def set_model(self, name: str) -> str:
        """Switch the active provider's model at runtime and persist it."""
        self.provider.model = name
        self.cfg["providers"][self.active_name()]["model"] = name
        self._save_config()
        return name

    # ---- chat ----

    def send(self, text: str, on_thinking=None, on_tool=None,
             on_delta=None, on_reason=None) -> str:
        self.turn_count += 1
        budget = Budget(max_turns=self.cfg.get("max_turns", 20),
                        max_tokens=self.cfg.get("budget_tokens", 32_000))
        answer = run_turn(text, self.ctx, self.provider, self.registry, budget,
                          yolo=self.yolo, on_thinking=on_thinking,
                          on_tool=on_tool, on_delta=on_delta, on_reason=on_reason,
                          tools_on=self.tools_enabled)
        mem.save_message("user", text)
        mem.save_message("assistant", answer)
        return answer

    def new_session(self):
        """Forget the in-memory context (memory db keeps history)."""
        self.ctx.messages.clear()
