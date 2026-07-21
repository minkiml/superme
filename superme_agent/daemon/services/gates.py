"""Gate transitions — the advance core, shared by the owner's route and the autopilot driver.

`advance_item` is the phase-advance body lifted out of the HTTP handler so two callers share ONE
implementation: the owner clicking Approve (`routers/dev/work_items.py`) and the autopilot driver
firing the same transition without a click. One code path means an autopiloted item and a
hand-driven item produce identical artifacts, git topology and events — the property the whole
design rests on (§2 "autopilot changes only who triggers the transition, never what it does").

The ONE thing that legitimately differs by caller is `ratify`:

    An owner approval RATIFIES the assumptions the gate brief listed — the contracted human moment
    where they saw the calls made without them and said go. An autopilot advance has NO such
    moment, so it must NOT ratify. Unratified assumptions then accumulate and the close gate refuses
    on them until a human clears them. This is the human floor (§2b) and it is load-bearing: it is
    what stops "autopilot" from silently deciding things that were the owner's to decide.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from ...core import artifacts, kind_profiles, git_layer, autopilot as autopilot_core

log = logging.getLogger("superme-agent")


def maybe_autopilot_advance(context_id: str, item_id: str) -> None:
    """Called after a background phase run rests an item at its gate (`_end_run`). For an autopilot
    item, this is the moment the owner would click — but autopilot only removes the WAITING; the
    JUDGMENT is the deputy's (design §2b, the two are orthogonal).

    **With a deputy (default, slice 4).** Dispatch the deputy to judge this gate — its verdict then
    advances / sends back / escalates. Every gate is judged by SOMEONE (the load-bearing invariant:
    never by nobody). This fires at triage-exit, plan, AND review (a human gate autopilot would
    otherwise leave sitting).

    **Without a deputy (opt-out).** The slice-2/3 unsupervised baseline: advance the non-review gates
    directly with `ratify=False` (the human floor still holds — unratified assumptions block close),
    cap-aware entering build, review left for the owner. This is the design's named DANGEROUS config,
    now reachable only by explicitly disabling the deputy.

    Best-effort and self-scheduling: any failure leaves the item resting at its gate for a click.
    """
    from .. import app_state
    from ..app_state import get_spine
    from ...gateway import contexts
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return
        dev = app_state.dev
        dev_root = ctx.internal_root / "dev"
        item = dev.read_work_item(dev_root, item_id)
        if item is None or not autopilot_core.is_autopilot(item):
            return
        spine = get_spine()
        # Deputy present → it judges the gate (triage/plan/review). Scheduled as a task since the
        # judgment is a headless agent turn. No running loop (offline tests) → fall through to the
        # direct-advance baseline, which the suites exercise without a deputy.
        if spine.get_deputy_enabled():
            from . import deputy as deputy_svc
            if deputy_svc.deputy_gate_for(item) is None:
                return
            try:
                asyncio.get_running_loop().create_task(
                    deputy_svc.run_deputy_gate(context_id, item_id))
                return
            except RuntimeError:
                pass  # no loop — offline; use the direct baseline below
        # No-deputy baseline (or offline): the mechanical auto-advance (excludes review).
        if autopilot_core.auto_advance_target(item, kind_profiles.next_phase) is None:
            return
        autopilot_advance(ctx, context_id, item_id, actor="autopilot")
    except HTTPException as e:
        log.info("autopilot hold for %s: %s", item_id, getattr(e, "detail", e))
    except Exception:
        log.exception("autopilot advance failed for %s (item stays at its gate)", item_id)


async def enter_build_loop(context_id: str, item_id: str) -> None:
    """An autopilot/deputy item just entered `build` (worktree created) — launch the autonomous
    build⟷vet loop it now owes. Build-first (owner, 2026-07-20): the loop OPENS with an
    implementation cycle from the plan, then loop.py self-drives (build→vet→decide→build|review).
    Vet stays the loop's sole DECISION point; it is just no longer the ENTRY (a vet against an
    empty tree is a wasted look). Best-effort: a failure leaves the item resting at build for the
    owner."""
    from .. import app_state
    from ...gateway import contexts
    from . import loop as loop_svc
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return
        dev = app_state.dev
        dev_root = ctx.internal_root / "dev"
        item = dev.read_work_item(dev_root, item_id)
        if item is None or str(item.get("phase")) != "build":
            return  # someone already moved it; don't fight them
        started, why = loop_svc.start_first_build(ctx, context_id, item_id)
        if not started:
            log.warning("autopilot loop entry did not start for %s: %s", item_id, why)
    except Exception:
        log.exception("autopilot loop entry failed for %s", item_id)


def autopilot_advance(ctx, context_id: str, item_id: str, *, actor: str):
    """Cap-aware phase advance for an autopilot/deputy transition. Computes the next phase; if it is
    `build` and the repo's autopilot concurrency cap is full, parks the item at `awaiting_slot` (the
    pump releases it when a slot frees) and returns None. Otherwise advances with `ratify=False` (the
    human floor — only the owner ratifies) and returns the advance dict. Shared by the deputy's
    `approve` and the no-deputy baseline so both honour the cap identically."""
    from .. import app_state
    from ..app_state import get_spine
    dev = app_state.dev
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        return None
    cur = str(item.get("phase") or "triage")
    try:
        nxt = kind_profiles.next_phase(item.get("kind"), cur)
    except KeyError:
        return None
    if not nxt:
        return None
    spine = get_spine()
    if nxt == "build":
        cap = spine.get_autopilot_concurrency(context_id)
        all_items = dev.read_all(dev_root)["work_items"]
        if autopilot_core.free_build_slots(all_items, cap) <= 0:
            if dev.set_work_item_status(dev_root, item_id, autopilot_core.AWAITING_SLOT):
                app_state.dev_store.log_event(
                    context_id, "item.await",
                    f"Autopilot cap full ({cap}) — queued for a build slot",
                    item_id=item_id, actor="daemon", meta={"cap": cap})
                log.info("autopilot cap full (%d) — %s queued for a slot", cap, item_id)
            return None
    return advance_item(ctx, context_id, item_id, dev=dev, dev_store=app_state.dev_store,
                        spine=spine, ratify=False, actor=actor)


def pump_autopilot_slots(context_id: str) -> None:
    """A build⟷vet slot just freed — release the next queued autopilot item(s) into build. Reads the
    repo cap, counts free slots, and advances the oldest-queued `awaiting_slot` items up to that
    count. Each advance flips the item plan→build (occupying the slot). Best-effort; idempotent
    (nothing to do when the queue is empty or the cap is still full)."""
    from .. import app_state
    from ..app_state import get_spine
    from ...gateway import contexts
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return
        dev = app_state.dev
        dev_root = ctx.internal_root / "dev"
        spine = get_spine()
        cap = spine.get_autopilot_concurrency(context_id)
        all_items = dev.read_all(dev_root)["work_items"]
        free = autopilot_core.free_build_slots(all_items, cap)
        if free <= 0:
            return
        for held in autopilot_core.held_for_slot(all_items)[:free]:
            hid = str(held.get("id"))
            # Un-park before advancing (advance_item asserts a normal resting state), then re-enter
            # build. If the advance is refused (e.g. plan not yet gate-ready), RE-PARK it — a queued
            # item that isn't ready must stay queued, never fall to awaiting_human (a false page).
            dev.set_work_item_status(dev_root, hid, "awaiting_human")
            try:
                advance_item(ctx, context_id, hid, dev=dev, dev_store=app_state.dev_store,
                             spine=spine, ratify=False, actor="autopilot")
                log.info("autopilot pump: released %s into build", hid)
            except HTTPException as e:
                dev.set_work_item_status(dev_root, hid, autopilot_core.AWAITING_SLOT)
                log.info("autopilot pump: %s not ready, re-queued (%s)", hid,
                         getattr(e, "detail", e))
    except Exception:
        log.exception("autopilot pump failed for %s", context_id)


def advance_item(ctx, context_id: str, item_id: str, *, dev, dev_store, spine,
                 ratify: bool, actor: str) -> dict:
    """Advance a work-item to its kind's next phase. Raises HTTPException on the same refusals the
    route always enforced (terminal, run in flight, no next phase, un-gate-ready plan, worktree
    failure). `ratify` — see module docstring. `actor` labels the phase.advance event
    (`owner` | `autopilot` | `deputy`). Returns `{ok, id, phase, from[, git]}`."""
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if item.get("done_at") or str(item.get("status")) == "done":
        raise HTTPException(status_code=409, detail="item is terminal")
    if spine.is_item_running(context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is in progress for this item")
    cur = str(item.get("phase") or "triage")
    try:
        nxt = kind_profiles.next_phase(item.get("kind"), cur)
    except KeyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not nxt:
        raise HTTPException(status_code=409, detail=f"phase {cur} has no next phase")
    # Entering the working phase CONSUMES plan.md (D6 validation-at-consumption): a gate-unready
    # plan means approving a plan that doesn't exist.
    git_record = None
    profile = kind_profiles.get_profile(item.get("kind"))
    if nxt in ("build", "investigate"):
        item_dir = dev_root / "work-items" / item_id
        plan_issues = artifacts.self_check(item_dir, "plan", item_kind=item.get("kind"))
        if plan_issues:
            raise HTTPException(status_code=409,
                                detail="plan.md isn't gate-ready — " + "; ".join(plan_issues[:3]))
    # Entering build for a worktree kind creates the branch + worktree TRANSACTIONALLY before the
    # phase flips (D4). Blocking child branches from its parent's branch; others from the trunk.
    if nxt == "build" and profile.worktree and not item.get("git_worktree"):
        base = None
        sf = item.get("spawned_from") or {}
        if isinstance(sf, dict) and sf.get("relation") == "blocking":
            base = (dev.read_work_item(dev_root, str(sf.get("item"))) or {}).get("git_branch")
        try:
            git_record = git_layer.create_worktree(ctx.cwd, ctx.id, item_id,
                                                   item.get("title") or "", base=base)
        except (git_layer.GitError, git_layer.GitBusy) as e:
            raise HTTPException(status_code=409, detail=f"worktree create failed: {e}")
        dev.set_work_item_git(dev_root, item_id, git_branch=git_record["branch"],
                              git_worktree=git_record["worktree"], git_base=git_record["base"])
        dev_store.log_event(context_id, "git.worktree",
                            f"Created worktree on branch {git_record['branch']}",
                            item_id=item_id, actor="daemon", meta=git_record)
    # The review decision IS the merge (B2): leaving review = merging the branch. Do it BEFORE the
    # phase flips (the merge machinery is review-only) and via the SAME body the `/git/merge` route
    # runs — so an owner Approve, a deputy approval, and the raw route all merge identically instead
    # of the Approve stranding the item in `close` unmerged. On CONFLICTS, DON'T advance: hold at
    # review so the owner syncs + resolves and approves again (a merge is never forced through).
    review_merge_out = None
    if cur == "review":
        from . import git_ops
        review_merge_out = git_ops.review_merge(ctx, context_id, item_id,
                                                dev=dev, dev_store=dev_store, spine=spine)
        if not (review_merge_out.get("merged") or review_merge_out.get("already_merged")):
            n = len(review_merge_out.get("conflicts") or [])
            raise HTTPException(
                status_code=409,
                detail=f"review merge hit {n} conflict(s) — sync + Resolve-with-Agent in the "
                       "worktree, then approve again. The item stays at review until it merges "
                       "cleanly (unvetted/conflicted work never lands on main).")
    # Vet forgets: entering vet retires the previous cycle's vet thread so this cycle mints fresh.
    if nxt == "vet":
        from .runs import reset_vet_thread
        reset_vet_thread(ctx, item)
    dev.set_work_item_phase(dev_root, item_id, nxt)
    # Resting status after the flip: `review` is a human/deputy GATE reached with no background run,
    # so it rests at `awaiting_human` — the paged gate state — NOT `active`, which with no run is
    # exactly the idle stall (P6). (An autopilot item then gets its review deputy dispatched below;
    # deputy-off, it correctly waits for the owner.) Every other advance moves an awaiting_human item
    # to `active` — it just got its answer and proceeds into a phase that runs.
    if nxt == "review":
        dev.set_work_item_status(dev_root, item_id, "awaiting_human")
        # B3: an owner force into review authors readiness.md too (the loop authors it on its own
        # vet→review hop; this covers the manual path). Best-effort, before the deputy is dispatched.
        from . import git_ops
        git_ops.write_review_readiness(ctx, item, dev_root / "work-items" / item_id, dev_root)
    elif str(item.get("status")) == "awaiting_human":
        dev.set_work_item_status(dev_root, item_id, "active")
    # Ratification — owner-only. See module docstring: an autopilot advance carries no human
    # moment, so it leaves the assumptions unratified for the close gate to catch.
    ratified = artifacts.ratify_assumptions(dev_root / "work-items" / item_id) if ratify else 0
    dev_store.log_event(context_id, "phase.advance",
                        f"Approved {cur} → {nxt}: {item.get('title') or item_id}",
                        item_id=item_id, actor=actor,
                        meta={"from": cur, "to": nxt, "assumptions_ratified": ratified,
                              "autopilot": actor != "owner"})
    # Auto-plan on approve: advancing INTO plan fires the plan run immediately.
    if nxt == "plan":
        try:
            from .runs import _begin_run, _run_background_plan
            p_model = spine.effective_model(context_id, item_model=item.get("model"))
            p_effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
            plan_dir = dev_root / "work-items" / item_id
            if _begin_run(ctx, context_id, item_id, "plan", p_model, phase="plan") is not None:
                asyncio.create_task(
                    _run_background_plan(ctx, context_id, item_id, plan_dir, p_model, p_effort))
        except Exception:
            log.exception("auto-plan on approve failed to start for %s", item_id)
    if spine.learning_enabled_for(context_id):
        from .learning import _fire_sweep_bg
        _fire_sweep_bg(ctx, item.get("session_id"))
    log.info("advanced work-item %s: %s → %s (actor=%s)", item_id, cur, nxt, actor)
    # Phase-entry effects hang off the TRANSITION, not the caller (B5). Whether the advance came
    # from the owner (a force / an Approve), the loop, the scheduler, or the deputy, an AUTOPILOT
    # item's next step must fire — the first-kick invariant (an autopilot item never lands at a
    # phase and just sits). The gate is `is_autopilot(item)`, NOT `actor != "owner"`: a
    # hand-driven item legitimately parks (no loop, no deputy), but an owner who force-advances an
    # autopilot item must keep autopilot semantics. `plan` is absent here — it already fired its
    # auto-plan run above (whose _end_run then dispatches the plan deputy), for every actor.
    autopiloted = autopilot_core.is_autopilot(item)
    if nxt == "build" and autopiloted:
        # Entering build → open the autonomous build⟷vet loop (build-first). The worktree just
        # landed above; scheduled after this transition settles. No-op off a loop.
        try:
            asyncio.get_running_loop().create_task(enter_build_loop(context_id, item_id))
        except RuntimeError:
            pass
    elif nxt == "review" and autopiloted:
        # Review is a deputy gate reached with NO background run (the loop CAS-flips vet→review and
        # rests awaiting_human, which _end_run already deputizes — but an OWNER force to review, or
        # any advance_item hop into review, would otherwise land the item at review with nobody
        # judging it). Dispatch the same gate driver the run path uses: deputy judges (or, deputy
        # off, review stays the owner's — auto_advance_target excludes review). No-op off a loop.
        try:
            asyncio.get_running_loop().call_soon(maybe_autopilot_advance, context_id, item_id)
        except RuntimeError:
            pass
    elif nxt == "close" and autopiloted:
        # #179: review→close merged the branch, but nobody was authoring the closeout on the
        # autonomous path — so the owner's Complete click failed the close gate and the item wedged.
        # Fire a close run to draft closeout.md + propose_close; completion itself stays the owner's
        # (D8 — the run prepares, never self-completes). No-op off a loop.
        try:
            from .runs import fire_close_run
            asyncio.get_running_loop().call_soon(fire_close_run, context_id, item_id, spine)
        except RuntimeError:
            pass
    # A slot just freed if this advance moved the item OUT of the build⟷vet loop (vet→review) —
    # pump the autopilot queue. Scheduled so it runs after this transition settles; no-op off a loop.
    if cur in autopilot_core.BUILD_SLOT_PHASES and nxt not in autopilot_core.BUILD_SLOT_PHASES:
        try:
            asyncio.get_running_loop().call_soon(pump_autopilot_slots, context_id)
        except RuntimeError:
            pass
    out = {"ok": True, "id": item_id, "phase": nxt, "from": cur}
    if git_record:
        out["git"] = git_record
    if review_merge_out:
        out["merge"] = review_merge_out
    return out
