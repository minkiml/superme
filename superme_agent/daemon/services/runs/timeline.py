"""The item's trail, rendered for a reader rather than for a machine."""

from datetime import datetime

from ...app_state import spine as _spine

def build_item_timeline(context_id: str, item_id: str) -> dict:
    """Every run this item has had, oldest-first, each tagged with its phase, role and model and
    carrying its ordered turn events.

    Chronological across phases, so the whole item reads as one conversation. Read-only mirror of
    the run/run_event tables."""
    runs = _spine.runs_for_item(context_id, item_id)
    out = []
    for r in runs:
        rid = r.get("id")
        out.append({
            "run_id": rid,
            "phase": r.get("phase"),
            "feature": r.get("feature"),
            "model": r.get("model"),
            "status": r.get("status"),
            "started_at": r.get("started_at"),
            "events": _spine.events_for_run(rid) if rid else [],
        })
    return {"item_id": str(item_id), "runs": out}


def _render_execution_md(context_id: str, item_id: str, item: dict) -> str:
    """Snapshot a work-item's execution trace to Markdown, so the item folder keeps its own copy.
    Chronological, oldest run first."""
    # The call trail only — prompt and reply rows belong to the conversation.
    arts = [e for e in _spine.events_for_item(context_id, item_id)
            if e.get("kind") not in ("prompt", "reply")]
    runs = {r["id"]: r for r in _spine.run_history(context_id)}
    title = item.get("title") or item_id
    out = [f"# Execution trace — {title}", "",
           f"Work-item `{item_id}` · snapshot taken {datetime.now().date().isoformat()}", ""]
    if not arts:
        return "\n".join(out + ["_No tool / sub-agent / skill calls were recorded._", ""])
    # `arts` is newest-run-first; collect run ids in that order, then emit oldest-first.
    order: list = []
    for a in arts:
        if a["run_id"] not in order:
            order.append(a["run_id"])
    for rid in reversed(order):
        calls = [a for a in arts if a["run_id"] == rid]
        r = runs.get(rid) or {}
        bits = [str(r[k]) for k in ("feature", "model") if r.get(k)]
        if r.get("tokens"):
            bits.append(f"{r['tokens']} tok")
        head = f"## Run #{rid}" if rid else "## Unattached"
        head += (" · " + " · ".join(bits) if bits else "") + f" · {len(calls)} call{'s' if len(calls) != 1 else ''}"
        out += [head, ""]
        for a in calls:
            d = f" — {a['description']}" if a.get("description") else ""
            # Indent a sub-agent's calls under its spawn, so the snapshot keeps the shape the live
            # trace shows.
            indent = "    " if a.get("parent_tool_id") else ""
            out.append(f"{indent}{a['seq']}. **{a['name']}**{d}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
