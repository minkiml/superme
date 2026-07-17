"""Run lifecycle — the orchestrator-owned helpers that drive a work-item's headless runs.

Open/close spine run rows, accumulate live telemetry, map tool-use to call-trail artifacts, drive
the headless /plan turn, and snapshot the execution trace on completion. The work-item routes (in
server.py / the work_items router) call these; they own run-state, the route owns the HTTP shape.

Imports singletons from `app_state` (never from server.py) so there's no import cycle.
"""

import logging
import time
from datetime import datetime
from pathlib import Path

from ..app_state import agent as _agent, dev as _dev, dev_store as _dev_store, \
    spine as _spine, sessions as _sessions
from ..deps import cache_slash as _cache_slash
from ...core import Init, Usage, Result, Status, TextDelta, ToolResult, scoped_writes_approve, deny_all
from ...core import artifacts as _arts
from ...core import git_layer, session_contract
from ...core.models import MODEL_TIERS

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
             ctx_pct: int | None = None, outcome: str | None = None) -> None:
    """Close out a run: finalize its spine row (keeping the accumulated live token sum, or the
    passed Result aggregate as a fallback) and set the work-item's resting status. Interactive
    (bound-chat) turns rest at `active`; a HEADLESS phase run that ends at a human gate passes
    `awaiting_human` (the only status that pages the owner — D2 typed awaiting; the full
    completion-report router lands S5). `kind` is recovered from the running row for the end event.
    `usage` is the whole-turn final dict — the typed-column fallback if no per-step Usage arrived.
    `ctx_pct` is the authoritative Result fill — persisted over the live-bump estimate (chat runs do
    the same via finish_run), so an item card's ctx% matches the true end-of-turn occupancy."""
    info = _spine.live_run(context_id, item_id)
    kind = (info or {}).get("feature", "plan")
    rid = _spine.finish_item_run(context_id, item_id, fallback_tokens=tokens, usage=usage,
                                 ctx_pct=ctx_pct, outcome=outcome)
    # Log the AUTHORITATIVE total that finish just reconciled onto the row (3-type, excl. cache_read —
    # the same basis the item card shows), NOT the pre-finish `info` snapshot, whose live `tokens` sums
    # the cumulative-for-the-turn Usage snapshots and so over-counts (that mismatch was the "62k vs 305k").
    total = _spine.run_tokens(rid) if rid else (tokens or 0)
    _set_status(ctx, item_id, status)
    # Run end — item-scoped, with the final token total. PRD §4.9.
    _dev_store.log_event(context_id, f"{kind}.end", f"Finished {kind} run · Σ {total} tok",
                         item_id=item_id, actor="daemon", meta={"tokens": total})


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


def capture_event(repo_id: str, ev, *, run_id: int | None = None, item_id: str | None = None) -> None:
    """Record one turn event onto a run's trail: a Status → its tool/skill/agent call, a ToolResult →
    that call's (capped) output, a TextDelta → an assistant reply block. Anything else is ignored.
    Best-effort (log_run_event never raises)."""
    if isinstance(ev, Status):
        kind, head, detail = _artifact_desc(ev.tool_name, ev.tool_input or {})
        _spine.log_run_event(repo_id=repo_id, kind=kind, name=head, description=detail,
                             run_id=run_id, item_id=item_id, tool_id=ev.tool_id)
    elif isinstance(ev, ToolResult):
        name, desc = _result_row(ev)
        # Record with the tool_use id so the FE pairs result→call exactly (concurrent tools return
        # out of order). Empty output stays empty (the call just won't be expandable).
        _spine.log_run_event(repo_id=repo_id, kind="result", name=name, description=desc,
                             run_id=run_id, item_id=item_id, tool_id=ev.tool_id)
    elif isinstance(ev, TextDelta):
        txt = (ev.text or "").strip()
        if txt:
            _spine.log_run_event(repo_id=repo_id, kind="reply", name="reply", description=txt[:_REPLY_CAP],
                                 run_id=run_id, item_id=item_id)


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
            working_on=f"{item.get('phase') or 'triage'} phase — {item.get('title') or item_id}",
            decisions="(auto-banked at session end — the session's reasoning lives in its transcript)",
            remaining=remaining,
            notes="AUTO checkpoint written by the daemon because the session ended without banking "
                  "one. Derived data only — verify against the artifacts before relying on it.",
        )
        return True
    except ValueError:
        return False


