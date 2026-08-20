"""Inbox → work-item push flow — the ONE transaction both the FE push route and the agent-side
auto-push (blocking/parallel branch-offs) run (workspace-workflow S3, D3/D5).

Uniform routing: EVERY work-item passes through the inbox (one trace, no two classes of items);
the only variable is who pushes — the owner (FE route; `spawn` waits for this) or the system
(auto-push, immediately after a blocking/parallel branch-off is filed).

The push:
1. creates the work-item at triage/active, carrying the row's `spawned_from` provenance + the
   originating `inbox_id`;
2. MOVES the inbox content folder (`<dev_root>/inbox/<id>/`, the handoff brief + any extra files)
   into the item as `preliminary/` — a move, never a delete: the content survives in the item,
   the inbox ROW stays as trace (status=pushed, routed_to = the item). `preliminary/` is
   immutable after arrival (provenance; corrections go in plan.md);
3. for a `blocking` child: pauses the parent (`awaiting_child` — the D2 typed-awaiting router
   auto-resumes it when the last open blocking child closes);
4. logs the push (+ pause) events.
"""

import logging
import shutil
from pathlib import Path

from .dev_knowledge import DevKnowledgeService

log = logging.getLogger("superme-agent")


def inbox_content_dir(dev_root: Path, inbox_id: int) -> Path:
    """The inbox item's content folder (handoff brief + extras): `<dev_root>/inbox/<id>/`."""
    return Path(dev_root) / "inbox" / str(inbox_id)


def brief_state(dev_root: Path, item_id: str) -> list[str]:
    """The handoff brief's issues as the item now holds it, or [] when it is fine (D5's gate-time
    self-check). An ABSENT brief is one issue, not zero: a bare FE capture is legal, and the point
    of saying so is that the item's whole cold-start context is then its one-line row."""
    from . import artifacts as _arts
    p = Path(dev_root) / "work-items" / item_id / "preliminary" / "handoff-brief.md"
    if not p.exists():
        return ["no handoff brief — this item's only context is its row text"]
    return _arts.self_check(Path(dev_root) / "work-items" / item_id, "handoff-brief", path=p)


