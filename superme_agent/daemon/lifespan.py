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
from .services.learning import _idle_sweep_loop, SWEEP_POLL_SECONDS, SWEEP_IDLE_SECONDS

log = logging.getLogger("superme-agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Flip any runs orphaned by a previous daemon's exit (running → aborted). Was at module import
    # before; doing it here keeps app_state import side-effect-free and runs it once per app start.
    app_state.spine.reconcile()

    task = asyncio.create_task(_idle_sweep_loop())
    log.info("idle sweep loop started (every %ds, idle threshold %ds, auto-learning=%s)",
             SWEEP_POLL_SECONDS, SWEEP_IDLE_SECONDS, app_state.spine.get_learning_enabled())
    try:
        yield
    finally:
        task.cancel()
