"""Run lifecycle — the orchestrator-owned helpers that drive a work-item's background runs.

Open/close spine run rows, accumulate live telemetry, map tool-use to call-trail artifacts, drive
the background intake/resolve turns, and snapshot the execution trace on completion. The work-item
routes (in server.py / the work_items router) call these; they own run-state, the route owns the
HTTP shape.

Imports singletons from `app_state` (never from server.py) so there's no import cycle.
"""

import asyncio
import json as _json
import logging
import time
from datetime import datetime
from pathlib import Path

from ..app_state import agent as _agent, dev as _dev, dev_store as _dev_store, \
    spine as _spine, sessions as _sessions
from ..deps import cache_slash as _cache_slash
from . import item_stream
from ...core import Init, Usage, Result, Status, TextDelta, ToolResult, scoped_writes_approve, deny_all
from ...core import artifacts as _arts
from ...core import autopilot as _autopilot
from ...core.autopilot import PROMPT_EXTRACTION_FEATURE
from ...core import git_layer, kernel_speech, kind_profiles
from ...core.models import MODEL_TIERS
from ...harness.tools.dev_tools import make_dev_mcp_server
from ...harness.tools.run_tools import make_run_report_server

log = logging.getLogger("superme-agent")

# Work-items default to the latest Sonnet (concrete id — the `sonnet` alias lags; see core/models.py).
DEFAULT_RUN_MODEL = MODEL_TIERS["sonnet"]


def _set_status(ctx, item_id: str, status: str) -> None:
    """Set a work-item's run-state status (orchestrator-owned). Best-effort; logs on failure.
    Run-lifecycle rests never OVERWRITE a typed pause: when the turn itself paused the item
    (`awaiting_child` — e.g. its session filed a blocking child mid-turn), resting it back to
    `active` at turn end would silently un-pause it. Only the status ROUTER (which calls
    dev.set_work_item_status directly) may resume a paused parent. Terminal is guarded at the
    data layer."""
    if not (item_id and ctx.internal_root):
        return
    try:
        dev_root = ctx.internal_root / "dev"
        if status == "active":
            cur = _dev.read_work_item(dev_root, item_id) or {}
            if str(cur.get("status")) == "awaiting_child":
                return  # the pause survives the turn's end-of-run rest
        _dev.set_work_item_status(dev_root, item_id, status)
    except Exception:
        log.exception("could not set status %s on %s", status, item_id)


def _begin_run(ctx, context_id: str, item_id: str, kind: str = "plan",
               model: str | None = None, phase: str | None = None) -> int | None:
    """Mark an item as running, atomically: open a spine run row (status=running) ONLY if the item
    isn't already running, then rest the work-item at `active` and log the start. The running row
    IS the live state (no in-memory mirror) and IS the per-item run-lock. Returns False without any
    side effect if a run was already in flight (the caller turns that into a 409), so the lock can't
    be lost to a check-then-start race (R5). `phase` stamps the item's current phase onto the run so
    tokens can be accumulated per-phase (Stage D)."""
    run_id = _spine.start_item_run(context_id, mode=ctx.mode, feature=kind,
                                   item_id=item_id, model=model, phase=phase)
    if run_id is None:
        return None  # already running — no status flip, no event
    # Runnable-state axis (workspace-workflow D2): a starting run means the item is being worked —
    # `active`. "Running right now" is NOT a status — it's derived from the live run row (the two
    # were conflated pre-workflow as in_progress/waiting).
    _set_status(ctx, item_id, "active")
    # Run start — item-scoped. PRD §4.9.
    _dev_store.log_event(context_id, f"{kind}.start", f"Started {kind} run",
                         item_id=item_id, actor="daemon", meta={"model": model})
    return run_id  # the live run id — the caller keys its per-run event trail on it


class _LiveTokens:
    """Per-run live token tally for an item's in-flight estimate, DEDUPED by message_id. The SDK
    emits several Usage steps per API call sharing one message_id (one per content block), so
    summing every step over-counts ~2-5x. Keep the latest 3-type value per message (usage can grow
    within a message) and write their SUM absolutely to the running row — each call counted once, so
    the card footer tracks accurately and lands on the authoritative finish figure. Older SDK builds
    with no message_id fall back to summing (`_legacy`)."""

    def __init__(self) -> None:
        self._by_msg: dict[str, int] = {}
        self._legacy = 0

    def bump(self, context_id: str, item_id: str, ev) -> None:
        mid = getattr(ev, "message_id", None)
        if mid:
            self._by_msg[mid] = ev.total_tokens   # latest wins
        else:
            self._legacy += ev.total_tokens
        _spine.set_item_run_tokens(
            context_id, item_id,
            tokens=sum(self._by_msg.values()) + self._legacy, ctx_pct=ev.ctx_pct,
        )


def _end_run(ctx, context_id: str, item_id: str, tokens: int | None,
             status: str = "active", usage: dict | None = None,
             ctx_pct: int | None = None, outcome: str | None = None,
             session_id: str | None = None, summary: str = "") -> None:
    """Close out a run: finalize its spine row (keeping the accumulated live token sum, or the
    passed Result aggregate as a fallback) and set the work-item's resting status. Interactive
    (bound-chat) turns rest at `active`; a BACKGROUND phase run that ends at a human gate passes
    `awaiting_human` (the only status that pages the owner — D2 typed awaiting; the full
    completion-report router lands S5). `kind` is recovered from the running row for the end event.
    `usage` is the whole-turn final dict — the typed-column fallback if no per-step Usage arrived.
    `ctx_pct` is the authoritative Result fill — persisted over the live-bump estimate (chat runs do
    the same via finish_run), so an item card's ctx% matches the true end-of-turn occupancy."""
    info = _spine.live_run(context_id, item_id)
    kind = (info or {}).get("feature", "plan")
    rid = _spine.finish_item_run(context_id, item_id, fallback_tokens=tokens, usage=usage,
                                 ctx_pct=ctx_pct, outcome=outcome, session_id=session_id)
    # Log the AUTHORITATIVE total that finish just reconciled onto the row (3-type, excl. cache_read —
    # the same basis the item card shows), NOT the pre-finish `info` snapshot, whose live `tokens` sums
    # the cumulative-for-the-turn Usage snapshots and so over-counts (that mismatch was the "62k vs 305k").
    total = _spine.run_tokens(rid) if rid else (tokens or 0)
    _set_status(ctx, item_id, status)
    # Run end — item-scoped, with the final token total. PRD §4.9.
    _dev_store.log_event(context_id, f"{kind}.end", f"Finished {kind} run · Σ {total} tok",
                         item_id=item_id, actor="daemon", meta={"tokens": total})
    # Autopilot hook: a background phase run that just rested the item at its gate (`awaiting_human`)
    # is the moment the owner would click Approve. If the item is enrolled, drive the same advance.
    # Scheduled (not inline) so this run's lock is fully released before the next phase's `_begin_run`
    # fires — and so a blocking git/plan step never stalls run finalization. No-op when not on a loop
    # (offline tests exercise the driver directly). See services/gates.maybe_autopilot_advance.
    # Plan's grill (renovation §2): `needs_user` pages the OWNER directly — questions are not
    # phase output, so the deputy is not in this path. The item parks per-item (the cohort keeps
    # running); the owner's answers arrive as chat, and only the agent's post-answer report
    # re-enters the normal judged flow.
    # `revise` (renovation §2.1): the review conversation concluded the WORK must change. It is the
    # ONE way back to re-work — there is no send-back button anywhere — and it rides the SAME path
    # the deputy's send_back already uses (`fire_phase_feedback` flips review→plan and posts the
    # turn on the item's own intake thread), because the conversation IS the context: nothing is
    # handed over and nothing is re-derived. Owner-attributed: they concluded it, the agent only
    # reported the conclusion.
    # Compaction: a real turn just reported fresh usage, so release this session's defer latch —
    # the NEXT run-start check will read this run's fill. Nothing is EVALUATED here. The trigger
    # lives at run start (ws.py for a bound turn, gates.maybe_autopilot_advance for the background
    # chain), where the lock is free by construction and there is no queued phase to strand.
    if kind != "compact" and session_id:
        from . import compaction
        compaction.note_turn_start(session_id)
    if outcome == "revise":
        try:
            fire_phase_feedback(context_id, item_id, phase="review",
                                feedback=summary or "the review concluded this needs re-planning",
                                by="owner")
        except Exception:
            log.exception("revise routing failed for %s (item stays at review)", item_id)
        return
    if status == "awaiting_human" and outcome != "needs_user":
        try:
            from .gates import maybe_autopilot_advance
            asyncio.get_running_loop().call_soon(
                maybe_autopilot_advance, context_id, item_id)
        except RuntimeError:
            pass  # no running loop (sync/test context) — driver is tested directly


