"""Inbox rows — filing one, appending to one, pushing one into a work-item."""

from typing import Annotated, Literal, Required, TypedDict

from .render import _err, _ok, _qid, _s

# The inbox is DB-backed, so it gets a tool rather than native Read, scoped server-side to this
# context.

class InboxArgs(TypedDict, total=False):
    status: Annotated[str, "filter by status (e.g. 'open'); omit for all"]
    limit: Annotated[int, "max rows (default 50, cap 200)"]


def _fmt_inbox(rows: list[dict]) -> str:
    """Render inbox rows compactly (open first, newest first — the store's order)."""
    if not rows:
        return "(inbox empty)"
    out = ["# read_inbox · open-first · record: inbox:<id> · <status> · <kind> [· tag · →routed · from …] · <title>"]
    if any((r.get("kind") or "") == "note" for r in rows):
        out.append("# `note` rows are the OWNER'S OWN — never pushed, never work. They are here to "
                   "be discussed: read one in full and take the conversation from there.")
    for r in rows:
        head = f"{_qid('inbox', r['id'])} · {r.get('status') or 'open'} · {r.get('kind') or 'note'}"
        if r.get("tag"):
            head += f" · {r['tag']}"
        if r.get("routed_to"):
            head += f" → {_qid('item', r['routed_to'])}"
        origin = r.get("origin")
        if origin:
            head += f" · from {', '.join(origin) if isinstance(origin, list) else origin}"
        title = r.get("title") or (r.get("text") or "").strip().replace("\n", " ")
        out.append(f"{head} · {title[:200]}")
    return "\n".join(out)


def _list_inbox(*, store, context_id, **_):
    async def list_inbox(args: dict) -> dict:
        try:
            rows = store.list_inbox(context_id)
        except Exception as e:
            return _err(f"Could not read the inbox: {e}")
        status = _s(args, "status")
        if status:
            rows = [r for r in rows if (r.get("status") or "open") == status]
        rows = rows[:max(1, min(int(args.get("limit") or 50), 200))]
        return _ok(_fmt_inbox(rows))
    return list_inbox


# The one write a general session may make. It can touch nothing else, so it cannot smuggle
# implementation.

class CreateInboxItemArgs(TypedDict, total=False):
    title: Required[Annotated[str, ("the ticket's headline — a few words naming it, under 60 "
                                    "characters, no closing period. Not the ask; that is `body`")]]
    body: Required[Annotated[str, ("the item content — a crisp synthesis of intent + the on-point "
                                   "context/decisions and any pointers or references (work-item ids, "
                                   "paths, doc names). NOT a raw transcript dump")]]
    work_kind: Required[Annotated[Literal["implementation", "research"],
                         ("which machinery this becomes when pushed: `implementation` changes code "
                          "(plan → build → vet → review, on its own branch), `research` answers a "
                          "question (investigate → findings, nothing merged). Pick by what the item "
                          "DELIVERS — one whose output is a decision, a report or an answer is "
                          "research even when code prompted it. If you cannot name which, this is "
                          "not an inbox item and must not be filed. Triage re-reads it, so a wrong "
                          "pick costs a question, not a wrong pipeline")]]
    spawned_from_item: Annotated[str, ("branch-off ONLY: the parent work-item id this spawns from "
                                       "(requires `relation`)")]
    relation: Annotated[Literal["blocking", "parallel", "spawn"],
                        ("branch-off type: blocking (parent can't proceed without it — auto-pushed, "
                         "pauses the parent) · parallel (child work, auto-pushed, gates the parent's "
                         "completion) · spawn (independent follow-up — waits in the inbox for the "
                         "owner's push)")]
    background: Annotated[str, "handoff brief: the problem/story — why this was raised"]
    discussion: Annotated[str, "handoff brief: what was discussed/concluded so far"]
    direction: Annotated[str, "handoff brief: high-level direction or options, with leanings — NO plans/implementation detail"]
    constraints: Annotated[str, "handoff brief: constraints, tried-but-failed, out-of-scope"]


def _wk_note(work_kind: str) -> str:
    """Say the filed kind back in the tool's own return. The choice is ANNOUNCED, not merely stored —
    an unsaid choice is one nobody can argue with."""
    return (f" Filed as a {work_kind} item (triage confirms it)." if work_kind else
            " No work kind filed — triage decides it alone.")


_BRIEF_FIELDS = ("background", "discussion", "direction", "constraints")


