#!/usr/bin/env python3
"""End-to-end smoke test — proves the agent loop works with a mock LLM."""
from __future__ import annotations
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mahoraga.core.context import Context
from mahoraga.core.budget import Budget
from mahoraga.core.loop import run_turn
from mahoraga.tools.registry import Registry
from mahoraga.tools.executor import execute
from mahoraga.llm.provider import ChatResponse, tool_call_from_raw
from mahoraga.plugins import register_all
from mahoraga.policy.permissions import tier_for

class MockLLM:
    """Returns tool calls then a final answer — simulates a real model."""
    def __init__(self):
        self.step = 0

    def chat(self, messages, tools=None):
        self.step += 1
        if self.step == 1:
            # first call: request a tool
            return ChatResponse(
                content="",
                tool_calls=[{
                    "id": "call_1",
                    "name": "estimate_tokens",
                    "arguments": {"text": "hello mahoraga"},
                }],
                usage={"total_tokens": 20},
            )
        elif self.step == 2:
            # second call: request another tool
            return ChatResponse(
                content="",
                tool_calls=[{
                    "id": "call_2",
                    "name": "list_dir",
                    "arguments": {"path": "."},
                }],
                usage={"total_tokens": 30},
            )
        else:
            # third call: final answer
            return ChatResponse(
                content="I used two tools: estimated tokens and listed the directory.",
                tool_calls=[],
                usage={"total_tokens": 40},
            )

def main():
    print("=== Mahoraga end-to-end smoke test ===\n")

    # build the pieces
    ctx = Context(system_prompt="You are a test agent.")
    budget = Budget(max_turns=5)
    registry = Registry()
    register_all(registry, "/tmp/mahoraga-smoke")
    llm = MockLLM()

    print("1. registry loaded:", len(registry.tools), "tools")
    print("2. policy tiers:", {t: tier_for(t) for t in registry.tools})

    # run the loop (yolo=True so no approval prompts block the test)
    events = []
    def on_tool(name, args):
        events.append(("tool", name, args))

    answer = run_turn(
        "list the directory and estimate tokens",
        ctx, llm, registry, budget,
        yolo=True,
        on_tool=lambda n, a: events.append((n, a)),
    )

    print("3. events:", events)
    print("4. final answer:", answer)
    print("5. context messages:", len(ctx.messages))
    print("6. budget: turns =", budget.turns, "tokens =", budget.tokens)

    assert "two tools" in answer
    assert len(ctx.messages) == 6  # user, asst+tool_calls, tool_result, asst+tool_calls, tool_result, asst
    assert budget.exhausted() is None
    assert events[0][0] == "estimate_tokens"
    assert events[1][0] == "list_dir"
    print("\n=== ALL CHECKS PASSED ===")

if __name__ == "__main__":
    main()
