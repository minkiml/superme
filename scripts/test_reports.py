"""The two specialised reports: the plan's and the cycle's.

The confirmation table is DERIVED, so a task nothing defends is named at the gate rather
than three cycles later. The cycle report names provenance: executed and attested differ.

Run: PYTHONPATH=. python -m scripts.test_reports
"""

import re as _re
import tempfile
from pathlib import Path

from superme_agent.core import artifacts as _arts

PASS = 0
ROOT = Path(__file__).resolve().parents[1]

PLAN = """# Plan — probe

## Tasks
- [ ] t1 — add the --date flag
- [ ] t2 — persist the given date

## Verification plan
depth: checks
reason: user-visible behaviour with a real failure mode
env: none

### date-flag
- proves: an expense recorded with an explicit date keeps that date
- traces: user story u-1
- covers: t1
- mode: command
- scenario: add an expense with an explicit date
- expect: the row lands with the date given, not today

### suite-green
- proves: nothing that already worked in this project stopped working
- traces: the suite guards every deliverable here
- mode: command
- scenario: run the suite from the repo root
- run: python -m pytest -q
- expect: exit 0 with no failures
- source: standing
"""


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def src(rel: str) -> str:
    return (ROOT / rel).read_text()


def _item(plan: str = PLAN) -> Path:
    item = Path(tempfile.mkdtemp(prefix="rep-")) / "item"
    (item / "artifacts").mkdir(parents=True)
    (item / "artifacts" / _arts.artifact_file("plan")).write_text(plan)
    return item


def _report(item: Path, **kw) -> str:
    return Path(_arts.write_plan_user_report(item, **{"summary": "s", **kw})["path"]).read_text()


def _lenses(d: Path) -> None:
    for ln in _arts.STANDING_LENSES:
        _arts.record_lens(d, lens=ln, probed="read the diff through this lens")


# ── the plan report ─────────────────────────────────────────────────────────────────────────────

def test_the_table_says_what_each_check_MEANS_not_what_it_runs():
    """The row is the check's own `proves:` line, verbatim. Deriving the meaning here instead would
    be a second account of the same exam, free to drift from the one the vetter reads."""
    item = _item()
    text = _report(item)
    ok("a row leads with what will be TRUE, in the owner's terms",
       "| an expense recorded with an explicit date keeps that date |" in text)
    ok("…and never with the command", "pytest" not in text and "--date flag" not in text)
    ok("the right column says how it will be known",
       "| an expense recorded with an explicit date keeps that date | run for real |" in text)
    ok("a kernel-run check says so BEFORE the owner approves the exam",
       "run for real · by SuperMe, not agent-claimed" in text)
    ok("an inherited check is marked as one the project already owned",
       "a check this project already owned" in text)


def test_the_row_is_never_clipped_mid_meaning():
    """The `proves:` line IS the row.

    Clipped to keep the column tidy, the first real item lost its meaning mid-word. A markdown
    cell wraps; a truncated sentence does not recover."""
    long_proof = ("an expense recorded with an explicit date keeps that date, even when it is "
                  "entered weeks later and lands in a month that is already closed")
    text = _report(_item(PLAN.replace(
        "- proves: an expense recorded with an explicit date keeps that date",
        f"- proves: {long_proof}")))
    ok("a long sentence reaches the owner whole", f"| {long_proof} |" in text)
    table = [ln for ln in text.splitlines() if ln.startswith("| ")]
    ok("…and no row in the table trails off", table and not any("…" in ln for ln in table))


def test_a_task_nothing_defends_is_named_under_the_table():
    """An empty cell reads as formatting. A sentence naming the work reads as a hole."""
    item = _item()
    text = _report(item)
    ok("the undefended task is named, in its own words",
       "**Nothing will prove:** persist the given date" in text)
    ok("…and the reader is told what to do about it at the gate",
       "the gate is where to say so" in text)
    covered = _report(_item(PLAN.replace("- covers: t1", "- covers: t1, t2")))
    ok("a fully defended plan says nothing at all", "Nothing will prove" not in covered)
    long_task = "- [ ] t1 — " + "add the flag to every subparser we own " * 4
    long_text = _report(_item(PLAN.replace("- [ ] t1 — add the --date flag", long_task)
                              .replace("- covers: t1\n", "")))
    ok("a long task name is trimmed at a WORD boundary, so a trim reads as a trim",
       "…" in long_text.split("**Nothing will prove:**")[1].splitlines()[0])


