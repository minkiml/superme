"""WS-S6 gate test (unit half) — gates, knowledge writes & lifecycle (PRD stage S6).

Covers, without a daemon or LLM: the D7 knowledge-delta pipeline (validation rejects dead file
refs / ghost slugs / missing sections / placeholders; the deterministic writer applies update/
append atomically and stamps applied; a blocking child's delta folds into the parent; the
freshness lint detects truth decay); the D8 close-criteria evaluator (each mechanical refusal +
the all-green pass, for both kinds); the four gate briefs (★ language: continuity + delta +
typed checks with their must-resolve marks, paged reasons, mid-phase description); and
the `apply_knowledge_delta` item tool (kind gating, close-phase gating, itemized rejects); and
the terminal path — the close driver's report ⇒ clear / no-report ⇒ retry-then-clear-anyway
budget. (The on-demand folder archive was RETIRED 2026-07-31 — see routers/dev/work_items.py.)

There is no live half any more: `test_ws_s6_live.py` was deleted 2026-07-30 rather than repaired.
It drove `/complete`, wrote `closeout.md`, and asserted `knowledge_ops_applied` — three things
renovation slice 5d retired — so it had stopped being a safety net and started being a lie. Live
lifecycle coverage belongs to the `bv_s*_live` suites, which run against the current contract.

Run: PYTHONPATH=. python -m scripts.test_ws_s6
"""

import asyncio
from datetime import date
import inspect as _inspect
import json
import subprocess
from re import sub as _re_sub
import tempfile
from pathlib import Path
from types import SimpleNamespace

from superme_agent.core import artifacts as A
from superme_agent.core import gate_briefs as GB
from superme_agent.core import knowledge_delta as KD
from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.core.kind_profiles import KIND_PROFILES
from superme_agent.harness.tools.dev_tools import _apply_knowledge_delta, _ITEM_DEV_TOOLS

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def _seed_anchor_docs(dev_root: Path) -> None:
    g = dev_root / "general"
    g.mkdir(parents=True, exist_ok=True)
    (g / "project-prd.md").write_text(
        "# PRD\n\n## Problem\nstuff\n\n## Deliverables\n- **d-alpha** — the alpha thing\n")
    (g / "architecture.md").write_text(
        "# Architecture\n\n## Stack\npython\n\n## Components\nOld component text.\n\n## Flows\nflow text\n")
    (g / "roadmap.md").write_text("# Roadmap\n\n## d-alpha\n- **w1** — first wave 🟢\n")
    (g / "capabilities.md").write_text("# Capabilities\n\n## Capabilities\n- **alpha** — ships\n")


def _git_repo(tmp: Path, name: str) -> Path:
    repo = tmp / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "real.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def test_delta_validation(tmp: Path) -> None:
    print("knowledge delta — validation (D7)")
    dev_root = tmp / "kd-root"
    _seed_anchor_docs(dev_root)
    repo = _git_repo(tmp, "kd-repo")

    good = [{"doc": "architecture", "section": "Components", "op": "update",
             "content": "New truth referencing `real.py` under d-alpha."}]
    ok("valid ops pass", KD.validate_ops(good, dev_root, repo) == [])
    cases = {
        "unknown doc": [{"doc": "nope", "section": "X", "op": "update", "content": "c"}],
        "missing section": [{"doc": "architecture", "section": "Ghost", "op": "update", "content": "c"}],
        "dead file ref": [{"doc": "architecture", "section": "Stack", "op": "append",
                           "content": "see `does/not/exist.py`"}],
        "ghost deliverable slug": [{"doc": "architecture", "section": "Stack", "op": "append",
                                    "content": "serves d-ghost"}],
        "placeholder": [{"doc": "architecture", "section": "Stack", "op": "append",
                         "content": "<fill:later>"}],
        "empty content": [{"doc": "architecture", "section": "Stack", "op": "append", "content": " "}],
        "bad op kind": [{"doc": "architecture", "section": "Stack", "op": "rewrite", "content": "c"}],
        # BV-A2 small-fix: the retired `spec` doc is read-only (not a known delta target).
        "retired doc target": [{"doc": "spec", "section": "Stack", "op": "update", "content": "c"}],
        # rename_section content is a heading LINE, not a body.
        "rename with ## prefix": [{"doc": "architecture", "section": "Stack", "op": "rename_section",
                                   "content": "## Runtime"}],
        "rename multiline": [{"doc": "architecture", "section": "Stack", "op": "rename_section",
                              "content": "Runtime\nand more"}],
    }
    for name, ops in cases.items():
        assert KD.validate_ops(ops, dev_root, repo), name
    ok("each malformed op rejected with itemized issues", True)
    ok("empty list rejected", KD.validate_ops([], dev_root, repo) != [])
    # rename_section on an existing section validates clean.
    ok("valid rename_section passes",
       KD.validate_ops([{"doc": "architecture", "section": "Stack", "op": "rename_section",
                         "content": "Runtime stack"}], dev_root, repo) == [])