def _brief_nudge(args: dict, *, spawned: bool, repairable: bool) -> str:
    """Name the brief slots this call left empty.

    The four fields ARE the cold-start context, thrown away at the only moment they were free."""
    missing = [f for f in _BRIEF_FIELDS if not _s(args, f)]
    if not missing or (not spawned and len(missing) < len(_BRIEF_FIELDS)):
        return ""
    head = (f" NOTE: the handoff brief's {', '.join(missing)} "
            f"{'slot is' if len(missing) == 1 else 'slots are'} EMPTY")
    if not repairable:
        return head + (" — the brief has already moved into the item's read-only `preliminary/`, "
                       "so it cannot be amended. Say in your reply what is missing from it.")
    if spawned:
        return head + (" — and this is a branch-off, so the parent item holds what belongs there. "
                       "Fill them now with `append_inbox_item` (it mirrors onto the brief).")
    return head + (". If this discussion holds real context, capture it now with "
                   "`append_inbox_item` (it mirrors onto the brief) — the future triage session "
                   "cold-starts from this brief and shouldn't start blind.")


def _create_inbox_item(*, store, context_id, dev_root=None, fire_triage=None, **_):
    async def create_inbox_item(args: dict) -> dict:
        from pathlib import Path
        from ....core import artifacts as _arts
        from ....core import inbox_flow as _flow
        from ....core.dev_knowledge import DevKnowledgeService
        from ....core.vocab.titles import check_title
        title, body = _s(args, "title"), _s(args, "body")
        if not body:
            return _err("An inbox item needs both `title` and `body` (a crisp synthesis, not a dump).")
        if (bad := check_title(title, description=body)):
            return _err(bad)
        parent, relation = _s(args, "spawned_from_item"), _s(args, "relation")
        if bool(parent) != bool(relation):
            return _err("A branch-off needs BOTH `spawned_from_item` and `relation` (or neither).")
        spawned_from = None
        dev = DevKnowledgeService()
        if parent:
            if relation not in ("blocking", "parallel", "spawn"):
                return _err("`relation` must be blocking | parallel | spawn.")
            parent_item = dev.read_work_item(Path(dev_root), parent) if dev_root else None
            if not parent_item:
                return _err(f"Parent work-item {parent!r} not found — a branch-off must name a real item.")
            # A kind with no worktree has no branch to hand over, and auto-pushing would start
            # work the owner never chose.
            if relation in ("blocking", "parallel"):
                from ....core.vocab.kind_profiles import get_profile
                if not get_profile(parent_item.get("kind")).worktree:
                    return _err(
                        f"A {parent_item.get('kind')!r} item has no worktree, so it cannot carry a "
                        f"{relation!r} branch-off (those auto-push and branch off git). Use "
                        "`relation: \"spawn\"` — it waits in the inbox for the owner's push.")
            spawned_from = {"item": parent, "relation": relation}
        # An inbox item BECOMES a work item, and `work_kind` is the whole test. Checked after the
        # shape checks.
        if (wk := _s(args, "work_kind")) not in ("implementation", "research"):
            return _err(
                "`work_kind` is required and must be `implementation` or `research`. An inbox item "
                "is a thing that becomes a WORK ITEM when pushed, so if you cannot name which of "
                "the two this becomes, it is not an inbox item. A settled decision, a ruling you "
                "want remembered, or anything with nothing to build belongs in the record that "
                "holds it — not on the owner's board.")
        try:
            # `note` is the owner's own thought, never pushed, so an agent has no way to mint one.
            row = store.add_inbox(
                context_id, body, kind="item",
                title=title, origin=["agent"], spawned_from=spawned_from, work_kind=wk,
            )
        except Exception as e:
            return _err(f"Could not create the inbox item: {e}")
        # Scaffolded, with prose slots filled from the args while context is hot. High-level only.
        brief = None
        if dev_root:
            brief = _arts.write_handoff_brief(
                _flow.inbox_content_dir(Path(dev_root), row["id"]), title,
                background=_s(args, "background"), discussion=_s(args, "discussion"),
                direction=_s(args, "direction"), constraints=_s(args, "constraints"),
            )
        # Blocking and parallel children route through the inbox for the trace but push
        # immediately.
        if spawned_from and relation in ("blocking", "parallel") and dev_root:
            try:
                wi = _flow.push_inbox_item(store, dev, Path(dev_root), row,
                                           context_id=context_id, actor="agent")
            except Exception as e:
                return _err(f"Inbox item #{row['id']} created but auto-push failed: {e}")
            # Without the first kick the child lands at triage with no run behind it and never
            # moves.
            triaged = False
            if fire_triage:
                try:
                    triaged = bool(fire_triage(wi["id"]))
                except Exception:
                    triaged = False
            paused = " Parent paused (awaiting_child) until it closes." if relation == "blocking" else ""
            kick = " Triage is running on it." if triaged else " It rests at triage for a triage pass."
            # Children that cold-start immediately need the brief warning too.
            return _ok(f"Branch-off filed and AUTO-PUSHED: inbox #{row['id']} → work-item "
                       f"{wi['id']} ({relation} child of {parent}; brief moved to its "
                       f"preliminary/).{paused}{kick}{_wk_note(_s(args, 'work_kind'))}"
                       + (_brief_nudge(args, spawned=True, repairable=False) if brief else ""))
        where = f" Handoff brief at {brief}." if brief else ""
        nudge = _brief_nudge(args, spawned=bool(parent), repairable=True) if brief else ""
        return _ok(f"Created inbox item #{row['id']} — \"{title}\".{where}{nudge}"
                   f"{_wk_note(_s(args, 'work_kind'))} "
                   f"It's in the Inbox for the owner to review and push into a work-item.")
    return create_inbox_item


