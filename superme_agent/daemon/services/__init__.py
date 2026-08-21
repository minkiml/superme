"""Daemon orchestration services — agent orchestration extracted out of the route handlers.

Daemon glue that spawns tasks and streams events, kept here rather than in `core/`, so the
surface-agnostic services stay pure.
"""
