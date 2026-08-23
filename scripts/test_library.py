"""The per-repo verification library, and the governance it rests on.

Vet nominates a check that has actually passed, close writes it, the owner promotes it. An entry
carrying item specifics is refused at the write, where someone is still looking.

Run: PYTHONPATH=. python -m scripts.test_library
"""

import tempfile
from pathlib import Path

from superme_agent.core import artifacts as _arts
from superme_agent.core import knowledge_delta as _kd
from superme_agent.core import verification_library as _vl
from scripts.sources import src

PASS = 0

ENTRY = """### older-ledgers-read
- proves: a ledger written by an older version of the tool still reads without migration
- traces: every deliverable depends on the on-disk format staying readable
- mode: command
- scenario: read a fixture ledger written by the previous release
- run: python -m pytest -q tests/test_compat.py::test_reads_v1_ledger
- expect: exit 0 with no failures
"""

PLAN = """# Plan — probe

## Verification plan
depth: checks
reason: probing the library
env: none

### older-ledgers-read
- proves: a ledger written by an older version of the tool still reads without migration
- traces: every deliverable depends on the on-disk format staying readable
- covers: t1
- mode: command
- scenario: read a fixture ledger written by the previous release
- run: python -m pytest -q tests/test_compat.py::test_reads_v1_ledger
- expect: exit 0 with no failures

### date-flag
- proves: an expense recorded with an explicit date keeps that date
- traces: user story u-1
- mode: command
- scenario: add an expense with an explicit date
- expect: the row lands with the date given, not today
"""


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def _root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="lib-"))
    _vl.seed(root)
    return root


def _stocked(tier: str = "available") -> Path:
    root = _root()
    _kd.apply_ops(root, [{"doc": _vl.LIBRARY_DOC, "section": _vl._SECTION[tier],
                          "op": "append", "content": ENTRY}])
    return root


def _item() -> Path:
    item = Path(tempfile.mkdtemp(prefix="lib-item-")) / "item"
    (item / "artifacts").mkdir(parents=True)
    (item / "artifacts" / _arts.artifact_file("plan")).write_text(PLAN, encoding="utf-8")
    _arts.scaffold_cycle(item, title="probe")
    return item


# ── the doc ─────────────────────────────────────────────────────────────────────────────────────

def test_a_repo_with_no_library_is_not_an_error():
    root = Path(tempfile.mkdtemp(prefix="lib-bare-"))
    ok("an absent library reads as empty, both tiers",
       _vl.read_library(root) == {"standing": [], "available": []})
    ok("…and the knowledge lint does not nag about it",
       not [f for f in __import__("superme_agent.core.dev_knowledge", fromlist=["x"])
            .DevKnowledgeService().lint_general(root)["findings"]
            if f["ref"] == _vl.LIBRARY_DOC])
    ok("it is created on first write, not at connect", _vl.seed(root) is True)
    ok("…and seeding twice is a no-op", _vl.seed(root) is False)


def test_an_entry_is_a_plan_check():
    root = _stocked()
    e = _vl.read_library(root)["available"][0]
    ok("the entry parses with the plan's own field names", e["id"] == "older-ledgers-read")
    ok("…including the kernel's run block", e["run"] == "python -m pytest -q tests/test_compat.py::test_reads_v1_ledger")
    ok("…and carries its tier", e["tier"] == "available")
    ok("one grammar, so inheriting is a copy and not a translation",
       _arts.parse_check_blocks(ENTRY)[0]["scenario"] == e["scenario"])


# ── the write bar ───────────────────────────────────────────────────────────────────────────────