def test_delta_apply(tmp: Path) -> None:
    print("knowledge delta — the deterministic writer + the weekly change log")
    dev_root = tmp / "ka-root"
    _seed_anchor_docs(dev_root)
    res = KD.apply_ops(dev_root, [
        {"doc": "architecture", "section": "Components", "op": "update", "content": "NEW BODY."},
        {"doc": "architecture", "section": "Flows", "op": "append", "content": "APPENDED LINE."},
    ])
    arch = (dev_root / "general" / "architecture.md").read_text()
    ok("update REPLACED the section body",
       "NEW BODY." in arch and "Old component text" not in arch)
    ok("append KEPT + extended the section",
       "flow text" in arch and "APPENDED LINE." in arch)
    ok("the writer reports what it touched",
       res["applied"] == 2 and res["docs"] == ["architecture"])
    ok("no ops → a clean no-op", KD.apply_ops(dev_root, []) == {"applied": 0, "docs": []})

    # BV-A2 small-fix: rename_section rewrites the `## heading` LINE, leaving the body intact.
    KD.apply_ops(dev_root, [{"doc": "architecture", "section": "Flows", "op": "rename_section",
                             "content": "Data flows"}])
    arch2 = (dev_root / "general" / "architecture.md").read_text()
    ok("rename_section rewrote the heading line, kept the body",
       "## Data flows" in arch2 and "## Flows\n" not in arch2 and "flow text" in arch2)

    # The STAGING half is retired (renovation §2.3): close is the sole writer, so there is no draft
    # to stage, no fold-into-parent, and no delta_status row for a gate to read.
    ok("the stage/apply/fold API is gone, not deprecated",
       not any(hasattr(KD, n) for n in
               ("stage_delta", "read_delta", "pending_delta", "apply_delta",
                "fold_into_parent", "delta_status", "DELTA_FILE")))

    # The weekly change log: one entry per item that wrote, appended, never rewritten.
    log = KD.append_change_log(dev_root, "abc123def456", "Add the month filter",
                               [{"doc": "architecture", "section": "Components",
                                 "op": "update", "content": "NEW BODY."}])
    body = Path(log).read_text()
    ok("the change log names the item, the doc·section, the op and the source",
       "Add the month filter" in body and "`architecture` · Components" in body
       and "| update |" in body and "`abc123def456`" in body)
    ok("...and it lands in general/change-logs/delta-<N>.md, N advancing weekly",
       Path(log).parent.name == "change-logs"
       and Path(log).name == f"delta-{KD.change_log_index()}.md"
       and KD.change_log_index(date(2026, 7, 30)) - KD.change_log_index(date(2026, 7, 23)) == 1)
    KD.append_change_log(dev_root, "999888777666", "A second item",
                         [{"doc": "architecture", "section": "Stack", "op": "append",
                           "content": "more"}])
    body2 = Path(log).read_text()
    ok("a second entry APPENDS — the log is history, never rewritten",
       "Add the month filter" in body2 and "A second item" in body2
       and body2.count("# Change log") == 1)