# Start an item the owner points at. Filing a ticket and starting one are different decisions.

class PushInboxItemArgs(TypedDict, total=False):
    item_id: Required[Annotated[int, ("the OPEN inbox item's numeric id — the number in the "
                                      "`inbox:<id>` that read_inbox prints, without the prefix")]]


def _push_inbox_item(*, store, context_id, dev_root=None, fire_triage=None,
                     bound_item_id=None, **_):
    async def push_inbox_item(args: dict) -> dict:
        from pathlib import Path
        from ....core import inbox_flow as _flow
        from ....core.dev_knowledge import DevKnowledgeService
        # A phase session operates its own item, never the board; its way to spin work off is the
        # branch-off.
        if bound_item_id:
            return _err(
                "push_inbox_item is not available inside a work-item session — a phase session works "
                "its own item, it doesn't start others. To spin off work from here, file a branch-off "
                "(`create_inbox_item` with `spawned_from_item` + `relation`): blocking/parallel "
                "children auto-push with the dependency edge recorded.")
        if not dev_root:
            return _err("This context has no dev-knowledge home, so there is no workspace to push into.")
        try:
            item_id = int(args["item_id"])
        except (KeyError, TypeError, ValueError):
            return _err("`item_id` must be the inbox item's numeric id (12 for `inbox:12`).")
        row = store.get_inbox(item_id)
        if row is None or row.get("context_id") != context_id:
            return _err(f"No {_qid('inbox', item_id)} in this project's inbox — `read_inbox` lists "
                        "what's actually there.")
        if row.get("status") == "pushed":
            routed = row.get("routed_to")
            return _err(f"{_qid('inbox', item_id)} was already pushed"
                        + (f" → {_qid('item', routed)}" if routed else "")
                        + ". A pushed row is trace; it can't be pushed twice.")
        try:
            wi = _flow.push_inbox_item(store, DevKnowledgeService(), Path(dev_root), row,
                                       context_id=context_id, actor="agent")
        except Exception as e:
            return _err(f"Could not push {_qid('inbox', item_id)}: {e}")
        # The same first kick the owner's Push button fires; without it the item never moves.
        triaged = False
        if fire_triage:
            try:
                triaged = bool(fire_triage(wi["id"]))
            except Exception:
                triaged = False
        title = (row.get("title") or row.get("text") or "")[:80]
        kick = ("Triage is running on it now." if triaged else
                "It rests at triage until a triage run starts.")
        # The brief is immutable from here, so this is the last place saying so can mean anything.
        cold = (f" NOTE: {'; '.join(wi.get('brief_issues') or [])} — triage starts colder than it "
                "should." if wi.get("brief_issues") else "")
        return _ok(f"Pushed {_qid('inbox', item_id)} → {_qid('item', wi['id'])} — \"{title}\". {kick}{cold}")
    return push_inbox_item


# Mint work-items directly on autopilot with their edges wired. `key` is a batch-local handle for
# `after` edges.

