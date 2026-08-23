"""Gate STATE — what the four human gates mechanically know.

ONE COMPUTATION, TWO PROJECTIONS: the drilldown renders rows, `kernel_speech` renders the
deputy's prompt. VISIBLE ≠ BLOCKING — only `_BLOCKING` greys Approve.
"""

import re
from pathlib import Path

from . import artifacts as A
from . import plan_revision
from .vocab import status_router
from .vocab.kind_profiles import get_profile, item_fanout, research_kind

# The four briefed human gates, keyed by the phase whose EXIT they guard.
GATE_FOR_PHASE = {"triage": "triage-exit", "plan": "pre-main", "review": "review",
                  "close": "close"}
_GATE_LABEL = {"triage-exit": "Triage exit", "pre-main": "Pre-main (plan approval)",
               "review": "Review (merge decision)", "close": "Close"}

_ALL = "*"
# Which FAILING checks grey Approve. Narrow: only where clicking through would actually fail.
_BLOCKING: dict[str, tuple[str, ...] | str] = {
    "triage-exit": (),
    "pre-main": ("plan_complete",),
    # `method_read` blocks: an instruction not followed, not a judgment call. `owner_rulings`
    # blocks only on MALFORMED fields.
    "review": ("no_pending_authorizations", "evidence_fresh", "artifacts_complete",
               "findings_delivered", "spawns_exist", "children_terminal", "method_read",
               "owner_rulings"),
    "close": _ALL,
}


def close_readiness(item: dict, item_dir: Path, all_items: list[dict]) -> dict:
    """Evaluate the kind's close criteria → {ok, checks}. Every required artifact
    EXISTS — not that it is good.

    NOTHING HERE RE-JUDGES THE WORK: close can fix nothing, so readiness is asked at gates with
    recourse."""
    profile = get_profile(item.get("kind"))
    item_dir = Path(item_dir)
    checks: list[dict] = []

    # EXISTENCE ONLY. Judging quality here would refuse a merged item nobody can still repair.
    missing = [k for k in profile.required_artifacts
               if not (Path(item_dir) / "artifacts" / A.artifact_file(k)).exists()]
    checks.append({"criterion": "required_artifacts",
                   "ok": not missing,
                   "detail": ("never written: " + ", ".join(missing)) if missing else
                             f"all present: {', '.join(profile.required_artifacts)}"})

    # No profile declares one today. The loop stays so an unknown slug fails visibly rather than
    # passes.
    for crit in profile.close_criteria:
        checks.append({"criterion": crit, "ok": False,
                       "detail": "no evaluator for this criterion (kernel gap)"})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def fanout_check(family: str | None, subagents: int | None,
                 fanout: str = "expected") -> dict | None:
    """The `fanned_out` row, or None when the question does not apply — no prescribed
    fan-out, nobody counted, or a BOUNDED surface.

    VISIBLE, NOT BLOCKING. What it removes is the SILENCE around a sweep that split nothing."""
    from .vocab.kind_profiles import FANOUT_FAMILIES, item_fanout
    if family not in FANOUT_FAMILIES or subagents is None:
        return None
    if item_fanout({"fanout": fanout}) == "bounded":
        # Not silence: the judgement stays on the gate, but a run that followed its brief gets no
        # red row.
        return {"criterion": "fanned_out", "ok": True,
                "detail": (f"triage judged this surface bounded, so no split was expected — "
                           f"investigate spawned {subagents}. Disagree with the sizing, not the run.")}
    return {
        "criterion": "fanned_out",
        "ok": subagents > 0,
        "detail": (f"investigate spawned {subagents} subagent(s) — the surface was split"
                   if subagents > 0 else
                   f"investigate ran SINGLE-THREADED (0 subagents). `{family}` sweeps the whole "
                   f"codebase and its guide splits the surface across subagents; a whole-repo pass "
                   f"in one thread either narrowed the surface without saying so, or read less of "
                   f"it than the record implies. Say which."),
    }


# The size below which a brief CANNOT have carried a bar. A floor for impossibility, not a target.
BRIEF_FLOOR = 600


def brief_check(sizes: list[int] | None) -> dict | None:
    """The `brief_carried` row: did the fan-out send its workers out with anything?
    A subagent inherits nothing.

    VISIBLE, NOT BLOCKING. Size proves a brief too short to carry a bar, never that a long one
    carried the right one."""
    if not sizes:
        return None
    thin = [n for n in sizes if n < BRIEF_FLOOR]
    if not thin:
        return {"criterion": "brief_carried", "ok": True,
                "detail": (f"{len(sizes)} subagent brief(s), smallest {min(sizes)} chars — each had "
                           f"room for the family's bar")}
    return {
        "criterion": "brief_carried", "ok": False,
        "detail": (f"{len(thin)} of {len(sizes)} subagent brief(s) were under {BRIEF_FLOOR} chars "
                   f"(smallest {min(thin)}). A brief that short cannot have carried the family's "
                   f"bar and the plan's boundaries — so those workers read to no "
                   f"standard, and what they returned reads like evidence gathered against one. "
                   f"Treat their findings as leads, not receipts."),
    }


