"""Runtime: wire provider + tools + memory + context into a working agent."""
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
    "You are Mahoraga, a minimal local agent. You have tools. "
    "Use them when they help; answer directly when they don't. "
    "Be concise and honest about what you did."
)

class Mahoraga:
    def __init__(self, yolo: bool = False, config_path: str | None = None):
        self.yolo = yolo
        self.cfg = self._load_config(config_path)
        self.provider = self._make_provider()
        self.registry = Registry()
        register_all(self.registry, data_dir=str(CONFIG_PATH.parent))
        mem.init()
        restored = mem.load_recent(30)
        self.ctx = Context(
            system_prompt=self.cfg.get("system", DEFAULT_SYSTEM),
            max_tokens=self.cfg.get("context_window", 8192),
        )
        # restore memory: replay stored turns into the context
        for role, content in restored:
            if role == "user":
                self.ctx.add_user(content)
            elif role == "assistant":
                self.ctx.add_assistant(content)
        self.turn_count = 0

    def _load_config(self, path: str | None) -> dict:
        p = pathlib.Path(path).expanduser() if path else CONFIG_PATH
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            example = {
                "base_url": "http://localhost:11434/v1",  # Ollama default
                "api_key_env": "MAHORAGA_API_KEY",        # key read from env, never stored
                "model": "llama3.2",
                "temperature": 0.7,
                "max_tokens": 4096,
                "context_window": 8192,
                "system": DEFAULT_SYSTEM,
            }
            p.write_text(json.dumps(example, indent=2))
            print(f"[mahoraga] created config at {p} — edit it, then relaunch.")
        return json.loads(p.read_text())

    def _make_provider(self) -> OpenAICompat:
        key = os.environ.get(self.cfg.get("api_key_env", "MAHORAGA_API_KEY"), "")
        return OpenAICompat(
            base_url=self.cfg["base_url"],
            api_key=key,
            model=self.cfg["model"],
            temperature=self.cfg.get("temperature", 0.7),
            max_tokens=self.cfg.get("max_tokens", 4096),
        )

    def send(self, text: str, on_thinking=None, on_tool=None) -> str:
        self.turn_count += 1
        budget = Budget(max_turns=self.cfg.get("max_turns", 20),
                        max_tokens=self.cfg.get("budget_tokens", 32_000))
        answer = run_turn(text, self.ctx, self.provider, self.registry, budget,
                          yolo=self.yolo, on_thinking=on_thinking, on_tool=on_tool)
        mem.save_message("user", text)
        mem.save_message("assistant", answer)
        return answer

    def current_model(self) -> str:
        return self.provider.model

    def available_models(self) -> list[str]:
        return self.provider.list_models()

    def set_model(self, name: str) -> str:
        """Switch the active model at runtime and persist it to config.json."""
        self.provider.model = name
        self.cfg["model"] = name
        CONFIG_PATH.write_text(json.dumps(self.cfg, indent=2))
        return name

    def new_session(self):
        """Forget the in-memory context (memory db keeps history)."""
        self.ctx.messages.clear()
