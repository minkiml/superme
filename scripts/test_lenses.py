"""The three standing lenses: intent, safety, robustness.

Verification could otherwise prove only what the planner thought to ask for. `depth: none` means
nothing to RUN, never nothing to read, so no cycle is silent about work nobody checked.

Run: PYTHONPATH=. python -m scripts.test_lenses
"""

import tempfile
from pathlib import Path

from superme_agent.core import artifacts as _arts
from superme_agent.daemon.services.loop import decide_after_vet
from scripts.sources import src

PASS = 0

NONE_PLAN = """# Plan — probe

## Verification plan
depth: none
reason: a comment-only change with no observable surface
env: none
"""


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def _item(plan: str = NONE_PLAN) -> Path:
    item = Path(tempfile.mkdtemp(prefix="lens-")) / "item"
    (item / "artifacts").mkdir(parents=True)
    (item / "artifacts" / _arts.artifact_file("plan")).write_text(plan)
    _arts.scaffold_cycle(item)
    return item


def _clean(item: Path, *, robustness: list[dict] | None = None) -> None:
    _arts.record_lens(item, lens="intent", probed="read brief.md § Problem against the diff")
    _arts.record_lens(item, lens="safety", probed="grepped eval/exec/shell=True; read the write paths")
    _arts.record_lens(item, lens="robustness", probed="tried empty string, None, a 1900 date",
                      findings=robustness or [])


def _decide(item: Path, evidence: str = "passed") -> dict:
    return decide_after_vet({"status": "active", "phase": "vet"},
                            evidence={"status": evidence}, fingerprint="fp", attempts=[],
                            spent=0, budget=99_999, lens_gaps=_arts.lens_gaps(item))


# ── the act ─────────────────────────────────────────────────────────────────────────────────────

def test_a_lens_read_needs_a_probe():
    item = _item()
    try:
        _arts.record_lens(item, lens="intent", probed="")
        ok("a lens with no probe record is refused", False)
    except ValueError as e:
        ok("a lens with no probe record is refused", "needs `probed`" in str(e))
        ok("…because it would be indistinguishable from a skipped one", "skipped" in str(e))
    try:
        _arts.record_lens(item, lens="vibes", probed="x")
        ok("an invented lens is refused", False)
    except ValueError as e:
        ok("an invented lens is refused", "unknown lens" in str(e))
    try:
        _arts.record_lens(item, lens="safety", probed="x",
                          findings=[{"severity": "catastrophic", "text": "y"}])
        ok("an invented severity is refused", False)
    except ValueError as e:
        ok("an invented severity is refused", "severity must be one of" in str(e))

    r = _arts.record_lens(item, lens="intent", probed="read the brief against the diff")
    ok("no findings is a complete read", r["findings"] == [] and r["gates"] is False)
    # `probed` is a LIST — one probe per entry — so the reader can count what was actually tried.
    probed = _arts.lens_reads(item)["intent"]["probed"]
    ok("…and what was probed is kept, as a list",
       isinstance(probed, list) and probed and probed[0].startswith("read"))


def test_lens_entries_are_not_verdicts():
    item = _item()
    _clean(item, robustness=[{"severity": "low", "text": "a 1900 date renders oddly"}])
    ok("the verdict ledger stays empty — a lens is not a check",
       _arts.evidence_entries(item) == [])
    ok("…and the reads are all there", set(_arts.lens_reads(item)) ==
       {"intent", "safety", "robustness"})


# ── the gating table ────────────────────────────────────────────────────────────────────────────

def test_who_gates():
    for lens, sev, gates in [("intent", "low", True), ("safety", "low", True),
                             ("safety", "high", True), ("robustness", "medium", False),
                             ("robustness", "high", True), ("performance", "high", False)]:
        item = _item()
        _arts.record_lens(item, lens=lens, probed="probed it",
                          findings=[{"severity": sev, "text": "something"}])
        got = bool(_arts.lens_gaps(item))
        ok(f"{lens} + {sev} {'gates' if gates else 'does not gate'}", got is gates)


def test_a_gating_lens_routes_like_any_other_failure():
    item = _item()
    _clean(item, robustness=[{"severity": "high", "text": "cli.py:31 — a None date crashes"}])
    d = _decide(item)
    ok("green checks do not advance past a gating lens", d["action"] == "build")
    ok("…and there is NO new loop exit for it", "exit" not in d)
    ok("…the reason names the lens, not a check count", "robustness lens" in d["reason"])
    ok("…and it rides the failed list", d["failed"] == ["lens:robustness"])

    # …while a deferred item with a clean read still advances, unchanged.
    clean = _item()
    _clean(clean)
    ok("a clean read leaves the decision alone", _decide(clean)["action"] == "review")
    ok("…including the deferred path", _decide(clean, "deferred")["action"] == "review")


