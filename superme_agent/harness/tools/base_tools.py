"""Base in-process tools — available in EVERY mode (core + dev), unlike dev_tools.py.

Right now this is just `pull_constitution`: the on-demand loader for the frontmatter-first
constitution model (context-model-spec §2). The always-on context carries only a CATALOG of
constitution names + descriptions; the body is fetched on demand by this tool, by name. Chosen
over raw `Read` so it is scope-safe (only ever resolves the host's in-scope items — the caller
binds the host's universal + repo constitution homes as deps), exposes no harness paths, and can
log usage. Constitutions exist in both core and dev, so this server loads in both modes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Required, TypedDict

from ...core.operational import resolve_constitution, list_constitution
from .registry import ToolSpec, build_mcp_server


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


class PullConstitutionArgs(TypedDict, total=False):
    name: Required[Annotated[str, "the constitution's name, exactly as listed in the catalog"]]


def _pull_constitution(*, mode: str, universal_dir: Path, repo_dir: Path | None, **_):
    """Resolve a catalog name → the item's full body. Scope is enforced by the dirs bound here
    (only the host's universal + repo constitution homes), so an out-of-scope name just misses."""
    async def pull_constitution(args: dict) -> dict:
        name = str(args.get("name") or "").strip()
        if not name:
            return _err("Pass `name` — the constitution to load (see the catalog).")
        it = resolve_constitution(mode, universal_dir, repo_dir, name)
        if it is None:
            avail = ", ".join(
                i["slug"] for i in list_constitution(mode, universal_dir, repo_dir) if i["enabled"]
            ) or "(none)"
            return _err(f"No in-scope constitution named '{name}'. Available: {avail}")
        return _ok(it["body"])
    return pull_constitution


BASE_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "pull_constitution",
        "Load a constitution's full body by name (from the always-on catalog).",
        PullConstitutionArgs, _pull_constitution,
    ),
]


def make_base_mcp_server(mode: str, universal_dir: Path, repo_dir: Path | None):
    """Build the `superme` MCP server (base tools, every mode), bound to this host's constitution
    homes so `pull_constitution` only ever serves in-scope items."""
    return build_mcp_server("superme", BASE_TOOLS, mode=mode,
                            universal_dir=universal_dir, repo_dir=repo_dir)
