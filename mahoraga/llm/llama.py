"""llama.cpp server — thin adapter (llama-server serves OpenAI /v1 too)."""
from __future__ import annotations
from .openai_compat import OpenAICompat

def make(base_url: str = "http://localhost:8080/v1", model: str = "local",
         **kw) -> OpenAICompat:
    return OpenAICompat(base_url=base_url, model=model, api_key="", **kw)