def test_nothing_factual_is_the_agent_s_to_assert():
    item = _item()
    r = _arts.write_plan_user_report(item, summary="s")
    ok("the writer reports the gap back to the planner", r["uncovered"] == ["t2"])
    ok("…and the counts come from the plan", (r["tasks"], r["checks"]) == (2, 2))
    ok("a fully defended plan reports no gap",
       _arts.write_plan_user_report(
           _item(PLAN.replace("- covers: t1", "- covers: t1, t2")),
           summary="s")["uncovered"] == [])
    try:
        _arts.write_plan_user_report(_item("# Plan\n\n## Verification plan\ndepth: none\n"),
                                     summary="s")
        ok("a plan with no tasks is refused", False)
    except ValueError as e:
        ok("a plan with no tasks is refused", "no `## Tasks`" in str(e))
        ok("…because an empty report would read as 'nothing needs proving'",
           "nothing needs proving" in str(e))
    # An omitted optional slot arrives as None, and the contract tells the planner to omit it.
    # Doing as told crashed the writer.
    omitted = Path(_arts.write_plan_user_report(
        _item(), summary="s", approach=None, confirm=None,
        decisions=None, assumptions=None)["path"]).read_text()
    ok("an omitted optional slot writes the report instead of raising", "**Stats:**" in omitted)
    ok("…and leaves no empty block behind it",
       "**Decisions:**" not in omitted and "**Assumptions:**" not in omitted)
    # The template owns the headings: an author who repeats one is being tidy, and it renders
    # twice.
    echoed = Path(_arts.write_plan_user_report(
        _item(), summary="s", approach="## Approach\n- the real body")["path"]).read_text()
    ok("a slot that echoes its own heading renders it once",
       echoed.count("## Approach") == 1 and "- the real body" in echoed)


def test_a_research_item_gets_the_other_shape():
    """No verification plan means no table to render, and every task would otherwise read as a
    hole. What replaces the table is how the answer will be made trustworthy."""
    res = _item("# Plan\n\n## Tasks\n- [ ] t1 — time it at four sizes\n")
    text = Path(_arts.write_plan_user_report(
        res, summary="Measure where the time goes.", approach="- where the time goes",
        confirm="Real runs at four sizes.", item_kind="research")["path"]).read_text()
    ok("it asks what we're trying to find out, not what will be built",
       "## What we're trying to find out" in text and "## Approach" not in text)
    ok("the confirmation table is gone with the verification plan it was rendered from",
       "how I'll know" not in text)
    ok("…and no task is reported as undefended, since none could be",
       "Nothing will prove" not in text and "check(s)" not in text)


def test_an_empty_block_is_dropped_rather_than_printed():
    item = _item()
    bare = _report(item)
    ok("no Decisions & Assumptions heading when there is neither",
       "Decisions & Assumptions" not in bare)
    ok("no Changed-since anywhere — a user report is current-state, never a diff",
       "Changed since" not in bare)
    ok("…and no blank-line scar where a block would have been", "\n\n\n" not in bare)
    both = _report(item, decisions="- ship without the CSV path (Owner)",
                   assumptions="- nobody scripts the old output (Agent)")
    ok("…and each appears once it has something real",
       "## Decisions & Assumptions" in both and "**Decisions:**" in both
       and "**Assumptions:**" in both)
    only_assumed = _report(item, assumptions="- nobody scripts the old output (Agent)")
    ok("assumptions alone still get the heading they live under",
       "## Decisions & Assumptions" in only_assumed and "**Decisions:**" not in only_assumed)



# ── the shape every user-facing report shares ───────────────────────────────────────────────────

REPORT_TEMPLATES = {
    "triage":       "triage/templates/report-triage-template.md",
    "plan":         "plan/templates/report-plan-template.md",
    "plan (research)": "plan/templates/report-plan-research-template.md",
    "build":        "build/templates/report-build-template.md",
    "vet":          "vet/templates/report-vet-template.md",
    "investigate":  "investigate/templates/report-investigate-template.md",
    "review":       "review/templates/report-review-template.md",
    "close":        "close/templates/report-close-template.md",
}


def test_every_user_facing_report_opens_with_one_summary_line():
    """The phase card renders the CURRENT phase's summary line and nothing else.

    A report without one leaves the card blank, so the line is a contract, not a convention."""
    for phase, rel in REPORT_TEMPLATES.items():
        # Comments stripped first: an authoring note instructs the writer, never the document.
        body = _re.sub(r"<!--.*?-->", "", src(
            "superme_agent/harness/plugins/superme-dev/skills/" + rel), flags=_re.DOTALL)
        ok(f"{phase}: opens with a Summary line", "**Summary:**" in body)
        head = [ln for ln in body.splitlines() if ln.startswith("# ")]
        ok(f"{phase}: …directly under the document title, before any section",
           bool(head) and body.index("**Summary:**") > body.index(head[0])
           and "## " not in body[:body.index("**Summary:**")])


