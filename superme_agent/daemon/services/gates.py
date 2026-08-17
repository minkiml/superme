"""Gate transitions — the advance core, shared by the owner's route and the autopilot driver.

`advance_item` is the phase-advance body lifted out of the HTTP handler so two callers share ONE
implementation: the owner clicking Approve (`routers/dev/work_items.py`) and the autopilot driver
firing the same transition without a click. One code path means an autopiloted item and a
hand-driven item produce identical artifacts, git topology and events — the property the whole
design rests on (§2 "autopilot changes only who triggers the transition, never what it does").

Callers differ only in `actor` (which labels the phase.advance event). The old `ratify` flag —
the owner-only ratification of the assumption ledger — went with `assumptions.md` in the §3.1
demolition (2026-07-27). The human floor now lives where it is enforceable: `revise` is the only
way back to re-work, and Approve is mechanically greyed while any authorization is undecided or any
vet check is failing (§2.1).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from ...core import artifacts, kind_profiles, git_layer, autopilot as autopilot_core
from . import run_tasks

log = logging.getLogger("superme-agent")


def _compact_then_readvance(ctx, context_id: str, item_id: str, item: dict) -> bool:
    """The background chain's run-start compaction check. True = a compaction was scheduled and
    the caller must return; the same seam re-enters once it finishes.

    Only the item's INTAKE session is checked here: it is the one that accumulates across the
    background chain (triage → plan → review → close all land in it). Build has its own thread and
    is compacted at its own run start; vet mints fresh every cycle and never needs it."""
    from . import compaction
    session_id = (item.get("sessions") or {}).get("intake") or item.get("session_id")
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
    """Called after a background phase run rests an item at its gate (`_end_run`). For an autopilot
    item, this is the moment the owner would click — but autopilot only removes the WAITING; the
    JUDGMENT is the deputy's (design §2b, the two are orthogonal).

    **With a deputy (default, slice 4).** Dispatch the deputy to judge this gate — its verdict then
    advances / sends back / escalates. Every gate is judged by SOMEONE (the load-bearing invariant:
    never by nobody). This fires at triage-exit, plan, AND review (a human gate autopilot would
    otherwise leave sitting).

    **Without a deputy (opt-out).** The slice-2/3 unsupervised baseline: advance the non-review gates
    directly, cap-aware entering build, review left for the owner. This is the design's named DANGEROUS config,
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
        if item is None:
            return
        # A DRIVERLESS NON-GATE PHASE ADVANCES ITSELF, autopilot or not.
        #
        # Autopilot removes the WAITING at a gate — so it is the right condition for everything
        # below. It is the WRONG condition here, because a phase that is not a gate has nothing to
        # wait for: nobody is being asked anything. `investigate` is the only such phase without a
        # driver of its own (build and vet are carried by the build⟷vet loop; the rest are gates),
        # so it is the only one named.
        #
        # Live, 2026-08-14: a sweep launched from the button is born AT `investigate`, and a
        # button-launched item is not on autopilot — so it finished its investigation, rested
        # `awaiting_human`, and offered the owner no Approve at all (investigate is not a gate, so
        # there was no gate to approve). Drop, re-run, or run investigate a second time were the
        # only live controls. The autopilot half of this same strand was fixed on 2026-08-13 in the
        # deputy branch below; this is the half that skipped everyone who never enrolled.
        if (str(item.get("phase") or "") == "investigate"
                and str(item.get("status") or "") == "awaiting_human"
                and not autopilot_core.is_autopilot(item)):
            autopilot_advance(ctx, context_id, item_id, actor="daemon")
            return
        if not autopilot_core.is_autopilot(item):
            return
        # Throwaway prompt-extraction probe (Prompt X-ray): NO deputy, NO judgment — it sails through
        # every gate (incl. review, via review_merge's synthetic skip) so each phase's prompt is
        # captured, then tears itself down once it rests at close. Handled before the deputy dispatch.
        if autopilot_core.is_prompt_extraction(item):
            if str(item.get("phase")) == "close" and str(item.get("status")) == "awaiting_human":
                from . import prompt_extraction as px
                px.teardown(context_id, item_id, reason="probe reached close")
            elif autopilot_core.throwaway_advance_target(item, kind_profiles.next_phase):
                # actor stays "autopilot" (a valid event-actor enum) — the probe IS autopilot-driven;
                # "prompt-extraction" is the run FEATURE tag, never an actor.
                autopilot_advance(ctx, context_id, item_id, actor="autopilot")
            return
        spine = get_spine()
        # A run took the item's lock between this dispatch being scheduled and it firing — judge
        # nothing. The concrete case (renovation §2.2): the loop closes the vet run, `_end_run`
        # schedules this, and the loop then fires review's ENTRY run before the callback lands. The
        # deputy would judge a `report-review.md` nobody has written yet, and the compaction seam
        # below would try to compact against a held lock. That entry run's own `_end_run` re-enters
        # here when it finishes, so nothing is lost by returning.
        if spine.is_item_running(context_id, item_id):
            return
        # Compaction, run-START for the BACKGROUND chain. This seam is scheduled by `_end_run` via
        # call_soon, so the finished run's lock is already released and the next phase's has not
        # been taken — the one instant in an autopilot chain where compacting strands nothing.
        # If the item's intake session is over its trigger, compact it and RE-ENTER: the advance
        # then proceeds normally on a compacted thread. The defer latch (released only by a real
        # turn) is what stops the re-entry from looping.
        if _compact_then_readvance(ctx, context_id, item_id, item):
            return
        # Deputy present → it judges the gate (triage/plan/review). Scheduled as a task since the
        # judgment is a headless agent turn. No running loop (offline tests) → fall through to the
        # direct-advance baseline, which the suites exercise without a deputy.
        if spine.get_deputy_enabled():
            from . import deputy as deputy_svc
            # Not one of the three gates → nothing to JUDGE, so fall through to the mechanical
            # advance rather than returning. Returning stranded every autopilot research item at
            # `investigate` (live, 2026-08-13): investigate-exit is not a gate and has no
            # self-driver either — build's exit is carried by the build⟷vet loop, this one by
            # nobody — so the item rested at `awaiting_human` forever with the deputy on (the
            # default), while the no-deputy baseline below advanced it correctly.
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
    """An item just entered `build` (worktree created) — launch the autonomous build⟷vet loop it
    now owes. Fires for EVERY item, autopiloted or hand-driven (owner, 2026-07-29): approving the
    plan gate is the instruction to build, and build is pure agent work with no human step inside
    it. Build-first (owner, 2026-07-20): the loop OPENS with an implementation cycle from the plan,
    then loop.py self-drives (build→vet→decide→build|review). Vet stays the loop's sole DECISION
    point; it is just no longer the ENTRY (a vet against an empty tree is a wasted look).
    Best-effort: a failure leaves the item resting at build for the owner."""
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
    pump releases it when a slot frees) and returns None. Otherwise advances and returns the advance
    dict. Shared by the deputy's `approve` and the no-deputy baseline so both honour the cap
    identically."""
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
    """Advance a work-item to its kind's next phase. Raises HTTPException on the same refusals the
    route always enforced (terminal, run in flight, no next phase, un-gate-ready plan, worktree
    failure). `actor` labels the phase.advance event
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
    #
    # ONLY A KIND THAT PLANS IS ASKED FOR ITS PLAN (live, 2026-08-13). Research lost its plan phase
    # (research-sweep-model-design §3), so `investigate` is now entered straight from triage and
    # there is no plan.md to consume — nor will there ever be. Unguarded, this refused EVERY research
    # item at triage-exit with "plan.md does not exist — scaffold it first", an instruction pointing
    # at a phase the kind no longer has. Caught on dbf1c5c7efa8, the first research item after the
    # change, which is exactly what the live E2E was for.
    #
    # Same fault as `evidence_fresh` and `spawns_exist` before it: a question asked where its
    # answerer does not exist. The condition is the kind's own pipeline, never the phase name.
    git_record = None
    profile = kind_profiles.get_profile(item.get("kind"))
    if nxt in ("build", "investigate") and "plan" in profile.phases:
        item_dir = dev_root / "work-items" / item_id
        plan_issues = artifacts.self_check(item_dir, "plan", item_kind=item.get("kind"))
        if plan_issues:
            raise HTTPException(status_code=409,
                                detail="plan.md isn't gate-ready — " + "; ".join(plan_issues[:3]))
    # Entering build for a worktree kind creates the branch + worktree TRANSACTIONALLY before the
    # phase flips (D4). Blocking child branches from its parent's branch; others from the anchor.
    if nxt == "build" and profile.worktree and not item.get("git_worktree"):
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
        # Say so when the commit gate is NOT in force. A refusal is deliberate (a hook this project
        # owns, or a `core.hooksPath` that routes hooks elsewhere) — but silent absence would read
        # as enforcement, and the first sign otherwise would be an unlabelled diff at review.
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
    # The review decision IS the merge (B2): leaving review = merging the branch. Do it BEFORE the
    # phase flips (the merge machinery is review-only) and via the SAME body the `/git/merge` route
    # runs — so an owner Approve, a deputy approval, and the raw route all merge identically instead
    # of the Approve stranding the item in `close` unmerged. On CONFLICTS, DON'T advance: hold at
    # review so the owner syncs + resolves and approves again (a merge is never forced through).
    # ...but only for kinds that PRODUCE CODE. A worktree-less kind (research) has no branch, so
    # `review_merge` would refuse with "item has no branch" and the item could never leave review.
    # The rule is "review is where the item's output becomes real", and what "real" means is the
    # kind's: for implementation it is the merge; for research it is the itemization decision fired
    # below. Keyed off the profile, so a future read-only kind inherits this without a second edit.
    review_merge_out = None
    if cur == "review" and profile.worktree:
        from . import git_ops
        # `strict` (§2.2): the deputy's approve is not the merge — it OPENS the PR and hands the
        # merge to the owner. Keyed off the actor, because that is exactly what the mode governs:
        # whether the diff gets a human look before it lands. The owner's own approve always
        # merges (they ARE the second gate — nothing requires the deputy to have gone first), and
        # the mode is read LIVE, so flipping a repo to `strict` catches items already sitting here.
        # The prompt-extraction probe is exempt: it must sail through every gate to be captured,
        # and it never touches the anchor anyway (review_merge's synthetic skip).
        # (The approver's own event — `deputy.approve` — is already logged by its caller; `open_pr`
        # adds the one this act owns. No third row saying the same thing twice.)
        if actor != "owner" and not autopilot_core.is_prompt_extraction(item) \
                and git_ops.repo_review_mode(ctx, spine) == "strict":
            return git_ops.open_pr(ctx, context_id, item_id, dev=dev, dev_store=dev_store)
        review_merge_out = git_ops.review_merge(ctx, context_id, item_id,
                                                dev=dev, dev_store=dev_store, spine=spine)
        # The merge act owns freshness (§2.3). Its two non-merge answers are NOT failures — they
        # are the anchor having moved under a gate the owner may have sat at for days.
        if review_merge_out.get("freshness") == "revet":
            # Clean sync, but the anchor changed files this item also changed: the evidence is
            # stale, so the item earns ONE vet cycle and comes back to review. The approval is
            # spent — the owner (or deputy) decides again on re-verified evidence.
            paths = review_merge_out.get("stale_paths") or []
            from .loop import _cas_phase, start_vet_run
            if _cas_phase(dev_root, item_id, "review", "vet"):
                # …and a spent approval closes the PR with it (git_ops.close_pr): the branch is
                # about to carry the anchor's changes too, so the diff the deputy approved is not
                # the diff that would land. The re-approval opens a new one.
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
    # Resting status after the flip: every advance moves an awaiting_human item to `active` — it just
    # got its answer and proceeds into a phase that runs. `review` used to be carved out here (it was
    # a gate reached with NO background run, and `active` with no run is the idle stall, P6); since
    # §2.2 gave review its entry run, the carve-out would be the bug — it would park the item at a
    # human state while a run works behind it. The `if not started` fallback below still rests it.
    if str(item.get("status")) == "awaiting_human":
        dev.set_work_item_status(dev_root, item_id, "active")
    # Ratification — owner-only. See module docstring: an autopilot advance carries no human
    # moment, so it leaves the assumptions unratified for the close gate to catch.
    dev_store.log_event(context_id, "phase.advance",
                        f"Approved {cur} → {nxt}: {item.get('title') or item_id}",
                        item_id=item_id, actor=actor,
                        meta={"from": cur, "to": nxt, "autopilot": actor != "owner"})
    # Advancing INTO a phase whose whole work is ONE background agent run fires it immediately, for
    # every actor: `plan`, a research item's `investigate`, and REVIEW ENTRY for every kind. These
    # have no human step — the run IS the phase — so an item that just went `active` above must have
    # a run behind it. When the dispatch doesn't take (a run already in flight, or it raised), the
    # item is rested back at `awaiting_human`: `active` with no run is the idle stall this branch
    # exists to prevent, and a status claiming work that isn't happening hides the item forever.
    #
    # `review` is keyed off the PHASE, not the kind (renovation §2.2, 2026-07-29). Before that,
    # implementation's review had no runner at all — its skill had never executed once — and
    # research fired a parallel `research-report`. One skill, one hook, per-kind templates inside.
    auto_skill = {"plan": "plan", "investigate": "investigate"}.get(nxt)
    if str(item.get("kind")) == "research" and nxt == "close":
        # A research item's review APPROVE fires `itemize` — putting `artifacts/review.md`'s
        # `## Proposed work` to the owner and filing the subset they adopt as INBOX items (never
        # auto-pushed). Approving research IS the itemization decision, so without this the approval
        # means nothing and the close gate's `spawns_exist` can never be satisfied. This is the ONE
        # close-entry auto-fire: an implementation close stays owner-triggered (it finalizes
        # knowledge), while a research close writes no knowledge and exists to record this decision.
        auto_skill = "itemize"
        # …and the SAME approve records the standing RULES this item settled, before `itemize` runs.
        # Most rulings settle nothing general and record nothing: an instruction is spent once its
        # work is done. The rare one stated as a rule outlives the item, which is the only reason it
        # is worth keeping — a later sweep reads it and does not re-raise what is already settled.
        #
        # Core writes it, not the itemize agent — the ledger is append-only and never pruned, so
        # every entry has to trace to a question an owner was asked. Idempotent by (item, question):
        # a resume or a second approve after a revision re-enters here and must not duplicate an
        # entry nobody is allowed to remove. Never fatal — a ledger write must not cost the owner
        # their approval.
        #
        # OWNER APPROVALS ONLY. The owner ruled on the question; the sentence generalising that
        # ruling was written by an agent, and it lands in a ledger every later phase reads before
        # asking anything — so an over-broad one suppresses questions that should have reached them.
        # The deputy is told to escalate rather than approve here; this is the half that does not
        # depend on it obeying. A skipped entry costs nothing the next sweep cannot re-raise, while
        # an entry nobody may prune costs forever.
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
        # Review's entry run goes through the SHARED firer, because the loop's vet→review hop
        # never reaches advance_item — one implementation, both doors (runs.fire_review_entry).
        from .runs import fire_review_entry
        auto_started = fire_review_entry(context_id, item_id, spine)
        if not auto_started:
            dev.set_work_item_status(dev_root, item_id, "awaiting_human")
    elif auto_skill:
        try:
            from .runs import _begin_run, _run_background_plan, _run_background_item_skill
            p_model = spine.effective_model(context_id, item_model=item.get("model"))
            p_effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
            phase_dir = dev_root / "work-items" / item_id
            if _begin_run(ctx, context_id, item_id, auto_skill, p_model, phase=nxt) is not None:
                coro = (_run_background_plan(ctx, context_id, item_id, phase_dir, p_model, p_effort)
                        if auto_skill == "plan" else
                        _run_background_item_skill(ctx, context_id, item_id, phase_dir, auto_skill,
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
    # Phase-entry effects hang off the TRANSITION, not the caller (B5). Whether the advance came
    # from the owner (a force / an Approve), the loop, the scheduler, or the deputy, the item's
    # next step must fire — the first-kick invariant (an item never lands at a phase and just
    # sits). Where a step IS gated on autopilot, the gate is `is_autopilot(item)`, NOT
    # `actor != "owner"`: an owner who force-advances an autopilot item must keep autopilot
    # semantics. `plan` is absent here — it already fired its auto-plan run above (whose _end_run
    # then dispatches the plan deputy), for every actor.
    autopiloted = autopilot_core.is_autopilot(item)
    if nxt == "build":
        # Entering build → open the autonomous build⟷vet loop (build-first). The worktree just
        # landed above; scheduled after this transition settles. No-op off a loop.
        #
        # NOT autopilot-gated (owner, 2026-07-29). Approving the plan gate IS the instruction to
        # build: the loop then self-drives build→vet→…→review for every item, which it already did
        # for hand-driven items once something started it. Gating the ENTRY on autopilot left a
        # manual item wedged at `build` with no owner-facing way to start — `/vet` refuses off-phase
        # and the modal's Continue only appears on a paged run — so the one phase that is pure
        # agent work was the one phase nothing fired. Autopilot governs whether the GATES advance
        # without a click, not whether the work inside a phase happens — and build⟷vet is
        # human-free either way, with the token budget as its ceiling.
        try:
            asyncio.get_running_loop().create_task(enter_build_loop(context_id, item_id))
        except RuntimeError:
            pass
    elif nxt == "review" and autopiloted and not auto_started:
        # Review's entry RUN normally covers this: when it started, its `_end_run` dispatches the
        # deputy once the report exists — judging before that would judge a report nobody wrote.
        # This branch is the fallback for when the run did NOT start (already-running item, or the
        # dispatch raised): the item would otherwise sit at review with nobody judging it. Same
        # shape as `plan`, which is absent from this chain for exactly the same reason. Deputy off →
        # review stays the owner's (auto_advance_target excludes it). No-op off a loop.
        try:
            asyncio.get_running_loop().call_soon(maybe_autopilot_advance, context_id, item_id)
        except RuntimeError:
            pass
    elif nxt == "close":
        # review→close merged the branch; the closing run reflects what landed into the anchor
        # docs and reports, and the kernel then CLEARS the item mechanically (services/clearance).
        # Fires for EVERY actor: there is no human step inside close, and a hand-driven item left
        # at `active` with no run is the idle stall the auto-run branch above exists to prevent.
        # No-op off a loop.
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
