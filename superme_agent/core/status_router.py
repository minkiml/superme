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


def relation_of(child: dict) -> str:
    """The child's relation word — `blocking` · `parallel` · `spawn`, or `child` when absent.
    Event copy hardcoded "Blocking child …" back when only a blocking child could release a
    parent; a parallel release now happens too, and the line has to say which one it was."""
    return str((spawned_from(child) or {}).get("relation") or "child")


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


def holding_children(items: list[dict], parent: dict, *, at_close: bool | None = None) -> list[dict]:
    """The open children that currently HOLD `parent` — the ONE answer both the close criterion and
    the auto-resume read.

    Which children hold depends on where the parent is, and that difference is the point:

    - **Before its final phase**, only `blocking` children hold it. A `parallel` child is explicitly
      allowed to run alongside; pausing on one would defeat the relation.
    - **At its final phase**, EVERY open child holds it (D3: a parent cannot complete while a child
      is still building on its work).

    Two functions used to answer this separately and they disagreed: `children_terminal` counted
    both relations, `parent_to_resume` released on `blocking` only. So a parent that reached close
    with an open PARALLEL child parked at `awaiting_child` — and nothing ever released it, because
    every release site in the daemon (the clearance cascade, the gate + work-item routes, the
    restart reconciler) goes through `parent_to_resume`. It sat silently, since `awaiting_child`
    does not page the owner. One rule per question: this is that rule.

    `at_close` states the question directly instead of deriving it from the parent's current phase.
    `children_terminal` passes True because it IS the close criterion — the drilldown previews that
    criterion from earlier phases, and a preview that quietly asked the mid-pipeline question would
    report a clean close gate while a parallel child was still open.
    """
    if at_close is None:
        try:
            from .kind_profiles import is_final_phase
            at_close = is_final_phase(parent.get("kind"), parent.get("phase") or "triage")
        except (KeyError, ImportError):
            # Unknown kind / hand-edited yaml. Default to the STRICT reading — every child holds —
            # so a bad row can never let a parent clear out from under an open child.
            at_close = True
    rel = CHILD_RELATIONS if at_close else ("blocking",)
    return [c for c in children_of(items, str(parent.get("id")), relations=rel)
            if not is_terminal(c)]


def children_terminal(items: list[dict], parent_id: str) -> tuple[bool, list[str]]:
    """(all children terminal?, open child ids) — the close gate's mechanical child check.
    A thin read of `holding_children`, so the gate and the release can never diverge again."""
    parent = next((it for it in items if str(it.get("id")) == str(parent_id)), None)
    opens = holding_children(items, parent if parent is not None else {"id": parent_id},
                             at_close=True)
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
    """When `child` just went terminal, the parent id to auto-resume — exactly when the parent sits
    at `awaiting_child` and NOTHING still holds it (`holding_children`). None otherwise.

    Both real relations are considered, not just `blocking`. A parallel child never PAUSES a parent
    mid-pipeline, but at close it does HOLD one, and only the same rule that decided the hold can
    decide the release. `spawn` items carry provenance only and never do either."""
    sf = spawned_from(child)
    if not sf or sf.get("relation") not in CHILD_RELATIONS:
        return None
    pid = str(sf.get("item"))
    parent = next((it for it in items if str(it.get("id")) == pid), None)
    if parent is None or str(parent.get("status")) != "awaiting_child":
        return None
    still_open = [c for c in holding_children(items, parent)
                  if str(c.get("id")) != str(child.get("id"))]
    return None if still_open else pid
