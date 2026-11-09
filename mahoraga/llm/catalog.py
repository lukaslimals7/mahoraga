"""The provider catalog — the data `/key` needs to build a config entry.

One record per OpenAI-compatible service: where it lives, which env var holds
its key, and the key prefixes that identify it. `/key <paste>` looks the
service up here and writes both halves of the setup at once — the provider
block into config.json, the secret into .env — so a pasted key becomes a
working provider without anyone hand-editing JSON.

`trust_listing` — whether GET /models is worth believing here. Platforms like
NVIDIA and OpenRouter advertise models the key cannot actually call (a 404
"Function … Not found for account"), so their listings are shown, never stored
as truth. Only a real one-token chat call decides what works.
"""
from __future__ import annotations

# exact: prefixes unique to this service (a strong match)
# weak:  prefixes shared with other services — the candidate list comes back
#        instead of a guess, because a wrong guess costs a confusing 404
KNOWN: dict[str, dict] = {
    "nvidia": {
        "label": "NVIDIA NIM (integrate.api.nvidia.com)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
        "exact": ["nvapi-"],
        "models": [],
        "trust_listing": False,
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "exact": ["sk-or-"],
        "models": ["openrouter/auto"],
        "trust_listing": False,
    },
    "opencode": {
        "label": "OpenCode Zen (opencode.ai)",
        "base_url": "https://opencode.ai/inference/v1",
        "api_key_env": "OPENCODE_API_KEY",
        "exact": ["oc_sk_"],
        "weak": ["sk-"],
        "models": ["mimo-v2.6-flash-free", "deepseek-v4.1-flash-free",
                   "glm-5.3-flash-free", "longcat-2.5-free"],
        "trust_listing": False,
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "weak": ["sk-"],
        "models": ["deepseek-v4-pro", "deepseek-flash"],
        "trust_listing": True,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "exact": ["sk-proj-", "sk-svcacct-", "sk-admin-"],
        "weak": ["sk-"],
        "models": ["gpt-4o-mini"],
        "trust_listing": True,
    },
    "xiaomi": {
        "label": "Xiaomi MiMo (token plan)",
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "api_key_env": "XIAOMI_API_KEY",
        "models": ["mimo-v2.6-flash", "mimo-v2.6-pro"],
        "trust_listing": True,
    },
    "moonshot": {
        "label": "Moonshot / Kimi",
        "base_url": "https://api.moonshot.ai/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "weak": ["sk-"],
        "models": [],
        "trust_listing": True,
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "exact": ["gsk_"],
        "models": [],
        "trust_listing": True,
    },
    "xai": {
        "label": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "exact": ["xai-"],
        "models": ["grok-4"],
        "trust_listing": True,
    },
    "gemini": {
        "label": "Google Gemini (OpenAI-compat)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": "GOOGLE_API_KEY",
        "exact": ["AIza"],
        "models": [],
        "trust_listing": True,
    },
    "cerebras": {
        "label": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "api_key_env": "CEREBRAS_API_KEY",
        "exact": ["csk-"],
        "models": [],
        "trust_listing": True,
    },
    "fireworks": {
        "label": "Fireworks",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "exact": ["fw_"],
        "models": [],
        "trust_listing": True,
    },
    "perplexity": {
        "label": "Perplexity",
        "base_url": "https://api.perplexity.ai",
        "api_key_env": "PERPLEXITY_API_KEY",
        "exact": ["pplx-"],
        "models": [],
        "trust_listing": True,
    },
    "together": {
        "label": "Together",
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "models": [],
        "trust_listing": False,
    },
    "mistral": {
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "models": [],
        "trust_listing": True,
    },
    "deepinfra": {
        "label": "DeepInfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "api_key_env": "DEEPINFRA_API_KEY",
        "models": [],
        "trust_listing": True,
    },
}

# longest prefix first: "sk-proj-" must win over "sk-", "sk-or-" over "sk-"
_EXACT = sorted(((p, n) for n, e in KNOWN.items() for p in e.get("exact", [])),
                key=lambda t: -len(t[0]))
_WEAK = sorted(((p, n) for n, e in KNOWN.items() for p in e.get("weak", [])),
               key=lambda t: -len(t[0]))

# ids that are never chat models — probing them wastes a call and always fails
_NOT_CHAT = ("embed", "rerank", "deplot", "guard", "safety", "diffusion",
             "content-safety", "moderation", "whisper", "tts")


def names() -> list[str]:
    return sorted(KNOWN)


def get(name: str) -> dict | None:
    return KNOWN.get((name or "").strip().lower())


def detect(secret: str) -> tuple[str | None, list[str]]:
    """Guess the service a key belongs to from its prefix.

    → ("nvidia", [])              certain: the prefix is this service's alone
    → (None, ["deepseek", ...])   shared prefix: let the human pick, never guess
    → (None, [])                  unknown shape
    """
    s = (secret or "").strip()
    if not s:
        return None, []
    for prefix, name in _EXACT:
        if s.startswith(prefix):
            return name, []
    for prefix, _ in _WEAK:
        if s.startswith(prefix):
            return None, sorted({n for p, n in _WEAK if p == prefix})
    return None, []


def is_chat_model(model_id: str) -> bool:
    """False for embedding / rerank / safety endpoints in a platform listing."""
    m = (model_id or "").lower()
    return bool(m) and not any(bad in m for bad in _NOT_CHAT)


def describe(name: str) -> str:
    entry = get(name)
    if not entry:
        return name
    return f"{name} · {entry.get('label', '')} · {entry.get('base_url', '')}"