def test_an_item_shaped_entry_is_refused():
    root = _root()

    def issues(content: str) -> list[str]:
        return _kd.validate_ops([{"doc": _vl.LIBRARY_DOC, "section": "Available",
                                  "op": "append", "content": content}], root, None)

    ok("a clean entry passes", issues(ENTRY) == [])
    ok("`covers` is refused — it names tasks no later item has",
       any("drop `covers`" in i for i in issues(ENTRY + "- covers: t1\n")))
    ok("a task id anywhere in the text is refused",
       any("t<n>" in i for i in issues(ENTRY.replace("previous release", "previous release for t3"))))
    ok("a work-item id is refused",
       any("work-item id" in i for i in issues(ENTRY.replace("fixture ledger", "fixture ledger 77720f784ded"))))
    bare = issues("### thing\n- traces: t\n- mode: command\n- scenario: s\n")
    ok("an entry with no way to fail is refused", any("no way to fail" in i for i in bare))
    ok("prose that is not an entry at all is refused",
       any("`### <entry-id>` block" in i for i in issues("we should test the thing")))


def test_the_library_doc_is_written_by_the_same_one_writer():
    root = _root()
    _kd.apply_ops(root, [{"doc": _vl.LIBRARY_DOC, "section": "Available", "op": "append",
                          "content": ENTRY}])
    ok("close's delta writer reaches the library like any anchor doc",
       [e["id"] for e in _vl.read_library(root)["available"]] == ["older-ledgers-read"])
    ok("…and the doc is in the anchor set, so the owner reads it where they read the others",
       _vl.LIBRARY_DOC in __import__("superme_agent.core.dev_knowledge",
                                     fromlist=["x"]).ANCHOR_DOCS)


# ── nomination: the one rule with teeth ─────────────────────────────────────────────────────────

def test_only_a_check_that_passed_may_be_nominated():
    item = _item()
    try:
        _arts.record_nomination(item, check="older-ledgers-read", general="this repo must stay readable across releases")
        ok("nominating a check with no verdict is refused", False)
    except ValueError as e:
        ok("nominating a check with no verdict is refused", "never passed" in str(e))
        ok("…and says why an untested entry is worse than none", "costs the next item" in str(e))

    _arts.record_verification(item, None, check="older-ledgers-read", how="ran it", result="exit 1",
                              passed=False)
    try:
        _arts.record_nomination(item, check="older-ledgers-read", general="this repo must stay readable across releases")
        ok("a failing check cannot be nominated either", False)
    except ValueError:
        ok("a failing check cannot be nominated either")

    _arts.record_verification(item, None, check="older-ledgers-read", how="ran it", result="exit 0",
                              passed=True)
    n = _arts.record_nomination(item, check="older-ledgers-read",
                                general="this repo's compatibility must stay green on every change")
    ok("a passing check may be nominated", n["check"] == "older-ledgers-read")
    ok("…and the claim it makes about the repo is kept",
       _arts.nominations(item)["older-ledgers-read"]["general"].startswith("this repo's compatibility"))


def test_a_nomination_is_not_a_verdict():
    item = _item()
    _arts.record_verification(item, None, check="older-ledgers-read", how="ran", result="exit 0",
                              passed=True)
    _arts.record_nomination(item, check="older-ledgers-read", general="this repo must stay readable across releases")
    ok("the verdict ledger still holds one entry", len(_arts.evidence_entries(item)) == 1)
    ok("…and the check has exactly one row", len(_arts.verdict_rows(item)) == 1)


# ── inheritance: the kernel attaches, the planner cites ──────────────────────────────────────────

def test_standing_entries_are_attached_by_the_kernel():
    root = _stocked("standing")
    blocks = _vl.standing_blocks(root)
    ok("a standing entry comes back plan-ready", len(blocks) == 1)
    ok("…marked so the plan gate can tell it from an authored check", "source: standing" in blocks[0])
    ok("…and WITHOUT covers, which belonged to the item that proved it", "covers" not in blocks[0])

    d = Path(tempfile.mkdtemp(prefix="lib-scaf-")) / "item"
    d.mkdir(parents=True)
    r = _arts.scaffold(d, "plan", title="T", item_kind="implementation", item_id="abc",
                       standing=blocks)
    ok("the scaffold reports what it attached", r["inherited"] == 1)
    vp = _arts.parse_vet_plan((d / "artifacts" / "plan.md").read_text(encoding="utf-8"))
    got = next(c for c in vp["checks"] if c["id"] == "older-ledgers-read")
    ok("the check is in the item's OWN plan — one plan, one exam", got["run"] == "python -m pytest -q tests/test_compat.py::test_reads_v1_ledger")
    ok("…and reads as inherited", got["source"] == "standing")

    d2 = Path(tempfile.mkdtemp(prefix="lib-scaf2-")) / "item"
    d2.mkdir(parents=True)
    ok("a repo with an empty library attaches nothing",
       _arts.scaffold(d2, "plan", title="T", item_kind="implementation",
                      item_id="abc", standing=[])["inherited"] == 0)


