"""WS-S2 gate test — artifact machinery (workspace-workflow PRD stage S2).

Covers the PRD gate: scaffold every artifact kind → fill → self-check passes; placeholder text
fails the check; an edit after a green evidence entry flips the ledger stale; checkpoints are
append-only and ordered; tasks parse from plan.md's `## Tasks`; the computed artifact-status map derives correctly.
Also: review's own agent-facing record and the three readers that moved onto it.
Self-cleaning (tempdirs + a scratch git repo). No daemon needed.

Run: PYTHONPATH=. python -m scripts.test_ws_s2
"""

import re
import subprocess
import tempfile
from pathlib import Path

from superme_agent.core import artifacts as A
from superme_agent.core.dev_knowledge import DevKnowledgeService

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def fill_all(path: Path, filler=lambda m: "real content") -> None:
    """Replace every <fill:…> slot with content."""
    text = path.read_text()
    path.write_text(A.FILL.sub("filled — real content here", text))


# Crude fill garbage fails the verification-plan §3.4 hard gate BY DESIGN — an implementation plan
# fixture must carry a structurally valid `## Verification plan` (details tested in test_bv_s2).
VET_OK = """depth: checks
reason: contained change — inspection suffices
env: none

### smoke-check
- proves: the changed module offers exactly the behaviour the approach promised
- traces: d-x — the deliverable this defends
- mode: inspection
- scenario: read the changed module against the approach
- expect: the module exposes exactly the functions the approach names, no placeholder bodies
"""


def fix_vet_plan(path: Path) -> None:
    """Overwrite the (garbage-filled) `## Verification plan` section body with a valid one."""
    text = path.read_text()
    path.write_text(re.sub(r"(?ms)(^## Verification plan\s*\n).*?(?=^## |\Z)",
                           r"\g<1>" + VET_OK + "\n", text))


# Same story for `## Touches` — crude fill inside the fenced yaml fails touches_hard_issues by
# design (action must be new/modify/read).
TOUCHES_OK = """```yaml
- component: demo
  path: src/demo.py
  action: modify
```
"""


def fix_touches(path: Path) -> None:
    text = path.read_text()
    path.write_text(re.sub(r"(?ms)(^## Touches\s*\n).*?(?=^## |\Z)",
                           r"\g<1>" + TOUCHES_OK + "\n", text))


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


def make_repo(tmp: Path) -> Path:
    repo = tmp / "scratch-repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("print('hi')\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "init")
    return repo


def test_template_section_spec() -> None:
    print("template → section spec (fill detection spans the whole section)")
    # A `<fill:…>` slot WRAPPED across lines matches neither line on its own. Detecting per-line
    # made such a section read as optional, so the template silently stopped demanding the content
    # it asks for — the one failure mode a derived-from-the-template check must not have.
    A._template_cache["__wrapped_probe"] = (
        "# T\n\n## One\n<fill:a slot that wraps\nacross two lines>\n\n## Two\nprose, no slot\n")
    ok("a wrapped slot still marks its section must-fill",
       A.template_section_spec("__wrapped_probe") == [("One", True), ("Two", False)],
       str(A.template_section_spec("__wrapped_probe")))
    A._template_cache.pop("__wrapped_probe")
    # And the real templates keep the spec they had: a comment-only section (a pen's or the
    # driver's) must merely EXIST, never be filled by the authoring agent.
    ok("research work-segment record derives four filled sections",
       A.template_section_spec("investigation")
       == [("Questions", True), ("Evidence", True), ("Dead ends", True), ("Open threads", True)])
    ok("pen/driver sections stay optional",
       dict(A.template_section_spec("build-vet"))["Verification"] is False
       and dict(A.template_section_spec("build-vet"))["Cycle outcome"] is False)


