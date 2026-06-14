"""SessionIndex — the single, surface-shared session log.

One file (`.sessions.json`) records every conversation SuperMe runs, on any surface,
keyed canonically by **cwd** — the SuperMe-hosted working dir, i.e. the workspace.
That key is what actually matters: the SDK stores each session's transcript under
~/.claude/projects/<encoded-cwd>/, so sessions sharing a cwd are mutually resumable
and belong in the same picker — regardless of whether Slack or the web created them.

Schema (v2):
    { "version": 2,
      "sessions": {
        "<session_id>": { "cwd": "/abs/workspace", "surface": "slack|web",
                          "thread_ts": "1780…|null",
                          "created_at": "iso", "updated_at": "iso" } } }

Two views over the same records:
    for_thread(thread_ts) -> session_id     (Slack thread resume; thread_ts is unique)
    for_cwd(cwd)          -> [session_id]    (web picker for a workspace)

The file is the source of truth and is re-read on every operation, so the Slack and
daemon processes stay consistent without sharing memory. It's small; this is cheap.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..runtime.config import SESSIONS_FILE, CLAUDE_PROJECTS_DIR

log = logging.getLogger("superme-agent")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(cwd) -> str:
    """Canonical absolute string for a cwd, so equal workspaces compare equal."""
    return str(Path(cwd).resolve())


class SessionIndex:
    """Read-modify-write store over `.sessions.json`, shared by every surface."""

    def __init__(self, path=SESSIONS_FILE, projects_dir=CLAUDE_PROJECTS_DIR):
        self._path = path
        self._projects = projects_dir

    # --- persistence (file is the source of truth; reload per op) ---------------
    def _load(self) -> dict:
        try:
            raw = json.loads(self._path.read_text())
        except (FileNotFoundError, ValueError):
            return {"version": 2, "sessions": {}}
        if isinstance(raw, dict) and raw.get("version") == 2:
            return raw
        # Anything else is the v1 flat {thread_ts: session_id} map — migrate it.
        return self._migrate_v1(raw if isinstance(raw, dict) else {})

    def _save(self, data: dict) -> None:
        try:
            self._path.write_text(json.dumps(data, indent=2))
        except OSError as e:
            log.warning("could not persist %s: %s", self._path.name, e)

    # --- v1 → v2 migration ------------------------------------------------------
    def _find_transcript_cwd(self, session_id: str) -> str | None:
        """Recover a session's true cwd by locating its transcript and reading the
        `cwd` field the SDK writes into each record."""
        if not self._projects.is_dir():
            return None
        for folder in self._projects.iterdir():
            f = folder / f"{session_id}.jsonl"
            if not f.exists():
                continue
            for line in f.read_text().splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("cwd"):
                    return _norm(d["cwd"])
            return None
        return None

    def _migrate_v1(self, flat: dict) -> dict:
        """Convert {thread_ts: session_id} -> v2, scanning transcripts for each cwd."""
        sessions: dict = {}
        for thread_ts, sid in flat.items():
            if not isinstance(sid, str):
                continue
            cwd = self._find_transcript_cwd(sid)
            sessions[sid] = {
                "cwd": cwd or "",                 # unknown cwd -> won't surface in a picker
                "surface": "slack",
                "thread_ts": thread_ts,
                "created_at": _now(),
                "updated_at": _now(),
            }
        data = {"version": 2, "sessions": sessions}
        # Back up the v1 file beside the new one, then write v2.
        try:
            backup = self._path.with_suffix(".v1.json")
            if self._path.exists() and not backup.exists():
                backup.write_text(self._path.read_text())
                log.info("migrated v1 sessions -> v2 (backup: %s)", backup.name)
        except OSError as e:
            log.warning("could not back up v1 sessions: %s", e)
        self._save(data)
        return data

    # --- write ------------------------------------------------------------------
    def record(self, session_id: str, cwd, surface: str, thread_ts: str | None = None) -> None:
        """Upsert a session. Sets created_at once; refreshes updated_at every turn."""
        if not session_id:
            return
        data = self._load()
        rec = data["sessions"].get(session_id)
        if rec is None:
            data["sessions"][session_id] = {
                "cwd": _norm(cwd),
                "surface": surface,
                "thread_ts": thread_ts,
                "created_at": _now(),
                "updated_at": _now(),
            }
        else:
            rec["cwd"] = _norm(cwd)
            rec["updated_at"] = _now()
            if thread_ts and not rec.get("thread_ts"):
                rec["thread_ts"] = thread_ts
        self._save(data)

    def forget(self, session_id: str) -> bool:
        data = self._load()
        existed = data["sessions"].pop(session_id, None) is not None
        if existed:
            self._save(data)
        return existed

    def forget_thread(self, thread_ts: str) -> bool:
        data = self._load()
        gone = [sid for sid, r in data["sessions"].items() if r.get("thread_ts") == thread_ts]
        for sid in gone:
            data["sessions"].pop(sid, None)
        if gone:
            self._save(data)
        return bool(gone)

    # --- read -------------------------------------------------------------------
    def for_thread(self, thread_ts: str) -> str | None:
        """The session id for a Slack thread (thread_ts is globally unique)."""
        for sid, r in self._load()["sessions"].items():
            if r.get("thread_ts") == thread_ts:
                return sid
        return None

    def for_cwd(self, cwd) -> list[str]:
        """All session ids that ran in a workspace (cwd), for the web picker."""
        target = _norm(cwd)
        return [sid for sid, r in self._load()["sessions"].items() if r.get("cwd") == target]

    def get(self, session_id: str) -> dict | None:
        """The stored record for a session (cwd, surface, thread_ts, timestamps)."""
        return self._load()["sessions"].get(session_id)