def test_no_user_facing_report_carries_a_diff_section():
    """User reports are CURRENT-STATE; the append-only history lives in the agent-facing docs.

    A "changed since" section makes every reader reconstruct the present from a delta they did
    not ask for."""
    for phase, rel in REPORT_TEMPLATES.items():
        body = src("superme_agent/harness/plugins/superme-dev/skills/" + rel)
        ok(f"{phase}: no Changed-since section", "## Changed since" not in body)
    review = src("superme_agent/harness/plugins/superme-dev/skills/review/templates/"
                 "report-review-template.md")
    ok("review answers a send-back at the TOP, not in a trailing delta",
       "## What you asked for" in review
       and review.index("## What you asked for") < review.index("## What you're approving"))
    record = src("superme_agent/harness/plugins/superme-dev/skills/review/templates/"
                 "review-template.md")
    ok("…and the history it drops is kept, append-only, in the agent-facing record",
       "## Revision rounds" in record)


def test_no_report_repeats_the_work_item_title():
    """The drilldown header carries the title three inches above. A `# Plan — {title}` heading
    spent the report's first line saying what the reader was already looking at."""
    for phase, rel in REPORT_TEMPLATES.items():
        body = src("superme_agent/harness/plugins/superme-dev/skills/" + rel)
        head = next((ln for ln in body.splitlines() if ln.startswith("# ")), "")
        ok(f"{phase}: the title names the DOCUMENT, not the item",
           "{title}" not in head and "<fill:" not in head)


# ── the cycle report ────────────────────────────────────────────────────────────────────────────

def test_the_check_table_names_who_actually_ran_it():
    item = _item()
    _arts.scaffold_cycle(item)
    _arts.record_verification(item, None, check="suite-green", how="kernel ran `run:`",
                              result="exit 0", passed=True, by=_arts.BY_MACHINE)
    _arts.record_verification(item, None, check="date-flag", how="ran it by hand",
                              result="the date lands", passed=True)
    _lenses(item)
    _arts.write_vet_user_report(item, None)
    # The per-check table left the vet REPORT, which read as a second copy of build's self-report.
    # Provenance rides the Proof row instead.
    by = {v["check"]: v["by"] for r in _arts.proof_rows(item) for v in r["verified"]}
    ok("a kernel-executed check is still marked machine", by.get("suite-green") == _arts.BY_MACHINE)
    ok("…and an attested one agent, rather than leaving the reader to guess",
       by.get("date-flag") == _arts.BY_AGENT)


def test_a_duplicated_section_never_swallows_recorded_evidence():
    # Reader and writer must agree on WHICH section: writing to the first and reading the last
    # made a full ledger read as empty.
    item = _item()
    _arts.scaffold_cycle(item)
    _arts.record_verification(item, None, check="suite-green", how="ran it", result="exit 0",
                              passed=True)
    path = next(Path(r["path"]) for r in _arts.cycle_reports(item))
    path.write_text(path.read_text() + "\n## Verification\n\n## Cycle outcome\n")
    ok("a second empty section does not erase the first one's entries",
       len(_arts.evidence_entries(item)) == 1)
    ok("…and the verdict still reaches the check table",
       [r["check"] for r in _arts.verdict_rows(item)] == ["suite-green"])


def test_a_check_that_changed_across_cycles_reads_as_a_trail():
    item = _item()
    _arts.scaffold_cycle(item)
    for c in ("date-flag", "suite-green"):
        _arts.record_verification(item, None, check=c, how="ran", result="exit 1", passed=False)
        _arts.record_diagnosis(item, check=c, where="cli.py:1", why="the flag is dropped")
    _lenses(item)
    _arts.write_vet_user_report(item, None)
    _arts.append_cycle_outcome(item, evidence="failed", decision="build", reason="retry")
    _arts.scaffold_cycle(item)
    _arts.record_verification(item, None, check="date-flag", how="ran", result="exit 0",
                              passed=True)
    _arts.record_verification(item, None, check="suite-green", how="ran", result="exit 1",
                              passed=False)
    _arts.record_diagnosis(item, check="suite-green", where="tally/dates.py:12",
                           why="the parser still returns today")
    _lenses(item)
    text = Path(_arts.write_vet_user_report(item, None)["path"]).read_text()
    hist = {v["check"]: [h["passed"] for h in v["history"]]
            for r in _arts.proof_rows(item) for v in r["verified"]}
    ok("a check that was fixed shows the loop's story, not just the latest mark",
       hist.get("date-flag") == [False, True])
    ok("a check that never moved is not padded into a fake trail",
       hist.get("suite-green") == [False, False])
    ok("the still-failing check's located cause reaches the owner's report",
       "tally/dates.py:12" in text and "## What didn't hold" in text)
    ok("…and the one that recovered is not reported as broken",
       "date-flag" not in text.split("## What didn't hold")[1].split("##")[0])


