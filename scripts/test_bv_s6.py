"""Handoff promotion into intake.

Only what is NEW since the watermark renders: the latest cycle's report verbatim, older ones
collapsed to one-liners. An absent or garbage watermark reads as zero, never as everything.

Run: PYTHONPATH=. python -m scripts.test_bv_s6
"""

import subprocess
import tempfile
from datetime import date
from pathlib import Path

from superme_agent.core import artifacts as A
from superme_agent.core import kernel_speech as SC
from superme_agent.daemon.app_state import dev as DEV

PASS = 0

PLAN = """---
artifact: plan
---
# Plan — t

## Approach
x

## Tasks
- [x] a

## Inner checks
- `pytest -q`

## Vet plan
depth: checks
reason: two contained checks cover the surface
env: none

### alpha-check
- traces: d-x
- mode: command
- scenario: run the alpha suite
- expect: pytest exits 0 with exactly 3 passed

### beta-check
- traces: d-x
- mode: inspection
- scenario: read the module
- expect: module.py defines beta() returning the literal string 'beta'
"""


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def seed_two_cycles(d: Path, repo: Path) -> None:
    """A realistic two-cycle loop record: cycle 1 FAIL (build hop), cycle 2 PASS (review exit)."""
    A.scaffold_cycle(d, title="t")
    A.record_verification(d, repo, check="alpha-check", how="pytest -q", result="3 passed", passed=True)
    A.record_verification(d, repo, check="beta-check", how="read module.py",
                          result="beta() returns 'BETA' uppercase", passed=False,
                          note="expected 'beta', got 'BETA'")
    A.append_cycle_outcome(d, evidence="failed", decision="build",
                           reason="1 check(s) failed — handing the cycle report to a build cycle",
                           fingerprint="ff1", failed=["beta-check"], tokens=1000, budget=500000)
    A.scaffold_cycle(d, title="t")
    A.record_verification(d, repo, check="alpha-check", how="pytest -q", result="3 passed", passed=True)
    A.record_verification(d, repo, check="beta-check", how="read module.py",
                          result="beta() returns 'beta'", passed=True)
    A.append_cycle_outcome(d, evidence="passed", decision="review",
                           reason="every check green and fresh — advancing to the review gate")


def test_render(tmp: Path, repo: Path) -> None:
    print("render_handoff_block")
    d = tmp / "item-r"
    (d / "artifacts").mkdir(parents=True)
    (d / "artifacts" / "plan.md").write_text(PLAN)

    text, mark = SC.render_handoff_block({"id": "i"}, d)
    ok("no loop activity → nothing to promote", text is None and mark == 0)

    seed_two_cycles(d, repo)
    text, mark = SC.render_handoff_block({"id": "i"}, d)
    ok("new record renders, mark = ledger length", text is not None and mark == 2)
    ok("attribution header present (narrate FROM THE RECORD)",
       "Loop record" in text and "narrate" in text and "FROM THIS RECORD" in text)
    ok("driver decisions in time order",
       text.index("cycle 1 · vet failed · → build") < text.index("cycle 2 · vet passed · → review"),
       text[:800])
    ok("failed checks named on the decision line", "failed: beta-check" in text)
    ok("LATEST cycle's report verbatim",
       "Latest cycle report (build-vet-2.md, verbatim)" in text and "Build⟷vet 2" in text)
    ok("older new cycle collapsed to verdict one-liners",
       "build-vet-1.md checks: alpha-check — PASS; beta-check — FAIL" in text
       and "Build⟷vet 1" not in text)

    text2, mark2 = SC.render_handoff_block({"id": "i", "handoffs_promoted": 2}, d)
    ok("advanced mark → nothing new", text2 is None and mark2 == 2)
    textg, _ = SC.render_handoff_block({"id": "i", "handoffs_promoted": "junk"}, d)
    ok("garbage watermark reads as 0 (promotes everything)", textg is not None)
    text1, mark1 = SC.render_handoff_block({"id": "i", "handoffs_promoted": 1}, d)
    ok("partial mark promotes only the tail (cycle 2, verbatim — no cycle-1 line)",
       mark1 == 2 and "cycle 2" in text1 and "cycle 1 · vet failed" not in text1)

    # A halt with no report at all (unverified fail-closed before any cycle).
    d2 = tmp / "item-noreport"
    (d2 / "artifacts").mkdir(parents=True)
    A.scaffold_cycle(d2, title="t")
    A.append_cycle_outcome(d2, evidence="unverified", decision="halt",
                           reason="vet recorded no evidence — failing closed")
    t3, m3 = SC.render_handoff_block({"id": "i"}, d2)
    ok("halt record renders with its own cycle report",
       t3 is not None and m3 == 1 and "halt" in t3)

    # Caps: a huge findings body must not blow the block.
    d3 = tmp / "item-cap"
    (d3 / "artifacts").mkdir(parents=True)
    (d3 / "artifacts" / "plan.md").write_text(PLAN)
    cy = A.scaffold_cycle(d3, title="t")
    # Target the HEADING, not the slot's prose: a fixture keyed to wording stops testing anything
    # the moment that wording changes.
    Path(cy["path"]).write_text(Path(cy["path"]).read_text().replace(
        "## Built\n", "## Built\n" + "very long build detail " * 800 + "\n"))
    A.record_verification(d3, repo, check="alpha-check", how="pytest", result="ok", passed=True)
    A.record_verification(d3, repo, check="beta-check", how="read", result="broken", passed=False)
    A.append_cycle_outcome(d3, evidence="failed", decision="build", reason="r")
    t4, _ = SC.render_handoff_block({"id": "i"}, d3)
    ok("block honors the total cap", len(t4) <= 12_100 and "truncated" in t4)


def test_watermark(tmp: Path) -> None:
    print("handoffs_promoted watermark writer")
    dev_root = tmp / "devroot"
    d = dev_root / "work-items" / "w1"
    d.mkdir(parents=True)
    today = date.today().isoformat()
    (d / "item.md").write_text(
        f"---\nid: w1\ntitle: t\nkind: implementation\nphase: review\nstatus: active\n"
        f"created_at: {today}\nupdated_at: {today}\n---\nbody\n")
    ok("mark write inserts the field", DEV.set_work_item_handoff_mark(dev_root, "w1", 2) is True)
    it = DEV.read_work_item(dev_root, "w1")
    ok("read-back through read_work_item", int(it.get("handoffs_promoted")) == 2)
    ok("same mark is an idempotent skip", DEV.set_work_item_handoff_mark(dev_root, "w1", 2) is False)
    ok("rewrite updates in place (no duplicate key)",
       DEV.set_work_item_handoff_mark(dev_root, "w1", 5) is True
       and (d / "item.md").read_text().count("handoffs_promoted:") == 1
       and int(DEV.read_work_item(dev_root, "w1")["handoffs_promoted"]) == 5)
    ok("missing item refuses", DEV.set_work_item_handoff_mark(dev_root, "nope", 1) is False)
    ok("frontmatter shape survives (field sits before created_at)",
       (d / "item.md").read_text().index("handoffs_promoted:")
       < (d / "item.md").read_text().index("created_at:"))


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo = make_repo(tmp)
        test_render(tmp, repo)
        test_watermark(tmp)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
