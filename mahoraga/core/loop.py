"""The agent loop — the beating heart, ~50 lines.

While the model returns tool calls: approve → execute → feed results back.
When it returns plain text: that's the answer, turn ends. Budget guards both.
"""
from __future__ import annotations
import json
from ..tools.registry import Registry
from ..tools.executor import execute
from ..llm.provider import LLMProvider
from ..core.context import Context
from ..core.budget import Budget

def run_turn(user_text: str, ctx: Context, provider: LLMProvider,
             registry: Registry, budget: Budget,
             yolo: bool = False, on_thinking=None, on_tool=None) -> str:
    """One user turn → final assistant answer (may involve many tool calls)."""
    ctx.add_user(user_text)
    tools = registry.schemas() or None

    while True:
        reason = budget.exhausted()
        if reason:
            return f"[mahoraga stopped: {reason}]"
        budget.charge_turn()

        if on_thinking:
            on_thinking()

        resp = provider.chat(ctx.build_payload(), tools=tools)
        budget.charge_tokens(resp.usage.get("total_tokens", 0))

        if not resp.tool_calls:
            ctx.add_assistant(resp.content)
            return resp.content

        # record the assistant's tool_call message exactly as OpenAI wants it
        ctx.add_assistant_tool_calls([
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"],
                          "arguments": json.dumps(tc["arguments"])}}
            for tc in resp.tool_calls
        ])

        for tc in resp.tool_calls:
            if on_tool:
                on_tool(tc["name"], tc["arguments"])
            result = execute(registry, tc["name"], tc["arguments"], yolo=yolo)
            ctx.add_tool_result(tc["id"], result)
