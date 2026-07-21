"""Status router — the typed-awaiting semantics of the workspace workflow (D2/D3).

`item.status` is the runnable-state axis: `active · awaiting_child · awaiting_upstream ·
awaiting_human · done`. Typed awaiting ROUTES instead of paging:
- `awaiting_child`    — auto-resumes when the last open BLOCKING child terminates (no human).
- `awaiting_upstream` — auto-starts when every PEER named in `after:` has COMPLETED (no human).
- `awaiting_human`    — the only type that pages the owner (attention surface, D10).

Two different edges, deliberately not one. `spawned_from` is vertical (a parent paused because it
spawned this work); `after` is horizontal (a sibling may not start until another sibling lands).
Overloading `awaiting_child` for both would break `parent_to_resume`'s meaning — it answers "which
parent do I un-pause", and a peer has no parent to name.

Everything here is a PURE function over plain item dicts (as read by DevKnowledgeService) — no IO,
no spine — so the routing logic is unit-testable and the callers (routes/services) own the writes.

Children are items whose `spawned_from.relation` is `blocking` or `parallel` (D3); `spawn` items
carry provenance but are NOT children — they never gate or pause anything.
"""

TERMINAL_STATUS = "done"
AWAITING_UPSTREAM = "awaiting_upstream"
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


# --- peer sequencing: the `after:` edge -------------------------------------------------------

def upstream_ids(item: dict) -> list[str]:
    """The peer ids this item declares it must follow (`after: [id, …]`), normalized to strings.
    Absent/malformed reads as no constraint — an item with no `after` starts immediately."""
    raw = item.get("after")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(x) for x in raw if x]


def upstream_state(items: list[dict], item: dict) -> tuple[list[str], list[str]]:
    """`(open, failed)` upstream ids for `item` — the two reasons it may not start yet.

    - **open** — named, present, still non-terminal. Wait; the release is automatic.
    - **failed** — terminal but NOT `completed` (abandoned/superseded). This is NOT a wait: nothing
      further will happen on its own, and starting anyway would build against a predecessor that
      never landed. The caller pages the owner instead of auto-releasing (§3 of the autopilot
      design: independent branches keep running, but a launch must never hide one bad item).

    A named id with **no matching item is treated as satisfied**, deliberately. The alternative
    wedges the downstream item forever the moment an upstream is hard-deleted — the same trap the
    delete route already guards for blocking children. Gone counts as closed.
    """
    by_id = {str(it.get("id")): it for it in items}
    open_ids, failed_ids = [], []
    for uid in upstream_ids(item):
        up = by_id.get(uid)
        if up is None:                       # deleted/never existed → satisfied, not a wedge
            continue
        if not is_terminal(up):
            open_ids.append(uid)
        elif str(up.get("outcome") or "completed") != "completed":
            failed_ids.append(uid)
    return open_ids, failed_ids


def items_to_release(items: list[dict], upstream_id: str) -> tuple[list[str], list[str]]:
    """After `upstream_id` went terminal: `(release, page)` — the parked peers that may now start,
    and the parked peers whose wait just became pointless because an upstream ended un-completed.

    Only items actually sitting at `awaiting_upstream` and actually naming `upstream_id` are
    considered, so this stays a no-op for every item the terminal event has nothing to do with.
    """
    release, page = [], []
    for it in items:
        if str(it.get("status")) != AWAITING_UPSTREAM:
            continue
        if str(upstream_id) not in upstream_ids(it):
            continue
        open_ids, failed_ids = upstream_state(items, it)
        if failed_ids:
            page.append(str(it.get("id")))
        elif not open_ids:
            release.append(str(it.get("id")))
    return release, page


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