def test_scaffold_and_check(item: Path) -> None:
    print("scaffold → fill → self-check (every kind)")
    try:
        A.scaffold(item, "bogus")
        ok("unknown artifact fails loud", False)
    except KeyError:
        ok("unknown artifact fails loud", True)

    for kind, item_kind in [("plan", "implementation"), ("plan", "research"),
                            ("brief", "implementation"),
                            ("investigation", "research"),
                            ("handoff-brief", None)]:
        d = item / f"case-{kind}-{item_kind or 'x'}"
        r = A.scaffold(d, kind, title="T", item_kind=item_kind, item_id="i1")
        assert r["created"], kind
        # Fresh scaffold must FAIL the check (unfilled slots) — except none? all have fills.
        issues = A.self_check(d, kind, item_kind=item_kind)
        assert issues, f"{kind}: fresh scaffold should not pass ({issues})"
        p = Path(r["path"])
        fill_all(p)
        if kind == "plan" and item_kind == "implementation":
            fix_vet_plan(p)  # crude fill fails the §3.4 hard gate by design
        issues = A.self_check(d, kind, item_kind=item_kind)
        assert not issues, f"{kind}: filled doc should pass, got {issues}"
        # Re-scaffold = no-op, never overwrite.
        r2 = A.scaffold(d, kind, title="T", item_kind=item_kind)
        assert not r2["created"] and "real content" in p.read_text()
    ok("all kinds scaffold, reject unfilled, pass when filled, never overwrite", True)

    # Required-section deletion is caught.
    d = item / "case-missing-section"
    p = Path(A.scaffold(d, "plan", title="T", item_kind="research")["path"])
    fill_all(p)
    p.write_text(p.read_text().replace("## Boundaries", "## Bound-ish"))
    issues = A.self_check(d, "plan", item_kind="research")
    ok("missing required section caught", any("Boundaries" in i for i in issues), str(issues))

    # Research plan requires its own sections.
    d = item / "case-research-plan"
    p = Path(A.scaffold(d, "plan", title="T", item_kind="research")["path"])
    assert "## Boundaries" in p.read_text()
    ok("plan template is item-kind-parameterized", True)


def test_evidence(item: Path, repo: Path) -> None:
    print("evidence ledger (stale-on-edit)")
    d = item / "case-evidence"
    try:
        A.record_verification(d, repo, check="x", how="", result="y", passed=True)
        ok("empty evidence fields rejected", False)
    except ValueError:
        ok("empty evidence fields rejected", True)
    A.record_verification(d, repo, check="unit tests", how="pytest -q", result="42 passed", passed=True)
    st = A.evidence_status(d, repo)
    ok("green + fresh → passed", st["status"] == "passed", str(st))
    # Edit the repo → stale.
    (repo / "a.py").write_text("print('changed')\n")
    st = A.evidence_status(d, repo)
    ok("repo edit flips stale", st["status"] == "stale", str(st))
    # Re-run the check green again → passed again.
    A.record_verification(d, repo, check="unit tests", how="pytest -q", result="42 passed", passed=True)
    st = A.evidence_status(d, repo)
    ok("re-run restores passed", st["status"] == "passed", str(st))
    # A failing latest entry → failed (beats staleness).
    A.record_verification(d, repo, check="parity", how="parity check", result="exit 1", passed=False)
    st = A.evidence_status(d, repo)
    ok("failing check → failed", st["status"] == "failed" and st["failed_checks"] == ["parity"], str(st))
    ok("entries parse back", len(A.evidence_entries(d)) == 3)
    git(repo, "checkout", "--", "a.py")  # restore for closeout test


def test_checkpoints(item: Path, repo: Path) -> None:
    print("checkpoints (append-only)")
    d = item / "case-checkpoints"
    try:
        A.write_checkpoint(d, repo, working_on="", decisions="", remaining="x")
        ok("empty checkpoint rejected", False)
    except ValueError:
        ok("empty checkpoint rejected", True)
    p1 = A.write_checkpoint(d, repo, working_on="stage S2", decisions="chose yaml facts",
                            remaining="wire tools", notes="tried X, failed")
    p2 = A.write_checkpoint(d, repo, working_on="stage S2 later", decisions="",
                            remaining="gate test")
    ok("append-only two files", p1 != p2 and len(list((d / 'checkpoints').glob('*.md'))) == 2)
    latest = A.latest_checkpoint(d)
    ok("latest = newest by filename", latest["path"] == p2 and "later" in latest["text"])
    ok("git-state header present", re.search(r"git: .+ @ ", Path(p1).read_text()) is not None)
    cap = A.latest_checkpoint(d, char_cap=20)
    ok("char cap honored", len(cap["text"]) == 20 and cap["truncated"])


