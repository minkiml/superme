"""Per-thread session persistence for the Slack surface.

Slack conversations are threads; this maps a thread to its SDK session_id so a thread
resumes after a restart. It's a thin wrapper over the system spine's session table (which
replaced `.sessions.json` in WI-3), so Slack and the web share one workspace-scoped log —
the same session can be seen from either surface when they share a cwd.
"""

import logging

from ..core.spine import SystemSpine, get_spine

log = logging.getLogger("superme-agent")


class SessionStore:
    """A thread_ts -> session_id view over the system spine's session table."""

    def __init__(self, spine: SystemSpine | None = None):
        self._spine = spine or get_spine()

    def get(self, thread_ts: str) -> str | None:
        """The session_id for a thread, or None if it's a fresh conversation."""
        return self._spine.session_for_thread(thread_ts)

    def remember(self, thread_ts: str, session_id: str, cwd) -> None:
        """Record a thread's session under its workspace (cwd)."""
        self._spine.record_session(session_id, cwd, surface="slack", thread_ts=thread_ts)

    def forget(self, thread_ts: str) -> bool:
        """Drop a thread's session (to reset it). Returns True if one existed."""
        return self._spine.forget_thread(thread_ts)