def test_freshness_lint(tmp: Path) -> None:
    print("freshness lint (truth decay)")
    dev_root = tmp / "fl-root"
    _seed_anchor_docs(dev_root)
    repo = _git_repo(tmp, "fl-repo")
    ok("clean docs lint clean", KD.freshness_lint(dev_root, repo) == [])
    arch = dev_root / "general" / "architecture.md"
    arch.write_text(arch.read_text() + "\nsee `gone/away.py` for details\n")
    warns = KD.freshness_lint(dev_root, repo)
    ok("dead anchor-doc file ref detected", any("gone/away.py" in w for w in warns), str(warns))


def _mk_item(dev, dev_root: Path, title: str, kind: str = "implementation", **fm) -> tuple[str, Path]:
    iid = dev.create_work_item(dev_root, title, "the description", kind=kind)["id"]
    item_dir = dev_root / "work-items" / iid
    return iid, item_dir


# A structurally valid vet plan (build⟷vet §3) — the crude fill_all garbage fails the §3.4 hard
# gate by design, so plan fixtures write this section properly.
_VET_OK = ("depth: checks\n"
           "reason: contained change — inspection checks suffice\n"
           "env: none\n\n"
           "### smoke-check\n"
           "- proves: the changed module offers exactly the behaviour the approach promised\n"
           "- traces: d-alpha — the thing the item delivers\n"
           "- mode: inspection\n"
           "- scenario: read the changed module against the plan's approach\n"
           "- expect: the module exposes exactly the functions the approach names, no placeholder bodies\n")


def _fill(item_dir: Path, artifact: str, sections: dict[str, str], *, kind: str = "implementation",
          facts: str | None = None) -> None:
    """Scaffold + crudely fill an artifact's sections (test stand-in for the agent)."""
    if artifact == "plan" and kind == "implementation":
        sections = {"Verification plan": _VET_OK, **sections}
    A.scaffold(item_dir, artifact, title="t", item_kind=kind)
    p = item_dir / "artifacts" / A.artifact_file(artifact)
    text = p.read_text()
    import re as _re
    for sec, content in sections.items():
        text = _re.sub(rf"(?ms)(^##\s+{_re.escape(sec)}\s*\n).*?(?=^##\s|\Z)",
                       rf"\g<1>{content}\n\n", text)
    text = _re.sub(r"<fill:[^>]*>", "filled", text)
    if facts is not None:
        text = _re.sub(r"(?ms)```yaml\n.*?```", f"```yaml\n{facts}\n```", text)
    p.write_text(text)