# Tool-use blocks the agent emits (Status events) → a (kind, head, detail) triple. `head` is the
# call type ("Read", "skill", "subagent", "mcp"); `detail` is the specific target (filename, skill
# name, sub-agent name…). The UI shows "head - detail" (detail dimmed), like the CLI's call lines.
def _short_path(p, keep: int = 4) -> str:
    """A readable file path for the trace: the last `keep` segments (so the containing folders show,
    not just the basename), prefixed with `…/` when the head is elided. Absolute paths are too long
    and the UI truncates the TAIL (the filename) — this keeps the meaningful end intact."""
    parts = [x for x in str(p or "").split("/") if x]
    if not parts:
        return ""
    return "/".join(parts) if len(parts) <= keep else "…/" + "/".join(parts[-keep:])


def _artifact_desc(tool_name: str, ti: dict) -> tuple[str, str, str]:
    """Map a tool-use to (kind, head, detail). kind ∈ tool|subagent|skill|mcp."""
    ti = ti or {}
    base = _short_path
    # Sub-agent spawn — arrives as either the `Task` or the `Agent` tool depending on SDK build. Render
    # it "Agent - <type>" (same label·detail shape as skills: "skill - <name>"), so the trace says WHICH
    # agent ran (e.g. `superme-dev:capture`) instead of a generic "Agent". kind=subagent → the Bot icon.
    if tool_name in ("Task", "Agent"):
        sub = (ti.get("subagent_type") or ti.get("subagentType") or ti.get("agent_type")
               or ti.get("agentType") or ti.get("name") or "agent")
        return "subagent", "Agent", str(sub)
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
    if tool_name == "ToolSearch":
        # what it searched for — the deferred-tool query (e.g. "select:Read,Edit" or keywords).
        return "tool", "ToolSearch", str(ti.get("query", ""))[:60]
    return "tool", tool_name, ""


# --- per-run trail caps (both trails: run_artifact for work-items, run_event for Activity/diagnosis) ---
_PROMPT_CAP = 4000   # a run's trigger prompt, trimmed
_REPLY_CAP = 8000    # one assistant text block, trimmed
_RESULT_CAP = 1200   # a tool's output, trimmed — enough to show what it returned without bloating the trail


def _result_row(ev: ToolResult) -> tuple[str, str]:
    """Map a ToolResult to (name, description) for a trail row: name = the tool that produced it
    (the specific tool, e.g. `read_dev_log` / `Bash`, via the same _artifact_desc mapping the call
    used), description = its capped output (error-flagged). It carries `ev.tool_id` so the FE can
    pair it back to its call (concurrent tools return out of order — position can't pair them)."""
    _, head, detail = _artifact_desc(ev.tool_name, {})
    name = detail or head
    body = (ev.content or "").strip()
    if ev.is_error:
        body = "[error] " + body
    return name, body[:_RESULT_CAP]


def _log_artifact(repo_id: str, item_id: str, ev) -> None:
    """Best-effort: record a tool-use (Status) or its output (ToolResult) the item's run made onto
    the run_artifact trail, carrying the tool_use id so result→call pairs. Never raises into the loop."""
    try:
        if isinstance(ev, ToolResult):
            name, desc = _result_row(ev)
            _spine.log_artifact(repo_id, item_id, kind="result", name=name, description=desc, tool_id=ev.tool_id)
            return
        kind, head, detail = _artifact_desc(ev.tool_name, ev.tool_input or {})
        _spine.log_artifact(repo_id, item_id, kind=kind, name=head, description=detail, tool_id=ev.tool_id)
    except Exception:
        log.exception("failed to log artifact %s for %s", getattr(ev, "tool_name", "?"), item_id)


def capture_prompt(repo_id: str, prompt: str, *, run_id: int | None = None,
                   item_id: str | None = None) -> None:
    """Record the prompt that opened a run as the first entry of its trail."""
    _spine.log_run_event(repo_id=repo_id, kind="prompt", name="prompt",
                         description=(prompt or "").strip()[:_PROMPT_CAP], run_id=run_id, item_id=item_id)