def test_a_repeating_lens_finding_can_exit_as_not_converging():
    """Without this the loop would burn its whole budget rediscovering the same wall."""
    item = _item()
    _clean(item, robustness=[{"severity": "high", "text": "cli.py:31 — a None date crashes"}])
    gaps = _arts.lens_gaps(item)
    fp = _arts.convergence_fingerprint(item, extra=[g["text"] for g in gaps])
    ok("a lens finding produces a fingerprint even with no failing check", fp != "")
    d = decide_after_vet({"status": "active", "phase": "vet"}, evidence={"status": "passed"},
                         fingerprint=fp, attempts=[{"fingerprint": fp}, {"fingerprint": fp}],
                         spent=0, budget=99_999, lens_gaps=gaps)
    ok("the same finding coming back trips the convergence guard",
       d.get("exit") == "not_converging")


# ── depth is a separate axis ────────────────────────────────────────────────────────────────────

def test_depth_none_still_owes_its_lenses():
    item = _item()
    try:
        _arts.write_vet_user_report(item, None)
        ok("a depth:none cycle cannot report with no lens read", False)
    except ValueError as e:
        ok("a depth:none cycle cannot report with no lens read", "has no read this cycle" in str(e))
        ok("…and the refusal says a clean read is a fine answer", "no findings is a fine answer" in str(e))
    _clean(item, robustness=[{"severity": "high", "text": "cli.py:31 — a None date crashes"}])
    text = Path(_arts.write_vet_user_report(item, None)["path"]).read_text()
    # The report keeps vet's prose plus, machine-authored, any finding that GATES: what sends the
    # item back cannot depend on prose.
    ok("a gating finding is machine-authored into the report",
       "## What didn't hold" in text and "a None date crashes" in text
       and "raised by the robustness reading (high)" in text)
    ok("…while what each lens probed stays readable from the record itself",
       "grepped eval/exec" in str(_arts.lens_reads(item)["safety"]["probed"]))
    clean = _item()
    _clean(clean)
    ok("a cycle with no gating finding prints no didn't-hold block",
       "What didn't hold" not in Path(
           _arts.write_vet_user_report(clean, None)["path"]).read_text())


# ── wiring ──────────────────────────────────────────────────────────────────────────────────────

def test_wiring():
    loop = src("superme_agent/daemon/services/loop.py")
    ok("the driver reads the gaps", "_arts.lens_gaps(item_dir)" in loop)
    ok("…folds them into the convergence signature", "extra=[g[\"text\"] for g in gaps]" in loop)
    ok("…and they lead the next build cycle's work order",
       "found.update(" in loop and "_arts.lens_gaps(item_dir)" in loop.split("found.update(")[1][:300])

    tools = src("superme_agent/harness/tools/dev_tools.py")
    ok("vet has a pen for it", '"record_lens"' in tools)
    ok("…and it never prompts a human mid-loop",
       "mcp__dev__record_lens" in src("superme_agent/harness/policy.py"))

    skill = src("superme_agent/harness/plugins/superme-dev/skills/vet/SKILL.md")
    ok("the vet skill carries the three lenses", "three standing lenses" in skill)
    ok("…forbids manufacturing a finding", "Nothing found is the right answer when nothing is wrong" in skill)
    ok("…and says they run under depth: none too",
       "including one whose plan declared `depth: none`" in " ".join(skill.split()))
    # Pinned on STRUCTURE, not the heading: that is owner-facing copy, and an edit must not read
    # as a broken build.
    modal = src("web/frontend/src/features/dev/WorkItemModal.tsx")
    ok("the surface can show them",
       "class LensRead" in src("superme_agent/daemon/schemas/dev/gates.py")
       and "lenses.map((l)" in modal and "l.probed.map" in modal)
    ok("…under the QUESTION each one asks, not its slug",
       "LENS_QUESTION" in modal and all(f"{ln}:" in modal for ln in _arts.STANDING_LENSES))


def main() -> None:
    test_a_lens_read_needs_a_probe()
    test_lens_entries_are_not_verdicts()
    test_who_gates()
    test_a_gating_lens_routes_like_any_other_failure()
    test_a_repeating_lens_finding_can_exit_as_not_converging()
    test_depth_none_still_owes_its_lenses()
    test_wiring()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
