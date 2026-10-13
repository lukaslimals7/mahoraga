"""Token / turn budget: stop runaway loops."""
from __future__ import annotations
import time

class Budget:
    def __init__(self, max_turns: int = 20, max_tokens: int = 32_000,
                 max_seconds: float = 300.0):
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.max_seconds = max_seconds
        self.turns = 0
        self.tokens = 0
        self.start = time.monotonic()

    def charge_turn(self):
        self.turns += 1

    def charge_tokens(self, n: int):
        self.tokens += n

    def exhausted(self) -> str | None:
        if self.turns >= self.max_turns:
            return f"max turns ({self.max_turns})"
        if self.tokens >= self.max_tokens:
            return f"max tokens ({self.max_tokens})"
        if time.monotonic() - self.start >= self.max_seconds:
            return f"max time ({self.max_seconds}s)"
        return None
