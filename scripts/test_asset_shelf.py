"""The knowledge shelf: what a repo may see, take and read.

An item on the shelf belongs to no project. Three switches gate it, and each reaches a different
distance.

Run: PYTHONPATH=. python -m scripts.test_asset_shelf
"""

import shutil
import tempfile
from pathlib import Path

from superme_agent.core import operational as ops
from superme_agent.paths import LOCAL_HARNESS_DIR
from scripts.sources import src

PASS = 0

HUB_HOME = LOCAL_HARNESS_DIR / "global" / "dev" / "constitution"
GUEST_HOME = LOCAL_HARNESS_DIR / "a-guest-project" / "dev" / "constitution"


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def item(name: str, *, enabled: str = "true", hub_only: str | None = None) -> str:
    fm = [f"name: {name}", f"description: {name} for testing", f"enabled: {enabled}"]
    if hub_only is not None:
        fm.append(f"hub-only: {hub_only}")
    return "---\n" + "\n".join(fm) + f"\n---\n\n# {name}\n\nA body.\n"


def shelf() -> Path:
    """A throwaway pool: one open item, one restricted, one withdrawn, one in a subfolder."""
    d = Path(tempfile.mkdtemp(prefix="shelf-"))
    (d / "open-one.md").write_text(item("open-one"), encoding="utf-8")
    (d / "restricted-one.md").write_text(item("restricted-one", hub_only="true"), encoding="utf-8")
    (d / "withdrawn-one.md").write_text(item("withdrawn-one", enabled="false"), encoding="utf-8")
    (d / "authoring").mkdir()
    (d / "authoring" / "nested-one.md").write_text(item("nested-one"), encoding="utf-8")
    return d


def slugs(repo_dir, d: Path) -> set[str]:
    return {it["slug"] for it in ops.available_assets(repo_dir, d)}


def test_the_shelf_is_a_tree():
    d = shelf()
    try:
        pool = ops.read_asset_pool(d)
        ok("a subfolder is read", "nested-one" in {it["slug"] for it in pool})
        nested = next(it for it in pool if it["slug"] == "nested-one")
        ok("…and its slug is the filename, not the path", "/" not in nested["slug"])
        ok("a shelf item carries no `scope`", "scope" not in nested)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_restriction_is_about_who_may_take_it():
    d = shelf()
    try:
        ok("the engine's own cell sees a restricted item", "restricted-one" in slugs(HUB_HOME, d))
        ok("…and no other project does", "restricted-one" not in slugs(GUEST_HOME, d))
        ok("…while an open item is open to both",
           {"open-one"} <= slugs(GUEST_HOME, d) & slugs(HUB_HOME, d))
        ok("a repo with no cell at all sees only the open items",
           "restricted-one" not in slugs(None, d))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_withdrawal_reaches_every_repo():
    d = shelf()
    try:
        ok("a withdrawn item is offered to nobody",
           "withdrawn-one" not in slugs(HUB_HOME, d) | slugs(GUEST_HOME, d))
        ok("…and it is still on the shelf, not deleted",
           "withdrawn-one" in {it["slug"] for it in ops.read_asset_pool(d)})
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_muted_item_keeps_its_adoption_record():
    d = shelf()
    repo = Path(tempfile.mkdtemp(prefix="repo-"))
    try:
        ops.adopt_repo_assets(repo, ["open-one", "withdrawn-one"], asset_dir=d)
        ok("adoption refuses what the shelf withdrew",
           ops.repo_asset_states(repo) == {"open-one": True})
        (repo / ".assets").write_text("open-one\nwithdrawn-one\n", encoding="utf-8")
        active = ops.list_repo_assets(repo)
        got = {it["slug"] for it in ops._activated_asset_items(active, repo, d)}
        ok("a withdrawn item contributes nothing to a repo that already had it",
           got == {"open-one"})
        ok("…and its line survives, so turning it back on restores the repo exactly",
           "withdrawn-one" in ops.repo_asset_states(repo))
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(repo, ignore_errors=True)


def test_ranking_never_proposes_what_cannot_be_adopted():
    d = shelf()
    try:
        spec = "restricted-one withdrawn-one open-one testing"
        ranked = {r["slug"] for r in ops.rank_assets_by_relevance(spec, repo_dir=GUEST_HOME,
                                                                 asset_dir=d)}
        ok("a project is never offered a restricted item", "restricted-one" not in ranked)
        ok("…nor a withdrawn one", "withdrawn-one" not in ranked)
        ok("…and the open ones still rank", "open-one" in ranked)
        ok("the engine's own cell does see the restricted one",
           "restricted-one" in {r["slug"] for r in ops.rank_assets_by_relevance(
               spec, repo_dir=HUB_HOME, asset_dir=d)})
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_three_standards_ship_and_the_hub_holds_them():
    names = {"skill-authoring", "tool-authoring", "comment-style"}
    pool = {it["slug"]: it for it in ops.read_asset_pool()}
    ok("all three standards are on the shelf", names <= set(pool))
    ok("…each restricted to the engine", all(pool[n]["hub_only"] for n in names))
    ok("…and adopted by it", names <= ops.list_repo_assets(HUB_HOME))
    ok("…and offered to it", names <= slugs(HUB_HOME, None))
    ok("no other project may take them", not (names & slugs(GUEST_HOME, None)))


def test_wiring():
    ok("the shelf has a contract gate", Path("scripts/asset_contract.py").is_file())
    ok("…that the fast gate runs", "scripts.asset_contract" in src("scripts/check_fast.sh"))
    gate = src("scripts/skill_contract.py")
    ok("no gate reads the owner's workshop", "general_docs" not in gate)
    ok("the skills standard's two tracked copies are checked against each other",
       "STANDARD_ASSET" in gate and "SKILL_PRINCIPLES" in gate)
    ok("…with the shelf item as the source", "--sync" in gate)
    fe = src("web/frontend/src/features/config/sections/ProjectArtifacts.tsx")
    ok("the dashboard drops an unusable asset from the adopted list", "onOffer(a)" in fe)
    ok("…and from the picker, which offers only what this project may take",
       "assets.filter(onOffer)" in fe)
    ok("…and counts what is available against what is adopted", "available ·" in fe)
    ok("an asset renders its body on click, and from the picker without adopting it",
       "AssetModal" in fe and "onView(a)" in fe)
    router = src("superme_agent/daemon/routers/dev/harness.py")
    ok("the API refuses to adopt what is not on offer", "is not on offer to this project" in router)
    ok("the publish stamp writes no dead field",
       'with_frontmatter_default(content, "scope"' not in src("superme_agent/core/operational.py"))


def main() -> None:
    test_the_shelf_is_a_tree()
    test_restriction_is_about_who_may_take_it()
    test_withdrawal_reaches_every_repo()
    test_a_muted_item_keeps_its_adoption_record()
    test_ranking_never_proposes_what_cannot_be_adopted()
    test_the_three_standards_ship_and_the_hub_holds_them()
    test_wiring()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