def test_tasks_and_status(tmp: Path, repo: Path) -> None:
    print("tasks-in-plan + computed artifact status")
    dev = DevKnowledgeService()
    root = tmp / "devroot"
    wid = dev.create_work_item(root, "s2 item", kind="implementation")["id"]
    item_dir = root / "work-items" / wid
    p = Path(A.scaffold(item_dir, "plan", title="s2 item", item_kind="implementation")["path"])
    text = A.FILL.sub("filled", p.read_text())
    text = text.replace("- [ ] t1 — filled", "- [x] step one\n- [ ] step two\n- [ ] step three")
    p.write_text(text)
    fix_vet_plan(p)
    ok("progress from plan.md ## Tasks", dev.task_progress(root, wid) == {"done": 1, "total": 3})
    tasks = dev.read_tasks(root, wid)
    ok("structured tasks parse", tasks[0] == {"text": "step one", "done": True} and len(tasks) == 3)

    item = dev.read_work_item(root, wid)
    st = A.artifact_status(item, item_dir, repo)
    ok("plan ok / research-only kinds not required",
       st["plan"]["status"] == "ok" and st["investigation"]["required"] is False, str(st))
    ok("demolished kinds are gone from the artifact set",
       not ({"validation", "readiness", "closeout"} & set(st)), str(sorted(st)))

    # Legacy fallback: an old item with only tasks.md still reads.
    wid2 = dev.create_work_item(root, "legacy item", kind="implementation")["id"]
    (root / "work-items" / wid2 / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "work-items" / wid2 / "artifacts" / "tasks.md").write_text("- [x] a\n- [ ] b\n")
    ok("legacy tasks.md fallback", dev.task_progress(root, wid2) == {"done": 1, "total": 2})


def test_pr_task_notes(tmp: Path) -> None:
    """The PR page's per-task notes: build's line parsed out of the cycle report, joined to the
    plan's requirement and the ledger's verdicts, with the LATEST cycle winning."""
    print("PR review notes, per task")
    dev = DevKnowledgeService()
    root = tmp / "pg-root"
    root.mkdir(parents=True, exist_ok=True)
    wid = dev.create_work_item(root, "guide item", kind="implementation")["id"]
    item = root / "work-items" / wid
    (item / "artifacts").mkdir(parents=True, exist_ok=True)
    (item / "artifacts" / "plan.md").write_text(
        "## Tasks\n- [x] t1 — Add the flag\n- [x] t2 — Wire the command\n\n"
        "## Verification plan\ndepth: checks\n\n"
        # Declared FIRST and covering BOTH tasks, so a naive first-wins pick hands t2 this line
        # instead of its own — exactly what the first live run did.
        "### shared-filter\n"
        "- proves: the shared filter narrows both surfaces the same way.\n"
        "- covers: t1, t2\n- mode: command\n\n"
        "### flag-shape\n"
        "- proves: the flag emits the documented shape.\n- covers: t1\n- mode: command\n")
    (item / "artifacts" / "build-vet-1.md").write_text(
        "# Build⟷vet 1\n\n## Built\n- t1 — first pass\n\n"
        "## For the reviewer\n"
        "- t1 — look: the FIRST cycle's note, superseded below · deviated: none\n"
        "- t2 — look: none · deviated: a shared helper → an inline branch, used once.\n")
    (item / "artifacts" / "build-vet-2.md").write_text(
        "# Build⟷vet 2\n\n## Built\n- t1 — rebuilt\n\n"
        "## For the reviewer\n"
        "- t1 — look: the debounce is 250 ms and nobody chose it · deviated: none\n"
        "- this line has no task id and must be ignored\n")

    notes = A.pr_task_notes(item)
    ok("the newest cycle's note wins", notes["t1"]["look"].startswith("the debounce"),
       str(notes["t1"]))
    ok("…and carries the cycle it came from", notes["t1"]["cycle"] == 2, str(notes["t1"]))
    ok("a task only cycle 1 touched keeps cycle 1's note",
       notes["t2"]["deviated"].startswith("a shared helper") and notes["t2"]["cycle"] == 1,
       str(notes.get("t2")))
    ok("`none` reads as nothing to say, not as the word", notes["t1"]["deviated"] == "",
       str(notes["t1"]))
    # Seen on the first live run: build declares `none` and then justifies it, which read literally
    # would put a restatement of the diff under a heading promising what the diff cannot show.
    ok("`none.` followed by a justification is still nothing",
       A._note_fields("look: none. Predicate exactly matches plan: `text in note.lower()`")
       == {"look": ""},
       str(A._note_fields("look: none. Predicate exactly matches plan: x")))
    ok("…but a real note containing a semicolon survives whole",
       A._note_fields("look: 250 ms was picked here; nobody chose it")["look"]
       == "250 ms was picked here; nobody chose it")
    ok("a bullet with no task id is ignored", set(notes) == {"t1", "t2"}, str(sorted(notes)))

    guide = A.pr_task_guide(item)
    ok("`needed` is the covering check's proves line, not the task spec",
       guide["t1"]["needed"] == "the flag emits the documented shape.", str(guide["t1"]))
    # A check covering two tasks states something true of BOTH, so it is a poor answer to "what did
    # THIS task have to make true" — the narrowest check wins even when a shared one is declared
    # first. Seen live: t2 ("wire the subcommand") showed the month-filter requirement.
    ok("the narrowest covering check answers, not whichever was declared first",
       guide["t2"]["needed"] == "the shared filter narrows both surfaces the same way."
       and guide["t1"]["needed"] != guide["t2"]["needed"], str(guide["t2"]))
    ok("a planned check with no verdict reads as not-run",
       {c["id"]: c["ran"] for c in guide["t1"]["checks"]}
       == {"shared-filter": False, "flag-shape": False}, str(guide["t1"]["checks"]))
    ok("every plan task gets an entry, notes or not", set(guide) == {"t1", "t2"}, str(sorted(guide)))
    ok("the notes are assembled, never stored — nothing lands in reports/",
       not list((item / "reports").glob("*")),
       str([x.name for x in (item / "reports").glob("*")]))

    # A cycle report with no reviewer section at all — the older shape — must not throw.
    (item / "artifacts" / "build-vet-2.md").write_text("# Build⟷vet 2\n\n## Built\n- t1 — x\n")
    ok("a cycle report without the section falls back to the older one",
       A.pr_task_notes(item)["t1"]["cycle"] == 1, str(A.pr_task_notes(item)))