def standards_check(reads: int | None) -> dict | None:
    """The `standards_read` row: did review read what the project already settled?
    A plan-only reading cannot see a cut-across decision."""
    if reads is None:
        return None
    return {
        "criterion": "standards_read",
        "ok": reads > 0,
        "detail": ("review read the project's recorded decisions "
                   f"({reads}× across `decisions.md` / `architecture.md`)" if reads > 0 else
                   "review never opened `decisions.md` or `architecture.md`. Those hold what this "
                   "project already settled — the second bar, separate from the plan. A verdict "
                   "written against the plan alone cannot report a departure from a decision it "
                   "never read, and the departure lands as a silent precedent."),
    }


def instrumentation_check(tags: list[dict] | None) -> dict | None:
    """Whether the run's own instrumentation recorded what this gate needs.
    None when there was nothing to record."""
    if tags is None:
        return None
    if not tags:
        return {"criterion": "debug_clean", "ok": True,
                "detail": "no tagged debug instrumentation survives in the branch"}
    where = "; ".join(f"`{t.get('path')}` {t.get('tag')}" for t in tags[:4])
    more = f" …and {len(tags) - 4} more" if len(tags) > 4 else ""
    return {
        "criterion": "debug_clean", "ok": False,
        "detail": (f"{len(tags)} tagged debug probe(s) still in the branch: {where}{more}. Build "
                   f"tags its instrumentation so removal is one grep; these were written and not "
                   f"swept. Delete them before this lands — they ship to the trunk otherwise."),
    }


def guide_check(family: str | None, reads: int | None) -> dict | None:
    """The `method_read` row: did investigate open `references/<family>.md`? None when
    there is no family, or nobody counted.

    BLOCKING, unlike its neighbours: the scaffolder stamps the shape regardless, so an unread
    method is otherwise invisible."""
    if not family or reads is None:
        return None
    return {
        "criterion": "method_read",
        "ok": reads > 0,
        "detail": (f"investigate read `references/{family}.md` ({reads}×)" if reads > 0 else
                   f"investigate NEVER opened `references/{family}.md`. That file is what defines "
                   f"what counts as an answer for a `{family}` — the bar the findings are read "
                   f"against, and the enumeration the record claims to have done. The artifact is "
                   f"in the right shape because the scaffolder put it there, not because the "
                   f"method was followed. Re-run investigate against the guide."),
    }


def judgment_current(item_dir: Path, kind: str | None) -> dict | None:
    """The `judgment_current` row: is `review.md` at least as new as what it judges?
    None when either file is missing.

    VISIBLE, NOT BLOCKING: an honest re-read that changed nothing writes no file, and would fail."""
    review = Path(item_dir) / "artifacts" / "review.md"
    if not review.is_file():
        return None
    if str(kind) == "research":
        subject = Path(item_dir) / "artifacts" / "investigation.md"
    else:
        cycles = sorted((Path(item_dir) / "artifacts").glob("build-vet-*.md"))
        subject = cycles[-1] if cycles else Path(item_dir) / "artifacts" / "_absent"
    if not subject.is_file():
        return None
    try:
        behind = subject.stat().st_mtime - review.stat().st_mtime
    except OSError:
        return None
    return {
        "criterion": "judgment_current",
        "ok": behind <= 0,
        "detail": (f"review.md is current with `artifacts/{subject.name}`" if behind <= 0 else
                   f"review.md is OLDER than `artifacts/{subject.name}` by {int(behind // 60)}m — "
                   f"the record it judges was rewritten after the verdict was written. Either the "
                   f"review ran again and re-read nothing, or it re-read and never updated its own "
                   f"record. Say which, and make review.md describe the current version."),
    }


