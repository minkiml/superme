"""Base in-process tools — available in EVERY mode, unlike `dev_tools.py`.

`pull_constitution` is the on-demand loader for the frontmatter-first model. Chosen over raw `Read`
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


class PullConstitutionArgs(TypedDict, total=False):
    name: Required[Annotated[str, "the constitution's name, exactly as listed in the catalog"]]


def _pull_constitution(*, mode: str, universal_dir: Path, repo_dir: Path | None,
                       activated: set | None = None, **_):
    """Resolve a catalog name to the item's full body.

    Scope is the dirs and active-set bound here, so an out-of-scope name simply misses."""
    async def pull_constitution(args: dict) -> dict:
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
    return pull_constitution


class SuggestAssetsArgs(TypedDict, total=False):
    spec: Required[Annotated[str, "the project spec / stack / approach text to match against"]]
    limit: Annotated[int, "max suggestions to return (default 8)"]


def _suggest_assets(*, activated: set | None = None, repo_dir: Path | None = None, **_):
    """Relevance-rank the shared ASSET POOL against the project spec and auto-adopt the confidently
    relevant ones.

    Confident means the spec shares a term with the asset's slug or description."""
    async def suggest_assets(args: dict) -> dict:
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
    return suggest_assets


BASE_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "pull_constitution",
        "Load one constitution item's full body by name, taken from the catalog already in front "
        "of you. Use it when the catalog line alone does not tell you what the rule requires. It "
        "resolves names from that catalog only — a name that is not in scope simply misses, and "
        "it reads nothing else on disk.",
        PullConstitutionArgs, _pull_constitution,
    ),
    ToolSpec(
        "suggest_assets",
        "Search and rank the shared knowledge-asset pool against a project spec and auto-adopt the "
        "confidently-relevant items for this repo. This tool WRITES the repo's adopted-asset list — "
        "call it only during project onboarding (project-init / retrofit, after drafting architecture.md), "
        "never in ordinary chat; the owner curates the adopted set afterwards.",
        SuggestAssetsArgs, _suggest_assets,
    ),
]


def make_base_mcp_server(mode: str, universal_dir: Path, repo_dir: Path | None,
                         activated: set | None = None):
    """Build the `superme` MCP server, bound to this host's constitution homes and activated assets.

    So `pull_constitution` and `suggest_assets` only ever serve in-scope items."""
    return build_mcp_server("superme", BASE_TOOLS, mode=mode,
                            universal_dir=universal_dir, repo_dir=repo_dir, activated=activated)
