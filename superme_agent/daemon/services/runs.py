"""Run lifecycle — the orchestrator-owned helpers that drive a work-item's headless runs.

Open/close spine run rows, accumulate live telemetry, map tool-use to call-trail artifacts, drive
the headless /plan turn, and snapshot the execution trace on completion. The work-item routes (in
server.py / the work_items router) call these; they own run-state, the route owns the HTTP shape.

Imports singletons from `app_state` (never from server.py) so there's no import cycle.
"""

import logging
from datetime import datetime
from pathlib import Path

from ..app_state import agent as _agent, dev as _dev, dev_store as _dev_store, \
    spine as _spine, sessions as _sessions
from ..deps import cache_slash as _cache_slash
from ...core import Init, Usage, Result, Status, scoped_writes_approve, deny_all
from ...core.models import MODEL_TIERS

log = logging.getLogger("superme-agent")

# Work-items default to the latest Sonnet (concrete id — the `sonnet` alias lags; see core/models.py).
DEFAULT_RUN_MODEL = MODEL_TIERS["sonnet"]


def _set_status(ctx, item_id: str, status: str) -> None:
    """Set a work-item's run-state status (orchestrator-owned). Best-effort; logs on failure."""
    if not (item_id and ctx.internal_root):
        return
    try:
        _dev.set_work_item_status(ctx.internal_root / "dev", item_id, status)
    except Exception:
        log.exception("could not set status %s on %s", status, item_id)


def _begin_run(ctx, context_id: str, item_id: str, kind: str = "plan",
               model: str | None = None) -> bool:
    """Mark an item as running, atomically: open a spine run row (status=running) ONLY if the item
    isn't already running, then flip the work-item to in_progress and log the start. The running row
    IS the live state (no in-memory mirror) and IS the per-item run-lock. Returns False without any
    side effect if a run was already in flight (the caller turns that into a 409), so the lock can't
    be lost to a check-then-start race (R5)."""
    run_id = _spine.start_item_run(context_id, mode=ctx.mode, feature=kind,
                                   item_id=item_id, model=model)
    if run_id is None:
        return False  # already running — no status flip, no event
    _set_status(ctx, item_id, "in_progress")
    # Run start — item-scoped. PRD §4.9.
    _dev_store.log_event(context_id, f"{kind}.start", f"Started {kind} run",
                         item_id=item_id, actor="daemon", meta={"model": model})
    return True


def _bump_run_tokens(context_id: str, item_id: str, total_tokens: int,
                     context_pct: int | None = None) -> None:
    """Update an item's LIVE in-flight estimate (legacy token counter + context fill) from a Usage
    snapshot. The authoritative per-type accounting is written once at finish from the whole-turn
    Result usage (see _end_run) — per-step Usage events are cumulative-for-the-turn snapshots."""
    _spine.bump_item_run(context_id, item_id, add_tokens=total_tokens, ctx_pct=context_pct)


def _end_run(ctx, context_id: str, item_id: str, tokens: int | None,
             status: str = "waiting", usage: dict | None = None) -> None:
    """Close out a run: finalize its spine row (keeping the accumulated live token sum, or the
    passed Result aggregate as a fallback) and set the work-item's resting status (the agent
    stopped → the owner's move). `kind` is recovered from the running row for the end event.
    `usage` is the whole-turn final dict — the typed-column fallback if no per-step Usage arrived."""
    info = _spine.live_run(context_id, item_id)
    kind = (info or {}).get("feature", "plan")
    _spine.finish_item_run(context_id, item_id, fallback_tokens=tokens, usage=usage)
    # The persisted figure prefers the accumulated live sum (set on the row by _bump_run_tokens);
    # `tokens` (the Result aggregate) only applied if no Usage steps arrived. Read it back to log.
    total = (info or {}).get("tokens") or tokens or 0
    _set_status(ctx, item_id, status)
    # Run end — item-scoped, with the final token total. PRD §4.9.
    _dev_store.log_event(context_id, f"{kind}.end", f"Finished {kind} run · Σ {total} tok",
                         item_id=item_id, actor="daemon", meta={"tokens": total})