def research_readiness(item_dir: Path) -> list[dict]:
    """A RESEARCH item's two deliverable checks, at its REVIEW gate. Both read
    review-phase output, so a failure is still answerable."""
    issues = A.report_issues(item_dir, "report-review")
    proposals = A.proposed_work(item_dir)
    decision = A.owner_decision(item_dir)
    return [
        {"criterion": "findings_delivered", "ok": not issues,
         "detail": "; ".join(issues) or "report-review.md complete"},
        # Asks whether the proposals are STATED, not filed — `itemize` files them on this gate's
        # approve.
        {"criterion": "spawns_exist", "ok": bool(proposals),
         "detail": (f"{proposals[:160]}" + (f" · itemized: {decision}" if decision else ""))
                   if proposals else
                   "`## Proposed work` in review.md is empty — say what work these findings imply, "
                   "or say plainly that none follows; itemize files it from there"},
        # A withheld proposal is INVISIBLE unless this row names it. Open questions pass; a
        # malformed ruling field blocks.
        _owner_rulings(item_dir),
    ]


def _owner_rulings(item_dir: Path) -> dict:
    props = A.research_proposals(item_dir)
    issues = A.research_proposal_issues(props)
    _, held = A.filed_and_withheld(props)
    # A rule outlives the item, so this is the last place the owner can refuse one.
    rules = [p for p in props if A.proposal_promotable(p)]
    tail = ("" if not rules else
            f" Approving records {len(rules)} standing rule(s) in this project's ledger — " +
            " · ".join(A.clip(p["rule"], 90) for p in rules) +
            ". Send the item back if a rule reaches further than your ruling did.")
    if issues:
        detail = "; ".join(issues)
    elif held:
        detail = f"{len(held)} of {len(props)} proposal(s) wait on you — " + " · ".join(
            f"{A.clip(p['title'], 48)}: {p['question']}"
            + (f" (suggested: {p['suggested']})" if p.get("suggested") else "")
            for p in held) + (". Approving without ruling drops them; the next sweep raises them "
                              "again.") + tail
    else:
        detail = ("no proposal needs your ruling" + tail) if not rules else tail.strip()
    return {"criterion": "owner_rulings", "ok": not issues, "detail": detail}


def _strip_fm(text: str) -> str:
    return re.sub(r"(?s)\A---\n.*?\n---\n", "", text or "")


def _artifact_text(item_dir: Path, kind: str) -> str | None:
    p = Path(item_dir) / "artifacts" / A.artifact_file(kind)
    return p.read_text(encoding="utf-8") if p.exists() else None


def _task_ratio(item_dir: Path) -> tuple[int, int]:
    plan = _artifact_text(item_dir, "plan") or ""
    done = len(re.findall(r"(?m)^\s*-\s*\[[xX]\]", plan))
    total = done + len(re.findall(r"(?m)^\s*-\s*\[ \]", plan))
    return done, total


def _numbers(item_dir: Path) -> dict:
    """The brief's real-ratio row: counts of real things, never scores. All derived,
    nothing stored."""
    done, total = _task_ratio(item_dir)
    plan_text = _strip_fm(_artifact_text(item_dir, "plan") or "")
    vp = A.parse_vet_plan(plan_text)
    plan_checks = [c["id"] for c in vp.get("checks", [])]
    latest: dict[str, dict] = {}
    for e in A.evidence_entries(item_dir):
        latest[e["check"]] = e
    passing = sum(1 for cid in plan_checks if latest.get(cid, {}).get("passed"))
    return {"tasks_done": done, "tasks_total": total,
            "cycle": len(A.cycle_reports(item_dir)),
            "checks_pass": passing, "checks_total": len(plan_checks)}


def _page_reason(item: dict, events: list[dict]) -> dict | None:
    """Why an item rests `awaiting_human` beyond a clean gate wait — escalation, loop
    halt, blocked run. None means a plain wait."""
    if str(item.get("status")) != "awaiting_human":
        return None
    # THIS PHASE ONLY. Sweeping the whole history makes a clean gate quote a failure from four
    # advances ago.
    window: list[dict] = []
    for e in events:
        if str(e.get("kind")) == "phase.advance":
            break
        window.append(e)
    # The report explaining a halt sits OLDER than the halt marker, so grab it up front.
    blocked = next(({**(e.get("meta") or {})} for e in window
                    if str(e.get("kind")) == "run.report"
                    and str((e.get("meta") or {}).get("outcome")) in ("blocked", "failed", "unverified")),
                   None)
    for e in window:
        kind = str(e.get("kind") or "")
        meta = e.get("meta") or {}
        summary = str(e.get("summary") or "")
        if kind.startswith("deputy.escalate"):
            gate = meta.get("gate")
            return {"source": "deputy", "gate": gate,
                    "headline": str(meta.get("speech") or summary
                                    or f"Your deputy paged you at the {gate or 'gate'}."),
                    "detail": str(meta.get("escalation") or summary or ""),
                    "next": None}
    if blocked:  # a blocked run with no explicit halt marker (defensive)
        return {"source": "agent", "gate": None,
                "headline": f"The {item.get('phase')} run stopped without finishing.",
                "detail": str(blocked.get("summary") or ""),
                "next": str(blocked.get("next") or "") or None}
    return None


