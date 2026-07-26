"""The build⟷vet LOOP DRIVER + breakers (build-vet-loop §5) — the daemon-side autonomy.

The driver is CODE (§4.5.1): it fires when a background vet run finishes, reads the evidence
ledger's derived verdict (`evidence_status()` — the loop condition existed before the loop), and
moves the item:

    passed      → advance to review (the owner's gate — the loop's ONLY happy exit)
    failed      → breakers? no  → new build cycle, handing over vet-report-<n>.md
                          yes → stop, awaiting_human, honest report
    stale       → re-run vet (code moved under a green ledger; ONE consecutive re-vet, then stop)
    unverified  → vet recorded nothing → FAIL CLOSED → awaiting_human

Breakers (§5.2): the TOKEN BUDGET is primary (measured spend over the item's build+vet phases —
not a cycle-count proxy); the CONVERGENCE GUARD compares failure fingerprints across consecutive
cycles (computed from ledger entries by the daemon — deterministic, never recalled by the vetter);
anything ambiguous (vet crashed, recorded nothing, kept going stale) FAILS CLOSED. Owner holds are
sticky: the driver only ever continues an item whose status is `active` — any human-set pause
stops the loop and is never auto-resumed (only the owner's next action restarts it).

Phase flips are CAS-guarded (§4.5.1, hermes claim_review_task precedent): each flip re-reads the
item and writes only if the phase still matches, in one synchronous block — atomic under the
single asyncio loop (no await between check and set), and the per-item run-lock (`_begin_run`)
already prevents two loop runs from coexisting. Every decision lands in `attempts.md` (the
driver's own append-only ledger) + a `loop.decision` dev event — the honest history review reads.

Entry point: the owner starts the loop explicitly (the vet quick-action route). From there it
self-drives while `loop_autorun` is on (default); off degrades every hop to decide-and-page.
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
from ...core import kernel_speech, session_contract
from ...core.permissions import VET_READONLY_NUDGE
from ...harness.tools.dev_tools import make_dev_mcp_server
from .runs import (_LiveTokens, _begin_run, _end_run, _log_artifact,
                   bank_auto_checkpoint, capture_event, capture_prompt, capture_run_input,
                   reset_vet_thread)

log = logging.getLogger("superme-agent")


# --------------------------------------------------------------------------- decision (pure)

def decide_after_vet(item: dict, *, evidence: dict, fingerprint: str, attempts: list[dict],
                     spent: int, budget: int, autorun: bool, turn_error: bool = False) -> dict:
    """The driver's decision function — PURE (no I/O; the wrapper reads the ledgers and passes
    them in), so every branch is unit-testable. Returns {action, status, reason, record}:
    action ∈ none|review|build|revet|halt · status = the item's resting status · record =
    whether an attempts.md entry is due (`none` leaves no trace — the loop didn't act)."""
    # Sticky owner holds (§5.2): the driver continues ONLY an active item. Terminal, paused,
    # or already-paged items are the owner's — a human-set stop never auto-resumes.
    status = str(item.get("status") or "")
    if item.get("done_at") or status != "active":
        return {"action": "none", "status": status, "record": False,
                "reason": f"item not active (status={status or 'unset'}) — loop yields to the owner"}
    # CAS pre-check: the owner (or another path) moved the item off vet mid-run — theirs wins.
    if str(item.get("phase")) != "vet":
        return {"action": "none", "status": status, "record": False,
                "reason": f"item left vet (phase={item.get('phase')}) — loop yields"}
    # Fail closed on infrastructure (§5.2): the vet run crashed before a verdict. Never charge
    # it as a cycle (no fingerprint recorded → it can't trip convergence), never guess a verdict.
    if turn_error:
        return {"action": "halt", "status": "awaiting_human", "record": True,
                "reason": "vet run failed to complete (infrastructure) — failing closed"}
    ev = str(evidence.get("status") or "unverified")
    if ev == "unverified":
        return {"action": "halt", "status": "awaiting_human", "record": True,
                "reason": "vet recorded no evidence — failing closed (an unrecorded vet is not a pass)"}
    if ev == "stale":
        # Code moved under a green ledger → re-vet. ONE consecutive re-vet only: a second stale
        # in a row means something keeps touching the worktree mid-loop — stop and say so.
        if attempts and str(attempts[-1].get("decision")) == "revet":
            return {"action": "halt", "status": "awaiting_human", "record": True,
                    "reason": "evidence went stale twice in a row — something is mutating the "
                              "worktree under the loop; stopping"}
        return {"action": "revet", "status": "active", "record": True,
                "reason": "evidence is green but the code moved since it was recorded — re-vetting",
                "stale": evidence.get("stale_checks") or []}
    if ev == "deferred":
        # BV-A2: a check is walled behind an authorization the build can't self-grant. It doesn't
        # fail the loop closed — it advances to review carrying the request, where the owner (or a
        # delegated deputy) grants or denies. Everything else is green.
        deferred = list(evidence.get("deferred_checks") or [])
        return {"action": "review", "status": "awaiting_human", "record": True, "deferred": deferred,
                "reason": f"{len(deferred)} check(s) deferred pending authorization — advancing to "
                          "review to grant or deny"}
    if ev == "passed":
        return {"action": "review", "status": "awaiting_human", "record": True,
                "reason": "every check green and fresh — advancing to the review gate"}
    # failed → the breakers decide whether another cycle is spent.
    failed = list(evidence.get("failed_checks") or [])
    if spent >= budget:
        return {"action": "halt", "status": "awaiting_human", "record": True, "failed": failed,
                "reason": f"token budget exhausted ({spent} ≥ {budget} over build+vet) — "
                          "stopping with WIP preserved"}
    prev_fp = next((str(a["fingerprint"]) for a in reversed(attempts) if a.get("fingerprint")), "")
    if fingerprint and prev_fp and fingerprint == prev_fp:
        return {"action": "halt", "status": "awaiting_human", "record": True, "failed": failed,
                "reason": "no progress between cycles (same checks failing the same way) — "
                          "escalating instead of spinning"}
    if not autorun:
        return {"action": "halt", "status": "awaiting_human", "record": True, "failed": failed,
                "reason": "checks failed and loop autorun is OFF — the next build cycle awaits "
                          "the owner"}
    return {"action": "build", "status": "active", "record": True, "failed": failed,
            "reason": f"{len(failed)} check(s) failed — handing the vet report to a build cycle"}


def decide_after_build(item: dict, *, outcome: str | None, turn_error: bool) -> dict:
    """The build-cycle exit decision — PURE (BV-A1/BV-A2). A build NEVER pages mid-loop, full stop:
    a content wall becomes an assumption, and a contract change the build can't self-authorize is
    DEFERRED via `request_authorization` (recorded as a pending auth + a deferred check) — either way
    the gap RIDES TO REVIEW, where the owner or deputy decides. So a build that ends without a
    turn_error ALWAYS advances toward vet; `blocked`/`exhausted`/`stagnated`/`approval_required` all
    advance (the deferred auth surfaces at review; the breakers catch a genuinely empty loop). Only
    TWO things stop at build: `moved` (the owner moved/paused it — theirs, the loop yields) and
    `infra` (a turn_error — a crashed turn with no verdict; BV-B wraps it in a retry ladder, today it
    pages, never as a work verdict). An authorization is NOT an infra fault and NEVER pages here.
    Returns {stopping, klass}; klass ∈ moved|infra|advance."""
    moved_away = (bool(item.get("done_at")) or str(item.get("status")) != "active"
                  or str(item.get("phase")) != "build")
    if moved_away:
        return {"stopping": True, "klass": "moved"}
    if turn_error:
        return {"stopping": True, "klass": "infra"}
    return {"stopping": False, "klass": "advance"}


# --------------------------------------------------------------------------- shared plumbing

def _cas_phase(dev_root: Path, item_id: str, frm: str, to: str) -> bool:
    """Compare-and-swap the item's phase: re-read, write only if it still reads `frm`. The whole
    read-check-write is synchronous (no await), so it is atomic under the single asyncio loop —
    the race this guards (a concurrent owner advance / second driver hop) runs on the same loop."""
    cur = _dev.read_work_item(dev_root, item_id) or {}
    if str(cur.get("phase")) != frm:
        return False
    return bool(_dev.set_work_item_phase(dev_root, item_id, to))


def _loop_ctx(ctx, item: dict):
    """The (worktree_ctx, worktree, item_dir, dev_root) tuple a loop run needs — or None when the
    item has no live worktree (nothing to vet/build against)."""
    dev_root = ctx.internal_root / "dev"
    item_dir = dev_root / "work-items" / str(item["id"])
    wt = Path(str(item.get("git_worktree") or ""))
    if not (item.get("git_worktree") and wt.is_dir()):
        return None
    return replace(ctx, cwd=wt), wt, item_dir, dev_root


def _resolve_run_params(context_id: str, item: dict) -> tuple[str, str]:
    """(model, effort) for a loop run — the same precedence every other item run resolves through
    (item's configured value → repo → system)."""
    return (_spine.effective_model(context_id, item_model=item.get("model")),
            _spine.effective_effort(context_id, item_effort=item.get("effort")))


def _dev_mcp(ctx, wt: Path, main_repo_dir: Path, item_id: str) -> dict:
    """The dev MCP server for a background loop run — same mount as an interactive bound turn
    (evidence + report pens scoped to THIS item; repo_dir = the worktree so evidence fingerprints
    the tree actually being vetted)."""
    return {"dev": make_dev_mcp_server(
        _dev_store, ctx.id, spine=_spine,
        dev_root=ctx.internal_root / "dev",
        repo_dir=wt, main_repo_dir=main_repo_dir,
        bound_item_id=item_id,
    )}


def _log_decision(context_id: str, item_id: str, cycle: int, d: dict) -> None:
    _dev_store.log_event(context_id, "loop.decision",
                         f"Loop: {d['action']} — {d['reason'][:160]}",
                         item_id=item_id, actor="daemon",
                         meta={"action": d["action"], "cycle": cycle,
                               "failed": d.get("failed") or []})


# --------------------------------------------------------------------------- vet runs

def start_vet_run(ctx, context_id: str, item_id: str) -> tuple[bool, str]:
    """Start one background vet run — the loop's DECISION hop, fired after every build cycle and
    by the owner's manual /vet action (build-first: vet is no longer the loop's entry). Guards: live
    worktree, item runnable at `vet`, no run in flight. Retires any previous vet thread first
    (vet FORGETS — fresh eyes every cycle, idempotent after the advance-route reset). Returns
    (started, reason). `awaiting_human` is runnable HERE only because reaching this call is
    itself an owner action (the quick-action route) or a driver hop from an active item."""
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
    model, effort = _resolve_run_params(context_id, item)
    if not _begin_run(ctx, context_id, item_id, "vet", model, phase="vet"):
        return False, "a run is already in progress for this item"
    # A paged item the owner just re-launched is active again (the launch IS the answer);
    # _begin_run rested it already.
    asyncio.create_task(_run_background_vet(ctx, context_id, item_id, model, effort))
    return True, "vet"


async def _run_background_vet(ctx, context_id: str, item_id: str,
                            model: str, effort: str) -> None:
    """Drive one fresh background vet turn at the item's worktree, then hand the outcome to the
    driver. Read-only on files (deny_write_tools) with the freeze-boundary shell; evidence +
    report land through the item-scoped MCP pens. ALWAYS a fresh session (vet forgets)."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    lc = _loop_ctx(ctx, item)
    if lc is None:   # worktree vanished between start and run
        _end_run(ctx, context_id, item_id, None, "awaiting_human", None, outcome="blocked")
        return
    wt_ctx, wt, item_dir, _ = lc
    title = item.get("title") or item_id
    # Thin trigger (Thread 3 §4): which skill for which item. The run contract rides the
    # per-turn system layer (background=True); the procedure lives in the vet skill. BV-A3: name the
    # checks the build DEFERRED (needs-you items in the auth ledger) so the vetter SKIPS them — it
    # doesn't re-judge a wall only the owner can clear, so the loop converges instead of churning.
    deferred = [str(a.get("check")) for a in _arts.pending_authorizations(item_dir) if a.get("check")]
    trigger = kernel_speech.vet_trigger(item_id, title, deferred=deferred or None)
    orient = kernel_speech.render_orient_block(item, item_dir)
    prompt = f"{orient}\n\n---\n\n{trigger}"
    capture_prompt(context_id, trigger, item_id=item_id)
    # Prompt inspector "A" — throwaway probes ONLY: vet passes work_item_preamble as system_append at
    # the worktree ctx. Normal items skip capture (the run_input table no longer grows per-run).
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=wt_ctx,
                          system_append=kernel_speech.work_item_preamble(item_id, item, str(item_dir), interactive=False),
                          prompt=prompt, phase="vet", background=True)
    final_tokens = None
    final_usage = None
    final_session = None
    turn_error = False
    run_started = time.time()
    live = _LiveTokens()
    try:
        async for ev in _agent.run_turn(
            wt_ctx, prompt,
            resume=None,                     # vet FORGETS — fresh eyes, prior reports are data
            model=model, effort=effort,
            approve=deny_all,                # background: nothing outside the boundary runs
            extra_mcp_servers=_dev_mcp(ctx, wt, ctx.cwd, item_id),
            system_append=kernel_speech.work_item_preamble(item_id, item, str(item_dir), interactive=False),
            write_boundary=[wt, item_dir],   # boundary Bash autonomy (running checks IS the job)
            deny_write_tools=VET_READONLY_NUDGE,   # …but file-write tools die outright (§4)
            background=True,                 # per-turn run contract in the system layer (Thread 3 §3)
        ):
            if isinstance(ev, Usage):
                live.bump(context_id, item_id, ev)
            elif isinstance(ev, (Status, ToolResult)):
                _log_artifact(context_id, item_id, ev)
            elif isinstance(ev, Result):
                final_tokens = ev.tokens
                final_usage = ev.usage
                final_session = ev.session_id
                _sessions.record(wt_ctx, ev.session_id)
                if ev.session_id:
                    try:
                        _dev.set_work_item_session(dev_root, item_id, ev.session_id, role="vet")
                        _spine.stamp_session_item(ev.session_id, item_id)
                        _spine.stamp_session_kind(ev.session_id, "vet")
                    except Exception:
                        log.exception("background vet: failed to persist session to %s", item_id)
            if isinstance(ev, (Status, TextDelta, ToolResult)):
                capture_event(context_id, ev, item_id=item_id)
    except Exception: #crash check
        turn_error = True
        log.exception("background vet run failed for %s", item_id)
    # ---- THE DRIVER (§5.1): decide off the ledger, close the run, apply, fire the next hop.
    item = _dev.read_work_item(dev_root, item_id) or {}
    evidence = _arts.evidence_status(item_dir, wt)
    fingerprint = _arts.convergence_fingerprint(item_dir)
    attempts = _arts.read_attempts(item_dir)
    reports = _arts.vet_reports(item_dir)
    cycle = reports[-1]["cycle"] if reports else 0
    spent = _spine.item_phase_tokens(context_id, item_id)
    budget = _spine.effective_loop_budget(context_id, item.get("loop_budget"))
    d = decide_after_vet(item, evidence=evidence, fingerprint=fingerprint, attempts=attempts,
                         spent=spent, budget=budget, autorun=_spine.get_loop_autorun(),
                         turn_error=turn_error)
    # CAS flips happen BEFORE the run row closes so the next hop's row stamps the new phase.
    moved = True
    if d["action"] == "review":
        moved = _cas_phase(dev_root, item_id, "vet", "review")
    elif d["action"] == "build":
        moved = _cas_phase(dev_root, item_id, "vet", "build")
    if not moved:   # lost the CAS — someone else moved the item; theirs wins, loop yields
        d = {"action": "none", "status": str(item.get("status") or "active"), "record": False,
             "reason": "phase flip lost its CAS — another actor moved the item; loop yields"}
    # B3: mechanically author readiness.md at the vet→review transition — BEFORE _end_run dispatches
    # the review deputy (which else escalates 100% of items for a doc no autopilot run authors).
    if d["action"] == "review":
        from . import git_ops
        git_ops.write_review_readiness(ctx, item, item_dir, dev_root)
    outcome = ("success" if d["action"] == "review"
               else "blocked" if turn_error
               else "exhausted" if d["action"] == "halt" and "budget" in d["reason"]
               else "stagnated" if d["action"] == "halt"
               else "success" if d["action"] in ("build", "revet") else None)
    _end_run(ctx, context_id, item_id, final_tokens, d["status"] or "active", final_usage,
             outcome=outcome, session_id=final_session)
    if d["record"]:
        try:
            _arts.append_attempt(item_dir, cycle=cycle, evidence=str(evidence.get("status")),
                                 decision=d["action"], reason=d["reason"],
                                 fingerprint=fingerprint if evidence.get("status") == "failed" else "",
                                 failed=d.get("failed") or (), tokens=spent, budget=budget)
        except Exception:
            log.exception("attempts.md append failed for %s", item_id)
    _log_decision(context_id, item_id, cycle, d)
    if d["action"] == "review":
        _dev_store.log_event(context_id, "phase.advance",
                             "Loop: all checks green — advanced vet → review",
                             item_id=item_id, actor="daemon", meta={"from": "vet", "to": "review"})
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after vet failed")
    # Fire the next hop AFTER the run row is closed (the per-item run-lock frees above).
    if d["action"] == "build":
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
    """Start the loop's OPENING build cycle (build-first): the item just entered `build` with
    nothing implemented, so the work order is the PLAN, not a vet report. Fires a build run whose
    trigger points at plan.md's Tasks + Inner checks; the run exits build→vet through the normal
    flow, and vet (the loop's sole DECISION point) takes it from there. This is the loop's entry —
    a vet against an empty tree is a wasted look (owner, 2026-07-20). Guards mirror
    start_build_cycle EXCEPT the vet-report requirement (there is deliberately none yet).
    Returns (started, reason)."""
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
    trigger = kernel_speech.build_first_trigger(item_id, title)
    model, effort = _resolve_run_params(context_id, item)
    if not _begin_run(ctx, context_id, item_id, "build", model, phase="build"):
        return False, "a run is already in progress for this item"
    asyncio.create_task(_run_background_build(ctx, context_id, item_id, model, effort,
                                              trigger=trigger))
    return True, "build"


def start_build_cycle(ctx, context_id: str, item_id: str) -> tuple[bool, str]:
    """Start one background build cycle: the loop's failure hop — the build session (which
    REMEMBERS: resumed across cycles) fixes what the latest vet report describes. Returns
    (started, reason)."""
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
    if _arts.latest_vet_report(dev_root / "work-items" / item_id) is None:
        return False, "no vet report to hand over — a loop build cycle needs one"
    model, effort = _resolve_run_params(context_id, item)
    if not _begin_run(ctx, context_id, item_id, "build", model, phase="build"):
        return False, "a run is already in progress for this item"
    asyncio.create_task(_run_background_build(ctx, context_id, item_id, model, effort))
    return True, "build"


def start_continue_build(ctx, context_id: str, item_id: str) -> tuple[bool, str]:
    """The owner's CONTINUE on a build parked at a human gate — RE-ENTER the loop (BV-A1). Reached
    only when a build parked for an infra reason (or a legacy pre-BV-A2 page); a content wall or a
    deferred authorization no longer parks here (they ride to review). The owner saw where it stopped
    and said keep going. This RESUMES the item's build thread with a finalize
    trigger — complete what's doable, record any wall as an assumption — and lets the normal
    build→vet→review flow carry the gap to review. Distinct from a bare chat reply to a paused build
    ([ws.py] `chat` run), which does NOT advance the loop. Guards mirror start_build_cycle EXCEPT
    the vet-report requirement (a parked opening build has none) and it accepts an awaiting_human
    item (the continue IS the answer; _begin_run rests it active). Returns (started, reason)."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    if not item:
        return False, "work-item not found"
    if item.get("done_at") or str(item.get("status")) not in ("active", "awaiting_human"):
        return False, f"item is not resumable (status={item.get('status')})"
    if str(item.get("phase")) != "build":
        return False, f"item is not in build (phase={item.get('phase')})"
    if _loop_ctx(ctx, item) is None:
        return False, "item has no live worktree"
    title = item.get("title") or item_id
    trigger = kernel_speech.build_continue_trigger(item_id, title)
    model, effort = _resolve_run_params(context_id, item)
    if not _begin_run(ctx, context_id, item_id, "build", model, phase="build"):
        return False, "a run is already in progress for this item"
    asyncio.create_task(_run_background_build(ctx, context_id, item_id, model, effort,
                                              trigger=trigger))
    return True, "build"


async def _run_background_build(ctx, context_id: str, item_id: str,
                              model: str, effort: str, *, trigger: str | None = None) -> None:
    """Drive one background build turn in the item's worktree, handing over the cycle's work order
    (injected ONCE, into this cycle's trigger — never per-turn), then flip the item back into vet
    and fire the next vet run. RESUMES the item's build thread (build REMEMBERS). The default
    work order is the latest vet report (the loop's failure hop); a review re-entry passes its
    own `trigger` (the routed check + the owner's feedback — §5.3)."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    lc = _loop_ctx(ctx, item)
    if lc is None:
        _end_run(ctx, context_id, item_id, None, "awaiting_human", None, outcome="blocked")
        return
    wt_ctx, wt, item_dir, _ = lc
    prev_build = (item.get("sessions") or {}).get("build")
    title = item.get("title") or item_id
    if trigger is None:
        report = _arts.latest_vet_report(item_dir)   # capped handoff (§8·O10)
        # Thin trigger (Thread 3 §4): the vet report IS the work order; the build skill owns the
        # procedure (Inner checks green, commit), the system layer carries the run contract.
        trigger = kernel_speech.build_loop_trigger(item_id, title, report["cycle"], report["text"])
    prompt = trigger
    if not prev_build:   # first build turn of the item's life happening in background → orient at birth
        prompt = f"{kernel_speech.render_orient_block(item, item_dir)}\n\n---\n\n{trigger}"
    capture_prompt(context_id, trigger, item_id=item_id)
    # Prompt inspector "A" — throwaway probes ONLY: build passes work_item_preamble as system_append
    # at the worktree ctx; the body carries the orient block only on the item's first build turn (else
    # just the trigger). Normal items skip capture (the run_input table no longer grows per-run).
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=wt_ctx,
                          system_append=kernel_speech.work_item_preamble(item_id, item, str(item_dir), interactive=False),
                          prompt=prompt, phase="build", background=True)
    final_tokens = None
    final_usage = None
    final_text = None
    final_session = None
    turn_error = False
    run_started = time.time()
    live = _LiveTokens()
    try:
        async for ev in _agent.run_turn(
            wt_ctx, prompt,
            resume=prev_build,               # build REMEMBERS — same thread every cycle
            model=model, effort=effort,
            approve=deny_all,
            extra_mcp_servers=_dev_mcp(ctx, wt, ctx.cwd, item_id),
            system_append=kernel_speech.work_item_preamble(item_id, item, str(item_dir), interactive=False),
            write_boundary=[wt, item_dir],   # S4 freeze: writes stay in worktree + item dir
            background=True,                 # per-turn run contract in the system layer (Thread 3 §3)
        ):
            if isinstance(ev, Usage):
                live.bump(context_id, item_id, ev)
            elif isinstance(ev, (Status, ToolResult)):
                _log_artifact(context_id, item_id, ev)
            elif isinstance(ev, Result):
                final_tokens = ev.tokens
                final_usage = ev.usage
                final_text = ev.text
                final_session = ev.session_id
                _sessions.record(wt_ctx, ev.session_id)
                if ev.session_id:
                    try:
                        _dev.set_work_item_session(dev_root, item_id, ev.session_id, role="build")
                        _spine.stamp_session_item(ev.session_id, item_id)
                        _spine.stamp_session_kind(ev.session_id, "build")
                    except Exception:
                        log.exception("background build: failed to persist session to %s", item_id)
            if isinstance(ev, (Status, TextDelta, ToolResult)):
                capture_event(context_id, ev, item_id=item_id)
    except Exception:
        turn_error = True
        log.exception("background build cycle failed for %s", item_id)
    report_out = session_contract.parse_completion_report(final_text)
    if report_out:
        _dev_store.log_event(context_id, "run.report",
                             f"{report_out['outcome']}: {report_out['summary'][:160]}",
                             item_id=item_id, actor="agent", meta=report_out)
    item = _dev.read_work_item(dev_root, item_id) or {}
    outcome = (report_out or {}).get("outcome")
    d = decide_after_build(item, outcome=outcome, turn_error=turn_error)
    if d["stopping"]:
        # `moved` = the owner moved/paused it → left theirs (loop yields). `infra` is still ours →
        # rest at awaiting_human. There is NO content/approval stop here: a deferred authorization
        # rides to review (BV-A2), never a mid-build page.
        still_ours = d["klass"] != "moved"
        rest = "awaiting_human" if still_ours else str(item.get("status") or "active")
        _end_run(ctx, context_id, item_id, final_tokens, rest, final_usage,
                 outcome=("blocked" if turn_error else outcome), session_id=final_session)
        if d["klass"] == "moved":
            msg, meta = ("Loop: item moved during build cycle — loop yields",
                         {"action": "halt", "outcome": outcome})
        else:  # infra — a crashed turn with no verdict; the only mid-build page (BV-B retry ladder)
            msg, meta = ("Loop: build turn crashed (infrastructure) — paging the owner",
                         {"action": "halt", "outcome": "blocked", "class": "infra"})
        _dev_store.log_event(context_id, "loop.decision", msg,
                             item_id=item_id, actor="daemon", meta=meta)
    else:
        # Back into vet: retire the old vetter (vet forgets), CAS the flip, fire the next look.
        reset_vet_thread(ctx, item)
        moved = _cas_phase(dev_root, item_id, "build", "vet")
        _end_run(ctx, context_id, item_id, final_tokens, "active", final_usage,
                 outcome=(report_out or {}).get("outcome") or "success", session_id=final_session)
        if moved:
            _dev_store.log_event(context_id, "phase.advance",
                                 "Loop: build cycle done — re-entering vet",
                                 item_id=item_id, actor="daemon",
                                 meta={"from": "build", "to": "vet"})
            if _spine.get_loop_autorun():
                started, why = start_vet_run(ctx, context_id, item_id)
                if not started:
                    log.warning("loop: next vet did not start for %s: %s", item_id, why)
            else:
                _dev.set_work_item_status(dev_root, item_id, "awaiting_human")
                _dev_store.log_event(context_id, "loop.decision",
                                     "Loop: autorun OFF — vet re-entry awaits the owner",
                                     item_id=item_id, actor="daemon", meta={"action": "halt"})
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after build cycle failed")
    log.info("background build cycle: done for %s (stopping=%s)", item_id, d["stopping"])


# ------------------------------------------------------------------- review→plan re-entry (#178)

def schedule_review_plan(context_id: str, item_id: str, feedback: str) -> None:
    """Owner feedback at the review gate → re-plan (#178, unifying the owner onto the deputy's
    review→plan router). Called MID-TURN from `route_review_feedback`: the interactive intake turn
    still holds the item's run-lock, so the re-plan cannot start synchronously — a background task
    waits for that run row to close, then fires `runs.fire_phase_feedback(by='owner')`, which flips
    review→plan and re-plans with a downstream digest. Forward flow (plan→build→vet→review) re-vets
    by construction, so the owner's feedback converges the same way the deputy's does."""
    asyncio.create_task(_fire_review_plan(context_id, item_id, feedback))


async def _fire_review_plan(context_id: str, item_id: str, feedback: str) -> None:
    from ...gateway import contexts
    from . import git_ops
    from . import runs as _runs
    deadline = time.time() + _REVIEW_FIRE_WAIT
    while time.time() < deadline:
        if not _spine.is_item_running(context_id, item_id):
            ctx = contexts.resolve(context_id, "dev")
            # The re-plan wants the downstream digest (what build/vet/review found) so it knows it's
            # feedback from the earlier plan's results — the same context the deputy's send-back carries.
            digest = git_ops.build_downstream_digest(ctx.internal_root / "dev" / "work-items" / item_id)
            if _runs.fire_phase_feedback(context_id, item_id, phase="review", feedback=feedback,
                                         digest=digest, by="owner"):
                return
            # is_item_running was false but the fire couldn't start (item moved/terminal, or a run
            # raced in) — one retry after a beat; a persistent refusal falls out to the timeout log.
        await asyncio.sleep(2)
    log.warning("loop: owner review→plan re-entry timed out waiting for the run-lock (%s)", item_id)
    _dev_store.log_event(context_id, "loop.decision",
                         "Loop: owner review feedback could not be routed — the running turn never ended",
                         item_id=item_id, actor="daemon", meta={"action": "halt"})


def start_authorized_build(ctx, context_id: str, item_id: str, *, auth: dict) -> tuple[bool, str]:
    """Grant-as-send_back (BV-A2.3): a deferred authorization has been GRANTED at review, so route
    the item back to build to PERFORM the now-allowed contract change. The item is at review; flip
    it review→build and fire a build cycle whose trigger names the granted request. It exits through
    the normal build→vet flow — the request is no longer pending, so the deferred check verifies
    against the real change this time — then returns to review. Caller has already recorded the
    grant (`resolve_authorization`). Returns (started, reason)."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    if not item:
        return False, "work-item not found"
    if item.get("done_at") or str(item.get("status")) not in ("active", "awaiting_human"):
        return False, f"item is not resumable (status={item.get('status')})"
    if str(item.get("phase")) != "review":
        return False, f"grant re-entry runs from review (phase={item.get('phase')})"
    if _loop_ctx(ctx, item) is None:
        return False, "item has no live worktree"
    if not _cas_phase(dev_root, item_id, "review", "build"):
        return False, "item left review — grant re-entry yields"
    title = item.get("title") or item_id
    trigger = kernel_speech.authorized_build_trigger(item_id, title, auth)
    model, effort = _resolve_run_params(context_id, item)
    if not _begin_run(ctx, context_id, item_id, "build", model, phase="build"):
        return False, "a run is already in progress for this item"
    asyncio.create_task(_run_background_build(ctx, context_id, item_id, model, effort,
                                              trigger=trigger))
    return True, "build"


def grant_authorization(ctx, context_id: str, item_id: str, auth_id: str, *,
                        by: str) -> tuple[bool, str]:
    """Record a GRANT on a pending authorization and route the item back to build to perform it
    (BV-A2.3). Shared by the owner's authorize action and the deputy's delegated grant. The caller
    (deputy path) enforces delegation BEFORE calling this — the owner path grants unconditionally.
    Returns (started, reason)."""
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
    return start_authorized_build(ctx, context_id, item_id, auth=auth)


def deny_authorization(ctx, context_id: str, item_id: str, auth_id: str, *,
                       by: str) -> tuple[bool, str]:
    """Record a DENIAL: the owner accepts the gap. The blocked check is WAIVED (evidence_status
    excuses it) and the item stays at review for the owner's approve. Returns (ok, reason)."""
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


_REVIEW_FIRE_WAIT = 900   # s — how long the deferred hop waits for the routing turn's run-lock
