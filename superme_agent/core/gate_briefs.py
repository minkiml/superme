"""Gate STATE + close criteria — what the four human gates mechanically know (renovation v2 §4).

★ What this module is, and what it stopped being (2026-07-30, slice 6). It used to assemble a
`gate brief`: a markdown narrative that embedded a truncated copy of the phase's artifact and closed
with the owner's decision block (recommendation · options · effort). That surface is dead. Two
consumers read it and neither wanted a narrative — the drilldown now renders typed rows (§4.2) and
the deputy is handed the report the OWNER reads plus a path to the contract (§2.1). What survives is
the part both actually need: the **gate state** — the mechanical checks, which of them grey Approve,
the counts, the pending authorizations, and the reason the item is parked.

**One computation, two projections.** `gate_state` is the single place a gate's checks are decided.
The drilldown route projects it into buttons + rows; `kernel_speech.deputy_brief_block` projects it
into the deputy's prompt. A deputy judging from different numbers than the owner sees is a deputy
whose call the owner cannot check — so there is exactly one function that counts.

**Visible ≠ blocking** (owner rule, slice 6). Every check renders as a NAMED row carrying its own
reason. Only the narrow must-resolve set (`_BLOCKING`) greys Approve: what the advance route would
actually refuse, plus §2.1's locked table. Freshness debt, a vague `expect` phrasing, an unrecorded
revision — real, worth showing, and the owner may act over them with their eyes open.

`close_readiness` is the D8 close gate's mechanical evaluator over KIND_PROFILES.close_criteria —
the complete/promote route refuses on any failing check (three-layer protocol, layer zero).

Pure + file-based (the router feeds it the item's events) — unit-testable without a daemon.
"""

import re
from pathlib import Path

from . import artifacts as A
from . import plan_revision
from . import status_router
from .kind_profiles import get_profile

# The four briefed human gates, keyed by the phase whose EXIT they guard (D2/D10).
GATE_FOR_PHASE = {"triage": "triage-exit", "plan": "pre-main", "review": "review",
                  "close": "close"}
_GATE_LABEL = {"triage-exit": "Triage exit", "pre-main": "Pre-main (plan approval)",
               "review": "Review (merge decision)", "close": "Close"}

_ALL = "*"
# Which FAILING checks grey Approve — the must-resolve set, per gate. Deliberately narrow: a gate is
# listed here only where clicking through would actually fail (`advance_item` 409s on a plan that
# isn't gate-ready; clearance refuses on any red close criterion) or where §2.1's locked table says
# so (an undecided authorization, a failed vet check). `triage-exit` blocks nothing — the route
# accepts an un-triaged advance, so greying it would invent a restriction the backend doesn't have.
_BLOCKING: dict[str, tuple[str, ...] | str] = {
    "triage-exit": (),
    "pre-main": ("plan_complete",),
    "review": ("no_pending_authorizations", "evidence_fresh"),
    "close": _ALL,
}


# --------------------------------------------------------------------------- close criteria (D8)


def close_readiness(item: dict, item_dir: Path, all_items: list[dict]) -> dict:
    """Evaluate the kind's close criteria mechanically → {ok, checks:[{criterion, ok, detail}]}.
    Universal first check: every required artifact exists and passes its self-check.

    NOTHING HERE RE-JUDGES THE WORK. Review's exit locked code + git (§2.3), so a close criterion
    can only ask about things close can still act on. `evidence_fresh` and `knowledge_row_resolved`
    were retired from every profile for that reason (see kind_profiles) — and with them went this
    function's read of the evidence ledger and the knowledge delta, hence the two parameters that
    fed them (`dev_root`, `main_repo_dir`). Every remaining criterion reads the item folder or the
    sibling items, so those are the only inputs left."""
    profile = get_profile(item.get("kind"))
    item_dir = Path(item_dir)
    checks: list[dict] = []

    missing = []
    for kind in profile.required_artifacts:
        issues = A.self_check(item_dir, kind, item_kind=profile.kind)
        if issues:
            missing.append(f"{kind}: {issues[0]}" + (f" (+{len(issues) - 1} more)"
                                                     if len(issues) > 1 else ""))
    checks.append({"criterion": "required_artifacts",
                   "ok": not missing,
                   "detail": "; ".join(missing) or
                             f"all present + clean: {', '.join(profile.required_artifacts)}"})

    for crit in profile.close_criteria:
        if crit == "children_terminal":
            ok, open_ids = status_router.children_terminal(all_items, item["id"])
            checks.append({"criterion": crit, "ok": ok,
                           "detail": f"open children: {', '.join(open_ids)}" if not ok
                                     else "no open children"})
        elif crit == "findings_delivered":
            # The research deliverable is the report the owner decided on, not an artifact:
            # The `review` entry run writes reports/report-review.md (findings.md retired).
            issues = A.report_issues(item_dir, "report-review")
            checks.append({"criterion": crit, "ok": not issues,
                           "detail": "; ".join(issues) or "report-review.md complete"})
        elif crit == "spawns_exist":
            # What must not happen is a research item closing with its proposals silently dropped.
            # `itemize` records the owner's call (adopted, with inbox ids, vs declined) into the
            # report's decision line; an unrecorded decision is one that was never put to them.
            decision = A.owner_decision(item_dir)
            checks.append({"criterion": crit, "ok": bool(decision),
                           "detail": decision or "the report's proposals were never put to you — "
                                                 "run itemize, or record that none were adopted"})
        else:  # an unknown slug fails LOUD-ish: visible, never silently green
            checks.append({"criterion": crit, "ok": False,
                           "detail": "no evaluator for this criterion (kernel gap)"})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