def test_an_inherited_check_still_has_to_clear_the_plan_gate():
    root = _stocked("standing")
    d = Path(tempfile.mkdtemp(prefix="lib-gate-")) / "item"
    d.mkdir(parents=True)
    _arts.scaffold(d, "plan", title="T", item_kind="implementation", item_id="abc",
                   standing=_vl.standing_blocks(root))
    text = (d / "artifacts" / "plan.md").read_text(encoding="utf-8")
    # Only the inherited check — the template's own example slot is a separate, unfilled concern.
    vp = _arts.parse_vet_plan(text)
    vp["checks"] = [c for c in vp["checks"] if c["id"] == "older-ledgers-read"]
    vp.update(depth="checks", reason="r", env="none")
    ok("an inherited check passes the same structural gate as an authored one",
       _arts.vet_plan_hard_issues(vp) == [])


# ── the owner's lever ───────────────────────────────────────────────────────────────────────────

def test_only_a_move_promotes_and_the_prose_survives_it():
    root = _stocked()
    ok("promote moves it across", _vl.move_entry(root, "older-ledgers-read", "standing") is True)
    lib = _vl.read_library(root)
    ok("…out of available", lib["available"] == [])
    ok("…and into standing", [e["id"] for e in lib["standing"]] == ["older-ledgers-read"])
    ok("the owner's own prose under each heading survives the move",
       "taxes" in _vl.read_doc(root) and "This is where a nomination" in _vl.read_doc(root))
    ok("demote sends it back", _vl.move_entry(root, "older-ledgers-read", "available") is True
       and [e["id"] for e in _vl.read_library(root)["available"]] == ["older-ledgers-read"])
    ok("drop removes it entirely", _vl.drop_entry(root, "older-ledgers-read") is True
       and _vl.entries(root) == [])
    ok("moving something that isn't there is a no, not a crash",
       _vl.move_entry(root, "ghost", "standing") is False
       and _vl.drop_entry(root, "ghost") is False)
    try:
        _vl.move_entry(root, "older-ledgers-read", "everywhere")
        ok("an unknown tier is refused", False)
    except ValueError:
        ok("an unknown tier is refused")


# ── wiring ──────────────────────────────────────────────────────────────────────────────────────

def test_close_actually_has_a_way_to_write_the_doc():
    # Vet nominated and close read it back, then had no legal write: the doc the constitution
    # names close as the writer of was unwritable.
    tools = src("superme_agent/harness/tools/dev_tools.py")
    ok("the delta tool's doc enum offers the library",
       '"roadmap", "resources", "verification"' in tools)

    dev = _root()
    block = ("### money-never-loses-precision\n"
             "- proves: an amount never renders with more than two decimal places\n"
             "- traces: every deliverable formats money somewhere\n"
             "- mode: command\n"
             "- scenario: render every amount shape the tool accepts\n"
             "- run: python3 -m pytest -q tests/test_format.py -k money\n"
             "- expect: exit 0 with no failures\n")
    ops = [{"doc": _vl.LIBRARY_DOC, "section": "Available", "op": "append", "content": block}]
    ok("a nomination-shaped op validates", not _kd.validate_ops(ops, dev, None))
    ok("…and applies to the library doc", _kd.apply_ops(dev, ops)["applied"] == 1)
    lib = _vl.read_library(dev)
    ok("…landing in Available, never Standing",
       [e["id"] for e in lib["available"]] == ["money-never-loses-precision"] and not lib["standing"])
    ok("…and the section's own prose survives above it",
       "This is where a nomination" in _vl.read_doc(dev))