def test_close_readiness(tmp: Path) -> None:
    print("close criteria (D8) — mechanical refusals + all-green")
    dev = DevKnowledgeService()
    dev_root = tmp / "cr-root"
    _seed_anchor_docs(dev_root)
    repo = _git_repo(tmp, "cr-repo")
    iid, item_dir = _mk_item(dev, dev_root, "closer")
    item = dev.read_work_item(dev_root, iid)

    cr = GB.close_readiness(item, item_dir, [item])
    ok("bare item refused — required artifacts missing", not cr["ok"]
       and not next(c for c in cr["checks"] if c["criterion"] == "required_artifacts")["ok"])

    # Fill everything green: artifacts + the merge fact ON THE ITEM (closeout.md and readiness.md
    # are demolished — the merge commit is read from the item record, never re-asserted in a doc a
    # closing agent could fill).
    _fill(item_dir, "plan", {"Tasks": "- [x] did it"})
    # Review's own agent-facing record: close reads what it settled, and the landing commit
    # reads its `**Delivered:**`. A close with no record is a close reading the owner's report.
    _fill(item_dir, "review", {})
    A.record_verification(item_dir, repo, check="smoke-check", how="pytest", result="ok", passed=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                         text=True).stdout.strip()
    dev.set_work_item_git(dev_root, iid, git_merge_commit=sha)
    item = dev.read_work_item(dev_root, iid)
    cr = GB.close_readiness(item, item_dir, [item])
    ok("all-green passes every criterion", cr["ok"],
       str([c for c in cr["checks"] if not c["ok"]]))

    # AN OPEN CHILD HOLDS THE PARENT AT REVIEW, NOT AT CLOSE (owner, 2026-08-09). A child is part
    # of the parent's work, so the parent must still be re-workable when the child lands — and at
    # close the branch is merged and the sessions are gone. Close was also the ONLY gate that ever
    # asked, so a parent could pass review, land, and meet its open child where nothing can act.
    # BOTH relations hold: a `parallel` child that should not hold the merge was never part of this
    # item, which is what `spawn` is for.
    kids = [item, {"id": "k1", "status": "active",
                   "spawned_from": {"item": iid, "relation": "parallel"}}]
    r_item = {**item, "phase": "review"}
    s = GB.gate_state(r_item, item_dir, dev_root, repo, all_items=kids)
    kid_row = next(c for c in s["checks"] if c["criterion"] == "children_terminal")
    ok("an open child greys Approve at the REVIEW gate, naming the child",
       not kid_row["ok"] and kid_row["blocking"] and "k1" in kid_row["detail"])
    ok("...and close no longer asks about children at all",
       GB.close_readiness(item, item_dir, kids)["ok"]
       and "children_terminal" not in {c["criterion"]
                                       for c in GB.close_readiness(item, item_dir, kids)["checks"]})

    # CLOSE RE-ADJUDICATES NOTHING (slice 5a, 2026-07-30). Review's exit locks code + git, so a
    # close criterion that reads the repo or the ledger can only refuse the paperwork for a
    # decision already made — and the whole-repo fingerprint read an unrelated freshness-sync
    # commit as "the code moved", wedging items permanently (dogfood D5). Both are asserted as
    # ABSENT, from the state that used to trip them.
    (repo / "real.py").write_text("x = 2\n")   # repo edit → the ledger IS stale...
    cr = GB.close_readiness(item, item_dir, [item])
    crits = {c["criterion"] for c in cr["checks"]}
    ok("stale evidence no longer refuses close", cr["ok"] and "evidence_fresh" not in crits,
       str([c for c in cr["checks"] if not c["ok"]]))
    ok("the knowledge row is gone from the criteria — close AUTHORS those ops",
       "knowledge_row_resolved" not in crits)
    ok("...and the criterion is gone from the profile, not just from the evaluator",
       "evidence_fresh" not in KIND_PROFILES["implementation"].close_criteria
       and "knowledge_row_resolved" not in KIND_PROFILES["implementation"].close_criteria)
    ok("...so close reads neither the repo nor dev_root — the parameters are gone too",
       "main_repo_dir" not in _inspect.signature(GB.close_readiness).parameters
       and "dev_root" not in _inspect.signature(GB.close_readiness).parameters)
    (repo / "real.py").write_text("x = 1\n")

    # Research kind: the deliverable is reports/report-review.md (findings.md retired), and the
    # itemization decision on its proposals must be RECORDED. Both are judged at the REVIEW gate,
    # not at close (owner's standing rule, 2026-08-09): they read review-phase output, and asking
    # them at close would refuse a locked item — or, in `spawns_exist`'s case, tell the owner to
    # "run itemize" at a phase whose sessions are already closed. Same invariants, right gate.
    rid, r_dir = _mk_item(dev, dev_root, "researcher", kind="research")
    r_item = dev.read_work_item(dev_root, rid)
    _fill(r_dir, "plan", {"Tasks": "- [x] read"}, kind="research")
    _fill(r_dir, "investigation", {"Questions": "answered"}, kind="research")
    _fill(r_dir, "review", {}, kind="research")

    def _research(crit: str) -> dict:
        return next(c for c in GB.research_readiness(r_dir) if c["criterion"] == crit)

    ok("research: no final report refuses", not _research("findings_delivered")["ok"])
    reports = r_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    # The owner's report is the research deliverable; the DECISION line lives in the record,
    # where `itemize` writes it and the review gate reads it back.
    (reports / "report-review.md").write_text(
        "# Review User-facing Report\n\n**Summary:** it holds.\n")
    rec = r_dir / "artifacts" / A.artifact_file("review")
    rec.write_text(_re_sub(r"(?ms)^## Proposed work\n.*?(?=^## |\Z)", "## Proposed work\n\n",
                           rec.read_text()))
    # Templates no longer carry authoring comments; the invariant is the READ, not the marker.
    ok("a freshly scaffolded record reads as no itemization yet", A.owner_decision(r_dir) == "")
    # `spawns_exist` asks whether the report STATES its proposed work — itemize (which files it)
    # fires on this gate's approve, so "was it filed" is a question no first approval can answer.
    ok("research: report present but no proposed work refuses",
       _research("findings_delivered")["ok"] and not _research("spawns_exist")["ok"])
    rec.write_text(_re_sub(r"(?ms)^## Proposed work\n.*?(?=^## |\Z)",
                           "## Proposed work\nNone — the findings imply no work.\n\n",
                           rec.read_text()))
    ok("research all-green passes", all(c["ok"] for c in GB.research_readiness(r_dir)),
       str([c for c in GB.research_readiness(r_dir) if not c["ok"]]))
    # And they BLOCK where they are asked — a check nobody has to answer is not a gate.
    ok("both research checks grey Approve at the review gate",
       {"findings_delivered", "spawns_exist"} <= set(GB._BLOCKING["review"]))
    # CLOSE now asks nothing of a research item beyond its files existing: it wraps up finished
    # work, it does not decide whether the work finished.
    ok("close carries no research criteria at all",
       GB.close_readiness(r_item, r_dir, [r_item])["ok"]
       and [c["criterion"] for c in GB.close_readiness(r_item, r_dir, [r_item])["checks"]]
           == ["required_artifacts"])
    # ...and SOMETHING must then clear it. Research fires `itemize` on close entry instead of the
    # close skill, and clearance hung off the close runner alone — so a finished research item sat
    # at close with no exit (live, 2026-08-13): Approve 409s (close has no next phase), Run refuses
    # (it is waiting on a decision), and only Drop moved it, filing the work `abandoned`.
    runs_src = Path("superme_agent/daemon/services/runs.py").read_text()
    ok("itemize is research's closing run — it clears the item like any other close",
       'if skill == "itemize" and not stopped:' in runs_src
       and runs_src.index('if skill == "itemize" and not stopped:')
           > runs_src.index("async def _background_intake_run"))