def test_owner_edit(tmp: Path) -> None:
    """The owner's hand-edit of an INTENT artifact: refused for record kinds, validated before it
    writes, and stamped so a later reader knows whose words these are."""
    print("owner edit of brief/plan")
    dev = DevKnowledgeService()
    root = tmp / "oe-root"
    wid = dev.create_work_item(root, "oe item", kind="implementation")["id"]
    item_dir = root / "work-items" / wid
    p = Path(A.scaffold(item_dir, "plan", title="oe", item_kind="implementation")["path"])
    A.scaffold(item_dir, "review", title="oe", item_kind="implementation")
    good = A.FILL.sub("filled", p.read_text())
    p.write_text(good)
    fix_vet_plan(p)
    good = p.read_text()

    ok("only the two intent kinds are editable", A.OWNER_EDITABLE == ("brief", "plan"))
    try:
        A.owner_edit(item_dir, "review", "anything")
        ok("a record kind is refused", False)
    except ValueError:
        ok("a record kind is refused", True)

    # A save that breaks the contract writes NOTHING — the same issues the gate would raise.
    before = p.read_text()
    issues = A.owner_edit(item_dir, "plan", "# Plan\n\njust prose, no sections\n",
                          item_kind="implementation")
    ok("a contract-breaking edit is refused with issues", bool(issues), str(issues))
    ok("...and the file on disk is untouched", p.read_text() == before)
    ok("...leaving no probe file behind",
       [f.name for f in (item_dir / "artifacts").iterdir() if f.name.endswith(".tmp")] == [])

    edited = good.replace("## Intent\nfilled", "## Intent\nwhat the OWNER actually wants")
    ok("a valid edit saves", A.owner_edit(item_dir, "plan", edited,
                                          item_kind="implementation") == [])
    after = p.read_text()
    ok("the owner's words landed", "what the OWNER actually wants" in after)
    ok("and it is stamped", A.owner_edited_at(after) is not None)
    ok("an untouched artifact carries no stamp",
       A.owner_edited_at((item_dir / "artifacts" / "review.md").read_text()) is None)

    # Re-editing REPLACES the stamp rather than stacking a second one.
    A.owner_edit(item_dir, "plan", after, item_kind="implementation")
    ok("re-editing keeps exactly one stamp",
       p.read_text().count("edited_by_owner:") == 1)

    # Frontmatter dropped by hand comes back — `artifact:`/`item_kind:` are read downstream.
    body_only = after.split("---\n", 2)[2]
    ok("an edit that drops the frontmatter still saves",
       A.owner_edit(item_dir, "plan", body_only, item_kind="implementation") == [])
    ok("...with the frontmatter restored",
       p.read_text().startswith("---\n") and "artifact: plan" in p.read_text())