def test_wiring():
    tools = src("superme_agent/harness/tools/dev_tools.py")
    ok("vet has a pen for the nomination", '"nominate_check"' in tools)
    ok("…and it never prompts a human mid-loop",
       "mcp__dev__nominate_check" in src("superme_agent/harness/policy.py"))
    ok("plan and close read the library through one tool", '"read_verification_library"' in tools)
    # Registered and wired into two skills but never allowlisted, so every call was denied.
    ok("…and reading it never needs a human — a background close would be denied otherwise",
       "mcp__dev__read_verification_library" in src("superme_agent/harness/policy.py"))
    ok("the scaffold attaches the standing entries itself", "standing=_vl.standing_blocks" in tools)
    ok("close's write seeds the doc when the repo has never had one", "_vl.seed(Path(dev_root))" in tools)

    plan_skill = src("superme_agent/harness/plugins/superme-dev/skills/plan/SKILL.md")
    ok("the plan skill says to look before authoring", "read_verification_library" in plan_skill)
    ok("…and to leave the standing entries alone", "leave them exactly as they are" in " ".join(plan_skill.split()))
    vet_skill = src("superme_agent/harness/plugins/superme-dev/skills/vet/SKILL.md")
    ok("the vet skill carries the nomination duty", "nominate_check" in vet_skill)
    # Rarity alone read as "don't", so the skill names the SHAPE to look for — a signal the plan
    # already carries.
    ok("…and names the shape that is usually repo-wide", "empty `covers:`" in vet_skill)
    close_skill = src("superme_agent/harness/plugins/superme-dev/skills/close/SKILL.md")
    ok("close knows it is the writer", "read_verification_library(item_id)" in close_skill)
    ok("…and that entries land available, never standing", "only the owner promotes" in close_skill)

    # The anchor-set CONTRACT has to know the doc exists, or an agent reads a tree that omits it
    # and infers the file is stray.
    con = src("superme_agent/harness/constitution/dev/dev-knowledge-structure.md")
    ok("the dev-knowledge constitution lists it in the tree", "verification.md" in con)
    ok("…and says it is machine-maintained, not hand-authored", "never hand-author" in con)
    guide = src("superme_agent/harness/plugins/superme-dev/general-dev-knowledge-asset/"
                "verification.md")
    ok("it has a per-file authoring guide like every other anchor doc",
       "Authoring contract" in guide)
    ok("…whose first instruction is that you do not author it",
       "You do not author this doc" in guide)
    for skill in ("project-init", "retrofit"):
        ok(f"{skill} is told not to seed it — a fresh repo has proven nothing",
           "not yours to write" in src(f"superme_agent/harness/plugins/superme-dev/skills/"
                                       f"{skill}/SKILL.md"))
    ok("the plan-check contract documents the inheritance marker",
       "`source:`" in src("superme_agent/harness/plugins/superme-dev/references/artifacts.md"))

    routes = src("superme_agent/daemon/routers/dev/general.py")
    ok("the owner can read the library", '"/dev/verification"' in routes)
    ok("…promote or demote one entry", '"/dev/verification/{entry_id}"' in routes)
    ok("the surface renders both tiers with the owner's lever",
       "moveLibraryEntry" in src("web/frontend/src/features/config/sections/ProjectArtifacts.tsx"))


def main() -> None:
    test_a_repo_with_no_library_is_not_an_error()
    test_an_entry_is_a_plan_check()
    test_an_item_shaped_entry_is_refused()
    test_the_library_doc_is_written_by_the_same_one_writer()
    test_only_a_check_that_passed_may_be_nominated()
    test_a_nomination_is_not_a_verdict()
    test_standing_entries_are_attached_by_the_kernel()
    test_an_inherited_check_still_has_to_clear_the_plan_gate()
    test_only_a_move_promotes_and_the_prose_survives_it()
    test_close_actually_has_a_way_to_write_the_doc()
    test_wiring()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
