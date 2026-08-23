"""The build⟷vet loop driver and its breakers — the daemon-side autonomy.

The loop never parks: every exit lands the item at review with a typed reason. Both breakers
meter one generation, so a plan revision refreshes the budget.
"""

import asyncio
import logging
import time
from dataclasses import replace
from pathlib import Path

from ..app_state import agent as _agent, dev as _dev, dev_store as _dev_store, \
    spine as _spine, sessions as _sessions
from ...core import Usage, Result, Status, TextDelta, ToolResult, deny_all
from ...core import artifacts as _arts
from ...core import autopilot as _autopilot
from ...core import kernel_speech
from ...core import plan_revision as _plan_revision
from . import checks as _checks, run_tasks
from ...core.vocab.sandbox import kernel_available
from .turns import ResilientTurn
from ...harness.tools.run_tools import make_run_report_server
from ...core.permissions import VET_READONLY_NUDGE
from ...harness.tools.dev_tools import make_dev_mcp_server
from .runs import (LiveTokens, begin_run, end_run, surface_from_turn,
                   bank_auto_checkpoint, capture_event, capture_prompt, capture_run_input,
                   compacted_checkpoint, ensure_completion, fire_auto_triage, mark_item_error, read_completion,
                   reset_vet_thread, retry_notice)

log = logging.getLogger("superme-agent")


# --------------------------------------------------------------------------- decision (pure)

# One repeat is not proof of a wall — a build can close in while the failure text holds still.
_MAX_RECURRENCE = 3
# A fault is not an attempt: retries reuse the cycle and never touch the convergence guard.
_MAX_FAULT_RETRY = 2


def decide_after_vet(item: dict, *, evidence: dict, fingerprint: str, attempts: list[dict],
                     spent: int, budget: int, turn_error: bool = False,
                     faults: int = 0, fault_reason: str = "", lens_gaps: list[dict] | None = None,
                     audit_gaps: list[dict] | None = None) -> dict:
    """The driver's decision function — PURE, so every branch is unit-testable. Returns
    {action, status, reason, record[, exit]}.

    An item never rests inside build⟷vet, which has no decision surface. `error` is not resting:
    it stays where the run died."""
    # The driver continues ONLY an active item; a human-set stop never auto-resumes.
    status = str(item.get("status") or "")
    if item.get("done_at") or status != "active":
        return {"action": "none", "status": status, "record": False,
                "reason": f"item not active (status={status or 'unset'}) — loop yields to the owner"}
    # CAS pre-check: the owner (or another path) moved the item off vet mid-run — theirs wins.
    if str(item.get("phase")) != "vet":
        return {"action": "none", "status": status, "record": False,
                "reason": f"item left vet (phase={item.get('phase')}) — loop yields"}
    ev = str(evidence.get("status") or "unverified")
    # The run STOPPED, and the retry ladder already waited it out, so the item stops where it
    # died.
    if turn_error:
        return {"action": "error", "status": "error", "record": True, "exit": "error",
                "fault": "the vet run stopped before it could report",
                "reason": fault_reason or "the vet run stopped before it could report"}
    # An empty ledger where the plan declared checks means the recording machinery failed, which
    # says nothing about the work.
    if ev == "unverified":
        fault = "vet recorded nothing while the plan declares checks"
        if faults < _MAX_FAULT_RETRY:
            return {"action": "revet", "status": "active", "record": False, "fault": fault,
                    "reason": f"{fault} — retrying (attempt {faults + 2} of {_MAX_FAULT_RETRY + 1})"}
        return {"action": "review", "status": "awaiting_human", "record": True,
                "exit": "system_fault", "fault": fault,
                "reason": f"{fault}, {_MAX_FAULT_RETRY + 1} times — this is a SuperMe fault, not a "
                          "verdict on the work; handing it to you with the trace"}
    # --- work-item outcomes ------------------------------------------------------------------
    if ev == "stale":
        # The tree moved under a green ledger, so re-vet. The fingerprint tracks committed content
        # only, so litter cannot fake it.
        return {"action": "revet", "status": "active", "record": True,
                "reason": "evidence is green but the code moved since it was recorded — re-vetting",
                "stale": evidence.get("stale_checks") or []}
    # A gating lens finding routes back to build like any failure: a lens is not a plan check.
    lens_failed = [f"lens:{g['lens']}" for g in (lens_gaps or [])]
    # Same for a validation claim the kernel could not reproduce — the cycle's own account of
    # itself failing to hold.
    audit_failed = [f"validation:{a['command']}" for a in (audit_gaps or [])]
    if ev in ("deferred", "passed") and (lens_failed or audit_failed):
        # The checks proved what the plan thought to ask; this is the part nobody thought to ask.
        ev = "failed"
    if ev == "deferred":
        # A check walled behind an authorization the build cannot self-grant advances to review
        # carrying the request.
        deferred = list(evidence.get("deferred_checks") or [])
        return {"action": "review", "status": "awaiting_human", "record": True, "deferred": deferred,
                "exit": "converged",
                "reason": f"{len(deferred)} check(s) deferred pending authorization — advancing to "
                          "review to grant or deny"}
    if ev == "passed":
        return {"action": "review", "status": "awaiting_human", "record": True, "exit": "converged",
                "reason": "every check green and fresh — advancing to the review gate"}
    # failed → the breakers decide whether another cycle is spent.
    failed = list(evidence.get("failed_checks") or []) + lens_failed + audit_failed
    if spent >= budget:
        return {"action": "review", "status": "awaiting_human", "record": True, "failed": failed,
                "exit": "budget",
                "reason": f"token budget exhausted ({spent} ≥ {budget} this generation) — handing "
                          "over what got done"}
    # A recurrence after an intervening different failure is the oscillation a compare-with-
    # previous test misses.
    seen = sum(1 for a in attempts if str(a.get("fingerprint") or "") == fingerprint) if fingerprint else 0
    if seen + 1 >= _MAX_RECURRENCE:
        return {"action": "review", "status": "awaiting_human", "record": True, "failed": failed,
                "exit": "not_converging",
                "reason": f"the same failure has now come back {seen + 1} times — the loop is not "
                          "converging on it; handing it to you"}
    # Name what actually failed: "1 check(s) failed" for a safety finding prints bookkeeping, not
    # an answer.
    n_checks = len(failed) - len(lens_failed) - len(audit_failed)
    what = ", ".join(filter(None, [
        f"{n_checks} check(s) failed" if n_checks else "",
        ", ".join(f"the {g['lens']} lens raised a {g['severity']} finding"
                  for g in (lens_gaps or [])),
        ", ".join(f"the build's `{a['command']}` claim did not reproduce"
                  for a in (audit_gaps or []))]))
    return {"action": "build", "status": "active", "record": True, "failed": failed,
            "reason": f"{what} — handing the vet report to a build cycle"}


