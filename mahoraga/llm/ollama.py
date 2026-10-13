"""Ollama — thin adapter. Ollama ships an OpenAI-compatible /v1, so this is
just OpenAICompat with Ollama-flavoured defaults."""
from __future__ import annotations
from .openai_compat import OpenAICompat

def make(base_url: str = "http://localhost:11434/v1", model: str = "llama3.2",
         **kw) -> OpenAICompat:
    return OpenAICompat(base_url=base_url, model=model, api_key="", **kw)