def test_carry_owner_input(tmp: Path) -> None:
    """The owner's words reach every phase MECHANICALLY. Each intake phase runs in its own session,
    so anything they said earlier is gone from the thread; this is the block that carries the
    durable copy — and it must never invent one from a scaffold."""
    print("owner input carried into every phase")
    dev = DevKnowledgeService()
    root = tmp / "carry-root"
    wid = dev.create_work_item(root, "carry", kind="implementation")["id"]
    d = root / "work-items" / wid

    ok("nothing said yet → nothing carried", A.carry_owner_input(d) is None)

    (d / "reports").mkdir(parents=True, exist_ok=True)
    (d / "reports" / "report-triage.md").write_text("# Triage\n\n## Context\nx\n")
    A.scaffold(d, "plan", title="carry", item_kind="implementation")
    ok("a SCAFFOLDED plan is not a decision — its section is comments",
       A.carry_owner_input(d) is None)

    A.write_owner_input(d, references=[{"source": "RFC 42", "description": "the wire format"}],
                        notes=[{"description": "prove it on staging, not a fixture"}])
    p = d / "artifacts" / "plan.md"
    p.write_text(p.read_text().replace(
        "## Decisions & clarifications\n",
        "## Decisions & clarifications\n- scope: sum only, do not touch total\n"))
    out = A.carry_owner_input(d) or ""
    ok("references, notes and decisions all carry",
       "RFC 42" in out and "staging" in out and "sum only" in out, out)
    ok("carried as STANDING instruction, not background",
       "STANDING" in out and "outrank" in out)

    # An owner who writes an essay must not push the phase contract out of the prompt.
    A.write_owner_input(d, references=[], notes=[{"description": "x" * 4000}])
    capped = A.carry_owner_input(d) or ""
    ok("capped, and says so rather than silently dropping",
       len(capped) < 2000 and "truncated" in capped, str(len(capped)))


def test_report_read_hygiene(tmp: Path) -> None:
    """A report's READ path drops what the author should have deleted. Every template says to
    remove a block it has nothing to put under; the first report the owner ever read carried two
    that survived. Structure only — the tokens are the contract, not any one sentence."""
    print("report read hygiene")
    item = tmp / "hygiene"
    (item / "reports").mkdir(parents=True)
    (item / "reports" / "report-triage.md").write_text(
        "# Triage — t\n\n"
        "**Delivering:** a --date flag.\n\n"
        "| In scope | Out of scope |\n| --- | --- |\n| a | b |\n\n"
        "**Needs your attention:** none.\n\n"
        "## Changed since v1\n\n(first run)\n")
    text = A.report_text(item, "triage")["text"]
    ok("a dead **Label:** block never reaches the reader", "Needs your attention" not in text)
    ok("an empty Changed-since section never reaches the reader", "Changed since" not in text)
    ok("real content survives untouched", "--date flag" in text and "| In scope |" in text)

    (item / "reports" / "report-plan.md").write_text(
        "**Needs your attention:** confirm the date format.\n\n"
        "## Changed since v2\n\nsplit the CSV work off.\n")
    kept = A.report_text(item, "plan")["text"]
    ok("a block with a real value is not dropped", "confirm the date format" in kept)
    ok("a Changed-since with a real delta is not dropped", "split the CSV work off." in kept)


def test_tool_registration() -> None:
    print("tool registration")
    from superme_agent.harness.tools.dev_tools import DEV_TOOLS, _ITEM_DEV_TOOLS
    names = {t.name for t in _ITEM_DEV_TOOLS}
    ok("S2 tools registered",
       names == {"scaffold_artifact", "record_verification", "write_checkpoint",
                 "record_validation",   # build's self-check as DATA, so vet can audit the claim
                 "dry_run_checks",      # …and plan's smoke test of the `run:` blocks it just wrote
                "sync_from_main",                            # joined in S4
                "apply_knowledge_delta",    # joined in S6
                "set_triage_classification",                 # joined in the audit batch (R6)
                "file_vet_report",                           # joined in build-vet-loop step 4
                "revise_plan",                               # joined in renovation slice D (§2.1)
                "request_authorization",                     # joined with BV-A2.1 (deferred auth)
                "record_lens",          # joined with the verification model (§3): the standing lenses
                "record_diagnosis",     # joined with the verification model (§5): where/why/unknown
                "nominate_check",       # the verification library (§8): vet nominates, close writes
                "read_verification_library",   # …and both plan and close read it through one tool
                "file_plan_report",     # the plan gate's report pen (§10): the matrix is derived
                "file_investigate_report",   # …and investigate's, which had no pen and no path
                # the remaining four pens of the same seven-template set (human-report slice 3)
                "file_triage_report", "file_build_report", "file_review_report",
                "file_close_report",
                # the open-questions slice: itemize is handed the filed/withheld split instead of
                # judging it, and every phase that can ask the owner something reads the ledger first
                "read_research_proposals", "read_decisions"}

       and all(t in {x.name for x in DEV_TOOLS} for t in names))