def push_inbox_item(store, dev: DevKnowledgeService, dev_root: Path, row: dict, *,
                    context_id: str, actor: str = "owner") -> dict:
    """Run the full push transaction for an OPEN inbox row. Returns the created work-item dict
    ({id, folder, brief_issues}). Raises ValueError on an already-pushed row."""
    if row.get("status") == "pushed":
        raise ValueError("inbox item already pushed")
    inbox_id = row["id"]
    # Idempotency: a crash between create_work_item and push_inbox leaves the row `open` with an
    # orphan item already carrying this inbox_id — a retry must ADOPT that item, never mint a
    # duplicate over the same provenance.
    existing = next((it for it in dev.read_all(dev_root)["work_items"]
                     if str(it.get("inbox_id") or "") == str(inbox_id)), None)
    if existing:
        wi = {"id": str(existing["id"]), "folder": str(existing["id"])}
        log.warning("push retry adopted existing work-item %s for inbox #%s", wi["id"], inbox_id)
    else:
        wi = dev.create_work_item(
            dev_root,
            title=row.get("title") or row.get("text") or "",
            description=row.get("text") or "",
            # The row's PROPOSED kind becomes the item's kind; an unproposed row falls to
            # `implementation`, which is what every push did before the field existed. The two are
            # kept apart on the item — `kind` is what it runs as, `proposed_kind` is what was
            # claimed — because triage has to be able to tell a fall-back from a judgment.
            kind=row.get("work_kind") or "implementation",
            proposed_kind=row.get("work_kind"),
            spawned_from=row.get("spawned_from"),
            inbox_id=inbox_id,
            # The third capture-time policy: whether the item drives its own gates. Set at BIRTH
            # rather than after, so an autopilot item never sits one manual click short of moving.
            autopilot=bool(row.get("autopilot")),
        )
        # F3: the push button LOCKS IN the capture-time run config — model/effort chosen on the
        # inbox row become the work-item's immutable defaults (no per-work-item override after).
        # NULL stays NULL → the item inherits the repo/system default via effective_model.
        if row.get("model"):
            dev.set_work_item_model(dev_root, wi["id"], row["model"])
        if row.get("effort"):
            dev.set_work_item_effort(dev_root, wi["id"], row["effort"])
        # The two roles that run on their own tier travel the same way — chosen on the row, frozen
        # onto the item at push. Unset stays unset, so the item falls through to the repo's vet tier
        # / the system deputy tier rather than to its own model.
        for role in dev.ROLE_FIELDS:
            if row.get(f"{role}_model"):
                dev.set_work_item_role_model(dev_root, wi["id"], role, row[f"{role}_model"])
            if row.get(f"{role}_effort"):
                dev.set_work_item_role_effort(dev_root, wi["id"], role, row[f"{role}_effort"])
    # Content folder → the item's preliminary/ (move, not delete; absent for bare FE captures).
    src = inbox_content_dir(dev_root, inbox_id)
    moved = False
    if src.is_dir():
        dst = Path(dev_root) / "work-items" / wi["id"] / "preliminary"
        if dst.exists():
            # A prior partial push already moved it — moving again would NEST src inside dst.
            log.warning("preliminary/ already present for %s; leaving inbox folder in place", wi["id"])
        else:
            shutil.move(str(src), str(dst))
            moved = True
    store.push_inbox(inbox_id, wi["id"])
    # D5's consuming check, at the last moment it can change anything. The brief is EDITABLE while
    # the row sits in the inbox and immutable the instant it lands in `preliminary/` — so push is
    # where "this item starts cold" is still actionable (cancel, fill, push again) and everywhere
    # later it is only a complaint. Reported, never blocking: D5 makes a bare-title capture legal
    # on purpose, and triage backfills nothing it cannot write.
    brief_issues = brief_state(dev_root, wi["id"])
    store.log_event(
        context_id, "inbox.push",
        f"Pushed to workspace: {row.get('title') or wi['id']}"
        + (f" — brief: {brief_issues[0]}" if brief_issues else ""),
        item_id=wi["id"], actor=actor,
        meta={"inbox_id": inbox_id, "preliminary": moved,
              **({"brief_issues": brief_issues} if brief_issues else {}),
              **({"relation": row["spawned_from"]["relation"],
                  "parent": row["spawned_from"]["item"]} if row.get("spawned_from") else {})},
    )
    wi["brief_issues"] = brief_issues
    # A blocking child pauses its parent until the child family closes (D3).
    sf = row.get("spawned_from") or {}
    if sf.get("relation") == "blocking":
        parent_id = str(sf.get("item"))
        parent = dev.read_work_item(dev_root, parent_id)
        if parent and str(parent.get("status")) not in ("done",):
            dev.set_work_item_status(dev_root, parent_id, "awaiting_child")
            store.log_event(
                context_id, "item.pause",
                f"Awaiting blocking child {wi['id']}",
                item_id=parent_id, actor="daemon", meta={"child": wi["id"]},
            )
    return wi


def brief_location(dev_root: Path, row: dict) -> tuple[Path, bool]:
    """Where this row's handoff brief lives, and whether it is still writable.

    An OPEN row keeps its brief in the inbox content folder and it is editable there: the brief is
    the whole cold-start context of the item this row becomes, and push is the last moment that
    changing it is free. A PUSHED row's brief has moved into the item's `preliminary/`, which is
    provenance from the instant it arrives — read-only, and a correction belongs in plan.md.

    The path is returned whether or not the file exists; an absent brief is a legal state (a bare
    capture), not an error, and on an open row it is the state the owner can still fix.
    """
    dev_root = Path(dev_root)
    if row.get("status") == "pushed" and row.get("routed_to"):
        return (dev_root / "work-items" / str(row["routed_to"]) / "preliminary"
                / "handoff-brief.md"), False
    return inbox_content_dir(dev_root, row["id"]) / "handoff-brief.md", True


def read_brief(dev_root: Path, row: dict) -> tuple[str | None, bool]:
    """This row's handoff brief as raw markdown (frontmatter kept — it is part of what the agent
    reading it sees), plus whether it may still be written. None = no brief filed."""
    path, editable = brief_location(dev_root, row)
    return (path.read_text() if path.is_file() else None), editable


def write_brief(dev_root: Path, row: dict, content: str) -> Path:
    """Overwrite this row's handoff brief with `content`, creating it if the row never had one.

    An OVERWRITE, unlike the agent-side `write_handoff_brief`, which appends: an agent adding to a
    brief cannot see what it would clobber, while the owner is editing the file's own text in front
    of them. Raises ValueError once the row is pushed — its brief is provenance by then.
    """
    path, editable = brief_location(dev_root, row)
    if not editable:
        raise ValueError("this row is pushed — its brief is the item's provenance and is read-only")
    from . import artifacts as _arts
    path.parent.mkdir(parents=True, exist_ok=True)
    _arts._atomic_write(path, content)
    return path
