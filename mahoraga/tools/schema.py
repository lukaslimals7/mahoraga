"""Schema helpers: derive a JSON schema from a Python signature —
so adding a tool is: write a function, decorate it, done."""
from __future__ import annotations
import inspect, typing

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}

def auto_schema(fn) -> tuple[str, dict]:
    """-> (description, parameters_schema) from the function itself."""
    sig = inspect.signature(fn)
    props, required = {}, []
    for name, param in sig.parameters.items():
        ann = param.annotation if param.annotation is not inspect.Parameter.empty else str
        props[name] = {"type": _TYPE_MAP.get(ann, "string")}
        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            props[name]["default"] = param.default
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return (inspect.getdoc(fn) or "").strip(), schema