def test_the_vet_report_is_hybrid_and_the_split_is_load_bearing():
    """Vet writes the narrative; code writes the red result off the ledger.

    What code must guarantee is that the driver and the owner read the SAME outcome."""
    item = _item()
    _arts.scaffold_cycle(item, title="probe")
    _arts.record_verification(item, None, check="date-flag", how="ran it", result="exit 1",
                              passed=False)
    _arts.record_diagnosis(item, check="date-flag", where="cli.py:42",
                           why="the flag never reaches the writer")
    _arts.record_verification(item, None, check="suite-green", how="ran", result="exit 0",
                              passed=True)
    _lenses(item)
    # Vet writes an all-clear. The ledger says otherwise, and the ledger wins.
    text = Path(_arts.write_vet_user_report(
        item, None, summary="Everything holds.", confirms="- it all works",
        looked_at="- read the diff")["path"]).read_text()
    ok("vet's own words are carried verbatim — this is its report",
       "**Summary:** Everything holds." in text and "- it all works" in text)
    ok("…but the failure lands anyway, in a section vet does not write",
       "## What didn't hold" in text
       and "an expense recorded with an explicit date keeps that date** — did not hold" in text)
    ok("…with the diagnosis the next build cycle starts from",
       "broke in cli.py:42" in text and "never reaches the writer" in text)
    ok("the passing check is not re-listed — that is the Task tab's job",
       "suite-green" not in text)
    # The template owns the headings, so a slot that repeats one ships it twice.
    echoed = Path(_arts.write_vet_user_report(
        item, None, summary="s", confirms="## What this confirms\n- it works",
        looked_at="## What else was looked at\n- the diff",
        unknown="## What I can't tell you\n- nothing")["path"]).read_text()
    for h in ("## What this confirms", "## What else was looked at", "## What I can't tell you"):
        ok(f"`{h}` is written once, not echoed", echoed.count(h) == 1)
    ok("…and the body under the echoed heading survives",
       "- it works" in echoed and "- the diff" in echoed and "- nothing" in echoed)
    # The lens name is the bullet's LABEL, and the surface tints an opening bold; written plain,
    # the readings render as grey.
    lensed = Path(_arts.write_vet_user_report(
        item, None, summary="s", confirms="- c",
        looked_at="- Intent: does it solve it?\n- **Safety:** already bold\n"
                  "- Something else: not a lens")["path"]).read_text()
    ok("a lens name opening a bullet is bolded", "- **Intent:** does it solve it?" in lensed)
    ok("…without double-bolding one that already is", "- **Safety:** already bold" in lensed)
    ok("…and a bullet that is not a lens is left alone",
       "- Something else: not a lens" in lensed)

    # And the refusals stay, because they are a completeness forcing function, not a trust measure.
    bare = _item()
    _arts.scaffold_cycle(bare, title="probe")
    try:
        _arts.write_vet_user_report(bare, None, summary="fine")
        ok("no report while a plan check has no entry", False)
    except ValueError as e:
        ok("no report while a plan check has no entry", "no recorded entry" in str(e))


def test_a_template_s_authoring_notes_never_become_the_document():
    # A template comment instructs whoever AUTHORS from it; one shipped onto the PR page as the
    # review.
    item = _item()
    r = _arts.scaffold_cycle(item)
    text = Path(r["path"]).read_text()
    ok("the scaffolded cycle report carries no authoring note", "<!--" not in text)
    # The guidance already rides the tool result, so an in-file copy shipped twice and only the
    # in-file one could leak.
    ok("…and the template carries none either — the tool result is where the guidance lives",
       "<!--" not in _arts.skill_template("build-vet"))
    ok("…and the sections it guarded are still named, in the skills where a rule belongs",
       "evidence nobody produced" in src("superme_agent/harness/plugins/superme-dev/skills/build/SKILL.md")
       and "machine-owned" in src("superme_agent/harness/plugins/superme-dev/skills/vet/SKILL.md"))
    # Five skills copy a template by hand. The invariant is that none ships an authoring note,
    # which is stronger than each warning about it.
    for s in ("review", "build", "close", "investigate", "triage"):
        d = ROOT / f"superme_agent/harness/plugins/superme-dev/skills/{s}/templates"
        for tpl in sorted(d.glob("*.md")) if d.is_dir() else []:
            ok(f"{s}/{tpl.name} ships no authoring comment to copy", "<!--" not in tpl.read_text())
    ok("…and the shared authoring contract states it once, for every kind",
       "neither survives into the file you write"
       in src("superme_agent/harness/plugins/superme-dev/references/artifacts.md"))
    ok("…and the renderer hides a comment the way every other markdown renderer does",
       "<!--[\\s\\S]*?-->" in src("web/frontend/src/ui/Markdown.tsx"))


