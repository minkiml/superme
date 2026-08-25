"""The knowledge shelf's writing contract.

A slug is unique across the tree. A description routes. Every field carries a value something
reads.

Run: PYTHONPATH=. python -m scripts.asset_contract
"""

import sys
from pathlib import Path

from superme_agent.core.operational import parse_frontmatter
from superme_agent.paths import ASSET_DIR, CONSTITUTION_DIR

DESC_MIN, DESC_MAX = 50, 160
BOOLEANS = ("true", "false")
# Deleted fields. Both named a thing nothing read, and both collide with a live meaning elsewhere.
DEAD_FIELDS = ("scope", "category")

FAILED: list[str] = []


def fail(item: str, rule: str, detail: str = "") -> None:
    FAILED.append(f"{item}: {rule}" + (f" — {detail}" if detail else ""))


def check_item(path: Path, meta: dict, body: str) -> list[tuple[str, str]]:
    """One shelf file against the contract. The frontmatter is the whole routing decision."""
    bad = []
    if (meta.get("name") or path.stem) != path.stem:
        bad.append(("name must equal the filename", f"{meta.get('name')} vs {path.stem}"))
    desc = (meta.get("description") or "").strip()
    if not desc:
        bad.append(("no description", "it is the catalog line and the whole routing decision"))
    elif not DESC_MIN <= len(desc) <= DESC_MAX:
        bad.append(("description outside the band", f"{len(desc)} not in {DESC_MIN}–{DESC_MAX}"))
    for field in ("enabled", "hub-only"):
        if field in meta and str(meta[field]).strip().lower() not in BOOLEANS:
            bad.append((f"`{field}` is not a boolean", str(meta[field])))
    if not body.strip():
        bad.append(("empty body", "the loader skips it, so it would never appear"))
    return bad


def main() -> None:
    files = sorted(ASSET_DIR.rglob("*.md")) if ASSET_DIR.is_dir() else []
    seen: dict[str, Path] = {}
    passed = 0
    for f in files:
        if f.name.upper() == "README.MD":
            continue
        meta, body = parse_frontmatter(f.read_text(encoding="utf-8"))
        rel = f.relative_to(ASSET_DIR)
        problems = check_item(f, meta, body)
        slug = meta.get("name") or f.stem
        if (first := seen.get(slug)) is not None:
            problems.append(("duplicate slug", f"also {first.relative_to(ASSET_DIR)}"))
        else:
            seen[slug] = f
        for rule, detail in problems:
            fail(str(rel), rule, detail)
        if not problems:
            passed += 1
            print(f"  ok  {rel}")

    # A dead field left in a file reads as live to the next author.
    homes = [d for d in (ASSET_DIR, CONSTITUTION_DIR) if d.is_dir()]
    for home in homes:
        for f in sorted(home.rglob("*.md")):
            meta, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
            for field in DEAD_FIELDS:
                if field in meta:
                    fail(str(f.name), f"`{field}` was deleted", "remove the line")

    print()
    if FAILED:
        print(f"✗ ASSET CONTRACT — {len(FAILED)} finding(s):")
        for line in FAILED:
            print(f"    - {line}")
        sys.exit(1)
    print(f"✓ every asset on contract ({passed} assets)")


if __name__ == "__main__":
    main()