def decide_after_build(item: dict, *, outcome: str | None, turn_error: bool) -> dict:
    """The build-cycle exit decision — PURE. A build NEVER pages mid-loop: a wall becomes an
    assumption or a deferred authorization.

    `needs_user` is the exception, because riding to review rests on the work being ON THE BRANCH."""
    moved_away = (bool(item.get("done_at")) or str(item.get("status")) != "active"
                  or str(item.get("phase")) != "build")
    if moved_away:
        return {"stopping": True, "klass": "moved"}
    if turn_error:
        return {"stopping": True, "klass": "infra"}
    if outcome == "needs_user":
        return {"stopping": True, "klass": "needs_user"}
    # Build concluded the PLAN is wrong and cannot fix that itself, so the item routes to plan.
    if outcome == "revise":
        return {"stopping": True, "klass": "revise"}
    return {"stopping": False, "klass": "advance"}


# --------------------------------------------------------------------------- shared plumbing

def _cas_phase(dev_root: Path, item_id: str, frm: str, to: str) -> bool:
    """Compare-and-swap the item's phase: re-read, write only if it still reads `frm`. Synchronous
    throughout, so it is atomic under the single asyncio loop."""
    cur = _dev.read_work_item(dev_root, item_id) or {}
    if str(cur.get("phase")) != frm:
        return False
    return bool(_dev.set_work_item_phase(dev_root, item_id, to))


def _loop_ctx(ctx, item: dict):
    """The (worktree_ctx, worktree, item_dir, dev_root) tuple a loop run needs, or None when the item
    has no live worktree."""
    dev_root = ctx.internal_root / "dev"
    item_dir = dev_root / "work-items" / str(item["id"])
    wt = Path(str(item.get("git_worktree") or ""))
    if not (item.get("git_worktree") and wt.is_dir()):
        return None
    return replace(ctx, cwd=wt), wt, item_dir, dev_root


def _resolve_run_params(context_id: str, item: dict) -> tuple[str, str]:
    """(model, effort) for a BUILD run, through the same precedence every other item run resolves:
    item → repo → system."""
    return (_spine.effective_model(context_id, item_model=item.get("model")),
            _spine.effective_effort(context_id, item_effort=item.get("effort")))


def _resolve_vet_params(context_id: str, item: dict) -> tuple[str, str]:
    """(model, effort) for a VET run — its own chain, NOT the item's or the project's.

    Inheriting build's tier made the check move with the thing it checks, so a cheap-builder,
    expensive-judge pairing was unreachable."""
    return (_spine.role_model(context_id, "vet", item_model=item.get("vet_model")),
            _spine.role_effort(context_id, "vet", item_effort=item.get("vet_effort")))