def test_a_cycle_outcome_names_the_cycle_it_closed():
    item = _item()
    _arts.scaffold_cycle(item)
    _arts.record_verification(item, None, check="suite-green", how="ran", result="exit 0",
                              passed=True)
    _arts.append_cycle_outcome(item, evidence="passed", decision="review", reason="green",
                               tokens=157844, budget=500000)
    text = Path(next(r["path"] for r in _arts.cycle_reports(item))).read_text()
    # The reader had the cycle and returned it; it never reached the page, so the owner counted
    # headings by hand.
    ok("the decision says which cycle it ended", "- cycle: 1" in text)
    ok("…and the meter still reads against the loop's ceiling", "tokens: 157844 / 500000" in text)
    # The heading is PARSED: putting the cycle in it renamed every decision and the loop stopped
    # recognising its own.
    ok("…without decorating the heading the loop's breakers parse",
       any(ln.startswith("### ") and ln.endswith("— review") for ln in text.splitlines()))
    ok("…and the reader still returns the cycle it took from the file",
       [o["cycle"] for o in _arts.read_cycle_outcomes(item)] == [1]
       and [o["decision"] for o in _arts.read_cycle_outcomes(item)] == ["review"])


BRIEF = """# Triage User-facing Brief

**Summary:** the date a receipt names is the date it should land on

## Scope & Out of scope

| Doing | Not doing |
|---|---|
| accept an explicit date | back-fill the existing rows |

## From you

**Useful imported references:**

**Verification notes:**
"""


def _brief(text: str = BRIEF) -> Path:
    item = Path(tempfile.mkdtemp(prefix="fromyou-")) / "item"
    (item / "reports").mkdir(parents=True)
    (item / "reports" / "report-triage.md").write_text(text)
    return item


def test_the_owner_s_own_section_survives_the_round_trip():
    """The only section of any report a PERSON writes.

    The words come back at plan as authority, so a lost line is an instruction the owner believes
    they gave. SLOTS, not prose."""
    item = _brief()
    ok("an untouched brief reads as empty rather than as missing",
       _arts.owner_input(item) == {"exists": True, "references": [], "notes": []})

    refs = [{"source": "docs/date-handling.md",
             "description": "the project's timezone rule — it governs storage, not display"},
            {"source": "https://example.test/receipts",
             "description": "**the** vendor's own dating rules"}]
    notes = [{"description": "a receipt dated last month lands in last month"},
             {"description": "today's date still wins when none is given"}]
    back = _arts.write_owner_input(item, references=refs, notes=notes)
    ok("what was added is what comes back", back == _arts.owner_input(item))
    ok("…one slot per entry, in the order they were added",
       [n["description"] for n in back["notes"]] == [n["description"] for n in notes])
    ok("…with the source kept apart from what it governs",
       back["references"][0] == refs[0])
    # A label-shaped run inside a slot ended collection, so everything after it vanished on save.
    ok("…and a bolded run of their own is content, not a section break",
       back["references"][1] == refs[1])

    text = (item / "reports" / "report-triage.md").read_text()
    ok("the rest of the brief is untouched", "| accept an explicit date | back-fill" in text
       and text.count("**Summary:**") == 1)
    ok("…and the section is still the two labels the surface renders",
       text.count("**Useful imported references:**") == 1
       and text.count("**Verification notes:**") == 1)

    # Removing one leaves the others alone: the delete path is a whole-list PUT, so a bug here
    # rewrites untouched slots.
    kept = _arts.write_owner_input(item, references=refs[:1], notes=notes)
    ok("removing one slot leaves the rest untouched",
       kept["references"] == refs[:1] and len(kept["notes"]) == 2)

    _arts.write_owner_input(item, references=[], notes=[])
    ok("clearing it clears it — no residue from the previous save",
       _arts.owner_input(item) == {"exists": True, "references": [], "notes": []})
    ok("…and the labels stay, because they are how the owner knows the section is theirs",
       "**Verification notes:**" in (item / "reports" / "report-triage.md").read_text())


