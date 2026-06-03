"""Per-thread session persistence.

The SDK persists each conversation's *transcript* to disk under ~/.claude. What it
does NOT track for us is which session belongs to which Slack thread — so this
small store keeps that thread -> session_id index, persisted to a JSON file so a
thread can be resumed even after the bot restarts.
"""

import json
import logging

from .config import SESSIONS_FILE

log = logging.getLogger("superme-agent")


class SessionStore:
    """A thread_ts -> session_id map, backed by a JSON file."""

    def __init__(self, path=SESSIONS_FILE):
        self._path = path
        self._map: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._map, indent=2))
        except OSError as e:
            log.warning("Could not persist sessions map: %s", e)

    def get(self, thread_ts: str) -> str | None:
        """The session_id for a thread, or None if it's a fresh conversation."""
        return self._map.get(thread_ts)

    def remember(self, thread_ts: str, session_id: str) -> None:
        """Record a thread's session, persisting only when it actually changes."""
        if self._map.get(thread_ts) != session_id:
            self._map[thread_ts] = session_id
            self._save()

    def forget(self, thread_ts: str) -> bool:
        """Drop a thread's session (to reset it). Returns True if one existed."""
        existed = self._map.pop(thread_ts, None) is not None
        if existed:
            self._save()
        return existed
