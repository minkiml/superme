"""Light MCP-tool registry — the mechanism behind SuperMe's in-process tools.

A `ToolSpec` declares one tool: its `description`, a TypedDict `schema` whose `Annotated` fields
document each parameter, optional whole-argument `examples`, and a `build` factory binding the
handler to its deps.
"""

from __future__ import annotations

import json
import types as _types
from dataclasses import dataclass
from typing import (Annotated, Any, Awaitable, Callable, Literal, Union,
                    get_args, get_origin, get_type_hints, is_typeddict)

from claude_agent_sdk import create_sdk_mcp_server, tool

Handler = Callable[[dict], Awaitable[dict[str, Any]]]


# --- TypedDict to JSON-Schema --- The SDK's converter degrades `Literal` to a bare string, so the
# allowed values vanish.

def _type_schema(py_type: Any) -> dict[str, Any]:
    origin = get_origin(py_type)
    if getattr(origin, "_name", None) in ("NotRequired", "Required", "ReadOnly"):
        return _type_schema(get_args(py_type)[0])
    if origin is Annotated:
        args = get_args(py_type)
        schema = _type_schema(args[0])
        for meta in args[1:]:
            if isinstance(meta, str):
                schema["description"] = meta
                break
        return schema
    if origin is Literal:
        values = list(get_args(py_type))
        base = {str: "string", int: "integer", bool: "boolean"}.get(type(values[0]), "string")
        return {"type": base, "enum": values}
    if py_type is str:
        return {"type": "string"}
    if py_type is int:
        return {"type": "integer"}
    if py_type is float:
        return {"type": "number"}
    if py_type is bool:
        return {"type": "boolean"}
    if origin is Union or isinstance(py_type, _types.UnionType):
        non_none = [a for a in get_args(py_type) if a is not type(None)]
        if len(non_none) == 1:
            return _type_schema(non_none[0])
        return {"anyOf": [_type_schema(a) for a in non_none]}
    if origin is list:
        args = get_args(py_type)
        return {"type": "array", "items": _type_schema(args[0])} if args else {"type": "array"}
    if origin is dict or py_type is dict:
        return {"type": "object"}
    if py_type is list:
        return {"type": "array"}
    if is_typeddict(py_type):
        return _render_schema(py_type)
    return {"type": "string"}


def _render_schema(schema: type | dict[str, Any]) -> dict[str, Any]:
    """A ToolSpec schema as the JSON-Schema dict the SDK tool will carry: TypedDicts rendered
    (Literal → enum), ready-made dicts passed through."""
    if isinstance(schema, dict):
        return schema
    hints = get_type_hints(schema, include_extras=True)
    out: dict[str, Any] = {"type": "object",
                           "properties": {k: _type_schema(t) for k, t in hints.items()}}
    # Requiredness from the RESOLVED hints: under `from __future__ import annotations`,
    # `Required[...]` never reaches `__required_keys__`.
    declared = getattr(schema, "__required_keys__", frozenset())
    required = set()
    for key, hint in hints.items():
        wrapper = getattr(get_origin(hint), "_name", None)
        if wrapper == "Required" or (wrapper != "NotRequired" and key in declared):
            required.add(key)
    if required:
        out["required"] = sorted(required)
    # Closed by default: without this an agent may invent a parameter and the call still validates.
    out["additionalProperties"] = False
    return out


def spec_schema(spec: "ToolSpec") -> dict[str, Any]:
    """One spec's schema exactly as the wire will carry it, examples included."""
    out = dict(_render_schema(spec.schema))
    if spec.examples:
        out["examples"] = [dict(e) for e in spec.examples]
    return out


@dataclass(frozen=True)
class ToolSpec:
    """One tool: description, typed schema, and a deps-bound handler factory.

    An agent routes on the `description`: what the tool achieves, when to use it, what it
    will NOT do."""

    name: str
    description: str                       # what it achieves · when to use it · what it won't do
    schema: type | dict[str, Any]          # a TypedDict (Annotated docs) or a full JSON-Schema dict
    build: Callable[..., Handler]          # (**deps) -> async handler
    examples: tuple[dict[str, Any], ...] = ()   # whole valid argument sets, for a shape prose can't fix


def _kind(prop: dict[str, Any]) -> str:
    """One property's type as the listing shows it, an array carrying what it holds."""
    if prop.get("enum"):
        return "|".join(str(v) for v in prop["enum"])
    name = str(prop.get("type") or "any")
    if name == "array":
        return f"array<{_kind(prop.get('items') or {})}>"
    return name


def describe_specs(specs: list[ToolSpec]) -> str:
    """The authored tool surface as readable text, in mount order.

    For READING, not for costing: Claude Code sends only the tool names and holds these schemas
    until an agent fetches one with `ToolSearch`. A long description is paid by the run that asks
    for it, not by every request."""
    blocks: list[str] = []
    for s in specs:
        schema = spec_schema(s)
        props: dict = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        lines = [s.name, f"    {s.description}"]
        for key, prop in props.items():
            doc = prop.get("description") or ""
            opt = "" if key in required else ", optional"
            lines.append(f"    · {key} ({_kind(prop)}{opt})" + (f" — {doc}" if doc else ""))
            # A nested object's own fields, or the listing shows only the word "object".
            inner = prop.get("items") if prop.get("type") == "array" else prop
            for sub, sub_prop in ((inner or {}).get("properties") or {}).items():
                sub_opt = "" if sub in set((inner or {}).get("required") or ()) else ", optional"
                sub_doc = sub_prop.get("description") or ""
                lines.append(f"        · {sub} ({_kind(sub_prop)}{sub_opt})"
                             + (f" — {sub_doc}" if sub_doc else ""))
        for example in schema.get("examples") or []:
            lines.append(f"    example: {json.dumps(example)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_mcp_server(name: str, specs: list[ToolSpec], *, version: str = "1.0.0", **deps):
    """Render `specs` into an SDK MCP server, binding each handler to the shared `deps`.

    Unknown deps are ignored by the factories, so callers can pass a superset."""
    tools = [tool(s.name, s.description, spec_schema(s))(s.build(**deps))
             for s in specs]
    return create_sdk_mcp_server(name=name, version=version, tools=tools)
