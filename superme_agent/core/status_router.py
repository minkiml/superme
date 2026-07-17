"""Status router — the typed-awaiting semantics of the workspace workflow (D2/D3).

`item.status` is the runnable-state axis: `active · awaiting_child · awaiting_human · done`.
Typed awaiting ROUTES instead of paging:
- `awaiting_child`  — auto-resumes when the last open BLOCKING child terminates (no human).
- `awaiting_human`  — the only type that pages the owner (attention surface, D10).

Everything here is a PURE function over plain item dicts (as read by DevKnowledgeService) — no IO,
no spine — so the routing logic is unit-testable and the callers (routes/services) own the writes.

Children are items whose `spawned_from.relation` is `blocking` or `parallel` (D3); `spawn` items
carry provenance but are NOT children — they never gate or pause anything.
"""

TERMINAL_STATUS = "done"
CHILD_RELATIONS = ("blocking", "parallel")
SPAWN_RELATIONS = ("blocking", "parallel", "spawn")


def spawned_from(item: dict) -> dict | None:
    """The item's `spawned_from {item, relation, note}` edge, or None (a true original)."""
    sf = item.get("spawned_from")
    return sf if isinstance(sf, dict) and sf.get("item") else None


def is_terminal(item: dict) -> bool:
    """Terminal = status done (outcome completed/abandoned/superseded stamped alongside)."""
    return str(item.get("status")) == TERMINAL_STATUS or bool(item.get("done_at"))


def children_of(items: list[dict], parent_id: str,
                relations: tuple[str, ...] = CHILD_RELATIONS) -> list[dict]:
    """Items spawned from `parent_id` with a relation in `relations` (default: real children —
    blocking + parallel; pass SPAWN_RELATIONS to include provenance-only spawns)."""
    out = []
    for it in items:
        sf = spawned_from(it)
        if sf and str(sf.get("item")) == str(parent_id) and sf.get("relation") in relations:
            out.append(it)
    return out


def open_children(items: list[dict], parent_id: str) -> list[dict]:
    """Non-terminal children (blocking + parallel) of `parent_id` — the set that gates its
    completion (D3: parent cannot reach `completed` while any child is open)."""
    return [c for c in children_of(items, parent_id) if not is_terminal(c)]


def children_terminal(items: list[dict], parent_id: str) -> tuple[bool, list[str]]:
    """(all children terminal?, open child ids) — the close gate's mechanical child check."""
    opens = open_children(items, parent_id)
    return (not opens, [c.get("id") for c in opens])


def parent_to_resume(items: list[dict], child: dict) -> str | None:
    """When `child` (just terminal) is a BLOCKING child, the parent id to auto-resume — exactly
    when the parent sits at `awaiting_child` and no OTHER blocking sibling is still open.
    None otherwise (parallel/spawn relations never pause the parent, so never resume it)."""
    sf = spawned_from(child)
    if not sf or sf.get("relation") != "blocking":
        return None
    pid = str(sf.get("item"))
    parent = next((it for it in items if str(it.get("id")) == pid), None)
    if parent is None or str(parent.get("status")) != "awaiting_child":
        return None
    still_open = [
        c for c in children_of(items, pid, relations=("blocking",))
        if not is_terminal(c) and str(c.get("id")) != str(child.get("id"))
    ]
    return None if still_open else pid
