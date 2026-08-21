"""Import-layering gate: the package stays acyclic and no area reaches upward.

Module-level imports only: an import deferred inside a function does not couple two modules
at load time.

    python -m scripts.layers            # report and gate
    python -m scripts.layers --graph    # also print every cross-area edge
"""

import ast
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "superme_agent"
SKIP_PARTS = ("plugins", "local-harness")

# What each area is allowed to import. `paths` is the one root module everything may reach.
ALLOWED: dict[str, set[str]] = {
    "core": {"core"},
    "gateway": {"core", "gateway"},
    "harness": {"core", "gateway", "harness"},
    "daemon": {"core", "gateway", "harness", "daemon"},
}
ROOT_MODULES = {"superme_agent.paths", "superme_agent"}

# Tolerated violations of ALLOWED, pinned so no new one slips in. These three make core
# and harness mutually dependent.
PINNED = {
    "superme_agent.core.agent_service → superme_agent.harness.tools.base_tools",
    "superme_agent.core.deputy → superme_agent.harness.tools.run_tools",
    "superme_agent.core.permissions → superme_agent.harness.policy",
}

# The tier the rest of core is defined in terms of. Importing nothing else in core is what
# keeps it a floor.
VOCAB = {
    "superme_agent.core.context",
    "superme_agent.core.events",
    "superme_agent.core.kind_profiles",
    "superme_agent.core.models",
    "superme_agent.core.sandbox",
    "superme_agent.core.titles",
    "superme_agent.core.token_taxonomy",
}


def _modname(p: Path) -> str:
    parts = list(p.relative_to(ROOT).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _resolve(me: str, is_pkg: bool, level: int, module: str | None) -> str:
    """Absolute name for a relative import, counting a package's own `__init__` as level zero."""
    base = me.split(".")
    if not is_pkg:
        base = base[:-1]
    for _ in range(level - 1):
        base = base[:-1]
    return ".".join(base + ([module] if module else []))


def _area(mod: str) -> str:
    parts = mod.split(".")
    return parts[1] if len(parts) > 1 else "<root>"


def graph() -> tuple[dict[str, set[str]], set[str]]:
    """Module-level import edges between modules of this package, and the module set."""
    files = [p for p in sorted(PKG.rglob("*.py")) if not any(x in p.parts for x in SKIP_PARTS)]
    mods = {_modname(p) for p in files}
    edges: dict[str, set[str]] = defaultdict(set)
    for p in files:
        me, is_pkg = _modname(p), p.name == "__init__.py"
        for node in ast.parse(p.read_text()).body:
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom):
                base = _resolve(me, is_pkg, node.level, node.module) if node.level else (node.module or "")
                if base.startswith("superme_agent"):
                    # `from .pkg import x` binds a submodule when one exists, else a symbol
                    # off `pkg/__init__` — only the latter couples them.
                    for a in node.names:
                        targets.append(f"{base}.{a.name}" if f"{base}.{a.name}" in mods else base)
            elif isinstance(node, ast.Import):
                targets = [a.name for a in node.names if a.name.startswith("superme_agent")]
            for t in targets:
                if t in mods and t != me:
                    edges[me].add(t)
    return edges, mods


def _cycles(edges: dict[str, set[str]], mods: set[str]) -> list[list[str]]:
    found: list[list[str]] = []
    seen: set[str] = set()

    def walk(node: str, path: list[str]) -> None:
        if node in path:
            cycle = path[path.index(node):] + [node]
            key = " ".join(sorted(set(cycle)))
            if key not in seen:
                seen.add(key)
                found.append(cycle)
            return
        for nxt in sorted(edges.get(node, ())):
            walk(nxt, path + [node])

    for m in sorted(mods):
        walk(m, [])
    return found


def main() -> int:
    edges, mods = graph()
    failed = False

    cycles = _cycles(edges, mods)
    if cycles:
        failed = True
        print("✗ IMPORT CYCLES:")
        for c in cycles:
            print("    " + " → ".join(c))
    else:
        print(f"✓ acyclic at module load ({len(mods)} modules)")

    violations, stale = [], set(PINNED)
    for src, dsts in sorted(edges.items()):
        src_area = _area(src)
        for dst in sorted(dsts):
            if dst in ROOT_MODULES or _area(dst) in ALLOWED.get(src_area, {src_area}):
                continue
            edge = f"{src} → {dst}"
            stale.discard(edge)
            if edge not in PINNED:
                violations.append(edge)
    if violations:
        failed = True
        print("✗ LAYER VIOLATIONS (an area reaching where it may not):")
        for v in violations:
            print(f"    {v}")
    else:
        print(f"✓ no new layer violations ({len(PINNED)} pinned)")
    if stale:
        failed = True
        print("✗ PINNED EDGES THAT NO LONGER EXIST — drop them from PINNED:")
        for s in sorted(stale):
            print(f"    {s}")

    leaks = sorted(f"{m} → {d}" for m in VOCAB & mods
                   for d in edges.get(m, ()) if d != m and _area(d) == "core")
    missing = sorted(VOCAB - mods)
    if leaks or missing:
        failed = True
        print("✗ VOCABULARY TIER BROKEN:")
        for m in missing:
            print(f"    {m} — declared vocab, no such module")
        for l in leaks:
            print(f"    {l} — vocab may not import the rest of core")
    else:
        print(f"✓ vocabulary tier is a floor ({len(VOCAB)} modules, no upward edge)")

    if "--graph" in sys.argv:
        print("\ncross-area edges:")
        counts: dict[str, int] = defaultdict(int)
        for src, dsts in edges.items():
            for dst in dsts:
                if _area(src) != _area(dst):
                    counts[f"{_area(src)} → {_area(dst)}"] += 1
        for k, v in sorted(counts.items()):
            print(f"    {k:24s} {v}")

    print("————", "LAYERS FAIL" if failed else "LAYERS OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