def dev_mcp(ctx, wt: Path, main_repo_dir: Path, item_id: str, *, scope: str) -> dict:
    """The dev MCP server for a background loop run: pens scoped to this item, `repo_dir` at the
    worktree so evidence fingerprints the vetted tree.

    `scope` is the phase this run IS — build carries its own recorder and never vet's."""
    return {"dev": make_dev_mcp_server(
        _dev_store, ctx.id, spine=_spine, scope=scope,
        dev_root=ctx.internal_root / "dev",
        repo_dir=wt, main_repo_dir=main_repo_dir,
        bound_item_id=item_id,
        fire_triage=lambda child_id: fire_auto_triage(ctx.id, child_id, _spine),
    )}


def _log_decision(context_id: str, item_id: str, cycle: int, d: dict) -> None:
    _dev_store.log_event(context_id, "loop.decision",
                         f"Loop: {d['action']} — {d['reason'][:160]}",
                         item_id=item_id, actor="daemon",
                         meta={"action": d["action"], "cycle": cycle,
                               "exit": d.get("exit") or "",
                               "fault": d.get("fault") or "",
                               "failed": d.get("failed") or []})


def _tree_moved_since_evidence(item_dir: Path, wt) -> bool:
    """Has the worktree changed since the last evidence entry? True when there is no evidence yet — the
    opening cycle must always be vetted.

    Same fingerprint the ledger stamps: one primitive, two readers."""
    entries = _arts.evidence_entries(item_dir)
    last = next((str(e.get("fingerprint") or "") for e in reversed(entries) if e.get("fingerprint")), "")
    if not last or last == "no-git":
        return True
    return _arts.repo_fingerprint(wt) != last


def _plan_moved_since_evidence(item_dir: Path) -> bool:
    """Has the plan been REVISED since the last verdict?

    Vet grades the tree AGAINST the plan, so the verdict has two inputs and the fingerprint sees
    one. A revision changing how verification happens is a reason to verify."""
    entries = _arts.evidence_entries(item_dir)
    if not entries:
        return True
    by_cycle = {r["cycle"]: str(r.get("revision") or "") for r in _arts.cycle_reports(item_dir)}
    return by_cycle.get(entries[-1].get("cycle"), "") != _plan_revision.current_revision(item_dir)


def _exit_no_progress(ctx, context_id: str, item_id: str, dev_root: Path, item_dir: Path) -> None:
    """Flip vet → review and hand the item over WITHOUT spending a vet run. Records the same cycle
    outcome and decision event as any other exit."""
    d = {"action": "review", "status": "awaiting_human", "record": True, "exit": "no_progress",
         "reason": "the build cycle changed nothing in the tree — no point vetting the same code "
                   "again; handing it to you"}
    if not _cas_phase(dev_root, item_id, "vet", "review"):
        log.info("no-progress exit lost its CAS for %s — another actor moved it", item_id)
        return
    reports = _arts.cycle_reports(item_dir)
    cycle = reports[-1]["cycle"] if reports else 0
    try:
        _arts.append_cycle_outcome(item_dir, evidence="unchanged", decision="review",
                                   reason=d["reason"])
    except Exception:
        log.exception("cycle-outcome append failed for %s", item_id)
    _log_decision(context_id, item_id, cycle, d)
    _dev_store.log_event(context_id, "phase.advance",
                         f"Loop: {d['reason'][:120]} — advanced vet → review",
                         item_id=item_id, actor="daemon",
                         meta={"from": "vet", "to": "review", "exit": "no_progress"})
    _dev.set_work_item_status(dev_root, item_id, "awaiting_human")
    from .runs import fire_review_entry
    if not fire_review_entry(context_id, item_id, _spine):
        log.warning("loop: review-entry run did not start for %s", item_id)


def _fault_retries(context_id: str, item_id: str, cycle: int) -> int:
    """How many times THIS cycle was retried after a SuperMe fault — read back from the decision
    trail, so a daemon restart cannot reset the counter."""
    try:
        rows = _dev_store.list_events(context_id, item_id=item_id, limit=40)
    except Exception:
        return 0
    return sum(1 for e in rows
               if str(e.get("kind")) == "loop.decision"
               and (e.get("meta") or {}).get("fault")
               and int((e.get("meta") or {}).get("cycle") or -1) == cycle)


# --------------------------------------------------------------------------- vet runs