# --------------------------------------------------------------------------- brief assembly

def _strip_fm(text: str) -> str:
    return re.sub(r"(?s)\A---\n.*?\n---\n", "", text or "")


def _artifact_text(item_dir: Path, kind: str) -> str | None:
    p = Path(item_dir) / "artifacts" / A.artifact_file(kind)
    return p.read_text() if p.exists() else None


def _task_ratio(item_dir: Path) -> tuple[int, int]:
    plan = _artifact_text(item_dir, "plan") or ""
    done = len(re.findall(r"(?m)^\s*-\s*\[[xX]\]", plan))
    total = done + len(re.findall(r"(?m)^\s*-\s*\[ \]", plan))
    return done, total


def _numbers(item_dir: Path) -> dict:
    """The brief's real-ratio row (renovation law 2 — counts of real things, never scores):
    task checkbox ratio · vet cycle count · latest-per-check ledger passes vs the plan's check
    total. All derived, nothing stored."""
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
    """When an item rests `awaiting_human` for a REASON beyond 'a clean gate is waiting' — a deputy
    escalation, a build⟷vet loop halt, or a blocked run — name it, so the drilldown can LEAD with why
    it paused and what to decide instead of previewing a future gate. Reads events newest-first and
    returns the first parking cause; stops at the last `phase.advance` (older events belong to a prior
    phase). None ⇒ a plain gate wait, render as before. Pure — {source, gate, headline, detail, next}."""
    if str(item.get("status")) != "awaiting_human":
        return None
    # The blocked/failed run report that explains a halt sits OLDER than the halt marker — grab it up
    # front so the loop branch below can quote its summary + owner-facing `next`.
    blocked = next(({**(e.get("meta") or {})} for e in events
                    if str(e.get("kind")) == "run.report"
                    and str((e.get("meta") or {}).get("outcome")) in ("blocked", "failed", "unverified")),
                   None)
    for e in events:  # newest-first
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
        if kind == "phase.advance":
            break
    if blocked:  # a blocked run with no explicit halt marker (defensive)
        return {"source": "agent", "gate": None,
                "headline": f"The {item.get('phase')} run stopped without finishing.",
                "detail": str(blocked.get("summary") or ""),
                "next": str(blocked.get("next") or "") or None}
    return None


def _mark_blocking(gate: str, checks: list[dict]) -> list[str]:
    """Stamp `blocking` on every check row and return the reasons Approve is greyed (in row order).
    ONE place decides this, so the drilldown's button and the deputy's must-resolve set can never
    disagree — the same rule as `attention.py` owning the needs-you buckets."""
    must = _BLOCKING.get(gate, ())
    for c in checks:
        c["blocking"] = must == _ALL or c["criterion"] in must
    return [c["detail"] for c in checks if c["blocking"] and not c["ok"]]