def test_a_slot_is_one_bullet_however_it_was_typed():
    """One slot, one bullet, always. A pasted newline would otherwise split one note into two the
    owner never wrote — and the plan phase turns each note into its own check, so a phantom slot is
    a phantom exam question."""
    item = _brief()
    back = _arts.write_owner_input(
        item,
        references=[{"source": "docs/a.md\nb.md", "description": "line one\nline two"}],
        notes=[{"description": "prove   the   spacing   collapses"},
               {"description": "   "}])
    ok("a pasted newline collapses instead of splitting the slot",
       len(back["references"]) == 1
       and back["references"][0]["description"] == "line one line two")
    ok("…and the source with it", back["references"][0]["source"] == "docs/a.md b.md")
    ok("a slot with nothing in it is dropped, not written as a bare bullet",
       [n["description"] for n in back["notes"]] == ["prove the spacing collapses"])
    ok("…so the file carries exactly one bullet under each label",
       (item / "reports" / "report-triage.md").read_text().count("\n- ") == 2)


def test_the_owner_s_words_reach_the_page_with_the_labels_that_name_them():
    """The read path decides what the owner and the deputy actually SEE.

    It dropped a label whose content sat one blank line below it, so the section arrived as two
    orphan paragraphs with nothing saying which was which."""
    item = _brief()
    _arts.write_owner_input(
        item, references=[{"source": "docs/budget-rules.md", "description": "the ceiling rule"}],
        notes=[{"description": "an over-budget category shows up without me asking"}])
    text = _arts.report_text(item, "triage")["text"]
    ok("the reference block keeps the label that names it",
       "**Useful imported references:**" in text and "docs/budget-rules.md" in text)
    ok("…and so does the block of things to prove",
       "**Verification notes:**" in text and "without me asking" in text)

    # …and the other direction: until they write in it, the section is a heading over nothing.
    empty = _brief()
    ok("an unwritten section is not printed as a bare heading",
       "## From you" not in _arts.report_text(empty, "triage")["text"])
    ok("…while the file still carries it, because the editor writes into the file",
       "## From you" in (empty / "reports" / "report-triage.md").read_text())


def test_the_editor_never_authors_a_report_no_phase_wrote():
    """Two absences that read the same to a component and must not: a brief with an empty section,
    and no brief at all. Creating one here would put a report on disk that triage never wrote."""
    empty = Path(tempfile.mkdtemp(prefix="fromyou-")) / "item"
    (empty / "reports").mkdir(parents=True)
    ok("a missing brief reports itself instead of raising",
       _arts.owner_input(empty) == {"exists": False, "references": [], "notes": []})
    try:
        _arts.write_owner_input(empty, references=[{"source": "x", "description": "y"}], notes=[])
        raise AssertionError("FAILED: writing into a missing brief should be refused")
    except FileNotFoundError:
        ok("…and a save into it is refused rather than inventing the document")

    # Older items predate the section, and dropping their owner's words silently is the worst
    # option.
    old = _brief("# Triage User-facing Brief\n\n**Summary:** written before the section existed\n")
    _arts.write_owner_input(old, references=[],
                            notes=[{"description": "prove the old path still works"}])
    ok("a brief with no section gets one rather than losing the input",
       [n["description"] for n in _arts.owner_input(old)["notes"]]
       == ["prove the old path still works"])

    # Pre-slot sections hold whatever was typed, so each line is read as its own slot and stays
    # addressable.
    prose = _brief(BRIEF.replace(
        "**Verification notes:**",
        "**Verification notes:**\n\nthe old total keeps working\nand the CSV header stays"))
    ok("a pre-slot section reads as slots rather than vanishing",
       [n["description"] for n in _arts.owner_input(prose)["notes"]]
       == ["the old total keeps working", "and the CSV header stays"])