async def _run_headless_plan(ctx, context_id: str, item_id: str, item_dir: Path,
                             model: str | None = None, effort: str | None = None) -> None:
    """Drive one /plan turn for `item_id` with no surface attached, then clear run-state.

    Always a FRESH pass (resume=None): a headless plan re-reads the item and re-plans from
    scratch, so re-planning (e.g. to try another model) actually re-does the work instead of a
    resumed agent saying "already done". The prior plan thread is replaced — its session is
    purged once the new one is recorded, keeping the picker clean. Runs autonomously (the prompt
    forbids questions), persists the new session, sandboxes writes to the item folder; status is
    owned here: active while running → awaiting_human (the plan sits at the owner's gate) on finish."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    prev_session = item.get("session_id") or None
    title = item.get("title") or item_id
    # Thin trigger: name the skill, the item, and the mode — nothing else. The steps live in
    # the superme-dev:plan skill (its "Autonomous (headless) runs" section), the single
    # source of truth, so this can't drift from it. On replay, sessions._strip_birth_block cuts
    # the orient prefix and sessions._NOISE_PREFIXES drops this trigger phrase — keep in sync.
    # NB: this session PERSISTS as the item's bound session and is later resumed for interactive
    # chat — so every headless claim below is scoped "for THIS run" (and the interactive preamble
    # states a human is present), or the transcript would keep asserting "no human" forever (H1).
    trigger = (
        f"Run the superme-dev:plan skill for work-item `{item_id}` (\"{title}\") in "
        f"autonomous mode — for THIS run only, no human is in this chat. Follow the "
        f"skill's autonomous-run instructions.\n\n{session_contract.completion_report_instructions()}"
    )
    # Cold-start orient block (S5): a headless plan is always a FRESH session, so its birth prompt
    # carries the same kernel-assembled orientation an interactive session gets.
    orient = session_contract.render_orient_block(item, item_dir)
    prompt = f"{orient}\n\n---\n\n{trigger}"
    # The trail's first entry = what this run was asked to do (the trigger, not the orient bulk) —
    # interactive turns get this from the ws path; headless runs record their own.
    capture_prompt(context_id, trigger, item_id=item_id)
    final_tokens = None
    final_usage = None
    final_text = None
    run_started = time.time()
    live = _LiveTokens()   # dedupes the Usage stream by message_id for an accurate live estimate
    try:
        async for ev in _agent.run_turn(
            ctx, prompt,
            resume=None,   # fresh pass — re-plan re-does the work, doesn't resume "already done"
            model=model,
            effort=effort or _spine.effective_effort(context_id),  # item → repo → system → medium
            approve=scoped_writes_approve(item_dir, deny_all),
        ):
            if isinstance(ev, Usage):
                live.bump(context_id, item_id, ev)
            elif isinstance(ev, (Status, ToolResult)):
                _log_artifact(context_id, item_id, ev)
            elif isinstance(ev, Result):
                final_tokens = ev.tokens
                final_usage = ev.usage
                final_text = ev.text
                _sessions.record(ctx, ev.session_id)
                if ev.session_id:
                    try:
                        _dev.set_work_item_session(dev_root, item_id, ev.session_id)
                        # Reverse stamp: the fresh headless-plan session is a work-item session,
                        # born here — stamp its durable identity (work-item-session-recognition-prd).
                        _spine.stamp_session_item(ev.session_id, item_id)
                    except Exception:
                        log.exception("headless plan: failed to persist session to %s", item_id)
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
        log.exception("headless plan run failed for %s", item_id)
    finally:
        # Structured completion contract (S5/D2): parse the run's final report and persist its
        # outcome onto the run row. A missing/invalid report = None (legacy/unstructured run —
        # the event flags it so drift is visible).
        report = session_contract.parse_completion_report(final_text)
        if report:
            _dev_store.log_event(context_id, "run.report",
                                 f"{report['outcome']}: {report['summary'][:160]}",
                                 item_id=item_id, actor="agent", meta=report)
        else:
            log.warning("headless plan for %s ended without a completion report", item_id)
        # Headless plan finished (or died) — the plan sits at the owner's pre-main gate, the one
        # status that pages them (D2 typed awaiting).
        _end_run(ctx, context_id, item_id, final_tokens, "awaiting_human", final_usage,
                 outcome=(report or {}).get("outcome"))
        # Session-end checkpoint hook: a headless session ends here — bank the fallback if the
        # run didn't write its own checkpoint.
        try:
            bank_auto_checkpoint(ctx, item_id, since=run_started)
        except Exception:
            log.exception("auto-checkpoint after headless plan failed")
        log.info("headless plan: done for %s", item_id)


async def _run_headless_resolve(ctx, context_id: str, item_id: str, worktree: Path,
                                conflicts: list[str], model: str | None = None,
                                effort: str | None = None) -> None:
    """Resolve-with-Agent (workspace-workflow S4/D4): a conflicted freshness merge was left IN the
    item's worktree; drive one headless turn that edits the conflict markers, then COMPLETE the
    merge mechanically daemon-side (marker scan + `git add` + commit — the agent never commits).
    The human decided WHETHER (they fired the route); the agent does the resolution; the item
    re-enters `validate` on success (D4: re-validate before re-presenting). Failure pages the
    owner (`awaiting_human`) with the merge still in the tree for a retry or manual abort."""
    dev_root = ctx.internal_root / "dev"
    files = "\n".join(f"- {f}" for f in conflicts) or "- (see `git status` in the worktree)"
    prompt = (
        f"A sync-from-main merge left CONFLICTS in this work-item's git worktree at `{worktree}` "
        f"(work-item `{item_id}`). Conflicted files:\n{files}\n\n"
        f"Resolve every conflict marker (`<<<<<<<`/`=======`/`>>>>>>>`) in these files, honoring "
        f"BOTH sides' intent: keep this item's changes AND the incoming trunk changes semantically "
        f"intact — never resolve by discarding one side wholesale unless the file makes that "
        f"clearly correct and no features should be lost or broken. Edit the files in place. "
        f"Do NOT run git commands and do NOT commit — your job is done when every conflict "
        f"marker in these files is resolved and the files are saved.\n\n"
        f"{session_contract.completion_report_instructions()}"
    )
    capture_prompt(context_id, prompt, item_id=item_id)
    final_tokens = None
    final_usage = None
    final_text = None
    run_started = time.time()
    live = _LiveTokens()
    try:
        async for ev in _agent.run_turn(
            ctx, prompt,
            resume=None,
            model=model,
            effort=effort or _spine.effective_effort(context_id),
            approve=scoped_writes_approve(worktree, deny_all),
        ):
            if isinstance(ev, Usage):
                live.bump(context_id, item_id, ev)
            elif isinstance(ev, (Status, ToolResult)):
                _log_artifact(context_id, item_id, ev)
            elif isinstance(ev, Result):
                final_tokens = ev.tokens
                final_usage = ev.usage
                final_text = ev.text
            if isinstance(ev, (Status, TextDelta, ToolResult)):
                capture_event(context_id, ev, item_id=item_id)
    except Exception:
        log.exception("headless resolve run failed for %s", item_id)
    # Mechanically finish the merge — ground truth (marker scan + git state), not the agent's claim.
    resolved = False
    detail = ""
    try:
        res = git_layer.finish_merge(worktree)
        resolved = True
        detail = f"merge completed at {res['commit'][:10]}"
    except git_layer.GitError as e:
        detail = str(e)
    # The MECHANICAL result outranks the agent's own report (ground truth over claims): a report
    # is recorded, but the persisted outcome is success/blocked by whether the merge finished.
    report = session_contract.parse_completion_report(final_text)
    outcome = "success" if resolved else ((report or {}).get("outcome") or "blocked")
    if resolved:
        item = _dev.read_work_item(dev_root, item_id) or {}
        if str(item.get("phase")) == "deliver":  # re-enters validate before re-presenting (D4)
            _dev.set_work_item_phase(dev_root, item_id, "validate")
            # Every phase move lands in the trail — this is the one non-gate transition.
            _dev_store.log_event(context_id, "phase.advance",
                                 "Conflict resolved — re-entering validate before re-presenting",
                                 item_id=item_id, actor="daemon",
                                 meta={"from": "deliver", "to": "validate"})
        _end_run(ctx, context_id, item_id, final_tokens, "active", final_usage, outcome=outcome)
    else:
        # Conflicts remain in the tree (deliberate — retry or manual abort); page the owner.
        _end_run(ctx, context_id, item_id, final_tokens, "awaiting_human", final_usage, outcome=outcome)
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after resolve failed")
    _dev_store.log_event(context_id, "git.resolve",
                         f"Conflict resolution {'succeeded' if resolved else 'FAILED'}: {detail}",
                         item_id=item_id, actor="daemon", meta={"resolved": resolved})
    log.info("headless resolve: %s for %s (%s)", "done" if resolved else "failed", item_id, detail)


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
