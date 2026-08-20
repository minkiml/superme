"""The build⟷vet LOOP DRIVER + breakers (build-vet-loop §5) — the daemon-side autonomy.

The driver is CODE (§4.5.1): it fires when a background vet run finishes, reads the derived
verdict over the recorded checks (`evidence_status()` — the loop condition existed before the
loop), and moves the item:

**THE LOOP NEVER PARKS** (owner, 2026-07-30). build⟷vet is a human-free stretch, so it has no
resting state: every way out lands the item at REVIEW, carrying a typed `exit` reason the owner
decides from. There are exactly FOUR work-item exits —

    converged       every check green → review
    not_converging  the same failure signature `_MAX_RECURRENCE` times → review
    no_progress     a build cycle changed nothing in the tree → review (the vet is SKIPPED)
    budget          measured build+vet spend hit the ceiling → review
    system_fault    OUR bug, not a verdict — after `_MAX_FAULT_RETRY` retries → review

— and one non-exit: `deferred`, which also goes to review (an authorization wall is the owner's
call, not a failure).

Everything else is a **SuperMe FAULT, not a verdict on the work**: a crashed turn, an empty
ledger where checks were declared. Those get a bounded retry and, if they persist, are surfaced as
a system fault (`loop.fault`) — the item still goes to review rather than sitting in a phase with
no decision surface. The rule: the breakers encode work-item outcomes only; our own defects are
bugs to fix, never workflow states.

Both breakers meter one GENERATION, not the item's whole life (§3-bis.4): a plan revision opens a
generation, so `spent` reads since the current revision's `spend_at` and the recurrence guard counts
only cycles scaffolded under it. Otherwise a `budget` exit could never be answered — every revise
round would die on its first vet with the ceiling already crossed. Total spend stays owner-bounded
because only a revision opens a generation, and only a human triggers a revision.

Owner holds are sticky: the driver only ever continues an item whose status is `active` — any
human-set pause stops the loop and is never auto-resumed.

Phase flips are CAS-guarded (§4.5.1, hermes claim_review_task precedent): each flip re-reads the
item and writes only if the phase still matches, in one synchronous block — atomic under the
single asyncio loop (no await between check and set), and the per-item run-lock (`_begin_run`)
already prevents two loop runs from coexisting. Every decision lands in the cycle report's
`§Cycle outcome` (the driver's append, which closes the cycle) + a `loop.decision` dev event —
the honest history review reads.

Entry point: approving the plan gate. From there the loop self-drives every hop to review —
build⟷vet is a HUMAN-FREE stretch by contract (owner, 2026-07-30), so there is no switch that
degrades it to decide-and-page and no resting state inside it that waits for a click.
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
from .turns import ResilientTurn
from ...harness.tools.run_tools import make_run_report_server
from ...core.permissions import VET_READONLY_NUDGE
from ...harness.tools.dev_tools import make_dev_mcp_server
from .runs import (_LiveTokens, _begin_run, _end_run, surface_from_turn,
                   bank_auto_checkpoint, capture_event, capture_prompt, capture_run_input,
                   compacted_checkpoint, ensure_completion, fire_auto_triage, mark_item_error, read_completion,
                   reset_vet_thread, retry_notice)

log = logging.getLogger("superme-agent")


# --------------------------------------------------------------------------- decision (pure)

# How many times the SAME failure signature may appear before the loop hands the item over. A
# recurrence is not proof of impossibility — a build can be closing in on a fix while the
# observable failure text stays put — so one repeat is too eager. Three appearances of the
# identical signature is the owner's call for "give it good trials, then let me look".
_MAX_RECURRENCE = 3
# Bounded retries for a SuperMe fault (crashed turn, empty ledger). A fault is not an attempt:
# retries reuse the cycle and never touch the convergence guard.
_MAX_FAULT_RETRY = 2


def decide_after_vet(item: dict, *, evidence: dict, fingerprint: str, attempts: list[dict],
                     spent: int, budget: int, turn_error: bool = False,
                     faults: int = 0, fault_reason: str = "", lens_gaps: list[dict] | None = None,
                     audit_gaps: list[dict] | None = None) -> dict:
    """The driver's decision function — PURE (no I/O; the wrapper reads the ledgers and passes
    them in), so every branch is unit-testable. Returns {action, status, reason, record[, exit]}:
    action ∈ none|review|build|revet|error · status = the item's resting status · record = whether
    a §Cycle outcome entry is due · `exit` = the typed reason review is handed.

    The loop never leaves an item resting inside build⟷vet with work still to do, because that
    stretch has no decision surface — but `error` is not resting, it is STOPPED (R2), and an item
    whose run died has to stay where it died rather than be advanced past work that never happened.
    `faults` is how many times this cycle has already been retried after a recording fault;
    `fault_reason` is R1's typed verdict on a stopped turn, used verbatim as the owner's label."""
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
    ev = str(evidence.get("status") or "unverified")
    # --- the run STOPPED (R2) -----------------------------------------------------------------
    # A crashed vet turn produced no verdict at all — and by the time this is reached, R1's ladder
    # has already waited it out (up to seven times, ~29 minutes). So there is nothing left to retry
    # here: the item STOPS at `error`, where it died, carrying the reason, until the owner Resumes
    # or re-runs it. Deliberately NOT `system_fault`: that word is for a run that COMPLETED while
    # our machinery misbehaved (below), which is review's business because the work still advanced.
    if turn_error:
        return {"action": "error", "status": "error", "record": True, "exit": "error",
                "fault": "the vet run stopped before it could report",
                "reason": fault_reason or "the vet run stopped before it could report"}
    # --- SuperMe FAULTS (not verdicts) -------------------------------------------------------
    # An empty ledger where the plan declared checks means the RECORDING machinery failed — the run
    # itself finished (both live causes to date were OUR allowlist gaps, and "genuinely nothing to
    # verify" now has its own honest representation: `depth: none`). That says nothing about the
    # work, so it may not fail the item closed. Retry — re-running vet IS the cure for a lost
    # ledger — then hand it over labelled as a fault so the bug is visible AS a bug.
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
        # The tree moved under a green ledger → re-vet. No consecutive cap: the fingerprint now
        # tracks COMMITTED+tracked content only, so test litter can't fake this, and the budget is
        # the backstop if something pathological ever does churn the worktree.
        return {"action": "revet", "status": "active", "record": True,
                "reason": "evidence is green but the code moved since it was recorded — re-vetting",
                "stale": evidence.get("stale_checks") or []}
    # A gating lens finding (verification-model §3) is a work-item outcome like any other failure:
    # it routes back to build, through the same breakers, with no exit of its own. It is folded in
    # HERE rather than inside evidence_status because a lens is not a plan check — the plan never
    # declared it, and the freshness machinery has nothing to say about it.
    lens_failed = [f"lens:{g['lens']}" for g in (lens_gaps or [])]
    # A build validation claim the kernel could not reproduce, folded in for the same reason and by
    # the same route as a lens finding: it is neither a plan check nor a freshness question, it is
    # the cycle's own account of itself failing to hold. Routing it back to build is what keeps the
    # unit tests inside build where they belong while still making the claim about them provable.
    audit_failed = [f"validation:{a['command']}" for a in (audit_gaps or [])]
    if ev in ("deferred", "passed") and (lens_failed or audit_failed):
        # Green checks, but a lens found something that gates — or build's own validation claim
        # does not reproduce. The checks proved what the plan thought to ask; this is the part
        # nobody thought to ask. Fall through to the failure path rather than advancing to review
        # with a clean-looking record.
        ev = "failed"
    if ev == "deferred":
        # BV-A2: a check is walled behind an authorization the build can't self-grant. It doesn't
        # fail the loop closed — it advances to review carrying the request, where the owner (or a
        # delegated deputy) grants or denies. Everything else is green.
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
    # Convergence: count how often THIS signature has already appeared. A recurrence after an
    # intervening different failure is the oscillation a compare-with-previous test misses (fix A →
    # break B → fix B → A regresses), but one repeat is not proof of a wall — build may be closing
    # in while the failure text holds still. (`fingerprint` covers only FAILING checks and is ""
    # when nothing fails, so a green cycle can never trip this.)
    seen = sum(1 for a in attempts if str(a.get("fingerprint") or "") == fingerprint) if fingerprint else 0
    if seen + 1 >= _MAX_RECURRENCE:
        return {"action": "review", "status": "awaiting_human", "record": True, "failed": failed,
                "exit": "not_converging",
                "reason": f"the same failure has now come back {seen + 1} times — the loop is not "
                          "converging on it; handing it to you"}
    # Name what actually failed. "1 check(s) failed" for a safety finding is the surface printing
    # its own bookkeeping instead of answering the reader's question.
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
    """The build-cycle exit decision — PURE (BV-A1/BV-A2). A build NEVER pages mid-loop, full stop:
    a content wall becomes an assumption, and a contract change the build can't self-authorize is
    DEFERRED via `request_authorization` (recorded as a pending auth + a deferred check) — either way
    the gap RIDES TO REVIEW, where the owner or deputy decides. So a build that ends without a
    turn_error ALWAYS advances toward vet; `blocked`/`exhausted`/`stagnated`/`approval_required` all
    advance (the deferred auth surfaces at review; the breakers catch a genuinely empty loop). Only
    THREE things stop at build: `moved` (the owner moved/paused it — theirs, the loop yields),
    `infra` (a turn_error — a crashed turn with no verdict; BV-B wraps it in a retry ladder, today it
    pages, never as a work verdict), and `needs_user`. An authorization is NOT an infra fault and
    NEVER pages here. Returns {stopping, klass}; klass ∈ moved|infra|needs_user|advance.

    **Why `needs_user` is an exception and an assumption is not.** "Ride to review" rests on the
    work being ON THE BRANCH — a gap the owner can judge against a real diff. The wall this covers
    is the opposite: a commit the build cannot make (the project's own hook refused it, and
    overruling that is not the agent's call). Nothing landed, so advancing would vet a tree whose
    content cannot reach review, and the owner would meet an empty diff a whole cycle later. The
    agent's instruction is explicit that this is not a retry: quote the refusal, park, ask."""
    moved_away = (bool(item.get("done_at")) or str(item.get("status")) != "active"
                  or str(item.get("phase")) != "build")
    if moved_away:
        return {"stopping": True, "klass": "moved"}
    if turn_error:
        return {"stopping": True, "klass": "infra"}
    if outcome == "needs_user":
        return {"stopping": True, "klass": "needs_user"}
    # `revise` — the build concluded the PLAN it was handed is wrong (its `run:` commands point at
    # the wrong tree, the design can't be built as written). It cannot fix that itself: build is
    # guarded out of amending the vet plan, and plan.md is the only document that can say what
    # changes. So the cycle stops and the item routes to plan, ATTRIBUTED TO THE AGENT.
    # This used to be routed from inside `report_completion` instead, which put two writers on one
    # transition — the item went to plan and started a plan run, and this branch then logged
    # `build → vet` on top of it (f0bda271d766, 2026-08-07). One writer: the driver.
    if outcome == "revise":
        return {"stopping": True, "klass": "revise"}
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
    """(model, effort) for a BUILD run — the same precedence every other item run resolves through
    (item's configured value → repo → system)."""
    return (_spine.effective_model(context_id, item_model=item.get("model")),
            _spine.effective_effort(context_id, item_effort=item.get("effort")))


def _resolve_vet_params(context_id: str, item: dict) -> tuple[str, str]:
    """(model, effort) for a VET run — its own chain, NOT the item's or the project's.

    Vet is the check on what build produced. Inheriting build's tier made the check move with the
    thing it checks: raise a project to Opus and its reviewer silently rose too, so the one pairing
    you might deliberately want (cheap builder, expensive judge, or the reverse) was unreachable.
    The item may still name a vet tier of its own; nothing else feeds this."""
    return (_spine.role_model(context_id, "vet", item_model=item.get("vet_model")),
            _spine.role_effort(context_id, "vet", item_effort=item.get("vet_effort")))


def _dev_mcp(ctx, wt: Path, main_repo_dir: Path, item_id: str, *, scope: str) -> dict:
    """The dev MCP server for a background loop run — same mount as an interactive bound turn
    (evidence + report pens scoped to THIS item; repo_dir = the worktree so evidence fingerprints
    the tree actually being vetted).

    `scope` is the phase this run IS (dev_tools.TOOL_SCOPES): build carries its own recorder and
    never vet's, which is the separation the skills used to assert in prose alone."""
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
    """Has the worktree changed since the last evidence entry was recorded? True when there is no
    evidence yet (the opening cycle has nothing to compare against, and must always be vetted).

    Same fingerprint the ledger stamps, so this asks exactly the question the freshness rule asks —
    one primitive, two readers."""
    entries = _arts.evidence_entries(item_dir)
    last = next((str(e.get("fingerprint") or "") for e in reversed(entries) if e.get("fingerprint")), "")
    if not last or last == "no-git":
        return True
    return _arts.repo_fingerprint(wt) != last


def _plan_moved_since_evidence(item_dir: Path) -> bool:
    """Has the plan been REVISED since the last verdict was recorded?

    Vet grades the tree AGAINST the plan's `## Verification plan`, so the verdict has TWO inputs,
    and the tree fingerprint can only see one of them — plan.md lives in the item's knowledge home,
    never in the worktree. This is the case the no-progress guard got wrong (dogfood, 2026-08-06):
    a vet failed because the plan's `run:` graded the wrong checkout, a revision fixed exactly that,
    and the build cycle after it had nothing to change. Tree identical → the guard skipped the vet,
    refusing the one run that would have proved the fix, and handed the owner a gate whose evidence
    was the failure it had just been asked to re-derive.

    A revision that changes how verification happens is a reason to verify, not a reason to skip."""
    entries = _arts.evidence_entries(item_dir)
    if not entries:
        return True
    by_cycle = {r["cycle"]: str(r.get("revision") or "") for r in _arts.cycle_reports(item_dir)}
    return by_cycle.get(entries[-1].get("cycle"), "") != _plan_revision.current_revision(item_dir)


def _exit_no_progress(ctx, context_id: str, item_id: str, dev_root: Path, item_dir: Path) -> None:
    """A build cycle that changed nothing: flip vet → review and hand it over, WITHOUT spending a
    vet run. Records the same §Cycle outcome + decision event any other exit does, so the trail
    reads the same whether the loop stopped at vet or short of it."""
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
        log.exception("§Cycle outcome append failed for %s", item_id)
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
    """How many times THIS cycle has already been retried after a SuperMe fault — read back from
    the decision trail rather than held in memory, so a daemon restart mid-retry can't reset the
    counter and spin forever. A fault is not an attempt: these retries reuse the cycle number and
    never reach the convergence guard."""
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
    model, effort = _resolve_vet_params(context_id, item)
    if not _begin_run(ctx, context_id, item_id, "vet", model, phase="vet"):
        return False, "a run is already in progress for this item"
    # A paged item the owner just re-launched is active again (the launch IS the answer);
    # _begin_run rested it already.
    run_tasks.track(asyncio.create_task(_run_background_vet(ctx, context_id, item_id, model, effort)))
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
    # ONE definition of what this run may write to — the turn is held to it, the sandbox enforces
    # it, and the X-ray records it. Three readers of the same list, so the capture cannot describe
    # a boundary the turn was never given.
    boundary = [wt, item_dir]
    title = item.get("title") or item_id
    # Thin trigger (Thread 3 §4): which skill for which item. The run protocol rides the
    # Current-focus background variant; the procedure lives in the vet skill. BV-A3: name the
    # checks the build DEFERRED (needs-you items in the auth ledger) so the vetter SKIPS them — it
    # doesn't re-judge a wall only the owner can clear, so the loop converges instead of churning.
    deferred = [str(a.get("check")) for a in _arts.pending_authorizations(item_dir) if a.get("check")]
    # The kernel runs what can be run BEFORE the session opens (design §4), so the vetter meets
    # those results as facts rather than as work. Deferrals are excluded — a check waiting on the
    # owner's authorization is not ours to execute. A failure here is a check verdict, never a run
    # fault: the loop is supposed to learn that the code is broken.
    try:
        machine = await asyncio.to_thread(_checks.execute, item_dir, wt,
                                          skip=deferred, title=title)
    except Exception:
        log.exception("kernel checks failed for %s — vet proceeds and performs them itself", item_id)
        machine = []
    # …and re-runs BUILD's own validation commands, comparing each to what build claimed. Validation
    # stays build's work; witnessing it is verification's (2026-08-07 amendment). A disagreement is
    # a finding about the build, recorded in its own lane — never a check in the item's exam.
    cycle_now = (_arts.cycle_reports(item_dir) or [{}])[-1].get("cycle")
    try:
        audit = await asyncio.to_thread(_checks.audit_validation, item_dir, wt, cycle=cycle_now)
    except Exception:
        log.exception("validation audit failed for %s — vet proceeds without it", item_id)
        audit = []
    # Whether this repo can boot a server from the worktree — the trigger carries the exact
    # command when it can, so a check needing one never falls back to whatever is already up.
    has_vet_env = bool(getattr(_spine.repos().get(context_id), "vet_env", None))
    trigger = kernel_speech.vet_trigger(item_id, title, deferred=deferred or None,
                                        machine=machine or None, audit=audit or None,
                                        vet_env=has_vet_env)
    prompt = trigger   # orientation is on-demand — the vet skill's directed reads (renovation §1)
    capture_prompt(context_id, trigger, item_id=item_id)
    # Prompt inspector "A" — throwaway probes ONLY: vet passes work_item_preamble as system_append at
    # the worktree ctx. Normal items skip capture (the run_input table no longer grows per-run).
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = _LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — recorded; the DRIVER decides
    # R1: the turn carries its own retry ladder. A vet that never got off the ground (upstream 5xx,
    # a dropped socket) is waited out here rather than surfacing as a fault the owner has to clear —
    # and `turn.fault` afterwards is a typed verdict, not a bare "something threw".
    turn = ResilientTurn("vet", item_id=item_id,
                         notify=retry_notice(context_id, item_id, "vet"))
    # Built once, then both SNAPSHOTTED and SENT — see `runs.surface_from_turn`.
    turn_kwargs = dict(
        resume=None,                     # vet FORGETS — fresh eyes, prior reports are data
        model=model, effort=effort,
        approve=deny_all,                # background: nothing outside the boundary runs
        extra_mcp_servers={**_dev_mcp(ctx, wt, ctx.cwd, item_id, scope="vet"),
                           "run": make_run_report_server(sink)},
        system_append=kernel_speech.work_item_preamble(item_id, item, str(item_dir), interactive=False),
        item_bound=True,                 # one item is the subject — no board-wide in-progress list
        write_boundary=boundary,         # boundary Bash autonomy (running checks IS the job)
        sandbox_writes=boundary,         # …and the kernel holds that same boundary (sandbox.py)
        deny_write_tools=VET_READONLY_NUDGE,   # …but file-write tools die outright (§4)
    )
    # Prompt inspector "A" — throwaway probes ONLY: vet passes work_item_preamble as system_append at
    # the worktree ctx. Normal items skip capture (the run_input table no longer grows per-run).
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
            # The turn total is the accumulated per-message usage (parent + subagents), NOT
            # `Result.usage`, which covers the parent conversation only — measured 3-8x smaller
            # on fan-out runs (see _LiveTokens). Falls back to the Result when no Usage step ever
            # arrived, which is the only case where it is the fuller of the two.
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
    # Record the vet run's own report (trail honesty); the DRIVER below decides off the LEDGER,
    # never off this payload — vet's claims don't route the loop.
    await ensure_completion(ctx, context_id, item_id, sink, skill="vet",
                            session_id=final_session, model=model, effort=effort)
    # ---- THE DRIVER (§5.1): decide off the ledger, close the run, apply, fire the next hop.
    item = _dev.read_work_item(dev_root, item_id) or {}
    evidence = _arts.evidence_status(item_dir, wt)
    # `depth: none` (slice 5b): the plan judged this item to have no observable surface, so the
    # empty ledger IS the right ledger and the cycle report says so in the kernel's words. Written
    # here rather than by vet, for the same reason every other derived fact is: what the plan
    # declared is not a claim an agent should be able to author for itself.
    if evidence.get("not_required"):
        try:
            _arts.note_no_verification(item_dir)
        except (OSError, ValueError):
            log.exception("no-verification note failed for %s", item_id)
    gaps = _arts.lens_gaps(item_dir)
    audit_gaps = _arts.validation_discrepancies(item_dir, cycle=cycle_now)
    # A lens finding — or a validation claim that will not reproduce — that keeps coming back is a
    # wall like any other, so both belong in the convergence signature. Without them, an unfixable
    # one would loop until the budget ran out instead of exiting as `not_converging` with an honest
    # reason.
    fingerprint = _arts.convergence_fingerprint(
        item_dir, extra=[g["text"] for g in gaps] + [f"validation:{a['command']}" for a in audit_gaps])
    # Both breakers read THIS GENERATION only (§3-bis.4): a revision opens a generation, so the
    # budget refreshes and a pre-redesign failure history stops counting against work it can't
    # describe. Without this, any revise round after a `budget` exit died on its first vet.
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
    # The run outcome mirrors the TYPED exit, not prose: `converged` is the clean pass, the two
    # non-convergent exits are `stagnated`, a budget exit is `exhausted`, and a fault is `blocked`
    # (it says the run couldn't produce a verdict — never that the work is bad).
    outcome = {"converged": "success", "budget": "exhausted",
               "not_converging": "stagnated", "no_progress": "stagnated",
               "system_fault": "blocked", "error": "blocked"}.get(str(d.get("exit") or "")) \
        or ("blocked" if turn_error else "success" if d["action"] in ("build", "revet") else None)
    # R2: the item STOPS where it died. No phase flip — advancing past work that never happened is
    # exactly the lie this status exists to stop telling — and the reason is the one R1 classified.
    if d["action"] == "error":
        mark_item_error(ctx, context_id, item_id, d["reason"], phase="vet")
    _end_run(ctx, context_id, item_id, final_tokens, d["status"] or "active", final_usage,
             outcome=outcome, session_id=final_session)
    if d["record"]:
        try:
            _arts.append_cycle_outcome(item_dir, evidence=str(evidence.get("status")),
                                       decision=d["action"], reason=d["reason"],
                                       loop_exit=str(d.get("exit") or ""),
                                       fingerprint=fingerprint if evidence.get("status") == "failed" else "",
                                       failed=d.get("failed") or (), tokens=spent, budget=budget)
        except Exception:
            log.exception("§Cycle outcome append failed for %s", item_id)
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
        # Review ENTRY run (renovation §2.2). This hop is how items normally reach review — it
        # CAS-flips the phase itself and never goes through `advance_item` — so the entry run has
        # to be fired here too, through the same shared firer. Its `_end_run` then dispatches the
        # deputy, which is why nothing deputizes the gate on this line: judging before the report
        # exists would judge a document nobody wrote.
        from .runs import fire_review_entry
        if not fire_review_entry(context_id, item_id, _spine):
            log.warning("loop: review-entry run did not start for %s", item_id)
    elif d["action"] == "build":
        # Compaction, run-START for the BUILD thread. Build REMEMBERS — the same session carries
        # every cycle — so it is the other accumulating thread besides intake (vet mints fresh and
        # never needs this). Here is its run boundary: the vet run's row is closed, the build
        # cycle's is not yet open. Awaited, because the compaction needs that free lock.
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
    trigger = kernel_speech.build_first_trigger(
        item_id, title,
        vet_env=bool(getattr(_spine.repos().get(context_id), "vet_env", None)))
    model, effort = _resolve_run_params(context_id, item)
    if not _begin_run(ctx, context_id, item_id, "build", model, phase="build"):
        return False, "a run is already in progress for this item"
    run_tasks.track(asyncio.create_task(
        _run_background_build(ctx, context_id, item_id, model, effort, trigger=trigger)))
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
    if _arts.latest_cycle_report(dev_root / "work-items" / item_id) is None:
        return False, "no cycle report to hand over — a loop build cycle needs one"
    model, effort = _resolve_run_params(context_id, item)
    if not _begin_run(ctx, context_id, item_id, "build", model, phase="build"):
        return False, "a run is already in progress for this item"
    run_tasks.track(asyncio.create_task(_run_background_build(ctx, context_id, item_id, model, effort)))
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
    boundary = [wt, item_dir]    # one definition — turn, sandbox, and X-ray capture all read it
    prev_build = (item.get("sessions") or {}).get("build")
    title = item.get("title") or item_id
    # Was this thread compacted since its last cycle? Build REMEMBERS, so the skill is invoked once
    # (cycle 1) and later cycles resume with the procedure already in context — a compaction can cut
    # it away, and nothing would re-invoke it. Same signal the checkpoint pointer uses.
    compacted = compacted_checkpoint(ctx, item, prev_build)
    if trigger is None:
        report = _arts.latest_cycle_report(item_dir)   # capped handoff (§8·O10)
        # Thin trigger (Thread 3 §4): the failed cycle's report IS the work order; the build skill
        # owns the procedure, the system layer carries the run contract.
        # Vet's located causes lead the work order (design §5) — the report carries them too, but
        # buried, and a build cycle that has to go find them re-derives what vet already knew.
        # Failing checks only: `verdict_rows` carries a diagnosis only while it matches the cycle
        # of the verdict it explains, so a cause the code has already moved past never leads.
        found = {r["check"]: r for r in _arts.verdict_rows(item_dir)
                 if r.get("why") and not r["passed"] and not r["deferred"]}
        # A gating lens finding is work for this cycle too, and it arrives with no `where` of its
        # own — the finding text carries the location (the vet skill says to name it).
        found.update({f"the {g['lens']} lens": {"where": f"{g['severity']} finding",
                                                "why": g["text"]}
                      for g in _arts.lens_gaps(item_dir)})
        trigger = kernel_speech.build_loop_trigger(item_id, title, report["cycle"], report["text"],
                                                   reload_skill=bool(compacted),
                                                   diagnoses=found or None)
    # Open this cycle's report (renovation §3.1: build fills §Built/§Validation as it works;
    # idempotent when the open cycle's file already exists — e.g. a continue on a parked build).
    try:
        _arts.scaffold_cycle(item_dir, title=title)
    except Exception:
        log.exception("cycle-report scaffold failed for %s", item_id)
    prompt = trigger   # orientation is on-demand — the build skill's directed reads (renovation §1)
    capture_prompt(context_id, trigger, item_id=item_id)
    # Prompt inspector "A" — throwaway probes ONLY: build passes work_item_preamble as system_append
    # at the worktree ctx; the body carries the orient block only on the item's first build turn (else
    # just the trigger). Normal items skip capture (the run_input table no longer grows per-run).
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = _LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    # R1: same ladder as vet. An upstream API error arrives as assistant TEXT rather than an
    # exception, so without a classifier the turn looks like a clean no-op and `decide_after_build`
    # advances it to vet as a successful cycle (run 804, 2026-07-30). `ResilientTurn` catches that
    # shape — no tool call, and a reply that IS the SDK's error line — waits it out, and only when
    # the ladder is spent reports a failure the loop treats as a fault.
    turn = ResilientTurn("build", item_id=item_id,
                         notify=retry_notice(context_id, item_id, "build"))
    # Built once, then both SNAPSHOTTED and SENT — see `runs.surface_from_turn`.
    turn_kwargs = dict(
        resume=prev_build,               # build REMEMBERS — same thread every cycle
        model=model, effort=effort,
        approve=deny_all,
        extra_mcp_servers={**_dev_mcp(ctx, wt, ctx.cwd, item_id, scope="build"),
                           "run": make_run_report_server(sink)},
        # Build REMEMBERS, so it is the other thread compaction can hit — carry the
        # post-compaction pointer when its newest finished run was the compaction itself.
        system_append=kernel_speech.work_item_preamble(
            item_id, item, str(item_dir), interactive=False,
            compacted_checkpoint=compacted),
        item_bound=True,                 # one item is the subject — no board-wide in-progress list
        write_boundary=boundary,         # S4 freeze: writes stay in worktree + item dir
        sandbox_writes=boundary,         # …enforced for shell commands by the OS (sandbox.py)
    )
    # Prompt inspector "A" — throwaway probes ONLY: build passes work_item_preamble as system_append
    # at the worktree ctx; the body carries the orient block only on the item's first build turn (else
    # just the trigger). Normal items skip capture (the run_input table no longer grows per-run).
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
            # The turn total is the accumulated per-message usage (parent + subagents), NOT
            # `Result.usage`, which covers the parent conversation only — measured 3-8x smaller
            # on fan-out runs (see _LiveTokens). Falls back to the Result when no Usage step ever
            # arrived, which is the only case where it is the fuller of the two.
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
        # `moved` = the owner moved/paused it → theirs (loop yields). `needs_user` is 4f's commit
        # wall — the ONE state that legitimately rests inside the loop, because nothing landed on
        # the branch and review would show an empty diff. `infra` is a SuperMe FAULT: bounded
        # retry, then hand the item to review labelled as our bug, never left sitting at `build`.
        reports = _arts.cycle_reports(item_dir)
        cycle = reports[-1]["cycle"] if reports else 0
        if d["klass"] == "infra":
            # R2: the build run STOPPED. R1's ladder already waited it out (up to seven attempts,
            # ~29 minutes), so a second retry ladder here would only multiply the wait — and the
            # old ending was worse than that: it advanced the item to REVIEW, presenting a gate on
            # a cycle that never ran. The item stops at `build`, where it died, labelled with R1's
            # typed reason, and Resume (R4) or re-run (R5) is the way out. Never terminal.
            reason = turn.fault.reason or "the build run stopped before it could report"
            mark_item_error(ctx, context_id, item_id, reason, phase="build")
            _end_run(ctx, context_id, item_id, final_tokens, "error", final_usage,
                     outcome="blocked", session_id=final_session)
            _log_decision(context_id, item_id, cycle,
                          {"action": "error", "exit": "error", "fault": reason,
                           "reason": f"the build run stopped — {reason}; the item is held at build "
                                     "for you to resume or re-run"})
            return
        # A `revise` is not a wall and not a hold: the item is about to run its plan phase, so it
        # rests `active` like any hand-off inside the loop. Only a state the OWNER must clear rests
        # at `awaiting_human`.
        still_ours = d["klass"] not in ("moved", "revise")
        rest = ("active" if d["klass"] == "revise" else
                "awaiting_human" if still_ours else str(item.get("status") or "active"))
        _end_run(ctx, context_id, item_id, final_tokens, rest, final_usage,
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
            # Routed HERE, after the run row is closed, so there is exactly one writer for this
            # transition and the record says who actually concluded it.
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
        _end_run(ctx, context_id, item_id, final_tokens, "active", final_usage,
                 outcome=(report_out or {}).get("outcome") or "success", session_id=final_session)
        if moved:
            _dev_store.log_event(context_id, "phase.advance",
                                 "Loop: build cycle done — re-entering vet",
                                 item_id=item_id, actor="daemon",
                                 meta={"from": "build", "to": "vet"})
            # NO-PROGRESS GUARD: if the tree is byte-identical to what the last evidence entry was
            # recorded against AND the plan has not been revised since, this build cycle changed
            # nothing either input can see — vetting it would re-derive the same verdict at full
            # cost. Skip the vet entirely and hand the item over. Catches both the empty build cycle
            # and the `clean_noop` case (a revise that touched only plan text still spawning a
            # build, then a vet on top of it) — EXCEPT when that plan text was the verification
            # plan itself, which is the whole point of `_plan_moved_since_evidence`.
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
    """Record a GRANT on a pending authorization. It RECORDS AND ROUTES NOTHING (renovation §2.1):
    the item stays at review, the owner resolves every pending request in any order, and ONE exit
    then fires — Approve (close applies the granted ops) or `revise` (they land as plan input).

    `start_authorized_build` — the old grant-as-send-back — is deleted. It assumed BUILD applies
    anchor-doc changes, but knowledge writes have one owner (close) and build's freeze boundary
    hard-denies writes outside the worktree, so a granted `doc-sync` kicked build into a cycle that
    could not perform the very thing it was granted. It also made per-request grants impossible: the
    first grant flipped the item to `build`, so the second silently no-opped on its phase guard.

    Shared by the owner's authorize action and the deputy's delegated grant. The caller (deputy
    path) enforces delegation BEFORE calling this — the owner grants unconditionally.
    Returns (recorded, reason)."""
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