def test_gate_state(tmp: Path) -> None:
    """The typed gate state (renovation v2 §4 · slice 6). The gate BRIEF is gone — a markdown
    narrative with the owner's recommendation baked in — so what is asserted here is the part both
    consumers actually read: which checks a gate computes, which of them BLOCK Approve, and whether
    the item is parked for a nameable reason."""
    print("gate state (typed checks · the must-resolve set · paged reasons)")
    dev = DevKnowledgeService()
    dev_root = tmp / "gb-root"
    _seed_anchor_docs(dev_root)
    iid, item_dir = _mk_item(dev, dev_root, "briefed")
    item = dev.read_work_item(dev_root, iid)

    s = GB.gate_state(item, item_dir, dev_root, None)
    ok("triage phase → triage-exit gate, at_gate", s["gate"] == "triage-exit" and s["at_gate"])
    ok("gate carries a label the surface can print", s["gate_label"] == "Triage exit")
    # F1 (bv-s8pre): readiness = the `triaged_at` stamp, not the old kind+body tautology.
    ok("triage-exit without a classification recorded → check fails",
       any(c["criterion"] == "triage_ran" and not c["ok"] for c in s["checks"]))
    # …and it does NOT grey Approve: the advance route accepts an un-triaged item, so greying it
    # would invent a restriction the backend does not have (§4's activation rule is about what is
    # workable, not about what is tidy).
    ok("a failing triage_ran is VISIBLE but not blocking", s["blocked_by"] == []
       and all(not c["blocking"] for c in s["checks"]))
    dev.set_work_item_triaged(dev_root, iid)
    item = dev.read_work_item(dev_root, iid)
    s = GB.gate_state(item, item_dir, dev_root, None)
    ok("triage_ran passes once the classification is recorded",
       all(c["ok"] for c in s["checks"]))

    events = [{"kind": "run.report", "actor": "agent", "created_at": "2026-07-16T10:00:00"},
              {"kind": "phase.advance", "actor": "owner", "summary": "Approved triage → plan",
               "created_at": "2026-07-15T09:00:00"}]
    item["phase"] = "plan"
    s = GB.gate_state(item, item_dir, dev_root, None, events=events)
    ok("plan phase → pre-main gate", s["gate"] == "pre-main")
    ok("no plan.md → plan_complete fails AND blocks (advance_item 409s on it)",
       any(c["criterion"] == "plan_complete" and not c["ok"] and c["blocking"] for c in s["checks"])
       and s["blocked_by"])
    _fill(item_dir, "plan", {"Tasks": "- [ ] one\n- [x] two"})
    s = GB.gate_state(item, item_dir, dev_root, None)
    ok("a clean plan clears the gate and counts its tasks",
       s["blocked_by"] == [] and s["numbers"]["tasks_done"] == 1
       and s["numbers"]["tasks_total"] == 2)
    ok("the gate-report HTML check is GONE (that surface died with the brief)",
       not any(c["criterion"] == "gate_report" for c in s["checks"]))

    item["phase"] = "build"
    s = GB.gate_state(item, item_dir, dev_root, None)
    ok("mid-build (active) → review gate described, at_gate false, no paged notice",
       s["gate"] == "review" and not s["at_gate"] and s["paged"] is None)

    # A build⟷vet loop HALT used to park the item mid-build and page the owner. RETIRED
    # 2026-07-31: "the loop never parks" (loop.py, owner 2026-07-30) removed `halt` from
    # `decide_after_vet`'s vocabulary, so `_page_reason` no longer has a branch for it. A stopped
    # build is `error` + Resume; a build that needs the owner is `needs_user` + a chat reply.
    item["status"] = "awaiting_human"
    halt_events = [
        {"kind": "loop.decision", "actor": "daemon", "summary": "halted", "meta": {"action": "halt"}},
        {"kind": "phase.advance", "actor": "owner", "summary": "Approved plan → build"},
    ]
    s2 = GB.gate_state(item, item_dir, dev_root, None, events=halt_events)
    ok("a legacy `halt` event no longer pages anyone", s2["paged"] is None)

    # A deputy escalation at a gate surfaces the SAME way (source deputy) — the escalation runbook,
    # not a plain gate wait the owner blows past (the invisible-escalation dogfood defect).
    esc_events = [
        {"kind": "deputy.escalate", "actor": "deputy", "summary": "escalated plan",
         "meta": {"gate": "plan", "speech": "a naming call is yours",
                  "escalation": "sum vs report is a public-contract choice"}},
        {"kind": "phase.advance", "actor": "owner", "summary": "Approved triage → plan"},
    ]
    item["phase"] = "plan"
    s = GB.gate_state(item, item_dir, dev_root, None, events=esc_events)
    ok("deputy escalation → paged notice (source deputy) carries gate + escalation text",
       s["paged"] and s["paged"]["source"] == "deputy" and s["paged"]["gate"] == "plan"
       and "public-contract" in s["paged"]["detail"])
    item["status"] = "active"  # restore for the downstream review/close checks

    item["phase"] = "review"
    s = GB.gate_state(item, item_dir, dev_root, None)
    # Pinned STRUCTURALLY, not on the sentence: the criterion that blocks is `evidence_fresh`, and
    # `blocked_by` carries that check's own detail (one writer for the reason). The wording of the
    # detail is owner-facing copy and is free to change without this going red.
    ev_check = next((c for c in s["checks"] if c["criterion"] == "evidence_fresh"), None)
    ok("review with no evidence → Approve greyed, with the reason named (§2.1 must-resolve)",
       ev_check is not None and ev_check["blocking"] and not ev_check["ok"]
       and ev_check["detail"] in s["blocked_by"])
    # Branch freshness is NOT a gate check (2026-08-01): it could neither block nor be acted on,
    # and the number lives on the Git tab. The gate must not grow a row for it again.
    ok("the review gate carries no branch-freshness row",
       not any(c["criterion"] == "git_fresh" for c in s["checks"]))
    repo = _git_repo(tmp, "gb-repo")
    A.record_verification(item_dir, repo, check="smoke-check", how="h", result="r", passed=True)
    # ARTIFACT COMPLETENESS IS A REVIEW-GATE CHECK NOW (owner's standing rule, 2026-08-09): close
    # cannot refuse an item over artifact quality, because close can fix nothing — so the question
    # moved to the last gate with recourse. A green review gate therefore requires the phase's own
    # record to exist and pass its self-check. The fixture writes one, as a real review run does
    # before the gate opens; the assertion below is unchanged.
    _fill(item_dir, "review", {}, kind="implementation")
    # evidence_fresh reads the item's worktree if set, else the main repo — point at repo.
    s = GB.gate_state(item, item_dir, dev_root, repo)
    ok("review all-green → nothing blocking (Approve is live)", s["blocked_by"] == [],
       str([c for c in s["checks"] if not c["ok"]]))

    item["phase"] = "close"
    s = GB.gate_state(item, item_dir, dev_root, repo, all_items=[item])
    ok("close gate carries the criteria table",
       s["gate"] == "close"
       and any(c["criterion"] == "required_artifacts" for c in s["checks"])
       # Demolished: §3.1's three, plus slice 5a's two — close re-adjudicates nothing about the
       # WORK, because review's exit already locked code + git.
       and not any(c["criterion"] in ("closeout_verified", "assumptions_ratified",
                                      "merged_or_logged_no_merge",
                                      "evidence_fresh", "knowledge_row_resolved")
                   for c in s["checks"]))
    ok("EVERY close criterion blocks — clearance refuses on any red one",
       all(c["blocking"] for c in s["checks"]))
    ok("gate state is deterministic",
       GB.gate_state(item, item_dir, dev_root, repo, all_items=[item])["checks"] == s["checks"])


