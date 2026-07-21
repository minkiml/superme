"""Compaction runtime (workspace-workflow S8/D11) — configurable trigger + checkpoint-first run
order + effectiveness verdict with back-off.

The kernel — not the CLI's hidden threshold — decides when a work-item session compacts:
- **Trigger**: after each bound turn, the turn's real context fill (ctx_pct — the last assistant
  step's single-call usage, already the authoritative gauge) is compared against
  `compaction_trigger_pct` (spine setting, per-kind override). The effective trigger is
  FLOOR-AWARE: never below the static incompressible minimum, and raised above this session's
  own observed floor (its first measured fill ≈ system prompt + tools + orient) — a trigger the
  floor alone would exceed can never fire-loop.
- **Run order**: pre-compaction checkpoint FIRST (the derived S2 banking, deduped against any
  the agent just wrote) → `/compact` sent to the session (the CLI performs it; the ONLY
  cache-break event; full pre-compaction transcript retained on disk) → **effectiveness
  verdict** on the REAL pre/post prompt tokens the compact boundary records.
- **Back-off**: attempts capped per turn; a defer latch blocks re-fire until the next real turn
  reports fresh usage; ≥2 ineffective compactions → this session stops auto-compacting and the
  item is parked `awaiting_human` — "needs a fresh session" lands in the attention engine
  (durable state, restart-proof). A PreCompact hook (wired in ws.py) banks the checkpoint even
  when the CLI compacts on its own mid-turn — the checkpoint-first guarantee holds either way.

State here is per-session and in-memory (a restart forgets strikes — the safe direction: at
worst one more attempt); everything durable (verdict, back-off, checkpoint) is events + item
status.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..app_state import agent as _agent, dev as _dev, dev_store as _dev_store, spine as _spine
from ...core import Result
from ...core.context import Context
from ...core.kind_profiles import get_profile
from ...core.permissions import deny_all

log = logging.getLogger("superme-agent")

# The static incompressible floor: persona + charter + tool schemas + orient block land well
# under this on every current model; a configured trigger at or below it could only thrash.
FLOOR_MIN_PCT = 25
SESSION_FLOOR_MARGIN = 10   # effective trigger ≥ this session's first observed fill + margin
ATTEMPTS_PER_TURN = 1
STRIKES_TO_BACKOFF = 2
_CHECKPOINT_DEDUPE_S = 120  # skip the derived bank if a checkpoint landed this recently


@dataclass
class _SessState:
    floor_pct: int | None = None     # first observed fill — the session's real floor proxy
    floor_model: str | None = None   # the model that fill was measured against (see note_fill)
    attempts: int = 0                # compactions fired since the last real turn
    defer: bool = False              # latch: wait for the next real turn's usage before re-firing
    strikes: int = 0                 # consecutive ineffective compactions
    backed_off: bool = False


_state: dict[str, _SessState] = {}


def _s(session_id: str) -> _SessState:
    return _state.setdefault(session_id, _SessState())


def note_turn_start(session_id: str | None) -> None:
    """A real turn began on this session — clear the per-turn attempt budget and the
    defer-to-real-usage latch (fresh usage is about to arrive)."""
    if session_id and session_id in _state:
        st = _state[session_id]
        st.attempts = 0
        st.defer = False


def note_fill(session_id: str, ctx_pct: int, model: str | None = None) -> None:
    """Record the session's observed floor (its FIRST measured fill — system prompt + tools +
    orient, before conversation grows). Later fills never lower it — EXCEPT when the session's
    model changes: a fill % is only meaningful against its model's context window, so a floor
    measured on a 1M-window model says nothing once the session runs on 200k (and vice versa).
    On a model switch the floor is re-measured from this fill."""
    st = _s(session_id)
    if st.floor_pct is None or (model and st.floor_model and model != st.floor_model):
        st.floor_pct = int(ctx_pct)
    if model:
        st.floor_model = model


def validate_trigger(pct: int) -> str | None:
    """The config-time floor guard: a trigger the incompressible floor alone would exceed is
    refused with the reason (None = acceptable). Makes the knob safe to expose."""
    if pct <= FLOOR_MIN_PCT:
        return (f"trigger {pct}% is at/below the incompressible floor ({FLOOR_MIN_PCT}% — "
                f"system prompt + tools alone); it would fire-loop. Use a higher value.")
    if pct > 95:
        return "trigger above 95% leaves no room to act — the model would truncate first."
    return None


def effective_trigger(session_id: str, kind: str | None) -> int:
    """The trigger this session actually compacts at: configured (per-kind override wins),
    raised above both the static floor and THIS session's observed floor + margin."""
    cfg = _spine.get_compaction_config()
    k = get_profile(kind).kind
    configured = int(cfg["by_kind"].get(k, cfg["trigger_pct"]))
    floor = _s(session_id).floor_pct
    minimum = max(FLOOR_MIN_PCT + 1, (floor + SESSION_FLOOR_MARGIN) if floor is not None else 0)
    return max(configured, minimum)