class ItemizeItemArgs(TypedDict, total=False):
    key: Required[Annotated[str, ("a batch-LOCAL handle for this item, used only to wire `after` "
                                  "edges within this launch (e.g. the deliverable id `d-cli`) — the "
                                  "real work-item id is minted on creation")]]
    title: Required[Annotated[str, ("the card label — a few words naming the change, under 60 "
                                    "characters, no closing period. Not the ask; that is "
                                    "`description`")]]
    description: Annotated[str, ("the intent — what value this item delivers. Crisp; NOT a plan "
                                 "(the item's own plan phase does that)")]
    after: Annotated[list[str], ("keys of items in THIS batch (or ids of existing work-items) that "
                                 "must finish before this one starts — from the PRD deliverables' "
                                 "`Needs` edges. Omit for items that can start immediately")]
    kind: Required[Annotated[Literal["implementation", "research"],
                             ("which machinery this item runs: `implementation` changes code (plan "
                              "→ build → vet → review, on its own branch), `research` answers a "
                              "question (investigate → findings, nothing merged). Pick by what the "
                              "item DELIVERS — one whose output is a decision, a report or an "
                              "answer is research even when code prompted it")]]


class ItemizeAndLaunchArgs(TypedDict, total=False):
    items: Required[Annotated[list[ItemizeItemArgs], ("the launch cohort — one entry per work-item. "
                                                      "Created together on autopilot, wired by their "
                                                      "`after` edges, stamped with one shared cohort id")]]


def _itemize_and_launch(*, store, context_id, **_):
    async def itemize_and_launch(args: dict) -> dict:
        items = args.get("items")
        if not isinstance(items, list) or not items:
            return _err("itemize_and_launch needs a non-empty `items` list (the cohort to launch).")
        from ....daemon.services import launch   # lazy: harness must not import daemon at load time
        try:
            r = launch.launch_cohort(context_id, items)
        except (ValueError, RuntimeError) as e:
            return _err(f"Could not launch the cohort: {e}")
        head = [f"# launched cohort {r['cohort']} · {len(r['created'])} item(s) on autopilot",
                f"# {r['launched']} started now · {len(r['waiting'])} waiting on upstreams · "
                f"record: <id> · <status> · <title> [· after <ids>]"]
        rows = []
        for c in r["created"]:
            edge = f" · after {', '.join(c['after'])}" if c["after"] else ""
            rows.append(f"{c['id']} · {c['status']} · {c['title']}{edge}")
        return _ok("\n".join(head + rows))
    return itemize_and_launch


class AppendInboxItemArgs(TypedDict, total=False):
    item_id: Required[Annotated[int, "the existing inbox item's id to augment"]]
    addition: Required[Annotated[str, ("the NEW, on-point content from this discussion to append — "
                                       "what the existing item doesn't already cover. Never a rewrite; "
                                       "the existing text is preserved and this is added under it")]]
    brief_field: Annotated[Literal["background", "discussion", "direction", "constraints"],
                           ("which handoff-brief section this addition belongs under (default "
                            "`discussion`). Name it when you are filling a slot the original "
                            "filing left empty — an addition mirrored into the wrong section is "
                            "as good as lost to the triage session that reads by section")]


def _append_inbox_item(*, store, context_id, dev_root=None, **_):
    async def append_inbox_item(args: dict) -> dict:
        addition = _s(args, "addition")
        try:
            item_id = int(args.get("item_id"))
        except (TypeError, ValueError):
            return _err("Pass a numeric `item_id` (the existing inbox item to append to).")
        if not addition:
            return _err("Nothing to append — pass `addition` (the new content this discussion adds).")
        try:
            row = store.append_inbox(item_id, addition, origin_add="agent")
        except Exception as e:
            return _err(f"Could not append to the inbox item: {e}")
        if row is None:
            return _err(f"No inbox item #{item_id} to append to.")
        # Append, never rewrite. The section is the caller's to name — the triage session reads
        # the brief BY SECTION.
        field = _s(args, "brief_field") or "discussion"
        if field not in _BRIEF_FIELDS:
            return _err(f"`brief_field` must be one of {', '.join(_BRIEF_FIELDS)}.")
        if dev_root:
            from pathlib import Path
            from ....core import artifacts as _arts
            from ....core.inbox_flow import inbox_content_dir
            folder = inbox_content_dir(Path(dev_root), row["id"])
            if (folder / "handoff-brief.md").exists():
                _arts.write_handoff_brief(folder, row.get("title") or "", **{field: addition})
        return _ok(f"Appended to inbox item #{row['id']} — \"{row.get('title') or ''}\" "
                   f"(existing content untouched; origin now {', '.join(row.get('origin') or [])}).")
    return append_inbox_item