class _Store:
    def __init__(self):
        self.events = []

    def log_event(self, *a, **kw):
        self.events.append((a, kw))


def test_tools(tmp: Path) -> None:
    print("tools: apply_knowledge_delta")
    dev = DevKnowledgeService()
    dev_root = tmp / "tl-root"
    _seed_anchor_docs(dev_root)
    repo = _git_repo(tmp, "tl-repo")
    iid, item_dir = _mk_item(dev, dev_root, "tooled")
    store = _Store()

    apply_kd = _apply_knowledge_delta(store=store, context_id="t", dev_root=dev_root,
                                      repo_dir=repo, bound_item_id=iid)
    good = json.dumps([{"doc": "architecture", "section": "Stack", "op": "append",
                        "content": "see `real.py`"}])
    # Close-phase ONLY: before the merge locks the code there is nothing true to write.
    r = asyncio.run(apply_kd({"item_id": iid, "ops": good}))
    ok("writing anchor docs off the close phase is refused",
       r.get("is_error") and "written at CLOSE" in json.dumps(r))
    dev.set_work_item_phase(dev_root, iid, "close")
    r = asyncio.run(apply_kd({"item_id": iid, "ops": good}))
    arch = (dev_root / "general" / "architecture.md").read_text()
    ok("at close it WRITES straight through — no staging step",
       not r.get("is_error") and "see `real.py`" in arch)
    ok("...and the same call records the week's change-log entry",
       KD.change_log_path(dev_root).is_file()
       and "`architecture` · Stack" in KD.change_log_path(dev_root).read_text())
    before = arch
    r = asyncio.run(apply_kd({"item_id": iid, "ops": json.dumps(
        [{"doc": "architecture", "section": "Ghost", "op": "append", "content": "x"}])}))
    ok("an invalid op is refused itemized and writes NOTHING",
       r.get("is_error") and "NOTHING was written" in json.dumps(r)
       and (dev_root / "general" / "architecture.md").read_text() == before)
    rid, _r_dir = _mk_item(dev, dev_root, "res", kind="research")
    dev.set_work_item_phase(dev_root, rid, "close")
    apply_r = _apply_knowledge_delta(store=store, context_id="t", dev_root=dev_root,
                                     repo_dir=repo, bound_item_id=rid)
    r = asyncio.run(apply_r({"item_id": rid, "ops": good}))
    ok("research kind refused (knowledge_writes=False)", r.get("is_error")
       and "never writes" in json.dumps(r))