def start_vet_run(ctx, context_id: str, item_id: str) -> tuple[bool, str]:
    """Start one background vet run — the loop's DECISION hop. Guards: live worktree, runnable at
    `vet`, no run in flight.

    Retires any previous vet thread first: vet FORGETS, fresh eyes every cycle."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    if not item:
        return False, "work-item not found"
    if item.get("done_at") or str(item.get("status")) not in ("active", "awaiting_human"):
        return False, f"item is not runnable (status={item.get('status')})"
    if str(item.get("phase")) != "vet":
        return False, f"item is not in vet (phase={item.get('phase')})"
    lc = _loop_ctx(ctx, item)
    if lc is None:
        return False, "item has no live worktree — nothing to vet"
    reset_vet_thread(ctx, item)   # fresh per cycle (no-op when the slot is already clear)
    model, effort = _resolve_vet_params(context_id, item)
    if not begin_run(ctx, context_id, item_id, "vet", model, phase="vet"):
        return False, "a run is already in progress for this item"
    # A paged item the owner just re-launched is active again (the launch IS the answer);
    # begin_run rested it already.
    run_tasks.track(asyncio.create_task(_run_background_vet(ctx, context_id, item_id, model, effort)))
    return True, "vet"


async def _run_background_vet(ctx, context_id: str, item_id: str,
                            model: str, effort: str) -> None:
    """Drive one fresh background vet turn at the item's worktree, then hand the outcome to the
    driver. Read-only on files, with the freeze-boundary shell. Always a fresh session."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    lc = _loop_ctx(ctx, item)
    if lc is None:   # worktree vanished between start and run
        end_run(ctx, context_id, item_id, None, "awaiting_human", None, outcome="blocked")
        return
    wt_ctx, wt, item_dir, _ = lc
    # ONE definition of what this run may write to: the turn, the sandbox and the X-ray all read
    # this list.
    boundary = [wt, item_dir]
    title = item.get("title") or item_id
    # Name the checks the build DEFERRED, so the vetter skips a wall only the owner can clear.
    deferred = [str(a.get("check")) for a in _arts.pending_authorizations(item_dir) if a.get("check")]
    # The kernel runs what it can first, so the vetter meets those results as facts, not work.
    try:
        machine = await asyncio.to_thread(_checks.execute, item_dir, wt,
                                          skip=deferred, title=title)
    except Exception:
        log.exception("kernel checks failed for %s — vet proceeds and performs them itself", item_id)
        machine = []
    # Validation stays build's work; witnessing it is verification's. A disagreement is a finding
    # about the build, not a check.
    cycle_now = (_arts.cycle_reports(item_dir) or [{}])[-1].get("cycle")
    try:
        audit = await asyncio.to_thread(_checks.audit_validation, item_dir, wt, cycle=cycle_now)
    except Exception:
        log.exception("validation audit failed for %s — vet proceeds without it", item_id)
        audit = []
    # The trigger carries the repo's boot command, so a check never uses whatever server is up.
    has_vet_env = bool(getattr(_spine.repos().get(context_id), "vet_env", None))
    trigger = kernel_speech.vet_trigger(item_id, title, deferred=deferred or None,
                                        machine=machine or None, audit=audit or None,
                                        vet_env=has_vet_env, kernel=kernel_available())
    prompt = trigger  # orientation is on-demand, through the skill's directed reads
    capture_prompt(context_id, trigger, item_id=item_id)
    # Throwaway probes only; normal items skip capture.
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — recorded; the DRIVER decides
    # The turn carries its own retry ladder, so a vet that never got off the ground is waited out
    # here.
    turn = ResilientTurn("vet", item_id=item_id,
                         notify=retry_notice(context_id, item_id, "vet"))
    # Built once, then both SNAPSHOTTED and SENT — see `runs.surface_from_turn`.
    turn_kwargs = dict(
        resume=None,                     # vet FORGETS — fresh eyes, prior reports are data
        model=model, effort=effort,
        approve=deny_all,                # background: nothing outside the boundary runs
        extra_mcp_servers={**dev_mcp(ctx, wt, ctx.cwd, item_id, scope="vet"),
                           "run": make_run_report_server(sink)},
        system_append=kernel_speech.work_item_preamble(item_id, item, str(item_dir), interactive=False),
        item_bound=True,                 # one item is the subject — no board-wide in-progress list
        write_boundary=boundary,         # boundary Bash autonomy (running checks IS the job)
        sandbox_writes=boundary,         # …and the kernel holds that same boundary (sandbox.py)
        deny_write_tools=VET_READONLY_NUDGE,  # …but file-write tools die outright
    )
    # Throwaway probes only; normal items skip capture.
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=wt_ctx,
                          system_append=turn_kwargs["system_append"],
                          prompt=prompt, phase="vet",
                          surface=surface_from_turn(turn_kwargs, mcp=["dev", "run"]),
                          background=True)
    async for ev in turn.stream(_agent, wt_ctx, prompt, **turn_kwargs):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens = ev.tokens
            # Accumulated per-message usage (parent + subagents), not the parent-only
            # `Result.usage`; falls back when no Usage step arrived.
            final_usage = live.usage(ev.usage) or ev.usage
            final_session = ev.session_id
            _sessions.record(wt_ctx, ev.session_id)
            if ev.session_id:
                try:
                    _dev.set_work_item_session(dev_root, item_id, ev.session_id, slot="vet")
                    _spine.stamp_session_item(ev.session_id, item_id)
                    _spine.stamp_session_kind(ev.session_id, "vet")
                except Exception:
                    log.exception("background vet: failed to persist session to %s", item_id)
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    turn_error = turn.fault.failed
    # Trail honesty only: the driver below decides off the LEDGER, never off vet's own payload.
    await ensure_completion(ctx, context_id, item_id, sink, skill="vet",
                            session_id=final_session, model=model, effort=effort)
    # ---- THE DRIVER: decide off the ledger, close the run, apply, fire the next hop.
    item = _dev.read_work_item(dev_root, item_id) or {}
    evidence = _arts.evidence_status(item_dir, wt)
    # The plan judged this item to have no observable surface, so the empty ledger is right.
    # Kernel-written, not vet's claim.
    if evidence.get("not_required"):
        try:
            _arts.note_no_verification(item_dir)
        except (OSError, ValueError):
            log.exception("no-verification note failed for %s", item_id)
    gaps = _arts.lens_gaps(item_dir)
    audit_gaps = _arts.validation_discrepancies(item_dir, cycle=cycle_now)
    # A lens finding or an unreproducible validation claim is a wall like any other, so both
    # belong in the signature.
    fingerprint = _arts.convergence_fingerprint(
        item_dir, extra=[g["text"] for g in gaps] + [f"validation:{a['command']}" for a in audit_gaps])
    # Both breakers read THIS generation only: a revision refreshes the budget, so pre-redesign
    # history stops counting.
    revision = _plan_revision.current_revision(item_dir)
    attempts = _arts.read_cycle_outcomes(item_dir, revision=revision)
    reports = _arts.cycle_reports(item_dir)
    cycle = reports[-1]["cycle"] if reports else 0
    spent = max(0, _spine.item_phase_tokens(context_id, item_id)
                - _plan_revision.spend_at(item_dir))
    budget = _spine.effective_loop_budget(context_id, item.get("loop_budget"))
    d = decide_after_vet(item, evidence=evidence, fingerprint=fingerprint, attempts=attempts,
                         spent=spent, budget=budget, turn_error=turn_error,
                         faults=_fault_retries(context_id, item_id, cycle),
                         fault_reason=turn.fault.reason, lens_gaps=gaps, audit_gaps=audit_gaps)
    # CAS flips happen BEFORE the run row closes so the next hop's row stamps the new phase.
    moved = True
    if d["action"] == "review":
        moved = _cas_phase(dev_root, item_id, "vet", "review")
    elif d["action"] == "build":
        moved = _cas_phase(dev_root, item_id, "vet", "build")
    if not moved:   # lost the CAS — someone else moved the item; theirs wins, loop yields
        d = {"action": "none", "status": str(item.get("status") or "active"), "record": False,
             "reason": "phase flip lost its CAS — another actor moved the item; loop yields"}
    # The run outcome mirrors the typed exit. `blocked` says no verdict was produced, never that
    # the work is bad.
    outcome = {"converged": "success", "budget": "exhausted",
               "not_converging": "stagnated", "no_progress": "stagnated",
               "system_fault": "blocked", "error": "blocked"}.get(str(d.get("exit") or "")) \
        or ("blocked" if turn_error else "success" if d["action"] in ("build", "revet") else None)
    # The item stops where it died: advancing past work that never happened is the lie this status
    # exists to stop.
    if d["action"] == "error":
        mark_item_error(ctx, context_id, item_id, d["reason"], phase="vet")
    end_run(ctx, context_id, item_id, final_tokens, d["status"] or "active", final_usage,
             outcome=outcome, session_id=final_session)
    if d["record"]:
        try:
            _arts.append_cycle_outcome(item_dir, evidence=str(evidence.get("status")),
                                       decision=d["action"], reason=d["reason"],
                                       loop_exit=str(d.get("exit") or ""),
                                       fingerprint=fingerprint if evidence.get("status") == "failed" else "",
                                       failed=d.get("failed") or (), tokens=spent, budget=budget)
        except Exception:
            log.exception("cycle-outcome append failed for %s", item_id)
    _log_decision(context_id, item_id, cycle, d)
    if d["action"] == "review":
        _dev_store.log_event(
            context_id, "phase.advance",
            ("Loop: all checks green — advanced vet → review" if d.get("exit") == "converged"
             else f"Loop: {d['reason'][:120]} — advanced vet → review"),
            item_id=item_id, actor="daemon",
            meta={"from": "vet", "to": "review", "exit": d.get("exit") or ""})
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after vet failed")
    # Fire the next hop AFTER the run row is closed (the per-item run-lock frees above).
    if d["action"] == "review":
        # This hop CAS-flips the phase itself and never calls `advance_item`, so the entry run
        # fires here too.
        from .runs import fire_review_entry
        if not fire_review_entry(context_id, item_id, _spine):
            log.warning("loop: review-entry run did not start for %s", item_id)
    elif d["action"] == "build":
        # Build's run boundary: the vet row is closed and the build's not yet open, so the lock is
        # free.
        try:
            from . import compaction
            await compaction.compact_before_run(
                ctx, context_id, item_id, (item.get("sessions") or {}).get("build"),
                kind=item.get("kind"), model=_resolve_run_params(context_id, item)[0])
        except Exception:
            log.exception("run-start compaction check failed for build cycle %s", item_id)
        started, why = start_build_cycle(ctx, context_id, item_id)
        if not started:
            log.warning("loop: build cycle did not start for %s: %s", item_id, why)
    elif d["action"] == "revet":
        started, why = start_vet_run(ctx, context_id, item_id)
        if not started:
            log.warning("loop: re-vet did not start for %s: %s", item_id, why)
    log.info("background vet: %s for %s (%s)", d["action"], item_id, d["reason"])


