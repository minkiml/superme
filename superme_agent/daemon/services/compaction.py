"""Compaction runtime: a configurable trigger, a checkpoint-first run order, and an effectiveness
verdict with back-off.

The check runs at run START, before the lock is taken, which makes "never mid-task" true by
construction.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..app_state import agent as _agent, dev as _dev, dev_store as _dev_store, spine as _spine
from ...core import Result, kernel_speech, scoped_writes_approve
from ...core import artifacts
from ...core.vocab.context import Context
from ...core.vocab.kind_profiles import get_profile
from ...core.permissions import deny_all
from .turns import ResilientTurn

log = logging.getLogger("superme-agent")

# The incompressible floor is about 10.6% of a 200K window, and 25% is that plus room for one
# exchange.
FLOOR_MIN_PCT = 25
# A trigger just above the floor re-fires next turn, because one exchange puts the session back
# over it.
TRIGGER_MIN_PCT = 40
STRIKES_TO_BACKOFF = 2
# A compaction has to buy at least this many real turns of runway, or it was a treadmill step.
MIN_RUNWAY_TURNS = 2
_CHECKPOINT_DEDUPE_S = 120  # skip the derived bank if a checkpoint landed this recently


@dataclass
class _SessState:
    defer: bool = False              # latch: wait for the next real turn's usage before re-firing
    strikes: int = 0                 # consecutive compactions that bought nothing
    backed_off: bool = False
    # `None` means it has not compacted yet. This is what makes thrash visible — shrink alone
    # cannot show it.
    turns_since_compact: int | None = None


_state: dict[str, _SessState] = {}


def _s(session_id: str) -> _SessState:
    return _state.setdefault(session_id, _SessState())


def note_turn_start(session_id: str | None) -> None:
    """A real turn ran, so release the defer latch — without this a seam re-entering after an
    ineffective compaction fires again. It also counts the turn, which is the runway measure."""
    if session_id and session_id in _state:
        st = _state[session_id]
        st.defer = False
        if st.turns_since_compact is not None:
            st.turns_since_compact += 1


def validate_trigger(pct: int) -> str | None:
    """The config-time floor guard: a trigger the incompressible floor alone would exceed is refused
    with the reason."""
    if pct <= FLOOR_MIN_PCT:
        return (f"trigger {pct}% is at/below the incompressible floor ({FLOOR_MIN_PCT}% — "
                f"system prompt + tools alone); it would fire-loop. Use a higher value.")
    if pct < TRIGGER_MIN_PCT:
        return (f"trigger {pct}% clears the floor but leaves no working room: compaction lands a "
                f"session near {FLOOR_MIN_PCT}% and one exchange (~14 points) puts it back over "
                f"{pct}%, so it would re-fire almost every turn — effectively, and pointlessly. "
                f"Use {TRIGGER_MIN_PCT}% or more.")
    if pct > 95:
        return "trigger above 95% leaves no room to act — the model would truncate first."
    return None


def effective_trigger(kind: str | None) -> int:
    """The trigger this session compacts at: the configured value, per-kind override winning. That is
    the whole rule — `validate_trigger` guards the floor at config time."""
    cfg = _spine.get_compaction_config()
    k = get_profile(kind).kind
    return int(cfg["by_kind"].get(k, cfg["trigger_pct"]))


def _summary_text(entry: dict) -> str:
    """The text of an `isCompactSummary` transcript entry, whose content is either a plain string or a
    block list."""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("text"))
    return ""


def _compact_metadata(session_id: str, *, after_iso: str | None = None
                      ) -> tuple[dict | None, str]:
    """The NEWEST compaction's evidence from the session transcript, as (meta, summary_text).

    The numbers say how much was shed; the text says WHAT survived. `after_iso` keeps a re-compact
    from being judged by a stale boundary."""
    hits = list(Path.home().glob(f".claude/projects/*/{session_id}.jsonl"))
    if not hits:
        return None, ""
    meta, summary = None, ""
    for line in hits[0].read_text().splitlines():
        if '"compact_boundary"' not in line and '"isCompactSummary"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if after_iso and str(d.get("timestamp") or "") <= after_iso:
            continue
        if d.get("subtype") == "compact_boundary" and isinstance(d.get("compactMetadata"), dict):
            meta = d["compactMetadata"]   # last qualifying one wins
        elif d.get("isCompactSummary"):
            summary = _summary_text(d) or summary
    return meta, summary


# Effective means it shed at least half of what a perfect summary could shed.
AUTO_RECLAIM_FRACTION = 0.5
_FALLBACK_GAIN_PCT = 30   # flat threshold when auto has no floor measurement to normalize against


def judge_effectiveness(meta: dict | None, min_gain_pct: int | str,
                        *, floor_tokens: int | None = None) -> dict:
    """The pure verdict from the boundary's real pre/post prompt tokens.

    `min_gain_pct` is "auto" (normalized against the measured floor) or a flat int percent.
    `preTokens` includes the floor and `postTokens` does not, so post is restated onto the pre
    basis."""
    if not meta or not meta.get("preTokens"):
        return {"pre_tokens": None, "post_tokens": None, "gain_pct": 0.0, "effective": False,
                "mode": "none"}
    pre, post = int(meta["preTokens"]), int(meta.get("postTokens") or 0)
    gain = (pre - post) / pre * 100 if pre else 0.0
    out = {"pre_tokens": pre, "post_tokens": post, "gain_pct": round(gain, 1)}
    auto = str(min_gain_pct).strip().lower() == "auto"
    if auto and floor_tokens is not None and 0 <= floor_tokens < pre:
        reclaimable = pre - floor_tokens
        # Both sides floor-inclusive: the next turn carries the summary PLUS the floor.
        post_real = post + int(floor_tokens)
        ratio = max(0.0, (pre - post_real) / reclaimable) if reclaimable else 0.0
        out.update(mode="auto", floor_tokens=int(floor_tokens), reclaimable=reclaimable,
                   post_tokens_with_floor=post_real, reclaimed_ratio=round(ratio, 2),
                   effective=ratio >= AUTO_RECLAIM_FRACTION)
    else:
        threshold = _FALLBACK_GAIN_PCT if auto else int(min_gain_pct)
        out.update(mode="auto-fallback" if auto else "manual", threshold=threshold,
                   effective=gain >= threshold)
    return out


def bank_precompaction_checkpoint(ctx: Context, item_id: str) -> bool:
    """Bank the derived checkpoint unless one landed in the last couple of minutes — the agent's own
    hot bank wins."""
    from .runs import bank_auto_checkpoint
    return bank_auto_checkpoint(ctx, item_id, since=time.time() - _CHECKPOINT_DEDUPE_S)


def session_memory_root(ctx: Context) -> Path | None:
    """The MODE root a general session's memory hangs off. Mode-scoped, because the knowledge tree
    already is."""
    return (ctx.internal_root / ctx.mode) if ctx.internal_root else None


async def run_handoff_turn(ctx: Context, context_id: str, item_id: str | None, session_id: str,
                           *, model: str | None) -> bool:
    """Ask the thread to bank its own checkpoint, as a TURN, before `/compact`.

    The reply becomes the LAST thing in the transcript, which the summarizer weights most. A
    work-item thread calls `write_checkpoint`; a general session writes `session-memory/<sid>.md`."""
    from .runs import dev_mcp, capture_prompt
    if item_id:
        prompt = kernel_speech.checkpoint_trigger(item_id)
        write_dir = (ctx.internal_root / "dev" / "work-items" / item_id
                     if ctx.internal_root else None)
        mcp = dev_mcp(ctx, ctx.cwd, item_id, scope="handoff")
    else:
        root = session_memory_root(ctx)
        if not root:
            return False
        prompt = kernel_speech.session_checkpoint_trigger(
            str(artifacts.session_memory_path(root, session_id)))
        write_dir = root / "session-memory"
        write_dir.mkdir(parents=True, exist_ok=True)   # the scope must exist to be writable
        mcp = None   # no item tools for a general session — the skill writes the file directly
    capture_prompt(context_id, prompt, item_id=item_id)
    turn = ResilientTurn("handoff", item_id=item_id)
    async for ev in turn.stream(
        _agent, ctx, prompt, resume=session_id, model=model,
        approve=scoped_writes_approve(write_dir, deny_all) if write_dir else deny_all,
        extra_mcp_servers=mcp,
    ):
        if isinstance(ev, Result):
            return True
    return False


async def run_compaction(ctx: Context, context_id: str, item_id: str | None, session_id: str,
                         *, model: str | None, pre_pct: int | None, manual: bool = False) -> dict:
    """Execute one full compaction sequence on a session and return the verdict record.

    The caller holds NO run — this opens its own, which is why every caller must be at a run
    boundary. `item_id=None` is a general session."""
    from .runs import begin_run, end_run, capture_prompt
    st = _s(session_id)
    # A compaction is a MAINTENANCE run and must not move the item's workflow state.
    rest_status = "active"
    cur: dict = {}
    if item_id and ctx.internal_root:
        cur = _dev.read_work_item(ctx.internal_root / "dev", item_id) or {}
        rest_status = str(cur.get("status") or "active")
    # Phase stamp: compaction spend attributes to the phase it happened in, like every item run.
    run_id = (begin_run(ctx, context_id, item_id, "compact", model, phase=cur.get("phase"))
              if item_id else
              _spine.start_run(context_id, mode=ctx.mode, feature="compact",
                               session_id=session_id, model=model))
    if not run_id:
        return {"skipped": "run in progress"}
    capture_prompt(context_id, "/compact (kernel-driven compaction sequence)",
                   run_id=run_id, item_id=item_id)
    st.defer = True   # no re-fire until the next real turn reports fresh usage
    started_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    verdict: dict = {}
    run_usage: dict | None = None
    post_pct: int | None = None   # bound BEFORE the try: the finally reads it on every path
    try:
        # Checkpoint first, before any compaction event exists. The thread writes its own; the
        # derived bank is the fallback.
        by_agent = await run_handoff_turn(ctx, context_id, item_id, session_id, model=model)
        # The derived fallback is assembled from item artifacts, so a general session has none,
        # and banking nothing is honest.
        banked = by_agent or (bool(item_id) and bank_precompaction_checkpoint(ctx, item_id))
        _dev_store.log_event(context_id, "compaction.checkpoint",
                             ("Pre-compaction checkpoint written by the session" if by_agent
                              else "Pre-compaction checkpoint banked (derived — the handoff turn "
                                   "did not produce one)" if banked
                              else "Pre-compaction checkpoint skipped (fresh one exists)"),
                             item_id=item_id, actor="daemon",
                             meta={"session_id": session_id, "banked": banked,
                                   "by_agent": by_agent})
        # The CLI performs the compaction; the transcript keeps its full pre-compaction history
        # and gains the boundary record.
        real_usage: dict | None = None
        window: int | None = None
        compact_turn = ResilientTurn("compact", item_id=item_id)
        async for ev in compact_turn.stream(_agent, ctx, "/compact", resume=session_id,
                                            model=model, approve=deny_all):
            if isinstance(ev, Result):
                real_usage = ev.usage
                window = ev.context_window   # the model's real window — converts floor % → tokens
                # The fill the NEXT turn starts from — the number that says whether this bought
                # any runway.
                post_pct = ev.ctx_pct
                break
        # The CLI flushes the boundary slightly after the result streams, so poll rather than
        # judge a not-yet-written one.
        meta, summary = None, ""
        for _ in range(10):
            meta, summary = _compact_metadata(session_id, after_iso=started_iso)
            if meta:
                break
            await asyncio.sleep(2)
        cfg = _spine.get_compaction_config()
        # Measured, not inferred, and cached per context and model. None if the probe failed; the
        # verdict then falls back.
        measured = await _agent.measure_context_floor(ctx, model)
        floor_tokens = measured[0] if measured else None
        verdict = judge_effectiveness(meta, cfg["min_gain_pct"], floor_tokens=floor_tokens)
        # The CLI reports no usage for a `/compact` turn; 0 is honest here. The shrink belongs to
        # `compaction.verdict`.
        run_usage = real_usage if (real_usage and sum(
            real_usage.get(k, 0) for k in ("input_tokens", "output_tokens",
                                           "cache_creation_input_tokens"))) else None
        # A manual compaction cannot loop, so it accrues no strike; and shrink alone cannot tell a
        # treadmill from runway.
        bought_runway = (st.turns_since_compact is None            # first compaction on this session
                         or st.turns_since_compact >= MIN_RUNWAY_TURNS)
        verdict["bought_runway"] = bought_runway
        verdict["runway_turns"] = st.turns_since_compact
        # `or manual`: the owner asked, and a hand-run compaction cannot loop, so effectiveness
        # alone clears the board.
        if verdict["effective"] and (bought_runway or manual):
            st.strikes = 0
        elif not manual:
            st.strikes += 1
        st.turns_since_compact = 0   # this compaction is now the one runway is measured from
        # The durable calibration record: full measurement, not just outcome. `post_pct` is
        # derived, since a compact turn reports no fill.
        if post_pct is None and window and verdict.get("post_tokens"):
            post_pct = round(verdict["post_tokens"] / window * 100)
        verdict.update(strikes=st.strikes, trigger_pct=pre_pct, post_pct=post_pct)
        pre, post = verdict["pre_tokens"], verdict["post_tokens"]
        _dev_store.log_event(context_id, "compaction.verdict",
                             (f"Compaction {'effective' if verdict['effective'] else 'INEFFECTIVE'}: "
                              f"{pre} → {post} tokens ({round(verdict['gain_pct'])}% shrink"
                              + (f", {round(verdict['reclaimed_ratio'] * 100)}% of reclaimable"
                                 if verdict.get("reclaimed_ratio") is not None else "")
                              + (f" → {post_pct}% fill" if post_pct is not None else "") + ")"
                              # The treadmill, named where the owner reads it: a shrink figure
                              # alone let seven of these look successful.
                              + ("" if verdict.get("bought_runway", True) else
                                 f" — but bought only {verdict.get('runway_turns')} turn(s) of "
                                 f"runway; the trigger is too low for this session")
                              if pre else "Compaction produced no boundary — counted ineffective"),
                             item_id=item_id, actor="daemon",
                             meta={**verdict, "session_id": session_id, "window": window,
                                   # WHAT survived, beside how much. Agent-facing — no owner
                                   # surface renders this.
                                   "summary": summary})
        # Back-off: two strikes stop compacting this session and page the owner.
        if st.strikes >= STRIKES_TO_BACKOFF and not st.backed_off and not manual:
            st.backed_off = True
            rest_status = "awaiting_human"   # the page — end_run below rests the item there
            _dev_store.log_event(context_id, "compaction.backoff",
                                 "Compaction ineffective twice — this session needs a fresh "
                                 "start (cold-start from its latest checkpoint)",
                                 item_id=item_id, actor="daemon",
                                 meta={"session_id": session_id, "strikes": st.strikes})
            verdict["backed_off"] = True
    except Exception:
        log.exception("compaction run failed for %s/%s", context_id, item_id)
        verdict = {"error": True}
        run_usage = None
    finally:
        if item_id:
            end_run(ctx, context_id, item_id, None, rest_status, run_usage,
                     ctx_pct=post_pct, session_id=session_id)
        else:
            _spine.finish_run(run_id, usage=run_usage, ctx_pct=post_pct,
                              session_id=session_id, model=model)
    return verdict


def due(session_id: str | None, kind: str | None, *, force: bool = False) -> int | None:
    """Is this session over its trigger and clear to compact? Returns the fill that decided it, or
    None."""
    if not session_id:
        return None
    st = _s(session_id)
    if st.backed_off or (st.defer and not force):
        return None
    pct = _spine.session_ctx_pct(session_id)
    if force:
        return pct if pct is not None else 0
    if pct is None or pct < effective_trigger(kind):
        return None
    return pct


async def compact_before_run(ctx: Context, context_id: str, item_id: str | None,
                             session_id: str | None, *, kind: str | None,
                             model: str | None, force: bool = False) -> dict | None:
    """The run-START gate: if this session is over its trigger, compact now and return the verdict.

    AWAITED, not fire-and-forget: `run_compaction` takes the item's run-lock, and a prompt sent
    mid-compaction is what "never mid-task" forbids."""
    pre = due(session_id, kind, force=force)
    if pre is None:
        return None
    log.info("compaction trigger%s: %s/%s at %d%% (session %s)",
             " (manual)" if force else "", context_id, item_id or "(session)", pre,
             (session_id or "")[:8])
    return await run_compaction(ctx, context_id, item_id, session_id, model=model,
                                pre_pct=None if force else pre, manual=force)