def test_close_driver() -> None:
    print("close driver: report ⇒ clear · no report ⇒ retry ×2 ⇒ clear ANYWAY")
    from superme_agent.daemon.services import runs as R, clearance as C
    seen: dict = {"clears": [], "fires": 0, "retry_events": 0}
    saved = (C.clear_item, C.close_retries, R.fire_close_run, R._dev_store)

    def _clear(_c, _i, **kw):
        seen["clears"].append(kw.get("knowledge_gap"))
        return {"ok": True}

    class _S:
        def log_event(self, _c, kind, _s, **_kw):
            if kind == "close.retry":
                seen["retry_events"] += 1

    C.clear_item = _clear
    C.close_retries = lambda _c, _i: seen["retry_events"]
    R.fire_close_run = lambda *_a, **_kw: seen.__setitem__("fires", seen["fires"] + 1)
    R._dev_store = _S()
    try:
        R._clear_or_retry("t", "i1", "success")
        ok("a reported close clears immediately, no gap",
           seen["clears"] == [None] and seen["fires"] == 0)
        seen["clears"].clear()
        R._clear_or_retry("t", "i1", "blocked")
        ok("a non-success report still clears — but the gap is recorded",
           len(seen["clears"]) == 1 and "blocked" in (seen["clears"][0] or ""))
        seen["clears"].clear()
        for _ in range(C.MAX_CLOSE_RETRY):
            R._clear_or_retry("t", "i1", "")
        ok("a crashed close run is retried, never cleared mid-budget",
           seen["fires"] == C.MAX_CLOSE_RETRY and seen["clears"] == [])
        R._clear_or_retry("t", "i1", "")
        ok("past the budget it clears ANYWAY with the missing knowledge write on record",
           len(seen["clears"]) == 1 and "without a report" in (seen["clears"][0] or "")
           and seen["fires"] == C.MAX_CLOSE_RETRY)
    finally:
        C.clear_item, C.close_retries, R.fire_close_run, R._dev_store = saved
    ok("`propose_close` is gone — clearance is mechanical, nothing proposes it",
       not any(t.name == "propose_close" for t in _ITEM_DEV_TOOLS))


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_delta_validation(tmp)
        test_delta_apply(tmp)
        test_freshness_lint(tmp)
        test_close_readiness(tmp)
        test_gate_state(tmp)
        test_tools(tmp)
        test_close_driver()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
