"""Vocabulary and small helpers the rest of the package is written in terms of."""

import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("superme-agent")

# The {core, dev} scope lattice axis (≡ Context.mode in this codebase).
MODES = ("core", "dev")
# Sessionless passes whose transcript is THROWAWAY. The run ROW is always kept — it is the durable
# telemetry.
DISPOSABLE_FEATURES = {"distill", "sweep", "write"}
# Explicit LEARNING agent jobs, sessionless, counted as "agents" only while running. Work-item
# jobs count separately.
LEARNING_FEATURES = {"distill", "sweep", "capture", "write"}
_RUN_STATUSES = {"running", "done", "aborted", "waiting"}
# Runs that ENDED without a final usage. Every display reports 0: measured usage only, never an
# estimate.
_UNRECONCILED_STATUSES = {"aborted"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(p) -> str:
    """Canonical absolute path string, so equal cwds compare equal."""
    return str(Path(p).resolve())


# Commands that put a file's CONTENTS into context. `ls` and `find` name a path without opening
# it.
_FILE_OPENERS = frozenset({"cat", "bat", "head", "tail", "less", "more", "sed", "awk", "grep",
                           "egrep", "fgrep", "rg", "nl", "strings"})


def _opens_a_file(command: str) -> bool:
    """True if any word in a shell command is a program that reads file contents."""
    return any(w.strip("'\"();|&`$") in _FILE_OPENERS for w in command.split())


def _duration_ms(started_at: str | None, ended_at: str | None) -> int | None:
    """Run wall-clock from the ISO started/ended stamps (None if either is missing/unparseable)."""
    if not started_at or not ended_at:
        return None
    try:
        return int((datetime.fromisoformat(ended_at)
                    - datetime.fromisoformat(started_at)).total_seconds() * 1000)
    except (ValueError, TypeError):
        return None