def test_the_drilldown_reads_the_owner_s_own_words_for_its_cards():
    """The cards are BUILT FROM THE REPORTS, not from a second summary of the same facts.

    That is what keeps a card and its document from disagreeing, and why every report opens with
    a one-line summary."""
    item = _brief("# Triage User-facing Brief\n\n"
                  "**Category:** Feature\n\n"
                  "**Background:** it came up while reconciling last month's receipts\n\n"
                  "**Problem:** every expense is stamped today, so a late receipt lands in the "
                  "wrong month\n\n"
                  "**Summary:** let `add` take an explicit date\n\n"
                  "## What you'll get\n\n**Current behavior:**\ntoday's date, always\n")
    f = _arts.triage_facts(item)
    ok("the card reads the owner's own framing, not a second one",
       f["category"] == "Feature" and f["problem"].startswith("every expense is stamped today"))
    ok("...including the optional background line when there is one",
       f["background"].startswith("it came up while"))
    ok("the phase card's line is the report's Summary, verbatim",
       _arts.report_summary(item, "triage") == "let `add` take an explicit date")
    ok("a phase with no report has no line to show — not a guessed one",
       _arts.report_summary(item, "vet") == "")

    # Both answer the card's one question, so both land in `problem` rather than adding a row that
    # is empty on every item.
    goal = _brief("# Triage User-facing Brief\n\n**Goal:** let the tool run from a phone\n")
    ok("a goal-shaped item still says what it is for", _arts.triage_facts(goal)["problem"]
       == "let the tool run from a phone")
    # An unfilled slot is not a value: a row showing the fill instruction is worse than no row.
    raw = _brief("# Triage User-facing Brief\n\n**Category:** <fill:one word — Bug · Feature>\n")
    ok("an unfilled slot never reaches a card", _arts.triage_facts(raw)["category"] == "")


def test_a_phase_card_never_shows_the_previous_pass():
    """Reports are a phase's CLOSING act, several overwritten in place.

    Rendered unconditionally, the card showed the last pass while the current one was still
    working — and in autopilot the item is running almost the whole time."""
    from superme_agent.daemon.services.drilldown import _live_summary
    item = Path(tempfile.mkdtemp(prefix="live-")) / "item"
    (item / "reports").mkdir(parents=True)
    (item / "reports" / "report-build.md").write_text(
        "# Build User-facing Report\n\n**Summary:** done, after one round.\n")
    entered_before = [{"kind": "phase.advance", "created_at": "2020-01-01T00:00:00+00:00"}]
    entered_after = [{"kind": "phase.advance", "created_at": "2099-01-01T00:00:00+00:00"}]
    ok("a report written since the item entered this phase is the current one",
       _live_summary(item, "build", entered_before) == "done, after one round.")
    ok("…and one written BEFORE it describes an earlier pass, so it is not shown",
       _live_summary(item, "build", entered_after) == "")
    ok("a phase that has written nothing shows nothing", _live_summary(item, "vet", []) == "")
    # Newest-first is the feed's contract; the wrong end compares against the item's FIRST
    # transition.
    mixed = [{"kind": "run.report", "created_at": "2099-06-06T00:00:00+00:00"},
             {"kind": "phase.advance", "created_at": "2099-01-01T00:00:00+00:00"},
             {"kind": "phase.advance", "created_at": "2020-01-01T00:00:00+00:00"}]
    ok("…and it reads the NEWEST transition, not the first", _live_summary(item, "build", mixed) == "")
    # A re-entered phase logs no advance, so a reader that knows only advances calls a stale
    # report current.
    (item / "reports" / "report-plan.md").write_text(
        "# Plan User-facing Report\n\n**Summary:** the plan from the first pass.\n")
    for route in ("review.route", "revise.route"):
        back = [{"kind": route, "created_at": "2099-01-01T00:00:00+00:00"},
                {"kind": "phase.advance", "created_at": "2020-01-01T00:00:00+00:00"}]
        ok(f"a `{route}` back into plan counts as entering it",
           _live_summary(item, "plan", back) == "")


def test_every_report_can_reach_the_record_behind_it():
    """Each user-facing report is the compact read; `contract` is the path to the whole thing.

    Review had a record and offered no way to reach it, so the owner read the judgment with its
    evidence unreachable."""
    item = Path(tempfile.mkdtemp(prefix="contract-")) / "item"
    (item / "reports").mkdir(parents=True)
    (item / "artifacts").mkdir(parents=True)
    for phase in ("triage", "plan", "review"):
        (item / "reports" / f"report-{phase}.md").write_text(f"# {phase}\n\n**Summary:** x\n")
    ok("a report whose record is not on disk links to nothing rather than to a 404",
       _arts.report_text(item, "review")["contract"] is None)

    (item / "artifacts" / "review.md").write_text("# Review record\n")
    (item / "artifacts" / "brief.md").write_text("# Brief\n")
    ok("the review report reaches its own agent-facing record",
       _arts.report_text(item, "review")["contract"] == "artifacts/review.md")
    ok("…and triage still reaches the brief", _arts.report_text(item, "triage")["contract"]
       == "artifacts/brief.md")
    # Close is the deliberate None: its report IS the record, so a link would point at itself.
    (item / "reports" / "report-close.md").write_text("# close\n\n**Summary:** x\n")
    ok("close links to nothing, because its report is the record",
       _arts.report_text(item, "close")["contract"] is None)