def _compact_metadata(session_id: str, *, after_iso: str | None = None) -> dict | None:
    """The NEWEST compact boundary's real numbers from the session transcript:
    {preTokens, postTokens, trigger, durationMs}. `after_iso` scopes the read to boundaries
    created AFTER this run started — a re-compact that produced NO new boundary must never be
    judged by a stale (usually highly effective) earlier one. None if nothing qualifies."""
    hits = list(Path.home().glob(f".claude/projects/*/{session_id}.jsonl"))
    if not hits:
        return None
    meta = None
    for line in hits[0].read_text().splitlines():
        if '"compact_boundary"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("subtype") == "compact_boundary" and isinstance(d.get("compactMetadata"), dict):
            if after_iso and str(d.get("timestamp") or "") <= after_iso:
                continue
            meta = d["compactMetadata"]   # last qualifying one wins
    return meta


# Auto mode: a compaction is effective when it reclaimed at least this fraction of the session's
# RECLAIMABLE space (pre − incompressible floor). 0.5 = "shed at least half of what a perfect
# summary could shed" — the v2 calibration (runway-based, from recorded verdicts + per-turn
# ctx_pct history) may replace this constant.
AUTO_RECLAIM_FRACTION = 0.5
_FALLBACK_GAIN_PCT = 30   # flat threshold when auto has no floor measurement to normalize against


def judge_effectiveness(meta: dict | None, min_gain_pct: int | str,
                        *, floor_tokens: int | None = None) -> dict:
    """The pure verdict: real pre/post prompt tokens from the boundary, judged ONCE per
    compaction. No boundary recorded = the compact never happened = ineffective.

    Two modes (min_gain_pct):
      "auto"  — reclaimable-normalized: effective ⇔ reclaimed ≥ AUTO_RECLAIM_FRACTION ×
                (pre − floor_tokens). The floor is what compaction can NEVER remove (system
                prompt + tools + orient, re-sent every turn), so a preload-heavy session isn't
                false-failed and a bloated one isn't false-passed. Falls back to the flat
                threshold when no floor measurement exists (e.g. post-restart).
      int %   — the manual escape hatch: effective ⇔ gain_pct ≥ min_gain_pct.

    The returned dict carries the full measurement (mode, threshold, floor, reclaimable,
    reclaimed ratio) — it lands in the permanent verdict event as the calibration record."""
    if not meta or not meta.get("preTokens"):
        return {"pre_tokens": None, "post_tokens": None, "gain_pct": 0.0, "effective": False,
                "mode": "none"}
    pre, post = int(meta["preTokens"]), int(meta.get("postTokens") or 0)
    gain = (pre - post) / pre * 100 if pre else 0.0
    out = {"pre_tokens": pre, "post_tokens": post, "gain_pct": round(gain, 1)}
    auto = str(min_gain_pct).strip().lower() == "auto"
    if auto and floor_tokens is not None and 0 <= floor_tokens < pre:
        reclaimable = pre - floor_tokens
        ratio = (pre - post) / reclaimable if reclaimable else 0.0
        out.update(mode="auto", floor_tokens=int(floor_tokens), reclaimable=reclaimable,
                   reclaimed_ratio=round(ratio, 2),
                   effective=ratio >= AUTO_RECLAIM_FRACTION)
    else:
        threshold = _FALLBACK_GAIN_PCT if auto else int(min_gain_pct)
        out.update(mode="auto-fallback" if auto else "manual", threshold=threshold,
                   effective=gain >= threshold)
    return out


