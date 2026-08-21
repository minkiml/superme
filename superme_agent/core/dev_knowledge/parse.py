"""Turning the work-item files into the ordered tree the dashboard renders."""

def _session_fields(meta: dict) -> tuple[dict, str | None]:
    """A work-item's session slots plus the COMPUTED `session_id` — the current
    phase's, so single-session readers follow the phase."""
    from ..kind_profiles import (INTAKE_PHASES, LEGACY_INTAKE_SLOT, SESSION_SLOTS, session_slot)
    keys = (*SESSION_SLOTS, LEGACY_INTAKE_SLOT)
    sessions = {s: str(meta[f"session_{s}"]) for s in keys if meta.get(f"session_{s}")}
    phase = str(meta.get("phase") or "triage")
    try:
        slot = session_slot(phase)
    except KeyError:
        slot = "triage"
    legacy_intake = sessions.get(LEGACY_INTAKE_SLOT) if slot in INTAKE_PHASES else None
    legacy_id = meta.get("session_id")
    computed = (sessions.get(slot) or legacy_intake
                or (str(legacy_id) if legacy_id else None))
    return sessions, computed


# Display order. Status ranks put what NEEDS THE OWNER first, then runnable work, then waits, then
# done.
_PHASE_RANK = {"triage": 0, "plan": 1, "build": 2, "investigate": 2,
               "vet": 3, "review": 4, "close": 5}
# `error` outranks awaiting_human: work that STOPPED is louder than work resting at a gate by
# design.
_STATUS_RANK = {"error": 0, "awaiting_human": 1, "active": 2, "awaiting_child": 3,
                "awaiting_upstream": 4, "awaiting_slot": 4, "done": 5}
# Non-terminal. A parked item IS live, and so is `error` — it is work waiting to be resumed.
_LIVE_STATUSES = ("active", "awaiting_child", "awaiting_upstream", "awaiting_slot",
                  "awaiting_human", "error")
_SPAWN_RELATIONS = ("blocking", "parallel", "spawn")


def _toposort_keys(specs: list[dict]) -> list[str]:
    """Order batch keys so every intra-batch `after` comes before its dependent.
    Raises naming the cycle if not a DAG."""
    keys = {s["key"] for s in specs}
    deps = {s["key"]: [a for a in s["after"] if a in keys] for s in specs}
    order: list[str] = []
    temp: set[str] = set()
    perm: set[str] = set()

    def visit(k: str, stack: list[str]) -> None:
        if k in perm:
            return
        if k in temp:
            raise ValueError(f"cyclic after: edge — {' → '.join(stack + [k])}")
        temp.add(k)
        for d in deps[k]:
            visit(d, stack + [k])
        temp.discard(k)
        perm.add(k)
        order.append(k)

    for s in specs:
        visit(s["key"], [])
    return order


def _norm_artifact(a) -> dict:
    """Normalize one `artifacts` entry to `{type, path}`. Agents write either a bare
    path or the dict."""
    def _stem(p: str) -> str:
        name = str(p or "").rsplit("/", 1)[-1]
        return name.rsplit(".", 1)[0] or "file"

    if isinstance(a, dict):
        path = str(a.get("path") or a.get("type") or "")
        kind = str(a.get("type") or _stem(path))
        return {"type": kind or "file", "path": path}
    s = str(a or "")
    return {"type": _stem(s), "path": s}


def _norm_artifacts(raw) -> list[dict]:
    """Coerce a raw `artifacts` frontmatter value (list, or None) to a list of normalized refs."""
    return [_norm_artifact(a) for a in (raw or [])]


def _item_view(it: dict) -> dict:
    """The board's per-item projection: identity, phase/status, and the one relevant date
    as a display string."""
    done_at, updated, created = it.get("done_at"), it.get("updated_at"), it.get("created_at")
    date_val = done_at or (updated if it.get("status") in _LIVE_STATUSES else None) or created
    return {"id": it.get("id"), "title": it.get("title"), "phase": it.get("phase"),
            "status": it.get("status"),
            "done_at": str(done_at) if done_at else None,
            "date": str(date_val) if date_val else None}


def _rollup(views: list[dict]) -> dict:
    """{done, total} for a set of item views — done = completed (done_at set)."""
    return {"done": sum(1 for v in views if v.get("done_at")), "total": len(views)}
