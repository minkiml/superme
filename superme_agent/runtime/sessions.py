"""Per-thread session persistence for the Slack surface.

Slack conversations are threads; this maps a thread to its SDK session_id so a thread
resumes after a restart. It's now a thin wrapper over the cross-surface SessionIndex
(`.sessions.json`) so Slack and the web share one workspace-scoped log — the same
session can be seen from either surface when they share a cwd.
"""

import logging

from ..core.session_index import SessionIndex

log = logging.getLogger("superme-agent")


class SessionStore:
    """A thread_ts -> session_id view over the shared SessionIndex."""

    def __init__(self, index: SessionIndex | None = None):
        self._index = index or SessionIndex()

    def get(self, thread_ts: str) -> str | None:
        """The session_id for a thread, or None if it's a fresh conversation."""
        return self._index.for_thread(thread_ts)

    def remember(self, thread_ts: str, session_id: str, cwd) -> None:
        """Record a thread's session under its workspace (cwd)."""
        self._index.record(session_id, cwd, surface="slack", thread_ts=thread_ts)

    def forget(self, thread_ts: str) -> bool:
        """Drop a thread's session (to reset it). Returns True if one existed."""
        return self._index.forget_thread(thread_ts)