def bank_precompaction_checkpoint(ctx: Context, item_id: str) -> bool:
    """The checkpoint-FIRST step (D11 run order #1): bank the derived S2 checkpoint unless one
    landed in the last couple of minutes (the agent's own hot bank wins — never duplicate)."""
    from .runs import bank_auto_checkpoint
    return bank_auto_checkpoint(ctx, item_id, since=time.time() - _CHECKPOINT_DEDUPE_S)


async def run_compaction(ctx: Context, context_id: str, item_id: str, session_id: str,
                         *, model: str | None, pre_pct: int | None) -> dict:
    """Execute one full compaction sequence on a bound session. Returns the verdict record.
    The caller has already decided (maybe_compact) and holds no run — this opens its own."""
    from .runs import _begin_run, _end_run, capture_prompt
    st = _s(session_id)
    # A compaction is a MAINTENANCE run — it must not move the item's workflow state. Remember
    # the resting status now; back-off overrides it to awaiting_human (the page).
    rest_status = "active"
    cur: dict = {}
    if ctx.internal_root:
        cur = _dev.read_work_item(ctx.internal_root / "dev", item_id) or {}
        rest_status = str(cur.get("status") or "active")
    # Phase stamp: compaction spend attributes to the phase it happened in, like every item run.
    run_id = _begin_run(ctx, context_id, item_id, "compact", model, phase=cur.get("phase"))
    if not run_id:
        return {"skipped": "run in progress"}
    capture_prompt(context_id, "/compact (kernel-driven compaction sequence)",
                   run_id=run_id, item_id=item_id)
    st.attempts += 1
    st.defer = True   # no re-fire until the next real turn reports fresh usage
    started_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    verdict: dict = {}
    run_usage: dict | None = None
    try:
        # 1. Checkpoint FIRST — banked and logged BEFORE any compaction event exists.
        banked = bank_precompaction_checkpoint(ctx, item_id)
        _dev_store.log_event(context_id, "compaction.checkpoint",
                             ("Pre-compaction checkpoint banked" if banked
                              else "Pre-compaction checkpoint skipped (fresh one exists)"),
                             item_id=item_id, actor="daemon",
                             meta={"session_id": session_id, "banked": banked})
        # 2. The compaction itself: /compact into the session (CLI performs it; the transcript
        #    keeps the full pre-compaction history + gains the boundary record).
        real_usage: dict | None = None
        window: int | None = None
        async for ev in _agent.run_turn(ctx, "/compact", resume=session_id, model=model,
                                        approve=deny_all):
            if isinstance(ev, Result):
                real_usage = ev.usage
                window = ev.context_window   # the model's real window — converts floor % → tokens
                break
        # 3. Effectiveness verdict on the boundary's REAL pre/post prompt tokens. The CLI
        #    flushes the boundary record to the transcript slightly AFTER the result streams —
        #    poll briefly rather than judging a not-yet-written boundary as a false strike.
        meta = None
        for _ in range(10):
            meta = _compact_metadata(session_id, after_iso=started_iso)
            if meta:
                break
            await asyncio.sleep(2)
        cfg = _spine.get_compaction_config()
        # The session's incompressible floor in TOKENS (auto mode's normalizer): its measured
        # floor % × the real window. None when either is unknown (fresh restart / no window
        # reported) — the verdict then falls back to the flat threshold.
        floor_tokens = (round(st.floor_pct / 100 * window)
                        if st.floor_pct is not None and window else None)
        verdict = judge_effectiveness(meta, cfg["min_gain_pct"], floor_tokens=floor_tokens)
        # Token attribution (token-accuracy): the CLI reports ZERO API usage for a /compact turn
        # (verified empirically — the summarization request never surfaces through the session),
        # so unless a future CLI starts reporting real usage, attribute a documented ESTIMATE
        # from the boundary: the summarizer read the whole transcript (~preTokens, uncached) and
        # wrote the summary (~postTokens). Lands on the run row's typed columns + raw_usage
        # (flagged "estimated"), categorized under Other via token_taxonomy ("compact").
        run_usage = real_usage if (real_usage and sum(
            real_usage.get(k, 0) for k in ("input_tokens", "output_tokens",
                                           "cache_creation_input_tokens"))) else None
        if run_usage is None and meta and meta.get("preTokens"):
            run_usage = {"input_tokens": int(meta["preTokens"]),
                         "output_tokens": int(meta.get("postTokens") or 0),
                         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                         "estimated": "compact-boundary"}
        st.strikes = 0 if verdict["effective"] else st.strikes + 1
        # The verdict event's meta is the durable CALIBRATION record (v2 runway tuning reads
        # these + the per-turn ctx_pct history on run rows): full measurement — mode, floor,
        # window, reclaimable, ratio — not just the outcome. `trigger_pct` is None for a manual
        # "Compact now" (no trigger fired — a 0 here would poison the calibration data).
        verdict.update(strikes=st.strikes, trigger_pct=pre_pct)
        pre, post = verdict["pre_tokens"], verdict["post_tokens"]
        _dev_store.log_event(context_id, "compaction.verdict",
                             (f"Compaction {'effective' if verdict['effective'] else 'INEFFECTIVE'}: "
                              f"{pre} → {post} tokens ({round(verdict['gain_pct'])}% shrink"
                              + (f", {round(verdict['reclaimed_ratio'] * 100)}% of reclaimable"
                                 if verdict.get("reclaimed_ratio") is not None else "") + ")"
                              if pre else "Compaction produced no boundary — counted ineffective"),
                             item_id=item_id, actor="daemon",
                             meta={**verdict, "session_id": session_id,
                                   "floor_pct": st.floor_pct, "window": window})
        # 4. Back-off: ≥2 strikes → stop compacting this session + page the owner (durable:
        #    awaiting_human IS the attention engine's needs-you signal).
        if st.strikes >= STRIKES_TO_BACKOFF and not st.backed_off:
            st.backed_off = True
            rest_status = "awaiting_human"   # the page — _end_run below rests the item there
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
        _end_run(ctx, context_id, item_id, None, rest_status, run_usage, session_id=session_id)
    return verdict


def maybe_compact(ctx: Context, context_id: str, item_id: str, session_id: str,
                  *, ctx_pct: int | None, kind: str | None, model: str | None) -> bool:
    """The post-turn trigger evaluation (called fire-and-forget from the ws turn end). True =
    a compaction task was scheduled."""
    if ctx_pct is None:
        return False
    note_fill(session_id, ctx_pct, model)
    st = _s(session_id)
    if st.backed_off or st.defer or st.attempts >= ATTEMPTS_PER_TURN:
        return False
    if ctx_pct < effective_trigger(session_id, kind):
        return False
    log.info("compaction trigger: %s/%s at %d%% (session %s)",
             context_id, item_id, ctx_pct, session_id[:8])
    asyncio.create_task(run_compaction(ctx, context_id, item_id, session_id,
                                       model=model, pre_pct=ctx_pct))
    return True