def test_the_section_is_the_owner_s_alone():
    tmpl = src("superme_agent/harness/plugins/superme-dev/skills/triage/templates/"
               "report-triage-template.md")
    # Comments stripped first: naming the thing a note forbids is documentation, not the thing.
    section = _re.sub(r"<!--.*?-->", "", tmpl, flags=_re.DOTALL).split("## From you", 1)[1]
    # A fill slot instructs the agent to WRITE something; under this heading that invents the
    # owner's references.
    ok("the triage template offers no slot to fill under the owner's heading",
       "<fill:" not in section)
    ok("…but still carries both labels, so the editor has its two blocks",
       "**Useful imported references:**" in section and "**Verification notes:**" in section)
    ok("triage is told the section is not its to write",
       "`## From you` is theirs" in src("superme_agent/harness/plugins/superme-dev/skills/"
                                     "triage/SKILL.md"))

    plan = src("superme_agent/harness/plugins/superme-dev/skills/plan/SKILL.md")
    ok("plan reads it", "reports/report-triage.md` § **From you**" in plan)
    ok("…treats an imported reference as authority, not as one input among several",
       "are AUTHORITY" in plan and "the reference wins" in plan)
    ok("…and turns every verification note into a check the item is measured by",
       "each become one check" in plan)


def test_wiring():
    tools = src("superme_agent/harness/tools/dev_tools.py")
    ok("the deputy's run ends with a token event like every other run — it is the gate's cost",
       'log_event(context_id, "deputy.end"'
       in src("superme_agent/daemon/services/deputy.py"))
    ok("plan has a report pen of its own", '"file_plan_report"' in tools)
    ok("…and it never prompts a human mid-run",
       "mcp__dev__file_plan_report" in src("superme_agent/harness/policy.py"))
    skill = src("superme_agent/harness/plugins/superme-dev/skills/plan/SKILL.md")
    ok("the plan skill files it instead of hand-writing the report", "file_plan_report" in skill)
    ok("…and treats an uncovered task as a question to answer, not a count to clear",
       "Never write a check you don't believe in" in skill)
    tmpl = src("superme_agent/harness/plugins/superme-dev/skills/plan/templates/"
               "report-plan-template.md")
    ok("the template carries the confirmation table, not a prose slot for it",
       "| what must be true | how I'll know |" in tmpl and "{coverage}" in tmpl)
    ok("…and the research variant carries no table at all, because there is nothing to render",
       "how I'll know" not in src("superme_agent/harness/plugins/superme-dev/skills/plan/"
                                  "templates/report-plan-research-template.md"))
    ok("the artifact reference names the pen",
       "file_plan_report" in src("superme_agent/harness/plugins/superme-dev/references/"
                                 "artifacts.md"))


def main() -> None:
    test_the_table_says_what_each_check_MEANS_not_what_it_runs()
    test_the_row_is_never_clipped_mid_meaning()
    test_a_task_nothing_defends_is_named_under_the_table()
    test_a_research_item_gets_the_other_shape()
    test_nothing_factual_is_the_agent_s_to_assert()
    test_an_empty_block_is_dropped_rather_than_printed()
    test_every_user_facing_report_opens_with_one_summary_line()
    test_no_user_facing_report_carries_a_diff_section()
    test_no_report_repeats_the_work_item_title()
    test_the_check_table_names_who_actually_ran_it()
    test_a_duplicated_section_never_swallows_recorded_evidence()
    test_a_check_that_changed_across_cycles_reads_as_a_trail()
    test_the_vet_report_is_hybrid_and_the_split_is_load_bearing()
    test_a_template_s_authoring_notes_never_become_the_document()
    test_a_cycle_outcome_names_the_cycle_it_closed()
    test_the_owner_s_own_section_survives_the_round_trip()
    test_a_slot_is_one_bullet_however_it_was_typed()
    test_every_report_can_reach_the_record_behind_it()
    test_the_owner_s_words_reach_the_page_with_the_labels_that_name_them()
    test_the_editor_never_authors_a_report_no_phase_wrote()
    test_the_drilldown_reads_the_owner_s_own_words_for_its_cards()
    test_a_phase_card_never_shows_the_previous_pass()
    test_the_section_is_the_owner_s_alone()
    test_wiring()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