# --------------------------------------------------------------------------- build cycles

def start_first_build(ctx, context_id: str, item_id: str) -> tuple[bool, str]:
    """Start the loop's OPENING build cycle: nothing is implemented yet, so the work order is the
    PLAN, not a vet report.

    Guards mirror `start_build_cycle` except the vet-report requirement — a vet against an empty
    tree is wasted."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    if not item:
        return False, "work-item not found"
    if item.get("done_at") or str(item.get("status")) != "active":
        return False, f"item is not runnable (status={item.get('status')})"
    if str(item.get("phase")) != "build":
        return False, f"item is not in build (phase={item.get('phase')})"
    if _loop_ctx(ctx, item) is None:
        return False, "item has no live worktree"
    title = item.get("title") or item_id
    trigger = kernel_speech.build_first_trigger(
        item_id, title,
        vet_env=bool(getattr(_spine.repos().get(context_id), "vet_env", None)))
    model, effort = _resolve_run_params(context_id, item)
    if not begin_run(ctx, context_id, item_id, "build", model, phase="build"):
        return False, "a run is already in progress for this item"
    run_tasks.track(asyncio.create_task(
        _run_background_build(ctx, context_id, item_id, model, effort, trigger=trigger)))
    return True, "build"


def start_build_cycle(ctx, context_id: str, item_id: str) -> tuple[bool, str]:
    """Start one background build cycle: the build session, which REMEMBERS, fixes what the latest
    vet report describes."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    if not item:
        return False, "work-item not found"
    if item.get("done_at") or str(item.get("status")) != "active":
        return False, f"item is not runnable (status={item.get('status')})"
    if str(item.get("phase")) != "build":
        return False, f"item is not in build (phase={item.get('phase')})"
    lc = _loop_ctx(ctx, item)
    if lc is None:
        return False, "item has no live worktree"
    if _arts.latest_cycle_report(dev_root / "work-items" / item_id) is None:
        return False, "no cycle report to hand over — a loop build cycle needs one"
    model, effort = _resolve_run_params(context_id, item)
    if not begin_run(ctx, context_id, item_id, "build", model, phase="build"):
        return False, "a run is already in progress for this item"
    run_tasks.track(asyncio.create_task(_run_background_build(ctx, context_id, item_id, model, effort)))
    return True, "build"


