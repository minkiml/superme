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

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

Handler = Callable[[dict], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    """One tool's full declaration: lean description + typed schema + a deps-bound handler factory."""

    name: str
    description: str                       # lean one-liner (no WHEN/HOW — that's the skill's job)
    schema: type | dict[str, Any]          # a TypedDict (Annotated docs) or a full JSON-Schema dict
    build: Callable[..., Handler]          # (**deps) -> async handler


def build_mcp_server(name: str, specs: list[ToolSpec], *, version: str = "1.0.0", **deps):
    """Render `specs` → an SDK MCP server, binding each handler to the shared `deps`.

    Each spec's `build(**deps)` returns the async handler; unknown deps are ignored by factories
    (they take `**_`), so callers can pass a superset without coupling every tool to every dep.
    """
    tools = [tool(s.name, s.description, s.schema)(s.build(**deps)) for s in specs]
    return create_sdk_mcp_server(name=name, version=version, tools=tools)
