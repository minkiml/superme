"""Light MCP-tool registry — the mechanism behind SuperMe's in-process dev/core tools.

A `ToolSpec` is the whole declaration of one tool:

- **`description`** — a LEAN one-liner. It sits in every turn's context (and invites accidental
  calls when bloated), so the WHEN/HOW lives in the owning skill/agent, not here. Tools are scoped
  to where they belong via the owning agent's `tools:` allowlist.
- **`schema`** — a TypedDict whose `Annotated[type, "doc"]` fields carry the per-param docs and
  whose `Required[...]` fields mark what's mandatory. The SDK renders a TypedDict straight to
  JSON-Schema (required-keys respected, `Annotated` → param `description`, nested TypedDicts
  inlined — no `$ref`), so typed inputs are self-documenting without hand-written JSON.
- **`build`** — a factory `(**deps) -> async handler` that binds the handler to its runtime deps
  (the event store, the context id, optional callbacks). Deps arrive at server-build time.

`build_mcp_server` turns a list of specs into an SDK MCP server. Keeping the specs in a list makes
"what tools sit in the agent's context, and what each costs" auditable at a glance.
"""

from __future__ import annotations

import types as _types
from dataclasses import dataclass
from typing import (Annotated, Any, Awaitable, Callable, Literal, Union,
                    get_args, get_origin, get_type_hints, is_typeddict)

from claude_agent_sdk import create_sdk_mcp_server, tool

Handler = Callable[[dict], Awaitable[dict[str, Any]]]


# --- TypedDict → JSON-Schema, Literal-aware -------------------------------------------------
# The SDK's own converter handles Annotated/Required/NotRequired but silently degrades
# `Literal["a","b"]` to a bare string (no `enum`) — the allowed values vanish from the schema
# the model sees. This renderer mirrors the SDK's semantics and adds the enum, so schemas can
# state closed value sets as types instead of prose. ToolSpec schemas are pre-rendered through
# it; a spec that's already a JSON-Schema dict passes through untouched.

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
    # Requiredness from the RESOLVED hints, not just __required_keys__: under
    # `from __future__ import annotations` the class sees only strings at creation time, so
    # `Required[...]` never reaches __required_keys__ (that latent hole shipped un-required
    # args in base_tools). The wrapper on the resolved hint is authoritative; __required_keys__
    # covers plain fields in total=True dicts.
    declared = getattr(schema, "__required_keys__", frozenset())
    required = set()
    for key, hint in hints.items():
        wrapper = getattr(get_origin(hint), "_name", None)
        if wrapper == "Required" or (wrapper != "NotRequired" and key in declared):
            required.add(key)
    if required:
        out["required"] = sorted(required)
    return out


@dataclass(frozen=True)
class ToolSpec:
    """One tool's full declaration: lean description + typed schema + a deps-bound handler factory."""

    name: str
    description: str                       # lean one-liner (no WHEN/HOW — that's the skill's job)
    schema: type | dict[str, Any]          # a TypedDict (Annotated docs) or a full JSON-Schema dict
    build: Callable[..., Handler]          # (**deps) -> async handler


def describe_specs(specs: list[ToolSpec]) -> str:
    """The authored tool surface as readable text: each tool's name, its description, and each
    argument's own doc, in mount order.

    This is PROMPT TEXT. A tool's description and its parameter docs ride in every request the turn
    makes, exactly like the system append does — so the prompt inspector renders them beside the
    prose they compete with, and a bloated description is visible as the cost it is.
    """
    blocks: list[str] = []
    for s in specs:
        schema = _render_schema(s.schema)
        props: dict = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        lines = [s.name, f"    {s.description}"]
        for key, prop in props.items():
            kind = "|".join(str(v) for v in prop["enum"]) if prop.get("enum") else \
                str(prop.get("type") or "any")
            doc = prop.get("description") or ""
            opt = "" if key in required else ", optional"
            lines.append(f"    · {key} ({kind}{opt})" + (f" — {doc}" if doc else ""))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_mcp_server(name: str, specs: list[ToolSpec], *, version: str = "1.0.0", **deps):
    """Render `specs` → an SDK MCP server, binding each handler to the shared `deps`.

    Each spec's `build(**deps)` returns the async handler; unknown deps are ignored by factories
    (they take `**_`), so callers can pass a superset without coupling every tool to every dep.
    """
    tools = [tool(s.name, s.description, _render_schema(s.schema))(s.build(**deps))
             for s in specs]
    return create_sdk_mcp_server(name=name, version=version, tools=tools)
