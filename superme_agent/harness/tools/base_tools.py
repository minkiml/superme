"""Base in-process tools — available in EVERY mode, unlike `dev_tools.py`.

`read_constitution` is the on-demand loader for the frontmatter-first model. Chosen over raw `Read`
so it is scope-safe and exposes no harness paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Required, TypedDict

from ...core.operational import (
    resolve_constitution, list_constitution, rank_assets_by_relevance, adopt_repo_assets,
)
from .registry import ToolSpec, build_mcp_server


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


class ReadConstitutionArgs(TypedDict, total=False):
    name: Required[Annotated[str, "the constitution's name, exactly as listed in the catalog"]]


def _read_constitution(*, mode: str, universal_dir: Path, repo_dir: Path | None,
                       activated: set | None = None, **_):
    """Resolve a catalog name to the item's full body.

    Scope is the dirs and active-set bound here, so an out-of-scope name simply misses."""
    async def read_constitution(args: dict) -> dict:
        name = str(args.get("name") or "").strip()
        if not name:
            return _err("Pass `name` — the constitution to load (see the catalog).")
        it = resolve_constitution(mode, universal_dir, repo_dir, name, activated=activated)
        if it is None:
            avail = ", ".join(
                i["slug"] for i in list_constitution(mode, universal_dir, repo_dir, activated=activated)
                if i["enabled"]
            ) or "(none)"
            return _err(f"No in-scope constitution named '{name}'. Available: {avail}")
        return _ok(it["body"])
    return read_constitution


class AdoptKnowledgeAssetsArgs(TypedDict, total=False):
    spec: Required[Annotated[str, "the project spec / stack / approach text to match against"]]
    limit: Annotated[int, "max suggestions to return (default 8)"]


def _adopt_knowledge_assets(*, activated: set | None = None, repo_dir: Path | None = None, **_):
    """Relevance-rank the shared ASSET POOL against the project spec and auto-adopt the confidently
    relevant ones.

    Confident means the spec shares a term with the asset's slug or description."""
    async def adopt_knowledge_assets(args: dict) -> dict:
        spec = str(args.get("spec") or "").strip()
        if not spec:
            return _err("Pass `spec` — the project's stack/approach text to match against.")
        try:
            limit = max(1, min(20, int(args.get("limit") or 8)))
        except (TypeError, ValueError):
            limit = 8
        ranked = rank_assets_by_relevance(spec, activated, limit=limit)
        if not ranked:
            return _ok("No knowledge assets look relevant to this spec.")
        confident = [r for r in ranked if r["confident"] and not r["activated"]]
        newly = set(adopt_repo_assets(repo_dir, [r["slug"] for r in confident]))
        adopted = [r for r in confident if r["slug"] in newly]
        maybe = [r for r in ranked if r["slug"] not in newly and not r["activated"]]

        def _row(r: dict) -> str:
            return f"- **{r['slug']}** — {(r.get('description') or '').strip()}"

        out: list[str] = []
        if adopted:
            out.append("**Auto-adopted & enabled for this repo** — curate in the dashboard "
                       "(disable, drop, or + Add more):")
            out += [_row(r) for r in adopted]
        if maybe:
            out.append(("\n" if out else "") + "**Also possibly relevant** — + Add from the dashboard "
                       "if you want them:")
            out += [_row(r) for r in maybe]
        if not out:
            return _ok("The relevant assets are already active for this repo; nothing new to adopt.")
        return _ok("\n".join(out))
    return adopt_knowledge_assets


BASE_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "read_constitution",
        "Loads one constitution item's full body by name. Use it when the catalog line in front "
        "of you does not say what the rule actually requires. Do not use it to read any other "
        "file: it resolves names from that catalog only, and a name outside your scope simply "
        "misses. Returns the item's full body text.",
        ReadConstitutionArgs, _read_constitution,
    ),
    ToolSpec(
        "adopt_knowledge_assets",
        "Ranks the shared knowledge-asset pool against a project spec and adopts the clearly "
        "relevant items for this repo. Use it once while onboarding a project, after its "
        "architecture is drafted. Do not use it in ordinary chat: it writes the repo's "
        "adopted-asset list, which the owner then curates. Returns the assets adopted and the "
        "others that looked relevant.",
        AdoptKnowledgeAssetsArgs, _adopt_knowledge_assets,
    ),
]


def make_base_mcp_server(mode: str, universal_dir: Path, repo_dir: Path | None,
                         activated: set | None = None):
    """Build the `superme` MCP server, bound to this host's constitution homes and activated assets.

    So `read_constitution` and `adopt_knowledge_assets` only ever serve in-scope items."""
    return build_mcp_server("superme", BASE_TOOLS, mode=mode,
                            universal_dir=universal_dir, repo_dir=repo_dir, activated=activated)
