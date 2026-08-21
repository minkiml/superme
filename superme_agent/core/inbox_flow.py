"""Inbox → work-item push — the ONE transaction the FE route and agent-side auto-push both run.

Every work-item passes through the inbox, so there is one trace and no second class of item. The
only variable is who pushes: the owner, or the system after a branch-off is filed.

The push creates the item at triage/active, MOVES the inbox content folder in as `preliminary/`
(a move, never a delete — the row stays as trace), pauses the parent for a blocking child, and
logs it.
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
    """The handoff brief's issues as the item now holds it, or [] when it is fine. An ABSENT
        brief is one issue, not zero: a bare capture is legal, and its whole cold-start context is
        then the one-line row."""
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
    # Idempotency: a crash mid-push leaves an orphan item carrying this inbox_id. Adopt it.
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
            # `kind` is what it runs as, `proposed_kind` what was claimed — triage must tell a
            # fall-back from a judgment.
            kind=row.get("work_kind") or "implementation",
            proposed_kind=row.get("work_kind"),
            spawned_from=row.get("spawned_from"),
            inbox_id=inbox_id,
            # Set at BIRTH, so an autopilot item never sits one manual click short of moving.
            autopilot=bool(row.get("autopilot")),
        )
        # Push LOCKS IN the capture-time run config. NULL stays NULL, so the item inherits.
        if row.get("model"):
            dev.set_work_item_model(dev_root, wi["id"], row["model"])
        if row.get("effort"):
            dev.set_work_item_effort(dev_root, wi["id"], row["effort"])
        # Unset stays unset, so vet and deputy fall through to their own tiers, not the item's.
        for role in dev.ROLE_FIELDS:
            if row.get(f"{role}_model"):
                dev.set_work_item_role_model(dev_root, wi["id"], role, row[f"{role}_model"])
            if row.get(f"{role}_effort"):
                dev.set_work_item_role_effort(dev_root, wi["id"], role, row[f"{role}_effort"])
    # Move, not delete; absent for bare captures.
    src = inbox_content_dir(dev_root, inbox_id)
    moved = False
    if src.is_dir():
        dst = Path(dev_root) / "work-items" / wi["id"] / "preliminary"
        if dst.exists():
            # A prior partial push moved it; moving again would nest src inside dst.
            log.warning("preliminary/ already present for %s; leaving inbox folder in place", wi["id"])
        else:
            shutil.move(str(src), str(dst))
            moved = True
    store.push_inbox(inbox_id, wi["id"])
    # The last moment "this item starts cold" is still actionable. Reported, never blocking.
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
    # A blocking child pauses its parent until the child family closes.
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

        An OPEN row's brief is editable in the inbox folder — push is the last moment changing it is
        free. A PUSHED row's has moved into `preliminary/` and is provenance. The path is returned
        whether or not the file exists."""
    dev_root = Path(dev_root)
    if row.get("status") == "pushed" and row.get("routed_to"):
        return (dev_root / "work-items" / str(row["routed_to"]) / "preliminary"
                / "handoff-brief.md"), False
    return inbox_content_dir(dev_root, row["id"]) / "handoff-brief.md", True


def read_brief(dev_root: Path, row: dict) -> tuple[str | None, bool]:
    """This row's handoff brief as raw markdown (frontmatter kept — the agent sees it too), plus
        whether it may still be written."""
    path, editable = brief_location(dev_root, row)
    return (path.read_text() if path.is_file() else None), editable


def write_brief(dev_root: Path, row: dict, content: str) -> Path:
    """Overwrite this row's handoff brief, creating it if the row never had one.

        An overwrite, unlike the agent-side append: the owner is editing text in front of them, an
        agent cannot see what it would clobber. Raises once the row is pushed."""
    path, editable = brief_location(dev_root, row)
    if not editable:
        raise ValueError("this row is pushed — its brief is the item's provenance and is read-only")
    from . import artifacts as _arts
    path.parent.mkdir(parents=True, exist_ok=True)
    _arts._atomic_write(path, content)
    return path
