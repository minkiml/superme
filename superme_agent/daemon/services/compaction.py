"""Compaction runtime (workspace-workflow S8/D11) — configurable trigger + checkpoint-first run
order + effectiveness verdict with back-off.

The kernel — not the CLI's hidden threshold — decides when a work-item session compacts:
- **Trigger**: ONE check, at run START, before the run-lock is taken. The session's current fill
  (`spine.session_ctx_pct` — the last finished run's authoritative reading on that session) is
  compared against `compaction_trigger_pct` (spine setting, per-kind override). Nothing else.
  Checking at run start is what makes "never mid-task" true by CONSTRUCTION: no run is in
  flight, so the lock is free and there is no work to strand. The two seams are the interactive
  bound turn (ws.py) and the autopilot gate seam (gates.maybe_autopilot_advance) — between them
  every accumulating session is covered, chat and background alike.
- **Run order**: pre-compaction checkpoint FIRST (the derived S2 banking, deduped against any
  the agent just wrote) → `/compact` sent to the session (the CLI performs it; the ONLY
  cache-break event; full pre-compaction transcript retained on disk) → **effectiveness
  verdict** on the REAL pre/post prompt tokens the compact boundary records, plus the summary
  text the CLI injected (WHAT survived, beside how much).
- **Back-off**: a defer latch blocks re-fire until the next REAL turn reports fresh usage (so an
  ineffective compaction can never loop with the seam that just re-entered); ≥2 ineffective
  compactions → this session stops auto-compacting and the item is parked `awaiting_human` —
  "needs a fresh session" lands in the attention engine (durable state, restart-proof). A
  PreCompact hook (wired in ws.py) banks the checkpoint even when the CLI compacts on its own
  mid-turn — the checkpoint-first guarantee holds either way.

**Boundaries are permission, not instruction** (owner, 2026-07-28): a run boundary says it is
SAFE to compact here; the threshold says whether it is WORTH it. Both must hold.

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
from ...core import Result, kernel_speech, scoped_writes_approve
from ...core import artifacts
from ...core.context import Context
from ...core.kind_profiles import get_profile
from ...core.permissions import deny_all
from .turns import ResilientTurn

log = logging.getLogger("superme-agent")

# The incompressible floor as a share of the window, with headroom. MEASURED 2026-07-28
# (`scripts/probe_context_floor.py`, `get_context_usage()` before any turn): 21.3K of 200,000 =
# 10.6%, and the window is 200,000 on every tier we run — so this is a stable percentage, not a
# per-model one. 25% is that floor plus room for one exchange; a trigger at or below it could
# only thrash, so `validate_trigger` refuses one at config time.
FLOOR_MIN_PCT = 25
# …and the minimum a TRIGGER may sit at, which is a different question the guard used to conflate.
# Clearing the floor is not enough: a trigger just above it re-fires on the next turn, because
# compaction lands the session near the floor and one exchange puts it back over.
#
# OBSERVED 2026-07-30 (item `dc00c47bc74f`, trigger 26%): seven compactions in three days, EVERY ONE
# 80–93% effective, each reclaiming to ~5% and re-crossed 26% within a turn. Nothing flagged it,
# because the strike rule only counts INEFFECTIVE compactions.
#
# Derived from the same measurement `FLOOR_MIN_PCT` uses, not picked: the floor is 10.6%
# (21.3K/200K) and 25% is that plus room for ONE exchange — so one exchange is ~14 points. A trigger
# needs the floor plus one exchange to do work in, and one more before it fires again: 10.6 + 2×14 ≈
# 40. Below that, compaction is a treadmill however well it shrinks.
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
    # Real turns since this session last compacted. `None` = it has not compacted yet. This is what
    # makes THRASH visible: a compaction that shrinks 90% but is re-crossed by the very next turn
    # bought no runway, and shrink alone cannot tell you that (see `_bought_runway`).
    turns_since_compact: int | None = None


_state: dict[str, _SessState] = {}


def _s(session_id: str) -> _SessState:
    return _state.setdefault(session_id, _SessState())


def note_turn_start(session_id: str | None) -> None:
    """A real (non-compact) turn ran on this session, so the next reading will be fresh — release
    the defer latch. This is the ONLY thing that releases it: without it, a seam that re-enters
    right after an ineffective compaction would read the same over-threshold fill and fire again.

    It also COUNTS the turn, which is the runway measure: how much work a compaction actually
    bought before the trigger was crossed again."""
    if session_id and session_id in _state:
        st = _state[session_id]
        st.defer = False
        if st.turns_since_compact is not None:
            st.turns_since_compact += 1


def validate_trigger(pct: int) -> str | None:
    """The config-time floor guard: a trigger the incompressible floor alone would exceed is
    refused with the reason (None = acceptable). Makes the knob safe to expose."""
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
    """The trigger this session compacts at: the configured value, per-kind override winning.
    That is the whole rule. It used to be raised above the session's "observed floor + margin",
    where the floor was the FIRST FILL SEEN — which is not a floor (a first turn already carries
    a prompt) and which a daemon restart re-measured mid-conversation, silently lifting the
    trigger to wherever the session happened to be. Observed 2026-07-28: floor recorded as 31%,
    trigger lifted 55 → 41 → never fired. `validate_trigger` guards the floor at config time,
    which is the right place for it; the runtime just honours the number."""
    cfg = _spine.get_compaction_config()
    k = get_profile(kind).kind
    return int(cfg["by_kind"].get(k, cfg["trigger_pct"]))


def _summary_text(entry: dict) -> str:
    """The text of an `isCompactSummary` transcript entry — the CLI writes it as a user message
    whose content is either a plain string or a block list."""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("text"))
    return ""


def _compact_metadata(session_id: str, *, after_iso: str | None = None
                      ) -> tuple[dict | None, str]:
    """The NEWEST compaction's evidence from the session transcript, as (meta, summary_text):

    - `meta` — the `compact_boundary` numbers {preTokens, postTokens, trigger, durationMs}.
    - `summary_text` — the `isCompactSummary` message the CLI injected as the compacted
      session's new opening. This is WHAT SURVIVED; the numbers only say how much did. Judging
      compaction on tokens alone can't tell a good summary from a lossy one, so the text is
      captured onto the verdict event for us and the diagnosis agent to read.

    `after_iso` scopes the read to entries created AFTER this run started — a re-compact that
    produced NO new boundary must never be judged by a stale (usually highly effective) earlier
    one. ("", None) when nothing qualifies.
    """
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

    **The two boundary numbers are not on the same basis** (measured 2026-07-28, two real
    compactions): `preTokens` INCLUDES the floor — 68,100 against a session reading 33% of a
    200K window is the whole prompt — while `postTokens` does NOT, landing BELOW the floor
    (12,025 vs a 21,297 floor, an impossibility for a real prompt) because it counts the summary
    the CLI wrote, before the next turn re-sends system prompt and tools. Normalizing without
    correcting for that inflates the ratio by floor/(pre − floor) and can push it ABOVE 1.0 —
    which is exactly what the first auto-mode verdict recorded (1.09). So the post side is
    restated onto the pre side's basis (`post + floor`) before the ratio is taken.

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
        # Both sides floor-inclusive: what the next turn will really carry is the summary PLUS
        # the floor, so that is what "how much did this shed" must be measured against.
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
    """The checkpoint-FIRST step (D11 run order #1): bank the derived S2 checkpoint unless one
    landed in the last couple of minutes (the agent's own hot bank wins — never duplicate)."""
    from .runs import bank_auto_checkpoint
    return bank_auto_checkpoint(ctx, item_id, since=time.time() - _CHECKPOINT_DEDUPE_S)


def session_memory_root(ctx: Context) -> Path | None:
    """The MODE root a general session's memory hangs off — `<internal_root>/dev` or `/core`.
    Mode-scoped because the knowledge tree already is (owner, 2026-07-28)."""
    return (ctx.internal_root / ctx.mode) if ctx.internal_root else None


async def run_handoff_turn(ctx: Context, context_id: str, item_id: str | None, session_id: str,
                           *, model: str | None) -> bool:
    """Ask the thread to bank its own checkpoint, as a TURN, before `/compact` runs.

    Two things happen at once, and both matter:
    - the `superme-dev:checkpoint` skill writes the thread's continuity record — OUR copy, on
      disk, which survives however the CLI's summary turns out;
    - the turn's reply becomes the LAST thing in the transcript, which is what the summarizer
      weights most heavily. That is the only lever we have on `/compact`'s output: we cannot pass
      it instructions (the SDK exposes compaction as read-only telemetry — §3), so we shape its
      INPUT instead.

    One content contract, two write targets (§13.4): a work-item thread calls the
    `write_checkpoint` tool (`item_id` given); a general session has no item folder and no such
    tool, so it WRITES `session-memory/<session-id>.md` and the trigger names the exact path. In
    both cases the turn's write scope is exactly that one directory and nothing else.

    A model call, so it is spent only when a compaction is actually about to happen. Returns True
    if the turn completed; False sends the caller to the derived fallback (Hermes's pattern: LLM
    summary primary, deterministic handoff when it fails, never a lost boundary)."""
    from .runs import _dev_mcp, capture_prompt
    if item_id:
        prompt = kernel_speech.checkpoint_trigger(item_id)
        write_dir = (ctx.internal_root / "dev" / "work-items" / item_id
                     if ctx.internal_root else None)
        mcp = _dev_mcp(ctx, ctx.cwd, item_id)
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
    """Execute one full compaction sequence on a session. Returns the verdict record.
    The caller has already decided (`due`) and holds NO run — this opens its own, which is why
    every caller must be at a run boundary rather than inside one.

    `item_id=None` is a GENERAL session (§13.4 / T5): same sequence, but the run row is a plain
    session run (no item, and therefore no per-item run-lock — `due`'s defer latch, set below
    before anything else, is what stops a second seam re-entering) and the handoff writes
    `session-memory/` instead of a checkpoint."""
    from .runs import _begin_run, _end_run, capture_prompt
    st = _s(session_id)
    # A compaction is a MAINTENANCE run — it must not move the item's workflow state. Remember
    # the resting status now; back-off overrides it to awaiting_human (the page).
    rest_status = "active"
    cur: dict = {}
    if item_id and ctx.internal_root:
        cur = _dev.read_work_item(ctx.internal_root / "dev", item_id) or {}
        rest_status = str(cur.get("status") or "active")
    # Phase stamp: compaction spend attributes to the phase it happened in, like every item run.
    run_id = (_begin_run(ctx, context_id, item_id, "compact", model, phase=cur.get("phase"))
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
        # 1. Checkpoint FIRST — banked and logged BEFORE any compaction event exists. The thread
        #    writes its own via the `checkpoint` skill (it is the only party that knows what was
        #    said); the derived bank is the fallback when that turn fails, so a boundary is never
        #    crossed with nothing banked.
        by_agent = await run_handoff_turn(ctx, context_id, item_id, session_id, model=model)
        # The derived fallback exists only for work-items — it is assembled from the item's
        # artifacts, and a general session has none. There, a failed handoff turn means nothing
        # is banked, and that is the honest outcome rather than a stub with no content in it.
        banked = by_agent or (bool(item_id) and bank_precompaction_checkpoint(ctx, item_id))
        _dev_store.log_event(context_id, "compaction.checkpoint",
                             ("Pre-compaction checkpoint written by the session" if by_agent
                              else "Pre-compaction checkpoint banked (derived — the handoff turn "
                                   "did not produce one)" if banked
                              else "Pre-compaction checkpoint skipped (fresh one exists)"),
                             item_id=item_id, actor="daemon",
                             meta={"session_id": session_id, "banked": banked,
                                   "by_agent": by_agent})
        # 2. The compaction itself: /compact into the session (CLI performs it; the transcript
        #    keeps the full pre-compaction history + gains the boundary record).
        real_usage: dict | None = None
        window: int | None = None
        compact_turn = ResilientTurn("compact", item_id=item_id)
        async for ev in compact_turn.stream(_agent, ctx, "/compact", resume=session_id,
                                            model=model, approve=deny_all):
            if isinstance(ev, Result):
                real_usage = ev.usage
                window = ev.context_window   # the model's real window — converts floor % → tokens
                # The fill the NEXT turn will start from — the number that says whether this bought
                # any runway. Without it the compact run row reads ctx None and the only evidence of
                # the gain is the verdict event's token pair.
                post_pct = ev.ctx_pct
                break
        # 3. Effectiveness verdict on the boundary's REAL pre/post prompt tokens. The CLI
        #    flushes the boundary record to the transcript slightly AFTER the result streams —
        #    poll briefly rather than judging a not-yet-written boundary as a false strike.
        meta, summary = None, ""
        for _ in range(10):
            meta, summary = _compact_metadata(session_id, after_iso=started_iso)
            if meta:
                break
            await asyncio.sleep(2)
        cfg = _spine.get_compaction_config()
        # The session's incompressible floor in TOKENS — auto mode's normalizer. MEASURED
        # (get_context_usage on a pre-turn session), not inferred from an observed fill; cached
        # per context+model, so this costs a probe once. None if the probe failed — the verdict
        # then falls back to the flat threshold.
        measured = await _agent.measure_context_floor(ctx, model)
        floor_tokens = measured[0] if measured else None
        verdict = judge_effectiveness(meta, cfg["min_gain_pct"], floor_tokens=floor_tokens)
        # Token attribution: the CLI reports ZERO API usage for a /compact turn (verified
        # empirically — the summarization request never surfaces through the session), so the run
        # row carries what we MEASURED, which is nothing. It stays 0.
        #
        # It used to carry `preTokens` as input + `postTokens` as output, "estimated from the
        # boundary". That was a category error (owner, 2026-07-28): those two numbers describe how
        # much context the compaction REMOVED, not what it spent — a compaction metric filed in a
        # usage column, which made a single cached summarization call out-rank build and plan on
        # the Activity board (132.7k vs 58.6k). The usage column means usage; the shrink belongs
        # to `compaction.verdict` (pre_tokens/post_tokens/gain_pct/reclaimed_ratio), where it
        # already lives. Do not re-mix them.
        #
        # The compaction does cost real money — one summarization call, mostly cache-read input
        # plus the summary's output. We simply cannot see it, and 0 is the honest value for a
        # column defined as "what we counted". If a future CLI reports it, `real_usage` below
        # picks it up automatically.
        run_usage = real_usage if (real_usage and sum(
            real_usage.get(k, 0) for k in ("input_tokens", "output_tokens",
                                           "cache_creation_input_tokens"))) else None
        # Strikes exist to stop the AUTO trigger looping on a session it cannot shrink. A manual
        # compaction cannot loop — the owner fired it — so it must not accrue one, and it must not
        # push a session into permanent back-off. Live finding (2026-07-28): a SMALL session scores
        # low by construction (reclaimable = pre − floor is tiny, so 28,390 → 3,703 came out at
        # 0.48 of reclaimable, just under the bar) — two hand-typed `/compact`s on a short thread
        # would have retired its auto-compaction for good. An effective one still CLEARS strikes:
        # that is evidence the session is compactable again, whoever asked.
        #
        # RUNWAY, not just shrink (2026-07-30). `effective` answers "did it get smaller"; it cannot
        # answer "did that buy any working room", and those came apart in practice: seven consecutive
        # 80–93%-effective compactions on one item, each re-crossing the trigger within a turn, with
        # strikes pinned at 0 the whole time. A compaction that bought fewer than MIN_RUNWAY_TURNS
        # real turns is a treadmill step and counts against the back-off however well it shrank.
        bought_runway = (st.turns_since_compact is None            # first compaction on this session
                         or st.turns_since_compact >= MIN_RUNWAY_TURNS)
        verdict["bought_runway"] = bought_runway
        verdict["runway_turns"] = st.turns_since_compact
        # `or manual` keeps the manual carve-out intact: the owner asked, and a hand-run compaction
        # cannot loop, so it clears the board on effectiveness alone. The runway condition is for the
        # AUTO trigger, which is the only thing that can treadmill.
        if verdict["effective"] and (bought_runway or manual):
            st.strikes = 0
        elif not manual:
            st.strikes += 1
        st.turns_since_compact = 0   # this compaction is now the one runway is measured from
        # The verdict event's meta is the durable CALIBRATION record (v2 runway tuning reads
        # these + the per-turn ctx_pct history on run rows): full measurement — mode, floor,
        # window, reclaimable, ratio — not just the outcome. `trigger_pct` is None for a manual
        # "Compact now" (no trigger fired — a 0 here would poison the calibration data).
        # `post_pct` came off the `/compact` turn's Result and was ALWAYS None: a compact turn
        # reports no usage (that is why its run row reads `Σ 0 tok`), so the SDK has no fill to give.
        # It is derived here instead, from two MEASURED numbers the boundary does record — post
        # tokens over the model's real window. Not an estimate: a ratio of two measurements, which is
        # what a fill percentage is everywhere else in the system.
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
                              # The treadmill, named where the owner actually reads it. A shrink
                              # figure alone let seven of these look like seven successes.
                              + ("" if verdict.get("bought_runway", True) else
                                 f" — but bought only {verdict.get('runway_turns')} turn(s) of "
                                 f"runway; the trigger is too low for this session")
                              if pre else "Compaction produced no boundary — counted ineffective"),
                             item_id=item_id, actor="daemon",
                             meta={**verdict, "session_id": session_id, "window": window,
                                   # WHAT survived, beside how much. Agent/diagnosis-facing —
                                   # no owner surface renders this (compaction is session
                                   # hygiene, not a story for the owner).
                                   "summary": summary})
        # 4. Back-off: ≥2 strikes → stop compacting this session + page the owner (durable:
        #    awaiting_human IS the attention engine's needs-you signal).
        if st.strikes >= STRIKES_TO_BACKOFF and not st.backed_off and not manual:
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
        if item_id:
            _end_run(ctx, context_id, item_id, None, rest_status, run_usage,
                     ctx_pct=post_pct, session_id=session_id)
        else:
            _spine.finish_run(run_id, usage=run_usage, ctx_pct=post_pct,
                              session_id=session_id, model=model)
    return verdict


def due(session_id: str | None, kind: str | None, *, force: bool = False) -> int | None:
    """Is this session over its trigger and clear to compact? Returns the fill that decided it
    (so the caller can log and pass it as `pre_pct`), or None for "no".

    Pure and cheap — one spine read, no side effects — so a run-start seam can ask on every run
    without paying for it. The reading is the last finished run's authoritative `ctx_pct` on this
    session: nothing has been appended to that transcript since, so it IS the current fill.

    `force` is the owner's explicit "compact now" (§13.1 row 4): the THRESHOLD is bypassed — they
    asked, so worth-it is their call, not ours — but the back-off is not. A session that has
    already compacted ineffectively twice is one where compaction demonstrably does nothing, and
    running it again on request would burn a model call to prove that a third time. Returns 0 when
    forced with no reading yet, which is falsy — so callers must test `is None`, never truthiness
    (that is why the return is the fill and not a bool)."""
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
    """The run-START gate: if this session is over its trigger, compact it NOW and return the
    verdict. None = it did not fire (the common case, one spine read).

    AWAITED, not fire-and-forget — the caller must not open its run until this returns, because
    `run_compaction` takes the item's run-lock (one live item-run is a data-layer invariant) and
    because sending a real prompt into a session mid-compaction is what "never mid-task" forbids.

    `item_id=None` is a general session; `force=True` is the owner's manual "compact now", which
    rides this SAME path with the threshold bypassed rather than being a second mechanism."""
    pre = due(session_id, kind, force=force)
    if pre is None:
        return None
    log.info("compaction trigger%s: %s/%s at %d%% (session %s)",
             " (manual)" if force else "", context_id, item_id or "(session)", pre,
             (session_id or "")[:8])
    return await run_compaction(ctx, context_id, item_id, session_id, model=model,
                                pre_pct=None if force else pre, manual=force)