def gate_state(item: dict, item_dir: Path, dev_root: Path,
               main_repo_dir: Path | None, *, all_items: list[dict] | None = None,
               events: list[dict] | None = None,
               ) -> dict:
    """One gate's MECHANICAL state, typed — no prose, no recommendation, no embedded artifact.

    → {id, gate, gate_label, at_gate, phase, title, terminal, checks[{criterion, ok, detail,
    blocking}], blocked_by[], numbers, paged, authorizations[]}.

    For a phase between gates (build/vet/investigate) it reports the NEXT gate with
    `at_gate: False` — the surface still shows what that gate will ask. `events` = this item's
    dev-log rows newest-first. Branch freshness is NOT read here — see the review gate's note.
    `blocked_by` is empty ⇔ Approve is live.

    Every consumer reads THIS: the drilldown route projects it into buttons + rows, and
    `kernel_speech.deputy_brief_block` projects it into the deputy's prompt (§2.1). There is no
    second computation of a gate's checks anywhere.
    """
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
        # brief.md is triage's product (renovation §3.1); pre-renovation items sharpened the item
        # body instead — read whichever exists.
        item_body = (_strip_fm(_artifact_text(item_dir, "brief") or "").strip()
                     or str(item.get("description") or "").strip())
        # F1 (playground-e2e-blockers): ready = the `triaged_at` stamp, written only by triage's
        # recording tool (set_triage_classification). The old `kind set + body filled` check was a
        # tautology — an inbox push satisfies both without any triage agent running.
        ready = bool(item.get("triaged_at"))
        checks.append({"criterion": "triage_ran", "ok": ready,
                       "detail": (f"classification recorded {item.get('triaged_at')}" if ready
                                  else "no classification recorded (set_triage_classification "
                                       "never ran)")
                                 + f" · kind={item.get('kind') or 'unset'}, "
                                   f"body {'filled' if item_body.strip() else 'empty'}"})
    elif gate == "pre-main":
        plan = _strip_fm(_artifact_text(item_dir, "plan") or "")
        issues = A.self_check(item_dir, "plan", item_kind=profile.kind)
        done, total = _task_ratio(item_dir)
        checks.append({"criterion": "plan_complete", "ok": not issues,
                       "detail": "; ".join(issues) or f"plan clean, {total} task(s)"})
        # The vet-plan judgment surface (build⟷vet §3.4 SOFT): depth+reason are a call the owner can
        # veto HERE (cheapest moment — before tokens burn on building), and a vague `expect` phrasing
        # is a NON-BLOCKING row, never a refusal — a human is present, the one fail-open that's safe.
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
        # Every re-routing round owes a revision block (§2.1): `revise` is the only way back here,
        # it always records a `review.route` event, and the only way to change plan.md is
        # `revise_plan`, which always writes the block. A round with no block means the plan was
        # hand-edited — so which feedback drove what is unrecoverable, and the next build reads a
        # plan it cannot tell has changed. Visible and named; it does not grey Approve, because the
        # move it asks for is a conversation, not a click.
        rounds = sum(1 for e in events if e.get("kind") == "review.route")
        if rounds:
            revs = plan_revision.revisions(item_dir)
            checks.append({
                "criterion": "revisions_recorded", "ok": len(revs) >= rounds,
                "detail": (f"{len(revs)} revision block(s) for {rounds} feedback round(s): "
                           + ", ".join(revs)) if len(revs) >= rounds else
                          (f"{rounds} feedback round(s) but only {len(revs)} revision block(s) — "
                           f"fold the feedback in with `revise_plan`, never by rewriting plan.md")})
    elif gate == "review":
        wt = item.get("git_worktree")
        ev_repo = Path(str(wt)) if wt and Path(str(wt)).is_dir() else main_repo_dir
        ev = A.evidence_status(item_dir, ev_repo)
        # The STATUS WORD alone ("stale") answered nothing anyone asked: it named a state without
        # naming its cause or its exit, so a gate that went red the instant the owner pressed Sync
        # read as a malfunction. Each status now says what happened and what clears it.
        n = ev.get("entries", 0)
        detail = {
            "passed": f"all {n} recorded checks pass, and the code hasn't moved since they ran",
            "stale": (f"{n} recorded checks pass, but the code moved after they ran (a sync or a "
                      f"commit) — one vet cycle re-runs them against the current tree"),
            "failed": f"a recorded check is failing ({n} entries) — build fixes it, then vet re-runs",
            "deferred": f"a check is waiting on an authorization you haven't granted ({n} entries)",
            "unverified": "nothing recorded yet — vet writes the ledger",
        }.get(str(ev.get("status")), f"evidence ledger: {ev.get('status')} ({n} entries)")
        checks.append({"criterion": "evidence_fresh", "ok": ev["status"] == "passed",
                       "detail": "no checks were owed — the approved plan declares `depth: none`"
                                 if ev.get("not_required") else detail})
        # NO `git_fresh` row (owner, 2026-08-01 — removed with the manual Sync button). It was the
        # only check that could neither block nor be acted on: behind-trunk is a FACT, the merge act
        # syncs and re-measures at the instant that matters, and the same number already reads
        # verbatim on the Git tab (`vs main: ahead N · behind N`). A permanently-inert row in a list
        # of things that gate the button trained the eye to skim the list.
        # BV-A2: deferred contract changes the build couldn't self-authorize surface HERE for the
        # owner's grant/deny — a pending request holds the merge (deferred ≠ passed), and each rides
        # the typed `authorizations` feed the FE renders Grant/Deny against. `delegable` tells the
        # owner whether the deputy could have granted it (sync-to-reality) or it's reserved (why it's
        # on their desk); the owner grants either way.
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
    else:  # close
        checks = close_readiness(item, item_dir, all_items)["checks"]

    return {"id": item["id"], "gate": gate, "gate_label": _GATE_LABEL[gate],
            "at_gate": at_gate, "phase": phase, "title": str(item.get("title") or item["id"]),
            "terminal": terminal, "checks": checks,
            "blocked_by": _mark_blocking(gate, checks),
            "numbers": _numbers(item_dir),
            "paged": _page_reason(item, events), "authorizations": authorizations}
