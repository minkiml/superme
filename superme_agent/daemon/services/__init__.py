"""Daemon orchestration services (Backend Refactor R4+).

Agent-orchestration extracted out of route handlers — the headless plan/distill/write runners, the
sweep + idle loop, and the proposal state-machine driver. This is daemon glue (spawns asyncio tasks,
streams events), kept here rather than pushed into `core/` so the surface-agnostic services stay pure.
Empty until R4.
"""