def capture_run_input(context_id: str, item_id: str, *, ctx, system_append: str | None,
                      prompt: str, background: bool, phase: str | None) -> None:
    """Prompt inspector "A": persist the ACTUAL input the item's live run is about to send — the
    assembled system prompt (built through the SAME `assemble_system_append` seam `_build_options`
    uses, with THIS run's exact ctx / system_append) + the prompt body. `background` is stored as
    display metadata only (the run-protocol delta now lives inside system_append itself). Keyed to
    the live run. Faithful by construction; best-effort — never breaks a turn.

    Called ONLY for throwaway prompt-extraction items (the call sites gate on
    `autopilot.is_prompt_extraction(item)`) — normal work-item runs no longer capture, so the
    `run_input` table stops growing per-run. Also stamps `run.feature='prompt-extraction'` so the
    KEPT trace (the item folder is torn down; the run rows survive) is tagged + token-bucketed."""
    try:
        info = _spine.live_run(context_id, item_id)
        rid = (info or {}).get("id")
        if rid is None:
            return
        _spine.set_run_feature(rid, PROMPT_EXTRACTION_FEATURE)   # tag the kept trace
        system_prompt = _agent.assemble_system_append(ctx, system_append=system_append)
        # Also capture the system prompt's provenance breakdown (ordered [{name,location,text}]) so
        # the inspector renders per-fragment sub-cards. Same builder as `system_prompt` above, so the
        # fragments sum to it. Guarded: a serialization hiccup must never lose the whole capture.
        try:
            frags = _agent.assemble_system_fragments(ctx, system_append=system_append)
            fragments_json = _json.dumps(frags, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            fragments_json = None
        _spine.record_run_input(rid, repo_id=context_id, item_id=item_id, phase=phase,
                                feature=PROMPT_EXTRACTION_FEATURE, background=background,
                                system_prompt=system_prompt, prompt_body=prompt,
                                system_fragments=fragments_json)
    except Exception:
        log.exception("capture_run_input failed for %s", item_id)


def capture_event(repo_id: str, ev, *, run_id: int | None = None, item_id: str | None = None,
                  publish_live: bool = True) -> None:
    """Record one turn event onto a run's trail: a Status → its tool/skill/agent call, a ToolResult →
    that call's (capped) output, a TextDelta → an assistant reply block. Anything else is ignored.
    Best-effort (log_run_event never raises).

    ALSO the single publish point for the F2 live timeline: the same row is fanned out to any panel
    watching this item (`item_stream.publish`), so build/vet/intake turns stream live. Run-lock means
    one live run per item, so the FE attributes each live frame to the item's CURRENT phase lane.
    `publish_live=False` for the INTERACTIVE ws turn — it already streams itself directly to the panel
    that fired it, so broadcasting would double it there (run-lock means no other panel needs the echo)."""
    if isinstance(ev, Status):
        kind, head, detail = _artifact_desc(ev.tool_name, ev.tool_input or {})
        _spine.log_run_event(repo_id=repo_id, kind=kind, name=head, description=detail,
                             run_id=run_id, item_id=item_id, tool_id=ev.tool_id)
        if publish_live:
            _publish_timeline(item_id, run_id, kind, head, detail, ev.tool_id)
    elif isinstance(ev, ToolResult):
        name, desc = _result_row(ev)
        # Record with the tool_use id so the FE pairs result→call exactly (concurrent tools return
        # out of order). Empty output stays empty (the call just won't be expandable).
        _spine.log_run_event(repo_id=repo_id, kind="result", name=name, description=desc,
                             run_id=run_id, item_id=item_id, tool_id=ev.tool_id)
        if publish_live:
            _publish_timeline(item_id, run_id, "result", name, desc, ev.tool_id)
    elif isinstance(ev, TextDelta):
        txt = (ev.text or "").strip()
        if txt:
            _spine.log_run_event(repo_id=repo_id, kind="reply", name="reply", description=txt[:_REPLY_CAP],
                                 run_id=run_id, item_id=item_id)
            if publish_live:
                _publish_timeline(item_id, run_id, "reply", "reply", txt[:_REPLY_CAP], None)


def _publish_timeline(item_id: str | None, run_id: int | None, kind: str, name: str,
                      description: str, tool_id: str | None) -> None:
    """Fan a captured event out to any panel watching this item (F2). No-op when nobody's watching
    (the common background-autopilot case) so we skip the frame build entirely. Never raises."""
    if not item_id or not item_stream.has_subscribers(item_id):
        return
    try:
        item_stream.publish(item_id, {
            "type": "timeline", "item_id": str(item_id), "run_id": run_id,
            "kind": kind, "name": name, "description": description, "tool_id": tool_id,
        })
    except Exception:
        log.debug("item_stream publish failed for %s", item_id, exc_info=True)


def bank_auto_checkpoint(ctx, item_id: str, *, since: float | None = None) -> bool:
    """The session-end checkpoint hook's mechanical fallback (S5): guarantees the orient block
    always has a latest checkpoint, even when the agent didn't bank its own. SKIPS when the item
    is terminal or a checkpoint newer than `since` (the session's start) already exists — the
    agent's own checkpoint is always better than this derived stub. Content is DATA the kernel can
    derive (phase, remaining tasks); it says so, and the orient block's verify-banner covers the
    rest. Returns True if a checkpoint was written."""
    if not (item_id and ctx.internal_root):
        return False
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    if not item or item.get("done_at") or str(item.get("status")) == "done":
        return False
    item_dir = dev_root / "work-items" / item_id
    latest = _arts.latest_checkpoint(item_dir, char_cap=1)
    if latest and since:
        try:
            if Path(latest["path"]).stat().st_mtime >= since:
                return False  # the session banked its own — keep it
        except OSError:
            pass
    tasks = _dev.read_tasks(dev_root, item_id) or []
    open_tasks = [t["text"] for t in tasks if not t.get("done")][:8]
    remaining = ("; ".join(open_tasks)) if open_tasks else "see plan.md ## Tasks (none parsed)"
    repo_dir = Path(str(item["git_worktree"])) if item.get("git_worktree") else ctx.cwd
    try:
        _arts.write_checkpoint(
            item_dir, repo_dir,
            role=kind_profiles.session_role(str(item.get("phase") or "triage")),
            working_on=f"{item.get('phase') or 'triage'} phase — {item.get('title') or item_id}",
            decisions="(auto-banked at session end — the session's reasoning lives in its transcript)",
            remaining=remaining,
            notes="AUTO checkpoint written by the daemon because the session ended without banking "
                  "one. Derived data only — verify against the artifacts before relying on it.",
        )
        return True
    except ValueError:
        return False


def compacted_checkpoint(ctx, item: dict, session_id: str | None) -> str | None:
    """The checkpoint path this thread is owed a pointer to, or None.

    Owed only while the session's newest finished run IS the compaction (spine —
    self-clearing, restart-proof), and resolved to THIS THREAD's newest checkpoint via the role
    stamp: three threads bank into one folder, and handing a compacted intake thread the build
    thread's checkpoint would present work it never did as recovered memory."""
    if not (session_id and ctx.internal_root and item):
        return None
    if not _spine.session_compacted_pending(session_id):
        return None
    role = kind_profiles.session_role(str(item.get("phase") or "triage"))
    item_dir = ctx.internal_root / "dev" / "work-items" / str(item.get("id") or "")
    cp = _arts.latest_checkpoint(item_dir, char_cap=1, role=role)
    return cp["path"] if cp else None


def compacted_session_memory(ctx, session_id: str | None) -> str | None:
    """`compacted_checkpoint`'s general-session twin (§13.4 / T5): the `session-memory/` path this
    thread is owed a pointer to, or None. Same self-clearing gate (the session's newest finished
    run IS the compaction), and no role scoping — a general session has exactly one thread. None
    when the handoff turn failed to write one, so the notice is never a pointer at nothing."""
    if not (session_id and ctx.internal_root):
        return None
    if not _spine.session_compacted_pending(session_id):
        return None
    mem = _arts.read_session_memory(ctx.internal_root / ctx.mode, session_id, char_cap=1)
    return mem["path"] if mem else None


def reset_vet_thread(ctx, item: dict, *, dev=None, sessions=None) -> bool:
    """VET FORGETS (build-vet-loop §1.3): called on every transition INTO the vet phase, so each
    build⟷vet cycle gets a FRESH vetter — no anchoring, no fatigue-capitulation; prior cycles'
    findings are handed over as DATA (the vet reports), never as memory. Retires the previous
    cycle's vet session (transcript reclaimed, run trace preserved + labeled) and clears the
    item's vet slot so the next vet turn MINTS. No-op on the first cycle (empty slot). `dev`/
    `sessions` are injectable for tests; default to the app singletons. Returns True if a
    previous thread was retired."""
    d, s = dev or _dev, sessions or _sessions
    prev = (item.get("sessions") or {}).get("vet")
    if not prev:
        return False
    s.delete(ctx, prev, cause="retired")
    d.set_work_item_session(ctx.internal_root / "dev", str(item["id"]), None, role="vet")
    return True


def read_completion(context_id: str, item_id: str, sink: dict) -> dict | None:
    """A background run's completion payload out of its `report_completion` sink (run_tools) —
    None when the run never called the tool (a legacy/unstructured ending; callers warn so drift
    is visible). Persists the payload as the run.report event, the DB record keyed to this item's
    run trail."""
    report = sink.get("report")
    if report:
        _dev_store.log_event(context_id, "run.report",
                             f"{report['outcome']}: {report['summary'][:160]}",
                             item_id=item_id, actor="agent", meta=report)
    return report


def _dev_mcp(ctx, repo_dir: Path, item_id: str) -> dict:
    """The dev MCP server for a background intake/resolve run (F8-residual, playground-e2e-blockers:
    these runners went tool-less while the loop runners got the mount in step 5) — same shape as
    loop.py's `_dev_mcp`. A background planner can now read the dev log / roadmap / inbox and use
    the item pens instead of planning blind. `repo_dir` = where evidence fingerprints (the
    worktree when one exists, else the repo root)."""
    return {"dev": make_dev_mcp_server(
        _dev_store, ctx.id, spine=_spine,
        dev_root=ctx.internal_root / "dev",
        repo_dir=repo_dir, main_repo_dir=ctx.cwd,
        bound_item_id=item_id,
        fire_triage=lambda child_id: fire_auto_triage(ctx.id, child_id, _spine),
    )}


async def _run_background_plan(ctx, context_id: str, item_id: str, item_dir: Path,
                               model: str | None = None, effort: str | None = None) -> None:
    """Background "Plan it" — one /plan turn, no surface. Thin wrapper over _background_intake_run."""
    await _background_intake_run(ctx, context_id, item_id, item_dir,
                                 skill="plan", model=model, effort=effort)


async def _run_background_item_skill(ctx, context_id: str, item_id: str, item_dir: Path,
                                     skill: str, model: str | None = None,
                                     effort: str | None = None) -> None:
    """The generic phase-entry runner: any auto-fired item skill that is NOT plan — `review` (every
    kind, on review entry), research's `investigate`, and research's `itemize` on review approve.
    All carry the item's INTAKE role (kind_profiles: one thread end to end, no fresh-perspective
    boundary), so they ride the same runner as plan/triage and only the skill differs. Named for
    what it does rather than for one kind: it was `_run_background_research` while review had no
    runner and research was the only caller. Thin wrapper over _background_intake_run."""
    await _background_intake_run(ctx, context_id, item_id, item_dir,
                                 skill=skill, model=model, effort=effort)


async def _run_background_triage(ctx, context_id: str, item_id: str, item_dir: Path,
                                 model: str | None = None, effort: str | None = None) -> None:
    """Auto-triage on push (#120) — one /triage turn, no surface, fired when an inbox item is
    pushed to the workspace. The item lands at `awaiting_human` with its classification recorded:
    the one-click triage-exit gate ("always-stop-but-trivial" — the run does the work, the owner
    glances + approves). Thin wrapper over _background_intake_run."""
    await _background_intake_run(ctx, context_id, item_id, item_dir,
                                 skill="triage", model=model, effort=effort)


def fire_review_entry(context_id: str, item_id: str, spine) -> bool:
    """Fire the REVIEW-ENTRY run for an item that just landed at review (renovation §2.2). Shared
    by the two ways in, so neither can drift from the other: `advance_item`'s auto-fire (an owner
    Approve at vet, an owner force, a deputy advance) and the LOOP's vet→review hop, which
    CAS-flips the phase itself and never touches advance_item.

    That second path is why this exists as a function. The loop is how items normally reach review;
    wiring the run only into advance_item would have left the common path with no report at all —
    the same shape of hole that left the old review skill with no runner for its whole life.

    The item rests `awaiting_human` when it gets here (the loop's rest status), so this flips it to
    `active` for the duration: `active` with no run is an idle stall, and `awaiting_human` while a
    run works is a lie about who is waiting. On any failure the item is put back to
    `awaiting_human` — a review with no report is still a gate the owner can act on, just a poorer
    one. Best-effort, never raises. Returns True iff a run was started."""
    from ...gateway import contexts   # lazy: avoid an import cycle at module load
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if not item or str(item.get("phase")) != "review" or item.get("done_at"):
            return False
        if str(item.get("status")) not in ("active", "awaiting_human"):
            return False   # paused / parked — the owner's hold wins
        model = spine.effective_model(context_id, item_model=item.get("model"))
        effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
        if _begin_run(ctx, context_id, item_id, "review", model, phase="review") is None:
            return False   # a run is already in flight — don't double-fire
        _dev.set_work_item_status(dev_root, item_id, "active")
        asyncio.create_task(
            _run_background_item_skill(ctx, context_id, item_id,
                                       dev_root / "work-items" / item_id, "review", model, effort))
        return True
    except Exception:
        log.exception("review-entry run failed to start for %s", item_id)
        try:
            _dev.set_work_item_status(ctx.internal_root / "dev", item_id, "awaiting_human")
        except Exception:
            pass
        return False


def fire_auto_triage(context_id: str, item_id: str, spine) -> bool:
    """Kick a freshly-active item into its first triage run — the SHARED "first push" for the
    autopilot chain (slice 4c). Three callers use it: the FE owner-push (inbox router), an
    onboarding-itemized cohort (launch service), and a peer released off its `after` edge
    (scheduler). Fires ONLY for an item that is `active` at phase `triage` (never re-fires a
    running item, an item already past triage, or a paused/parked one) and only when no run is
    already in flight (the per-item run-lock in `_begin_run` gates that). Best-effort — returns
    True iff a triage run was started; any failure leaves the item resting at triage for a
    chat-driven pass, never raises."""
    from ...gateway import contexts   # lazy: avoid an import cycle at module load
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if str(item.get("status")) != "active" or str(item.get("phase")) != "triage":
            return False   # only a fresh, active, un-triaged item is kicked
        # Resolve with the item's LOCKED config (F3) — the first kick must honour the model/effort
        # chosen at capture, same as every later phase (precedence: item → repo → system).
        model = spine.effective_model(context_id, item_model=item.get("model"))
        effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
        if _begin_run(ctx, context_id, item_id, "triage", model, phase="triage") is None:
            return False   # a run is already in flight — don't double-fire
        asyncio.create_task(
            _run_background_triage(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                   model, effort))
        return True
    except Exception:
        log.exception("auto-triage failed to start for %s", item_id)
        return False


# --------------------------------------------------------------------------- deputy send-back re-run

# Which skill a phase's re-run fires (the phase that OWNS the fix; deputy-live-turns-design §"New
# model"). triage feedback re-triages, plan/review feedback re-plans (review flips review→plan first).
_PHASE_FEEDBACK_SKILL = {"triage": "triage", "plan": "plan", "review": "plan"}


def fire_phase_feedback(context_id: str, item_id: str, *, phase: str, feedback: str,
                        digest: str | None = None, by: str = "deputy") -> bool:
    """Deliver gate feedback as a REAL turn on the item's own session (deputy-live-turns design Q1;
    #178 unifies the OWNER onto the same path): resume the item's intake/plan session and post the
    feedback so the agent re-runs the phase in-thread. The chat panel is a shared terminal — owner,
    work-item agent, and deputy all speak here; `by` ("owner" | "deputy") only sets attribution, the
    ROUTING is identical (that is the point: feedback advances the loop the same way whoever gives it).

    Persists a `<by>.query` marker so the FE can attribute the resulting transcript turn. Fires ONLY
    when the item is resting at that gate with no run in flight; returns True iff the re-run started.
    Best-effort — any failure leaves the item at `awaiting_human` for the owner, never raises."""
    from ...gateway import contexts   # lazy: avoid an import cycle at module load
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if not item or item.get("done_at"):
            return False
        skill = _PHASE_FEEDBACK_SKILL.get(phase)
        if skill is None:
            return False   # not a gate that negotiates via a phase re-run
        # Review feedback FALLS BACK TO THE PLAN PHASE (deputy-live-turns Q1-B; #178 owner too): flip
        # review→plan so the re-plan re-runs and forward flow (plan→build→vet→review) re-vets by
        # construction. The run + trigger then speak in plan terms; the `digest` carries the
        # review/build/vet context so the re-plan knows it's feedback from the earlier plan's results.
        run_phase = "plan" if phase == "review" else phase
        if phase == "review":
            # `strict` (§2.2): a send-back spends the approval, so the PR closes with it — the diff
            # it described is about to change. See git_ops.close_pr. No-op in `fast` repos and on
            # any item that never had one open.
            from .git_ops import close_pr
            close_pr(_dev, dev_root, item_id)
            _dev.set_work_item_phase(dev_root, item_id, "plan")
            _dev_store.log_event(context_id, "review.route",
                                 f"Review feedback routed back to plan: {feedback[:160]}",
                                 item_id=item_id, actor=by,
                                 meta={"from": "review", "to": "plan", "feedback": feedback[:400],
                                       "by": by})
        session_id = (item.get("sessions") or {}).get("intake") or item.get("session_id") or None
        model = _spine.effective_model(context_id, item_model=item.get("model"))
        effort = _spine.effective_effort(context_id, item_effort=item.get("effort"))
        if _begin_run(ctx, context_id, item_id, skill, model, phase=run_phase) is None:
            return False   # a run is already in flight — don't double-fire
        # The marker the FE matches to attribute the resulting user turn to the `<by>` role, and the
        # record that this round's feedback was delivered (its own event so the trail is honest even
        # if the turn then dies). `speech` = the short bubble; `text` = what actually gets sent.
        title = item.get("title") or item_id
        prompt = kernel_speech.phase_feedback_trigger(item_id, title, run_phase, skill, feedback, digest)
        _dev_store.log_event(context_id, f"{by}.query",
                             f"{by.title()} sent feedback into the {run_phase} thread: {feedback[:160]}",
                             item_id=item_id, actor=by,
                             meta={"phase": run_phase, "origin_gate": phase, "speech": feedback,
                                   "text": prompt, "by": by})
        asyncio.create_task(
            _run_deputy_feedback_turn(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                      session_id=session_id, phase=run_phase, prompt=prompt,
                                      model=model, effort=effort))
        return True
    except Exception:
        log.exception("%s feedback re-run failed to start for %s", by, item_id)
        return False


def fire_deputy_feedback(context_id: str, item_id: str, *, phase: str, feedback: str,
                         digest: str | None = None) -> bool:
    """Back-compat alias — the deputy's send-back path (deputy._do_send_back). See
    `fire_phase_feedback` (by='deputy')."""
    return fire_phase_feedback(context_id, item_id, phase=phase, feedback=feedback,
                               digest=digest, by="deputy")


async def _run_deputy_feedback_turn(ctx, context_id: str, item_id: str, item_dir: Path, *,
                                    session_id: str | None, phase: str, prompt: str,
                                    model: str | None, effort: str | None) -> None:
    """Run one deputy send-back re-run: RESUME the item's session (so the query + reply are real
    3-speaker transcript) and let the agent re-run the phase against the feedback. Ends the item at
    `awaiting_human` — which re-fires the gate seam (`_end_run` → maybe_autopilot_advance), so the
    deputy re-judges the result. That chaining IS the negotiation loop; the send-back cap (counted in
    the decision log) turns a 4th round into an escalation."""
    dev_root = ctx.internal_root / "dev"
    capture_prompt(context_id, prompt, item_id=item_id)
    # Current-focus pointer (thin, per-turn): re-read the item AFTER any review→plan phase flip so
    # the pointer names the phase this re-run actually works.
    live_item = _dev.read_work_item(dev_root, item_id) or {"id": item_id, "phase": phase}
    focus = kernel_speech.work_item_preamble(
        item_id, live_item, str(item_dir), interactive=False,
        compacted_checkpoint=compacted_checkpoint(ctx, live_item, session_id))
    final_tokens = final_usage = final_session = None
    run_started = time.time()
    live = _LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    try:
        async for ev in _agent.run_turn(
            ctx, prompt,
            resume=session_id,   # RESUME — the deputy's turn lands in the item's own transcript
            model=model,
            effort=effort or _spine.effective_effort(context_id),
            approve=scoped_writes_approve(item_dir, deny_all),
            extra_mcp_servers={**_dev_mcp(ctx, ctx.cwd, item_id),
                               "run": make_run_report_server(sink)},
            system_append=focus,   # Current-focus pointer incl. the run protocol
        ):
            if isinstance(ev, Usage):
                live.bump(context_id, item_id, ev)
            elif isinstance(ev, (Status, ToolResult)):
                _log_artifact(context_id, item_id, ev)
            elif isinstance(ev, Result):
                final_tokens, final_usage, final_session = (ev.tokens, ev.usage, ev.session_id)
                _sessions.record(ctx, ev.session_id)
                if ev.session_id:
                    try:
                        _dev.set_work_item_session(dev_root, item_id, ev.session_id, role="intake")
                        _spine.stamp_session_item(ev.session_id, item_id)
                    except Exception:
                        log.exception("deputy feedback: failed to persist session to %s", item_id)
            elif isinstance(ev, Init):
                _cache_slash(ctx.id, ev.slash_commands)
            if isinstance(ev, (Status, TextDelta, ToolResult)):
                capture_event(context_id, ev, item_id=item_id)
    except Exception:
        log.exception("deputy feedback re-run turn failed for %s", item_id)
    finally:
        report = read_completion(context_id, item_id, sink)
        _end_run(ctx, context_id, item_id, final_tokens, "awaiting_human", final_usage,
                 outcome=(report or {}).get("outcome"), session_id=final_session)
        try:
            bank_auto_checkpoint(ctx, item_id, since=run_started)
        except Exception:
            log.exception("auto-checkpoint after deputy feedback failed")
        log.info("deputy feedback re-run: done for %s (%s)", item_id, phase)


def fire_close_run(context_id: str, item_id: str, spine) -> bool:
    """Fire the ONE closing run of an item's CLOSE phase — knowledge finalization (anchor-doc ops
    + the weekly change-log entry), the only knowledge write in the workflow. Mirrors the
    auto-plan-on-advance kick: fires ONLY for an item resting at phase `close` with no run in
    flight. When the run reports, the kernel clears the item mechanically (`_clear_or_retry` →
    services/clearance) — there is no proposal and no owner click.

    Best-effort — returns True iff the run started. When no run can start at all, the item is
    CLEARED anyway with the knowledge gap on record: clearance always completes, so a close-phase
    item is never left `active` with nothing behind it (the idle stall). Never raises."""
    from ...gateway import contexts   # lazy: avoid an import cycle at module load
    ctx = None
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if item.get("done_at") or str(item.get("phase")) != "close":
            return False
        model = spine.effective_model(context_id, item_model=item.get("model"))
        effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
        if _begin_run(ctx, context_id, item_id, "close", model, phase="close") is None:
            return False   # a run is already in flight — don't double-fire (it owns the status)
        asyncio.create_task(
            _run_background_close(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                  model, effort))
        return True
    except Exception:
        log.exception("auto-close failed to start for %s", item_id)
        return False
    finally:
        # No run started and the item is still sitting `active` at close ⇒ nothing will ever move
        # it. Clear it instead, with the missing knowledge write on record. (A started run owns
        # the status from here — `_begin_run` registered it before the task was created.)
        try:
            if ctx is not None and ctx.internal_root:
                d_root = ctx.internal_root / "dev"
                it = _dev.read_work_item(d_root, item_id) or {}
                if (not it.get("done_at") and str(it.get("phase")) == "close"
                        and str(it.get("status")) == "active"
                        and not _spine.is_item_running(context_id, item_id)):
                    from . import clearance
                    clearance.clear_item(
                        context_id, item_id,
                        knowledge_gap="no closing run could start — the anchor docs were "
                                      "not updated")
        except Exception:
            log.exception("close-phase clearance fallback failed for %s", item_id)


async def _run_background_close(ctx, context_id: str, item_id: str, item_dir: Path,
                                model: str | None = None, effort: str | None = None) -> None:
    """Drive the item's ONE closing turn: RESUME its intake thread (the narrative that knows what
    the item was for) and let the close skill reflect the LOCKED changes into the general anchor
    docs + this week's change log, then report. The kernel clears the item from there
    (`_clear_or_retry`) — the run never completes the item itself. Best-effort: a turn that dies
    without reporting is retried, and after the budget the item clears with the gap recorded."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    session_id = ((item.get("sessions") or {}).get("intake") or item.get("session_id") or None)
    title = item.get("title") or item_id
    prompt = kernel_speech.close_trigger(item_id, title)
    capture_prompt(context_id, prompt, item_id=item_id)
    # Current-focus pointer (thin, per-turn) — the resumed transcript carries the orient bulk;
    # this is the standing phase/role pointer every runner now sends.
    focus = kernel_speech.work_item_preamble(
        item_id, item, str(item_dir), interactive=False,
        compacted_checkpoint=compacted_checkpoint(ctx, item, session_id))
    # Prompt inspector "A" — throwaway probes ONLY: capture matches the real send exactly.
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=ctx, system_append=focus, prompt=prompt,
                          phase="close", background=True)
    final_tokens = final_usage = final_session = None
    run_started = time.time()
    live = _LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    try:
        async for ev in _agent.run_turn(
            ctx, prompt,
            resume=session_id,   # RESUME the intake thread — the closeout narrates the whole item
            model=model,
            effort=effort or _spine.effective_effort(context_id),
            approve=scoped_writes_approve(item_dir, deny_all),
            extra_mcp_servers={**_dev_mcp(ctx, ctx.cwd, item_id),
                               "run": make_run_report_server(sink)},
            system_append=focus,   # Current-focus pointer incl. the run protocol
        ):
            if isinstance(ev, Usage):
                live.bump(context_id, item_id, ev)
            elif isinstance(ev, (Status, ToolResult)):
                _log_artifact(context_id, item_id, ev)
            elif isinstance(ev, Result):
                final_tokens, final_usage, final_session = (ev.tokens, ev.usage, ev.session_id)
                _sessions.record(ctx, ev.session_id)
                if ev.session_id:
                    try:
                        _dev.set_work_item_session(dev_root, item_id, ev.session_id, role="intake")
                        _spine.stamp_session_item(ev.session_id, item_id)
                    except Exception:
                        log.exception("auto-close: failed to persist session to %s", item_id)
            elif isinstance(ev, Init):
                _cache_slash(ctx.id, ev.slash_commands)
            if isinstance(ev, (Status, TextDelta, ToolResult)):
                capture_event(context_id, ev, item_id=item_id)
    except Exception:
        log.exception("auto-close run failed for %s", item_id)
    finally:
        report = read_completion(context_id, item_id, sink)
        outcome = str((report or {}).get("outcome") or "")
        # `active`, never `awaiting_human`: nobody is being paged. Clearance decides what happens
        # next, and it is mechanical (the item is terminal a moment later, or retried).
        _end_run(ctx, context_id, item_id, final_tokens, "active", final_usage,
                 outcome=outcome or None, session_id=final_session)
        try:
            bank_auto_checkpoint(ctx, item_id, since=run_started)
        except Exception:
            log.exception("auto-checkpoint after auto-close failed")
        _clear_or_retry(context_id, item_id, outcome)


def _clear_or_retry(context_id: str, item_id: str, outcome: str) -> None:
    """The post-CLOSE kernel hook. The closing run reported ⇒ clear the item. It didn't ⇒ re-fire
    the run, and once the retry budget is spent clear it ANYWAY with the knowledge gap on record.

    Clearance always completes (renovation §2): a closing run that cannot finish is a SuperMe
    fault to fix, never a work-item left sitting at close with no way out. Never raises."""
    from . import clearance
    try:
        if outcome:
            # Any report clears. Close has no authority to change anything (review's exit locked
            # code and git), so a non-success outcome is a knowledge gap to record, not a hold.
            gap = None if outcome in ("success", "clean_noop") else \
                f"the closing run reported `{outcome}`"
            res = clearance.clear_item(context_id, item_id, knowledge_gap=gap)
            if not res.get("ok"):
                log.info("close: clearance held for %s — %s", item_id, res.get("refused"))
            return
        tries = clearance.close_retries(context_id, item_id)
        if tries < clearance.MAX_CLOSE_RETRY:
            _dev_store.log_event(
                context_id, "close.retry",
                f"Closing run ended without a report — retry {tries + 1} of "
                f"{clearance.MAX_CLOSE_RETRY}",
                item_id=item_id, actor="daemon", meta={"attempt": tries + 1})
            fire_close_run(context_id, item_id, _spine)
            return
        clearance.clear_item(context_id, item_id,
                             knowledge_gap=f"the closing run ended without a report "
                                           f"{tries + 1} times — the anchor docs were not updated")
    except Exception:
        log.exception("post-close clearance failed for %s", item_id)


async def _background_intake_run(ctx, context_id: str, item_id: str, item_dir: Path, *,
                                 skill: str, model: str | None = None,
                                 effort: str | None = None) -> None:
    """Drive one background intake-phase turn (plan or triage) for `item_id` with no surface
    attached, then clear run-state. Both phases are the item's INTAKE role (§1.3), so they share
    this driver — only the skill + trigger differ.

    Always a FRESH pass (resume=None): re-reads the item and re-does the work from scratch, so a
    re-run (e.g. to try another model) actually re-does it instead of a resumed agent saying
    "already done". Any prior intake thread is replaced — its session is purged once the new one
    is recorded, keeping the picker clean (a no-op for a fresh triage, which has none). The run
    protocol (kernel-fired, report_completion ending) rides the per-turn Current-focus background
    variant — never the durable transcript, which this session keeps for later
    interactive chat. Persists the new session, sandboxes writes to the item folder; status is
    owned here: active while running → awaiting_human (the item sits at the owner's gate) on
    finish."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    # The thread being REPLACED is the item's intake thread (plan phase ⇒ intake role, §1.3) —
    # or its still-unadopted legacy `session_id` on a pre-roles item.
    prev_session = ((item.get("sessions") or {}).get("intake")
                    or item.get("session_id") or None)
    # Bank BEFORE the thread dies (compaction-redesign §13, row 5). This runner mints a fresh
    # intake session and then DELETES the previous one — so the whole triage conversation, owner
    # clarifications at the gate included, is gone with nothing carrying it. Not resuming is right
    # (a re-plan must not read as "already done") and deleting is right (the picker holds one
    # intake slot), but carrying NOTHING is not: lossy beats gone. Derived, not a model call —
    # there is no agent turn available here (the old thread is dying, nothing is running), and
    # paying a summarization call at every triage→plan for a conversation that is often just
    # "approve" is bad economics. `since=None` so it always banks, even if one landed recently.
    if prev_session:
        try:
            bank_auto_checkpoint(ctx, item_id)
        except Exception:
            log.exception("pre-replace checkpoint failed for %s", item_id)
    title = item.get("title") or item_id
    # Thin trigger: which skill for which item — nothing else. The procedure lives in the
    # superme-dev:<skill> skill (its "## Background runs" section); the run protocol rides the
    # Current-focus background variant below. Orientation is on-demand — the skill's directed
    # reads over the item folder Current focus points at (the orient birth-block is retired,
    # workflow-renovation-v2 §1). On replay, sessions._NOISE_PREFIXES drops this trigger phrase
    # (one entry per intake skill — keep in sync when adding one).
    trigger = kernel_speech.intake_trigger(skill, item_id, title)
    prompt = trigger
    capture_prompt(context_id, trigger, item_id=item_id)
    focus = kernel_speech.work_item_preamble(item_id, item, str(item_dir), interactive=False)
    # Prompt inspector "A" — throwaway probes ONLY: capture matches the real send exactly.
    # Normal items skip capture (the run_input table no longer grows per-run).
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=ctx, system_append=focus, prompt=prompt,
                          phase=skill, background=True)
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = _LiveTokens()   # dedupes the Usage stream by message_id for an accurate live estimate
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    try:
        async for ev in _agent.run_turn(
            ctx, prompt,
            resume=None,   # fresh pass — re-plan re-does the work, doesn't resume "already done"
            model=model,
            effort=effort or _spine.effective_effort(context_id),  # item → repo → system → medium
            approve=scoped_writes_approve(item_dir, deny_all),
            extra_mcp_servers={**_dev_mcp(ctx, ctx.cwd, item_id),
                               "run": make_run_report_server(sink)},
            system_append=focus,   # Current-focus pointer incl. the run protocol
        ):
            if isinstance(ev, Usage):
                live.bump(context_id, item_id, ev)
            elif isinstance(ev, (Status, ToolResult)):
                _log_artifact(context_id, item_id, ev)
            elif isinstance(ev, Result):
                final_tokens = ev.tokens
                final_usage = ev.usage
                final_session = ev.session_id
                _sessions.record(ctx, ev.session_id)
                if ev.session_id:
                    try:
                        _dev.set_work_item_session(dev_root, item_id, ev.session_id,
                                                   role="intake")
                        # Reverse stamp: the fresh background intake session is a work-item session,
                        # born here — stamp its durable identity (work-item-session-recognition-prd)
                        # + its ROLE (plan phase ⇒ intake, §1.3).
                        _spine.stamp_session_item(ev.session_id, item_id)
                        _spine.stamp_session_kind(ev.session_id, "intake")
                    except Exception:
                        log.exception("background %s: failed to persist session to %s" % (skill, item_id))
                    # The replaced thread is superseded — delete it so the picker stays clean; its
                    # run trace is preserved + labeled 'retired'.
                    if prev_session and prev_session != ev.session_id:
                        _sessions.delete(ctx, prev_session, cause="retired")
            elif isinstance(ev, Init):
                _cache_slash(ctx.id, ev.slash_commands)
            # Per-run trail for the Activity trace: the reply text + each call + its output, keyed to
            # this run (resolved from the item's running row). Parallel to the work-item run_artifact log.
            if isinstance(ev, (Status, TextDelta, ToolResult)):
                capture_event(context_id, ev, item_id=item_id)
    except Exception:
        log.exception("background %s run failed for %s", skill, item_id)
    finally:
        # Structured completion (renovation §3.2): the run's report_completion payload, delivered
        # through the sink and persisted onto the run row. A run that never called the tool is a
        # legacy/unstructured ending — warned so drift is visible.
        report = read_completion(context_id, item_id, sink)
        if not report:
            log.warning("background %s for %s ended without a report_completion call", skill, item_id)
        # Background intake finished (or died) — the item sits at the owner's pre-main gate (triage
        # exit or plan), the one status that pages them (D2 typed awaiting).
        _end_run(ctx, context_id, item_id, final_tokens, "awaiting_human", final_usage,
                 outcome=(report or {}).get("outcome"), session_id=final_session,
                 summary=str((report or {}).get("summary") or ""))
        # Session-end checkpoint hook: a background session ends here — bank the fallback if the
        # run didn't write its own checkpoint.
        try:
            bank_auto_checkpoint(ctx, item_id, since=run_started)
        except Exception:
            log.exception("auto-checkpoint after background %s failed", skill)
        log.info("background %s: done for %s", skill, item_id)


async def _run_background_resolve(ctx, context_id: str, item_id: str, worktree: Path,
                                conflicts: list[str], model: str | None = None,
                                  effort: str | None = None) -> None:
    """Resolve-with-Agent (workspace-workflow S4/D4): a conflicted freshness merge was left IN the
    item's worktree; drive one background turn that edits the conflict markers, then COMPLETE the
    merge mechanically daemon-side (marker scan + `git add` + commit — the agent never commits).
    The human decided WHETHER (they fired the route); the agent does the resolution; the item
    re-enters `vet` on success (D4: re-vet before re-presenting). Failure pages the
    owner (`awaiting_human`) with the merge still in the tree for a retry or manual abort."""
    dev_root = ctx.internal_root / "dev"
    # The conflict procedure is genuine task policy and stays in the trigger (kernel_speech).
    # No report_completion mount here: the outcome is MECHANICAL (did the merge finish), never
    # the agent's claim — this runner has no structured-report seam by design.
    prompt = kernel_speech.resolve_trigger(worktree, item_id, conflicts)
    capture_prompt(context_id, prompt, item_id=item_id)
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = _LiveTokens()
    try:
        async for ev in _agent.run_turn(
            ctx, prompt,
            resume=None,
            model=model,
            effort=effort or _spine.effective_effort(context_id),
            approve=scoped_writes_approve(worktree, deny_all),
            extra_mcp_servers=_dev_mcp(ctx, worktree, item_id),  # F8-residual: dev tools mounted
        ):
            if isinstance(ev, Usage):
                live.bump(context_id, item_id, ev)
            elif isinstance(ev, (Status, ToolResult)):
                _log_artifact(context_id, item_id, ev)
            elif isinstance(ev, Result):
                final_tokens = ev.tokens
                final_usage = ev.usage
                final_session = ev.session_id
                _sessions.record(ctx, ev.session_id)
            if isinstance(ev, (Status, TextDelta, ToolResult)):
                capture_event(context_id, ev, item_id=item_id)
    except Exception:
        log.exception("background resolve run failed for %s", item_id)
    # Mechanically finish the merge — ground truth (marker scan + git state), not the agent's claim.
    resolved = False
    detail = ""
    try:
        res = git_layer.finish_merge(worktree)
        resolved = True
        detail = f"merge completed at {res['commit'][:10]}"
    except git_layer.GitError as e:
        detail = str(e)
    outcome = "success" if resolved else "blocked"
    if resolved:
        item = _dev.read_work_item(dev_root, item_id) or {}
        revet = str(item.get("phase")) == "review"
        if revet:                               # re-enters vet before re-presenting (D4)
            reset_vet_thread(ctx, item)         # vet forgets — fresh vetter for the re-entry
            _dev.set_work_item_phase(dev_root, item_id, "vet")
            # Every phase move lands in the trail — this is the one non-gate transition.
            _dev_store.log_event(context_id, "phase.advance",
                                 "Conflict resolved — re-entering vet before re-presenting",
                                 item_id=item_id, actor="daemon",
                                 meta={"from": "review", "to": "vet"})
        _end_run(ctx, context_id, item_id, final_tokens, "active", final_usage, outcome=outcome,
                 session_id=final_session)
        # …and something must actually RUN behind that `active`. This runner used to flip the phase
        # and stop: no vet run was ever dispatched, so the item sat at `vet` · `active` with nothing
        # working — the P6 idle stall, and the worst shape it takes, because `active` doesn't page
        # the owner either. The board showed "IN PROGRESS" with a frozen timer forever. (Found live,
        # 2026-07-29, on the first real conflict.) Fired AFTER `_end_run` — the resolve run still
        # holds the item's lock until then, and `start_vet_run` refuses while a run is in flight.
        if revet:
            from .loop import start_vet_run
            started, why = start_vet_run(ctx, context_id, item_id)
            if not started:
                # Never leave `active` with no run: rest it where the owner can see it instead.
                _dev.set_work_item_status(dev_root, item_id, "awaiting_human")
                log.warning("resolve: vet re-entry did not start for %s (%s)", item_id, why)
    else:
        # Conflicts remain in the tree (deliberate — retry or manual abort); page the owner.
        _end_run(ctx, context_id, item_id, final_tokens, "awaiting_human", final_usage,
                 outcome=outcome, session_id=final_session)
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after resolve failed")
    _dev_store.log_event(context_id, "git.resolve",
                         f"Conflict resolution {'succeeded' if resolved else 'FAILED'}: {detail}",
                         item_id=item_id, actor="daemon", meta={"resolved": resolved})
    log.info("background resolve: %s for %s (%s)", "done" if resolved else "failed", item_id, detail)


def build_item_timeline(context_id: str, item_id: str) -> dict:
    """The F2 unified timeline read model: every run this item has had, oldest-first, each tagged
    with its phase · role (feature) · model and carrying its ordered turn events (prompt · reply ·
    calls · results). The chat panel loads this on open, then live-appends new frames from
    `item_stream`. Chronological across phases, so triage→plan→build→vet→review reads as one
    conversation. Read-only mirror — the durable source is the run/run_event tables."""
    runs = _spine.runs_for_item(context_id, item_id)
    out = []
    for r in runs:
        rid = r.get("id")
        out.append({
            "run_id": rid,
            "phase": r.get("phase"),
            "feature": r.get("feature"),      # chat (interactive) · deputy · triage/plan/build/vet/close
            "model": r.get("model"),
            "status": r.get("status"),
            "started_at": r.get("started_at"),
            "events": _spine.events_for_run(rid) if rid else [],
        })
    return {"item_id": str(item_id), "runs": out}


def _render_execution_md(context_id: str, item_id: str, item: dict) -> str:
    """Snapshot a work-item's execution trace (its run call-trail + per-run telemetry) to Markdown,
    so the item folder carries its own copy after completion (the spine rows themselves are
    permanent — never-delete-logs). Chronological (oldest run
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
