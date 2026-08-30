"""Gate transitions — the advance core, shared by the owner's route and the autopilot driver.

`advance_item` is the phase-advance body lifted out of the HTTP handler so both callers share ONE
implementation; they differ solely in `actor`.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from ...core import artifacts, git_layer, autopilot as autopilot_core
from ...core.vocab import kind_profiles
from . import run_tasks

log = logging.getLogger("superme-agent")


def _compact_then_readvance(ctx, context_id: str, item_id: str, item: dict) -> bool:
    """The background chain's run-start compaction check.

    True means one was scheduled and the caller must return. The session checked is the one the
    phase that just ran left behind; nothing accumulates across phases."""
    from . import compaction
    session_id = item.get("session_id")
    if compaction.due(session_id, item.get("kind")) is None:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False   # offline/sync (test driver) — nothing to schedule onto

    from ..app_state import get_spine
    model = get_spine().effective_model(context_id, item_model=item.get("model"))

    async def _run() -> None:
        try:
            await compaction.compact_before_run(ctx, context_id, item_id, session_id,
                                                kind=item.get("kind"), model=model)
        except Exception:
            log.exception("gate-seam compaction failed for %s", item_id)
        finally:
            maybe_autopilot_advance(context_id, item_id)   # the advance this call deferred

    loop.create_task(_run())
    return True


def maybe_autopilot_advance(context_id: str, item_id: str) -> None:
    """Called after a background phase run rests an item at its gate.

    Autopilot removes the waiting; the judgment is the deputy's. Without one, review is left for the
    owner."""
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
        if item is None:
            return
        # A phase that is not a gate has nothing to wait for, and `investigate` has no driver of
        # its own.
        if (str(item.get("phase") or "") == "investigate"
                and str(item.get("status") or "") == "awaiting_human"
                and not autopilot_core.is_autopilot(item)):
            autopilot_advance(ctx, context_id, item_id, actor="daemon")
            return
        if not autopilot_core.is_autopilot(item):
            return
        # The probe sails through every gate so each phase's prompt is captured, then tears itself
        # down at close.
        if autopilot_core.is_prompt_extraction(item):
            if str(item.get("phase")) == "close" and str(item.get("status")) == "awaiting_human":
                from . import prompt_extraction as px
                px.teardown(context_id, item_id, reason="probe reached close")
            elif autopilot_core.throwaway_advance_target(item, kind_profiles.next_phase):
                # The probe IS autopilot-driven; `prompt-extraction` is a run feature tag, never
                # an actor.
                autopilot_advance(ctx, context_id, item_id, actor="autopilot")
            return
        spine = get_spine()
        # A run took the item's lock after this dispatch was scheduled — judge nothing; its own
        # end re-enters.
        if spine.is_item_running(context_id, item_id):
            return
        # The finished run's lock is released and the next has not been taken: the one instant
        # where compacting strands nothing.
        if _compact_then_readvance(ctx, context_id, item_id, item):
            return
        # Scheduled as a task, since the judgment is a headless agent turn. No loop falls through
        # to direct advance.
        if spine.get_deputy_enabled():
            from . import deputy as deputy_svc
            # Not a judged gate, so fall through to the mechanical advance — returning would
            # strand the item forever.
            if deputy_svc.deputy_gate_for(item) is not None:
                try:
                    asyncio.get_running_loop().create_task(
                        deputy_svc.run_deputy_gate(context_id, item_id))
                    return
                except RuntimeError:
                    pass  # no loop — offline; use the direct baseline below
        # No-deputy baseline, a non-gate phase, or offline: the mechanical advance (excludes review).
        if autopilot_core.auto_advance_target(item, kind_profiles.next_phase) is None:
            return
        autopilot_advance(ctx, context_id, item_id, actor="autopilot")
    except HTTPException as e:
        log.info("autopilot hold for %s: %s", item_id, getattr(e, "detail", e))
    except Exception:
        log.exception("autopilot advance failed for %s (item stays at its gate)", item_id)


async def enter_build_loop(context_id: str, item_id: str) -> None:
    """An item just entered `build`, so launch the autonomous build⟷vet loop it now owes.

    Fires for every item, because approving the plan gate is the instruction to build."""
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
    """Cap-aware phase advance for an autopilot or deputy transition.

    Parks at `awaiting_slot` when the next phase is `build` and the repo's cap is full."""
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
                        spine=spine, actor=actor)