# Tool-use blocks the agent emits (Status events) → a (kind, head, detail) triple. `head` is the
# call type ("Read", "skill", "subagent", "mcp"); `detail` is the specific target (filename, skill
# name, sub-agent name…). The UI shows "head - detail" (detail dimmed), like the CLI's call lines.
def _artifact_desc(tool_name: str, ti: dict) -> tuple[str, str, str]:
    """Map a tool-use to (kind, head, detail). kind ∈ tool|subagent|skill|mcp."""
    ti = ti or {}
    base = lambda p: str(p).rsplit("/", 1)[-1] if p else ""
    if tool_name == "Task":
        sub = ti.get("subagent_type") or ti.get("subagentType") or "agent"
        d = ti.get("description") or ""
        return "subagent", "subagent", (f"{sub} — {d}" if d else str(sub))
    if tool_name == "Skill":
        # The skill identity may arrive under any of several keys depending on the SDK build.
        name = (ti.get("command") or ti.get("name") or ti.get("skill")
                or ti.get("skill_name") or ti.get("skillName") or "")
        return "skill", "skill", str(name).lstrip("/")
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        return "mcp", "mcp", parts[-1] if parts else tool_name
    if tool_name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        return "tool", tool_name, base(ti.get("file_path") or ti.get("notebook_path"))
    if tool_name == "Bash":
        return "tool", "Bash", (ti.get("description") or str(ti.get("command", "")))[:60]
    if tool_name in ("Grep", "Glob"):
        return "tool", tool_name, str(ti.get("pattern", ""))
    if tool_name in ("WebFetch", "WebSearch"):
        return "tool", tool_name, str(ti.get("url") or ti.get("query") or "")[:60]
    return "tool", tool_name, ""


def _log_artifact(repo_id: str, item_id: str, ev: Status) -> None:
    """Best-effort: record a tool-use the item's run made. Never raises into the turn loop."""
    try:
        kind, head, detail = _artifact_desc(ev.tool_name, ev.tool_input or {})
        _spine.log_artifact(repo_id, item_id, kind=kind, name=head, description=detail)
    except Exception:
        log.exception("failed to log artifact %s for %s", getattr(ev, "tool_name", "?"), item_id)


async def _run_headless_plan(ctx, context_id: str, item_id: str, item_dir: Path,
                             model: str | None = None, effort: str | None = None) -> None:
    """Drive one /plan turn for `item_id` with no surface attached, then clear run-state.

    Always a FRESH pass (resume=None): a headless plan re-reads the item and re-plans from
    scratch, so re-planning (e.g. to try another model) actually re-does the work instead of a
    resumed agent saying "already done". The prior plan thread is replaced — its session is
    purged once the new one is recorded, keeping the picker clean. Runs autonomously (the prompt
    forbids questions), persists the new session, sandboxes writes to the item folder; status is
    owned here: in_progress while running → waiting (awaiting the owner) on finish."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    prev_session = item.get("session_id") or None
    title = item.get("title") or item_id
    # Thin trigger: name the skill, the item, and the mode — nothing else. The steps live in
    # the superme-dev:plan skill (its "Autonomous (headless) runs" section), the single
    # source of truth, so this can't drift from it. Keep the leading phrase in sync with
    # sessions._NOISE_PREFIXES (it's filtered from replay so the bubble doesn't show).
    prompt = (
        f"Run the superme-dev:plan skill for work-item `{item_id}` (\"{title}\") in "
        f"autonomous mode — this is a headless run with no human in this chat. Follow the "
        f"skill's autonomous-run instructions."
    )
    final_tokens = None
    final_usage = None
    try:
        async for ev in _agent.run_turn(
            ctx, prompt,
            resume=None,   # fresh pass — re-plan re-does the work, doesn't resume "already done"
            model=model,
            effort=effort or _spine.effective_effort(context_id),  # item → repo → system → medium
            approve=scoped_writes_approve(item_dir, deny_all),
        ):
            if isinstance(ev, Usage):
                _bump_run_tokens(context_id, item_id, ev.total_tokens, ev.context_pct)
            elif isinstance(ev, Status):
                _log_artifact(context_id, item_id, ev)
            elif isinstance(ev, Result):
                final_tokens = ev.tokens
                final_usage = ev.usage
                _sessions.record(ctx, ev.session_id)
                if ev.session_id:
                    try:
                        _dev.set_work_item_session(dev_root, item_id, ev.session_id)
                    except Exception:
                        log.exception("headless plan: failed to persist session to %s", item_id)
                    # The replaced thread is now stale — purge it so the picker stays clean.
                    if prev_session and prev_session != ev.session_id:
                        _sessions.purge(ctx, prev_session)
            elif isinstance(ev, Init):
                _cache_slash(ctx.id, ev.slash_commands)
    except Exception:
        log.exception("headless plan run failed for %s", item_id)
    finally:
        # Run finished (or died) — the agent is no longer working, so it's the owner's move.
        _end_run(ctx, context_id, item_id, final_tokens, "waiting", final_usage)
        log.info("headless plan: done for %s", item_id)


def _render_execution_md(context_id: str, item_id: str, item: dict) -> str:
    """Snapshot a work-item's execution trace (its run call-trail + per-run telemetry) to Markdown,
    so the record survives after the spine rows are freed on completion. Chronological (oldest run
    first)."""
    arts = _spine.artifacts_for_item(context_id, item_id)
    runs = {r["id"]: r for r in _spine.run_history(context_id)}
    title = item.get("title") or item_id
    out = [f"# Execution trace — {title}", "",
           f"Work-item `{item_id}` · archived {datetime.now().date().isoformat()}", ""]
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
            out.append(f"{a['seq']}. **{a['name']}**{d}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