def test_review_record(tmp: Path) -> None:
    """`artifacts/review.md` — review's own agent-facing record (human-report phase, slice 2).

    Review was the one phase with no agent doc, so its OWNER report had accumulated five fields
    that only machines read: the landing-commit body, the research proposals, the itemization
    decision, close's list of owed docs, and the re-plan digest. Each one moves here, and what the
    owner reads becomes prose again."""
    print("review record — the phase's agent-facing doc")
    from superme_agent.core import kind_profiles as _kp
    from superme_agent.daemon.services import git_ops as _go

    impl, res = tmp / "rr-impl", tmp / "rr-res"
    A.scaffold(impl, "review", title="t", item_kind="implementation")
    A.scaffold(res, "review", title="t", item_kind="research")
    heads = A.required_sections("review", "implementation")
    ok("implementation shape: inventory · departures · settled · proven · risks · rounds",
       heads == ("Change inventory", "Against our own decisions",
                 "Settled — do not re-open in a revision cycle",
                 "Proven vs taken on trust", "Risks surviving merge", "Revision rounds"), str(heads))
    # `## Against our own decisions` is REQUIRED, not optional, for the same reason `## Risks
    # surviving merge` is: the sentence "nothing departs" is a finding, and an absent section is
    # indistinguishable from a review that never looked. The two bars are judged separately — the
    # plan says what the item owed, `decisions.md` says what the project had already settled.
    ok("…and the departures section is required, so 'nothing departs' has to be SAID",
       ("Against our own decisions", True) in A.section_spec("review", "implementation"))
    ok("research has no departures section — it concluded nothing into the code",
       "Against our own decisions" not in A.required_sections("review", "research"))
    ok("research swaps in its own two, and drops the merge risks",
       "Proposed work" in A.required_sections("review", "research")
       and "Risks surviving merge" not in A.required_sections("review", "research"))
    ok("`## Revision rounds` is EXISTS-only — it is appended to, never authored from a slot",
       ("Revision rounds", False) in A.section_spec("review", "implementation"))
    ok("both kinds are close-gate REQUIRED — a close with no record reads the owner's report",
       "review" in _kp.get_profile("implementation").required_artifacts
       and "review" in _kp.get_profile("research").required_artifacts)

    # Reader 1 — the landing commit body.
    (impl / "artifacts" / "review.md").write_text(
        "# Review Agent-facing Report\n\n**Delivered:** the counter went quiet\n\n## Change inventory\n")
    ok("the landing commit reads `**Delivered:**` from HERE",
       _go._delivered_line(impl) == "the counter went quiet")
    (impl / "reports").mkdir(parents=True, exist_ok=True)
    (impl / "reports" / "report-review.md").write_text("**Delivered:** the owner's prose\n")
    ok("…and never from the owner's report, even when that carries the field",
       _go._delivered_line(impl) == "the counter went quiet")

    # Reader 2 — the close gate's itemization check.
    rec = res / "artifacts" / "review.md"
    ok("a scaffolded record reads as NO decision — the comment is not an answer",
       A.owner_decision(res) == "")
    rec.write_text(re.sub(r"(?m)^\*\*Owner's decision:\*\*.*$",
                          "**Owner's decision:** declined — nothing follows.", rec.read_text()))
    ok("…and the filled line reads back whole", A.owner_decision(res) == "declined — nothing follows.")

    # Reader 3 — the review→plan re-plan digest.
    (res / "artifacts" / "build-vet-1.md").write_text("## Verification\nedge case X fails\n")
    dig = _go.build_downstream_digest(res) or ""
    ok("a re-plan digest carries the record, not the report", "Owner's decision" in dig)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo = make_repo(tmp)
        item = tmp / "items"
        test_template_section_spec()
        test_scaffold_and_check(item)
        test_evidence(item, repo)
        test_checkpoints(item, repo)
        test_tasks_and_status(tmp, repo)
        test_owner_edit(tmp)
        test_carry_owner_input(tmp)
        test_pr_task_notes(tmp)
        test_report_read_hygiene(tmp)
        test_review_record(tmp)
        test_tool_registration()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