def pump_autopilot_slots(context_id: str) -> None:
    """A build⟷vet slot just freed — release the next queued autopilot items into build.

    Advances the oldest-queued `awaiting_slot` items up to the number of free slots. Idempotent."""
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
            # Re-park on refusal: a queued item that is not ready must stay queued, never fall to
            # `awaiting_human`.
            dev.set_work_item_status(dev_root, hid, "awaiting_human")
            try:
                advance_item(ctx, context_id, hid, dev=dev, dev_store=app_state.dev_store,
                             spine=spine, actor="autopilot")
                log.info("autopilot pump: released %s into build", hid)
            except HTTPException as e:
                dev.set_work_item_status(dev_root, hid, autopilot_core.AWAITING_SLOT)
                log.info("autopilot pump: %s not ready, re-queued (%s)", hid,
                         getattr(e, "detail", e))
    except Exception:
        log.exception("autopilot pump failed for %s", context_id)


def advance_item(ctx, context_id: str, item_id: str, *, dev, dev_store, spine,
                 actor: str) -> dict:
    """Advance a work-item to its kind's next phase.

    Raises on the refusals the route always enforced: terminal, run in flight, no next phase, an
    un-gate-ready plan."""
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
    # Only a kind that PLANS is asked for its plan; research enters `investigate` straight from
    # triage and has no plan.md.
    git_record = None
    profile = kind_profiles.get_profile(item.get("kind"))
    if nxt in ("build", "investigate") and "plan" in profile.phases:
        item_dir = dev_root / "work-items" / item_id
        plan_issues = artifacts.self_check(item_dir, "plan", item_kind=item.get("kind"))
        if plan_issues:
            raise HTTPException(status_code=409,
                                detail="plan.md isn't gate-ready — " + "; ".join(plan_issues[:3]))
    # The branch and worktree are created transactionally before the phase flips. A blocking child
    # branches from its parent.
    if nxt in ("plan", "build") and profile.worktree and not item.get("git_worktree"):
        from .git_ops import repo_anchor
        base = repo_anchor(ctx, spine)
        sf = item.get("spawned_from") or {}
        if isinstance(sf, dict) and sf.get("relation") == "blocking":
            base = (dev.read_work_item(dev_root, str(sf.get("item"))) or {}).get("git_branch") or base
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
        # Say so when the commit gate is not in force: silent absence would read as enforcement.
        hook = git_record.get("commit_hook") or {}
        if not hook.get("installed"):
            dev_store.log_event(
                context_id, "git.hook",
                {"foreign": "Commit trailers are NOT enforced — this repo already has its own "
                            "commit-msg hook, which SuperMe will not overwrite.",
                 "hooks_path_override": "Commit trailers are NOT enforced — this repo sets "
                                        "core.hooksPath, so .git/hooks is never consulted."}
                .get(str(hook.get("reason")),
                     f"Commit trailers are NOT enforced ({hook.get('reason') or 'unknown'})."),
                item_id=item_id, actor="daemon", meta=hook)
    # A research item has nothing to land, so it waits to be read rather than closing on a
    # deputy's word.
    if cur == "review" and not profile.worktree and actor != "owner" \
            and not autopilot_core.is_prompt_extraction(item):
        from . import git_ops
        if git_ops.repo_review_mode(ctx, spine) == "strict":
            dev.set_work_item_status(dev_root, item_id, "awaiting_human")
            return {"ok": True, "id": item_id, "phase": "review", "from": "review",
                    "held_for_owner": True}
    # Leaving review merges the branch, through the same body the merge route runs, but only for
    # kinds that produce code.
    review_merge_out = None
    if cur == "review" and profile.worktree:
        from . import git_ops
        # In `strict` a deputy approve opens the PR and hands the merge over; the owner's approve
        # always merges.
        if actor != "owner" and not autopilot_core.is_prompt_extraction(item) \
                and git_ops.repo_review_mode(ctx, spine) == "strict":
            return git_ops.open_pr(ctx, context_id, item_id, dev=dev, dev_store=dev_store)
        review_merge_out = git_ops.review_merge(ctx, context_id, item_id,
                                                dev=dev, dev_store=dev_store, spine=spine)
        # The merge act owns freshness. Its two non-merge answers are the anchor having moved
        # under a waiting gate.
        if review_merge_out.get("freshness") == "revet":
            # The anchor changed files this item also changed, so the evidence is stale and the
            # item earns one vet cycle.
            paths = review_merge_out.get("stale_paths") or []
            from .loop import _cas_phase, start_vet_run
            if _cas_phase(dev_root, item_id, "review", "vet"):
                # A spent approval closes the PR with it: the branch is about to carry the
                # anchor's changes too.
                git_ops.close_pr(dev, dev_root, item_id)
                dev.set_work_item_status(dev_root, item_id, "active")
                start_vet_run(ctx, context_id, item_id)
            raise HTTPException(
                status_code=409,
                detail=f"the anchor moved over {len(paths)} file(s) this item also changed "
                       f"({', '.join(paths[:3])}) — it has been synced and sent back for one vet "
                       "cycle. It returns to review with re-verified evidence.")
        if review_merge_out.get("freshness") == "park":
            n = len(review_merge_out.get("conflicts") or [])
            raise HTTPException(
                status_code=409,
                detail=f"syncing the anchor into this branch hit {n} conflict(s) — Resolve-with-"
                       "Agent in the worktree, then approve again. Conflicts are never resolved "
                       "unwatched.")
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
    # Every advance moves an `awaiting_human` item to `active`: it just got its answer and
    # proceeds.
    if str(item.get("status")) == "awaiting_human":
        dev.set_work_item_status(dev_root, item_id, "active")
    # Ratification is owner-only: an autopilot advance carries no human moment.
    dev_store.log_event(context_id, "phase.advance",
                        f"Approved {cur} → {nxt}: {item.get('title') or item_id}",
                        item_id=item_id, actor=actor,
                        meta={"from": cur, "to": nxt, "autopilot": actor != "owner"})
    # A phase whose whole work is one background run fires it now; a failed dispatch rests the
    # item back.
    auto_skill = {"plan": "plan", "investigate": "investigate"}.get(nxt)
    if str(item.get("kind")) == "research" and nxt == "close":
        # Approving research IS the itemization decision, so without this the approval means
        # nothing.
        auto_skill = "itemize"
        # Owner approvals only: an over-broad rule suppresses questions that should have reached
        # them. Idempotent by question.
        if actor == "owner":
            try:
                from datetime import date as _date

                from ...core import decision_ledger as _ledger
                ids = _ledger.record_rulings(dev_root, dev_root / "work-items" / item_id, item_id,
                                             date=_date.today().isoformat(), project=str(ctx.id))
                if ids:
                    dev_store.log_event(context_id, "decision.recorded", item_id=item_id,
                                        summary=f"recorded {len(ids)} standing rule(s): "
                                                + ", ".join(ids),
                                        meta={"ids": ids})
            except Exception:
                log.exception("recording standing rules failed for %s", item_id)
    auto_started = False
    if nxt == "review":
        # Review's entry run goes through the shared firer, because the loop's vet→review hop
        # never reaches `advance_item`.
        from .runs import fire_review_entry
        auto_started = fire_review_entry(context_id, item_id, spine)
        if not auto_started:
            dev.set_work_item_status(dev_root, item_id, "awaiting_human")
    elif auto_skill:
        try:
            from .runs import begin_run, run_background_plan, run_background_item_skill
            p_model = spine.effective_model(context_id, item_model=item.get("model"))
            p_effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
            phase_dir = dev_root / "work-items" / item_id
            if begin_run(ctx, context_id, item_id, auto_skill, p_model, phase=nxt) is not None:
                coro = (run_background_plan(ctx, context_id, item_id, phase_dir, p_model, p_effort)
                        if auto_skill == "plan" else
                        run_background_item_skill(ctx, context_id, item_id, phase_dir, auto_skill,
                                                   p_model, p_effort))
                run_tasks.track(asyncio.create_task(coro))
                auto_started = True
        except Exception:
            log.exception("auto-%s on approve failed to start for %s", auto_skill, item_id)
        if not auto_started:
            dev.set_work_item_status(dev_root, item_id, "awaiting_human")
    if spine.learning_enabled_for(context_id):
        from .learning import _fire_sweep_bg
        _fire_sweep_bg(ctx, item.get("session_id"))
    log.info("advanced work-item %s: %s → %s (actor=%s)", item_id, cur, nxt, actor)
    # Phase-entry effects hang off the TRANSITION, not the caller, so an item never lands at a
    # phase and just sits.
    autopiloted = autopilot_core.is_autopilot(item)
    if nxt == "build":
        # NOT autopilot-gated: approving the plan gate IS the instruction to build, and build⟷vet
        # is human-free either way.
        try:
            asyncio.get_running_loop().create_task(enter_build_loop(context_id, item_id))
        except RuntimeError:
            pass
    elif nxt == "review" and autopiloted and not auto_started:
        # The fallback for when review's entry run did not start; otherwise the item sits at
        # review with nobody judging.
        try:
            asyncio.get_running_loop().call_soon(maybe_autopilot_advance, context_id, item_id)
        except RuntimeError:
            pass
    elif nxt == "close":
        # Fires for EVERY actor: there is no human step inside close, and `active` with no run is
        # an idle stall.
        try:
            from .runs import fire_close_run
            asyncio.get_running_loop().call_soon(fire_close_run, context_id, item_id, spine)
        except RuntimeError:
            pass
    # A slot just freed, so pump the autopilot queue. Scheduled to run after this transition
    # settles.
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