async def _run_background_build(ctx, context_id: str, item_id: str,
                              model: str, effort: str, *, trigger: str | None = None) -> None:
    """Drive one background build turn in the item's worktree, then flip the item into vet and fire
    the next vet run. RESUMES the item's build thread — build REMEMBERS.

    The work order is injected once into this cycle's trigger."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    lc = _loop_ctx(ctx, item)
    if lc is None:
        end_run(ctx, context_id, item_id, None, "awaiting_human", None, outcome="blocked")
        return
    wt_ctx, wt, item_dir, _ = lc
    boundary = [wt, item_dir]    # one definition — turn, sandbox, and X-ray capture all read it
    prev_build = (item.get("sessions") or {}).get("build")
    title = item.get("title") or item_id
    # Build REMEMBERS, so the skill is invoked once; a compaction can cut it out of context.
    compacted = compacted_checkpoint(ctx, item, prev_build)
    if trigger is None:
        report = _arts.latest_cycle_report(item_dir)  # capped handoff
        # Vet's located causes lead the work order; a build that has to find them re-derives what
        # vet already knew.
        found = {r["check"]: r for r in _arts.verdict_rows(item_dir)
                 if r.get("why") and not r["passed"] and not r["deferred"]}
        # A gating lens finding arrives with no `where` of its own — the finding text carries the
        # location.
        found.update({f"the {g['lens']} lens": {"where": f"{g['severity']} finding",
                                                "why": g["text"]}
                      for g in _arts.lens_gaps(item_dir)})
        trigger = kernel_speech.build_loop_trigger(item_id, title, report["cycle"], report["text"],
                                                   reload_skill=bool(compacted),
                                                   diagnoses=found or None)
    # Idempotent: the open cycle's file may already exist, e.g. after a continue on a parked
    # build.
    try:
        _arts.scaffold_cycle(item_dir, title=title)
    except Exception:
        log.exception("cycle-report scaffold failed for %s", item_id)
    prompt = trigger  # orientation is on-demand, through the skill's directed reads
    capture_prompt(context_id, trigger, item_id=item_id)
    # Throwaway probes only; normal items skip capture.
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    # An upstream API error arrives as assistant TEXT, so without a classifier the turn looks like
    # a clean no-op.
    turn = ResilientTurn("build", item_id=item_id,
                         notify=retry_notice(context_id, item_id, "build"))
    # Built once, then both SNAPSHOTTED and SENT — see `runs.surface_from_turn`.
    turn_kwargs = dict(
        resume=prev_build,               # build REMEMBERS — same thread every cycle
        model=model, effort=effort,
        approve=deny_all,
        extra_mcp_servers={**dev_mcp(ctx, wt, ctx.cwd, item_id, scope="build"),
                           "run": make_run_report_server(sink)},
        # Build REMEMBERS, so it is the other thread compaction can hit.
        system_append=kernel_speech.work_item_preamble(
            item_id, item, str(item_dir), interactive=False,
            compacted_checkpoint=compacted),
        item_bound=True,                 # one item is the subject — no board-wide in-progress list
        write_boundary=boundary,  # writes stay in the worktree and item dir
        sandbox_writes=boundary,         # …enforced for shell commands by the OS (sandbox.py)
    )
    # Throwaway probes only; normal items skip capture.
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=wt_ctx,
                          system_append=turn_kwargs["system_append"],
                          prompt=prompt, phase="build",
                          surface=surface_from_turn(turn_kwargs, mcp=["dev", "run"]),
                          background=True)
    async for ev in turn.stream(_agent, wt_ctx, prompt, **turn_kwargs):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens = ev.tokens
            # Accumulated per-message usage (parent + subagents), not the parent-only
            # `Result.usage`; falls back when no Usage step arrived.
            final_usage = live.usage(ev.usage) or ev.usage
            final_session = ev.session_id
            _sessions.record(wt_ctx, ev.session_id)
            if ev.session_id:
                try:
                    _dev.set_work_item_session(dev_root, item_id, ev.session_id, slot="build")
                    _spine.stamp_session_item(ev.session_id, item_id)
                    _spine.stamp_session_kind(ev.session_id, "build")
                except Exception:
                    log.exception("background build: failed to persist session to %s", item_id)
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    turn_error = turn.fault.failed
    report_out = await ensure_completion(ctx, context_id, item_id, sink, skill="build",
                                         session_id=final_session, model=model, effort=effort)
    item = _dev.read_work_item(dev_root, item_id) or {}
    outcome = (report_out or {}).get("outcome")
    d = decide_after_build(item, outcome=outcome, turn_error=turn_error)
    if d["stopping"]:
        # `needs_user` is the one state that legitimately rests inside the loop: nothing landed on
        # the branch to review.
        reports = _arts.cycle_reports(item_dir)
        cycle = reports[-1]["cycle"] if reports else 0
        if d["klass"] == "infra":
            # The build run STOPPED after the retry ladder was spent; the item stops at `build`.
            reason = turn.fault.reason or "the build run stopped before it could report"
            mark_item_error(ctx, context_id, item_id, reason, phase="build")
            end_run(ctx, context_id, item_id, final_tokens, "error", final_usage,
                     outcome="blocked", session_id=final_session)
            _log_decision(context_id, item_id, cycle,
                          {"action": "error", "exit": "error", "fault": reason,
                           "reason": f"the build run stopped — {reason}; the item is held at build "
                                     "for you to resume or re-run"})
            return
        # A `revise` is neither a wall nor a hold — only a state the OWNER must clear rests at
        # `awaiting_human`.
        still_ours = d["klass"] not in ("moved", "revise")
        rest = ("active" if d["klass"] == "revise" else
                "awaiting_human" if still_ours else str(item.get("status") or "active"))
        end_run(ctx, context_id, item_id, final_tokens, rest, final_usage,
                 outcome=("blocked" if turn_error else outcome), session_id=final_session)
        if d["klass"] == "moved":
            msg, meta = ("Loop: item moved during build cycle — loop yields",
                         {"action": "none", "outcome": outcome})
        elif d["klass"] == "revise":
            msg, meta = ("Loop: build says the plan itself must change — routing to plan",
                         {"action": "revise", "outcome": "revise"})
        else:  # needs_user — the question is the run's report; attention renders the ask card.
            msg, meta = ("Loop: build hit a wall only the owner can clear — asking",
                         {"action": "ask", "outcome": "needs_user"})
        _dev_store.log_event(context_id, "loop.decision", msg,
                             item_id=item_id, actor="daemon", meta=meta)
        if d["klass"] == "revise":
            # Routed after the run row is closed, so exactly one writer owns this transition.
            try:
                from .runs import fire_phase_feedback
                fire_phase_feedback(
                    context_id, item_id, phase="build", by="agent",
                    feedback=str((report_out or {}).get("summary")
                                 or "the build cycle concluded the plan must change"))
            except Exception:
                log.exception("revise routing failed for %s (item stays at build)", item_id)
    else:
        # Back into vet: retire the old vetter (vet forgets), CAS the flip, fire the next look.
        reset_vet_thread(ctx, item)
        moved = _cas_phase(dev_root, item_id, "build", "vet")
        end_run(ctx, context_id, item_id, final_tokens, "active", final_usage,
                 outcome=(report_out or {}).get("outcome") or "success", session_id=final_session)
        if moved:
            _dev_store.log_event(context_id, "phase.advance",
                                 "Loop: build cycle done — re-entering vet",
                                 item_id=item_id, actor="daemon",
                                 meta={"from": "build", "to": "vet"})
            # A cycle neither input can see changed nothing, so vetting would re-derive the same
            # verdict at full cost.
            if not (_tree_moved_since_evidence(item_dir, wt) or _plan_moved_since_evidence(item_dir)):
                _exit_no_progress(ctx, context_id, item_id, dev_root, item_dir)
            else:
                started, why = start_vet_run(ctx, context_id, item_id)
                if not started:
                    log.warning("loop: next vet did not start for %s: %s", item_id, why)
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after build cycle failed")
    log.info("background build cycle: done for %s (stopping=%s)", item_id, d["stopping"])


def grant_authorization(ctx, context_id: str, item_id: str, auth_id: str, *,
                        by: str) -> tuple[bool, str]:
    """Record a GRANT on a pending authorization. It ROUTES NOTHING: the item stays at review until
    one exit fires.

    Shared by the owner's authorize action and the deputy's delegated grant; the deputy path
    enforces delegation before calling this."""
    dev_root = ctx.internal_root / "dev"
    item_dir = dev_root / "work-items" / item_id
    auth = _arts.resolve_authorization(item_dir, auth_id, decision="granted", by=by)
    if auth is None:
        return False, f"authorization {auth_id!r} not found or already decided"
    _dev_store.log_event(context_id, "authorization.granted",
                         f"Authorization granted ({by}): {(auth.get('what') or '')[:120]}",
                         item_id=item_id, actor=(by if by in ("owner", "deputy") else "daemon"),
                         meta={"auth_id": auth_id, "scope": auth.get("scope"), "by": by,
                               "check": auth.get("check")})
    return True, "recorded"


def deny_authorization(ctx, context_id: str, item_id: str, auth_id: str, *,
                       by: str) -> tuple[bool, str]:
    """Record a DENIAL: the owner accepts the gap. The blocked check is waived and the item stays at
    review."""
    dev_root = ctx.internal_root / "dev"
    item_dir = dev_root / "work-items" / item_id
    auth = _arts.resolve_authorization(item_dir, auth_id, decision="denied", by=by)
    if auth is None:
        return False, f"authorization {auth_id!r} not found or already decided"
    _dev_store.log_event(context_id, "authorization.denied",
                         f"Authorization denied ({by}) — gap accepted: {(auth.get('what') or '')[:120]}",
                         item_id=item_id, actor=(by if by in ("owner", "deputy") else "daemon"),
                         meta={"auth_id": auth_id, "scope": auth.get("scope"), "by": by,
                               "check": auth.get("check")})
    return True, "denied"
