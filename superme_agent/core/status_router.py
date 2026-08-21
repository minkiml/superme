"""Typed-awaiting semantics: which items are waiting, and on what.

    awaiting_child     auto-resumes when the last open BLOCKING child terminates
    awaiting_upstream  auto-starts when every peer named in `after:` has completed
    awaiting_human     the only type that pages the owner

`spawned_from` is vertical, `after` is horizontal — two edges, deliberately not one.
Pure functions over item dicts: no IO, no spine, so callers own the writes.
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
    """The child's relation word — `blocking` · `parallel` · `spawn`, or `child` when absent."""
    return str((spawned_from(child) or {}).get("relation") or "child")


def is_terminal(item: dict) -> bool:
    """Terminal = status done (outcome completed/abandoned/superseded stamped alongside)."""
    return str(item.get("status")) == TERMINAL_STATUS or bool(item.get("done_at"))


def children_of(items: list[dict], parent_id: str,
                relations: tuple[str, ...] = CHILD_RELATIONS) -> list[dict]:
    """Items spawned from `parent_id` with a relation in `relations` (default: blocking + parallel)."""
    out = []
    for it in items:
        sf = spawned_from(it)
        if sf and str(sf.get("item")) == str(parent_id) and sf.get("relation") in relations:
            out.append(it)
    return out


def open_children(items: list[dict], parent_id: str) -> list[dict]:
    """Non-terminal children (blocking + parallel) — the set that gates the parent's completion."""
    return [c for c in children_of(items, parent_id) if not is_terminal(c)]


def holding_children(items: list[dict], parent: dict, *, at_close: bool | None = None) -> list[dict]:
    """The open children that currently HOLD `parent` — the one answer the close criterion and
        the auto-resume both read.

        Before the final phase only `blocking` children hold; at the final phase every open child does.
        `at_close` states that question directly rather than deriving it from the parent's phase, so a
        drilldown previewing the close gate asks the close question."""
    if at_close is None:
        try:
            from .kind_profiles import is_final_phase
            at_close = is_final_phase(parent.get("kind"), parent.get("phase") or "triage")
        except (KeyError, ImportError):
            # Unknown kind or hand-edited yaml: default STRICT, so a bad row cannot clear a
            # parent early.
            at_close = True
    rel = CHILD_RELATIONS if at_close else ("blocking",)
    return [c for c in children_of(items, str(parent.get("id")), relations=rel)
            if not is_terminal(c)]


def children_terminal(items: list[dict], parent_id: str) -> tuple[bool, list[str]]:
    """(all children terminal?, open child ids) — a thin read of `holding_children`."""
    parent = next((it for it in items if str(it.get("id")) == str(parent_id)), None)
    opens = holding_children(items, parent if parent is not None else {"id": parent_id},
                             at_close=True)
    return (not opens, [c.get("id") for c in opens])


# --- peer sequencing: the `after:` edge -------------------------------------------------------

def upstream_ids(item: dict) -> list[str]:
    """The peer ids this item must follow (`after:`). Absent or malformed reads as no constraint."""
    raw = item.get("after")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(x) for x in raw if x]


def upstream_state(items: list[dict], item: dict) -> tuple[list[str], list[str]]:
    """`(open, failed)` upstream ids — the two reasons this item may not start yet.

        **open** is a wait and releases automatically. **failed** is terminal but not `completed`, so
        nothing happens on its own and the caller pages the owner. A named id with no matching item
        counts as satisfied — otherwise a hard delete wedges the downstream forever."""
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
    """After `upstream_id` went terminal: `(release, page)` — peers that may now start, and peers
        whose wait just became pointless."""
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
    """The parent id to auto-resume when `child` goes terminal: it sits at `awaiting_child` and
        nothing still holds it. Both real relations count — only the rule that decided the hold can
        decide the release."""
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
