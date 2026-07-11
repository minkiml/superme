"""Daemon lifespan — startup hygiene + background loops.

Replaces the deprecated `@app.on_event("startup")` hook with a modern `lifespan` context manager:
on entry it reconciles orphaned runs and launches the idle-sweep heartbeat; on exit it cancels the
loop cleanly. The heartbeat lives in `services.learning` (R4b) — a leaf module with no server.py
dependency, so this imports it directly (no cycle).
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import app_state
from ..gateway import contexts
from .services.learning import _idle_sweep_loop, SWEEP_POLL_SECONDS, SWEEP_IDLE_SECONDS

log = logging.getLogger("superme-agent")


def _backfill_session_stamps() -> None:
    """One-time migration (work-item-session-recognition-prd): stamp `session.item_id` for every
    work-item that already carries a `session_id`, so in-progress items created before the stamp
    existed aren't stranded as 'general' sessions. Write-once, so it never clobbers a real stamp
    and is a no-op on subsequent starts. Best-effort — a bad repo must not block daemon startup."""
    try:
        pairs: list[tuple[str, str]] = []
        for rid in app_state.spine.repos():
            ctx = contexts.resolve(rid, "dev")
            if not ctx.internal_root:
                continue
            try:
                data = app_state.dev.read_all(ctx.internal_root / "dev")
            except Exception:
                continue
            for it in data.get("work_items", []):
                sid = it.get("session_id")
                if sid and it.get("id"):
                    pairs.append((sid, it["id"]))
        if pairs:
            n = app_state.spine.backfill_session_items(pairs)
            if n:
                log.info("backfilled work-item stamp onto %d session(s)", n)
    except Exception:
        log.exception("session-stamp backfill failed (non-fatal)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Flip any runs orphaned by a previous daemon's exit (running → aborted). Was at module import
    # before; doing it here keeps app_state import side-effect-free and runs it once per app start.
    app_state.spine.reconcile()
    # Re-pin the learning sub-agents' `.md` model to their tier's current concrete id (so a
    # MODEL_TIERS bump auto-propagates to the files). No-op when already current.
    app_state.spine.reconcile_agent_models()
    # Migrate any legacy CONCRETE picker override (system default + per-repo) back to its tier alias —
    # the canonical DB form, so a saved pick auto-tracks a MODEL_TIERS bump. No-op when already alias.
    app_state.spine.reconcile_model_overrides()
    # One-time: stamp durable work-item identity onto pre-existing sessions (idempotent).
    _backfill_session_stamps()

    task = asyncio.create_task(_idle_sweep_loop())
    log.info("idle sweep loop started (every %ds, idle threshold %ds, auto-learning=%s)",
             SWEEP_POLL_SECONDS, SWEEP_IDLE_SECONDS, app_state.spine.get_learning_enabled())
    try:
        yield
    finally:
        task.cancel()
