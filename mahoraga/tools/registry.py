"""Tool registry: register, describe, execute. One tool = name + description + params + fn."""
from __future__ import annotations
import json, inspect, typing

class Tool:
    def __init__(self, name: str, description: str, parameters: dict, fn):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

class Registry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, name: str, description: str, parameters: dict, fn):
        self.tools[name] = Tool(name, description, parameters, fn)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def execute(self, name: str, arguments: dict) -> str:
        tool = self.tools.get(name)
        if not tool:
            return json.dumps({"error": f"unknown tool: {name}"})
        try:
            result = tool.fn(**arguments)
            return str(result) if not isinstance(result, str) else result
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