def _mark_blocking(gate: str, checks: list[dict]) -> list[str]:
    """Stamp `blocking` on every row and return the greying reasons. ONE place decides,
    so button and deputy agree."""
    must = _BLOCKING.get(gate, ())
    for c in checks:
        c["blocking"] = must == _ALL or c["criterion"] in must
    return [c["detail"] for c in checks if c["blocking"] and not c["ok"]]


def gate_state(item: dict, item_dir: Path, dev_root: Path,
               main_repo_dir: Path | None, *, all_items: list[dict] | None = None,
               events: list[dict] | None = None,
               subagents: int | None = None,
               guide_reads: int | None = None,
               brief_sizes: list[int] | None = None,
               debug_tags: list[dict] | None = None,
               standards_reads: int | None = None,
               ) -> dict:
    """The gate's full mechanical state: checks, which block, counts, pending
    authorizations, and why the item is parked."""
    item_dir, dev_root = Path(item_dir), Path(dev_root)
    all_items, events = all_items or [], events or []
    profile = get_profile(item.get("kind"))
    phase = str(item.get("phase") or profile.phases[0])
    if phase not in profile.phases:   # hand-edited/garbage yaml — degrade, don't 500 the surface
        phase = profile.phases[0]
    terminal = bool(item.get("done_at")) or str(item.get("status")) == "done"
    at_gate = phase in GATE_FOR_PHASE and not terminal  # a terminal item asks nothing anymore
    gate_phase = phase if at_gate else next(
        (p for p in profile.phases[profile.phases.index(phase):] if p in GATE_FOR_PHASE),
        profile.phases[-1])
    gate = GATE_FOR_PHASE[gate_phase]

    checks: list[dict] = []
    authorizations: list[dict] = []
    if gate == "triage-exit":
        # brief.md is triage's product; older items sharpened the item body instead. Read
        # whichever exists.
        item_body = (_strip_fm(_artifact_text(item_dir, "brief") or "").strip()
                     or str(item.get("description") or "").strip())
        # Ready = the `triaged_at` stamp. `kind set + body filled` would be a tautology: an inbox
        # push satisfies both.
        ready = bool(item.get("triaged_at"))
        # The kind is frozen past this gate, so show that a proposal was overruled, not just the
        # winner.
        proposed = str(item.get("proposed_kind") or "")
        overruled = (f" (filed as {proposed})"
                     if proposed and proposed != str(item.get("kind") or "") else "")
        checks.append({"criterion": "triage_ran", "ok": ready,
                       "detail": (f"classification recorded {item.get('triaged_at')}" if ready
                                  else "no classification recorded (set_triage_classification "
                                       "never ran)")
                                 + f" · kind={item.get('kind') or 'unset'}{overruled}, "
                                   f"body {'filled' if item_body.strip() else 'empty'}"})
    elif gate == "pre-main":
        plan = _strip_fm(_artifact_text(item_dir, "plan") or "")
        issues = A.self_check(item_dir, "plan", item_kind=profile.kind)
        done, total = _task_ratio(item_dir)
        checks.append({"criterion": "plan_complete", "ok": not issues,
                       "detail": "; ".join(issues) or f"plan clean, {total} task(s)"})
        # Depth and reason are a call the owner can veto HERE, before tokens burn. A vague
        # `expect` is non-blocking.
        vp = A.parse_vet_plan(plan)
        if profile.kind == "implementation" and vp.get("present"):
            depth = vp.get("depth") or "?"
            soft = A.vet_plan_soft_flags(vp)
            checks.append({
                "criterion": "vet_plan_sharp", "ok": not soft,
                "detail": "; ".join(soft) or
                          (f"depth `{depth}` — {vp.get('reason') or '(no reason given)'}"
                           + (" · NO check will run: the vet pass confirms there is nothing "
                              "observable and records that" if depth == "none" else ""))})
        # Every re-routing round owes a revision block; a round without one means plan.md was
        # hand-edited.
        rounds = sum(1 for e in events if e.get("kind") in ("review.route", "revise.route"))
        if rounds:
            revs = plan_revision.revisions(item_dir)
            checks.append({
                "criterion": "revisions_recorded", "ok": len(revs) >= rounds,
                "detail": (f"{len(revs)} revision block(s) for {rounds} feedback round(s): "
                           + ", ".join(revs)) if len(revs) >= rounds else
                          (f"{rounds} feedback round(s) but only {len(revs)} revision block(s) — "
                           f"fold the feedback in with `revise_plan`, never by rewriting plan.md")})
    elif gate == "review":
        # ARTIFACT COMPLETENESS IS JUDGED HERE, NOT AT CLOSE: review is the last gate with
        # recourse.
        arts = []
        for kind in profile.required_artifacts:
            issues = A.self_check(item_dir, kind, item_kind=profile.kind)
            if issues:
                arts.append(f"{kind}: {issues[0]}"
                            + (f" (+{len(issues) - 1} more)" if len(issues) > 1 else ""))
        checks.append({"criterion": "artifacts_complete", "ok": not arts,
                       "detail": "; ".join(arts) or
                                 f"clean: {', '.join(profile.required_artifacts) or 'none required'}"})
        # Is the verdict newer than what it judges? Asked of both kinds.
        fresh = judgment_current(item_dir, profile.kind)
        if fresh is not None:
            checks.append(fresh)
        # Did any tagged probe survive the build? Asked only of items that have a branch.
        probes = instrumentation_check(debug_tags)
        if probes is not None:
            checks.append(probes)
        # The SECOND bar: did the judging phase read what the project settled before this item?
        second_bar = standards_check(standards_reads)
        if second_bar is not None:
            checks.append(second_bar)
        if profile.kind == "research":
            checks.extend(research_readiness(item_dir))
            # `method_read` comes first: whether the guide was followed decides what the rest of
            # the record is worth.
            for row in (guide_check(research_kind(item), guide_reads),
                        fanout_check(research_kind(item), subagents,
                                     fanout=item_fanout(item)),
                        brief_check(brief_sizes)):
                if row is not None:
                    checks.append(row)
        # CHILDREN HOLD THE PARENT HERE, NOT AT CLOSE — at close the branch is merged and the
        # sessions are gone.
        kids_done, open_kids = status_router.children_terminal(all_items or [], str(item.get("id")))
        checks.append({"criterion": "children_terminal", "ok": kids_done,
                       "detail": f"open sub-item(s): {', '.join(open_kids)}" if not kids_done
                                 else "no open sub-items"})
        # ONLY A KIND THAT VETS IS ASKED ABOUT ITS EVIDENCE. Research has no vet, so nothing
        # writes this ledger.
        if "vet" in profile.phases:
            wt = item.get("git_worktree")
            ev_repo = Path(str(wt)) if wt and Path(str(wt)).is_dir() else main_repo_dir
            ev = A.evidence_status(item_dir, ev_repo)
        # The status word alone named a state without its cause or its exit. Each status now says
        # both.
            n = ev.get("entries", 0)
            detail = {
                "passed": f"all {n} recorded checks pass, and the code hasn't moved since they ran",
                "stale": (f"{n} recorded checks pass, but the code moved after they ran (a sync or "
                          f"a commit) — one vet cycle re-runs them against the current tree"),
                "failed": f"a recorded check is failing ({n} entries) — build fixes it, then vet "
                          f"re-runs",
                "deferred": f"a check is waiting on an authorization you haven't granted "
                            f"({n} entries)",
                "unverified": "nothing recorded yet — vet writes the ledger",
            }.get(str(ev.get("status")), f"evidence ledger: {ev.get('status')} ({n} entries)")
            checks.append({"criterion": "evidence_fresh", "ok": ev["status"] == "passed",
                           "detail": "no checks were owed — the approved plan declares `depth: none`"
                                     if ev.get("not_required") else detail})
        # A pending request HOLDS the merge: deferred is not passed. `delegable` says whether the
        # deputy could have granted it.
        pend_auth = A.pending_authorizations(item_dir)
        if pend_auth:
            authorizations = [{"id": a.get("id", ""), "what": a.get("what", ""),
                               "why": a.get("why", ""), "doc": a.get("doc", ""),
                               "scope": a.get("scope", ""),
                               "delegable": a.get("scope", "") in A.DELEGABLE_SCOPES}
                              for a in pend_auth]
            checks.append({"criterion": "no_pending_authorizations", "ok": False,
                           "detail": f"{len(pend_auth)} authorization(s) awaiting your grant/deny: "
                                     + "; ".join(a["what"] for a in authorizations[:3])})
    else:
        checks = close_readiness(item, item_dir, all_items)["checks"]

    return {"id": item["id"], "gate": gate, "gate_label": _GATE_LABEL[gate],
            "at_gate": at_gate, "phase": phase, "title": str(item.get("title") or item["id"]),
            "terminal": terminal, "checks": checks,
            "blocked_by": _mark_blocking(gate, checks),
            "numbers": _numbers(item_dir),
            "paged": _page_reason(item, events), "authorizations": authorizations}
