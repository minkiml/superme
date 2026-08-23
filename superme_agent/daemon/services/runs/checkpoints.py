"""What a run banks before compaction, and what the next one is handed."""

from pathlib import Path

from ...app_state import dev as _dev, sessions as _sessions, spine as _spine
from ....core import artifacts as _arts
from ....core.vocab import kind_profiles

def bank_auto_checkpoint(ctx, item_id: str, *, since: float | None = None) -> bool:
    """Mechanical fallback for the session-end hook, so the orient block always has one.

    Skipped when the item is terminal or a newer checkpoint exists: the agent's own is better."""
    if not (item_id and ctx.internal_root):
        return False
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    if not item or item.get("done_at") or str(item.get("status")) == "done":
        return False
    item_dir = dev_root / "work-items" / item_id
    latest = _arts.latest_checkpoint(item_dir, char_cap=1)
    if latest and since:
        try:
            if Path(latest["path"]).stat().st_mtime >= since:
                return False  # the session banked its own — keep it
        except OSError:
            pass
    tasks = _dev.read_tasks(dev_root, item_id) or []
    open_tasks = [t["text"] for t in tasks if not t.get("done")][:8]
    remaining = ("; ".join(open_tasks)) if open_tasks else "see plan.md ## Tasks (none parsed)"
    repo_dir = Path(str(item["git_worktree"])) if item.get("git_worktree") else ctx.cwd
    try:
        _arts.write_checkpoint(
            item_dir, repo_dir,
            role=kind_profiles.session_slot(str(item.get("phase") or "triage")),
            working_on=f"{item.get('phase') or 'triage'} phase — {item.get('title') or item_id}",
            decisions="(auto-banked at session end — the session's reasoning lives in its transcript)",
            remaining=remaining,
            notes="AUTO checkpoint written by the daemon because the session ended without banking "
                  "one. Derived data only — verify against the artifacts before relying on it.",
        )
        return True
    except ValueError:
        return False


def compacted_checkpoint(ctx, item: dict, session_id: str | None) -> str | None:
    """The checkpoint path this thread is owed a pointer to, or None.

    Owed only while the session's newest finished run IS the compaction, resolved via the role
    stamp."""
    if not (session_id and ctx.internal_root and item):
        return None
    if not _spine.session_compacted_pending(session_id):
        return None
    role = kind_profiles.session_slot(str(item.get("phase") or "triage"))
    item_dir = ctx.internal_root / "dev" / "work-items" / str(item.get("id") or "")
    cp = _arts.latest_checkpoint(item_dir, char_cap=1, role=role)
    return cp["path"] if cp else None


def compacted_session_memory(ctx, session_id: str | None) -> str | None:
    """The `session-memory/` path this thread is owed a pointer to, or None. Same self-clearing gate
    as `compacted_checkpoint`; a general session has one thread, so no role scoping."""
    if not (session_id and ctx.internal_root):
        return None
    if not _spine.session_compacted_pending(session_id):
        return None
    mem = _arts.read_session_memory(ctx.internal_root / ctx.mode, session_id, char_cap=1)
    return mem["path"] if mem else None


def reset_vet_thread(ctx, item: dict, *, dev=None, sessions=None) -> bool:
    """Retire the previous cycle's vet session and clear the slot, so the next vet mints.

    Each cycle gets a fresh vetter: prior findings arrive as reports, never as memory."""
    d, s = dev or _dev, sessions or _sessions
    prev = (item.get("sessions") or {}).get("vet")
    if not prev:
        return False
    s.delete(ctx, prev, cause="retired")
    d.set_work_item_session(ctx.internal_root / "dev", str(item["id"]), None, slot="vet")
    return True
