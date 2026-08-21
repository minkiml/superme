"""Run lifecycle — the helpers that drive a work-item's background runs.

Open and close spine run rows, accumulate telemetry, map tool-use to trail rows, and snapshot
the execution trace.

Imports singletons from `app_state`, never server.py, to avoid an import cycle.
"""

import asyncio
import json as _json
import logging
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ..app_state import agent as _agent, dev as _dev, dev_store as _dev_store, \
    spine as _spine, sessions as _sessions
from ..deps import cache_slash as _cache_slash
from . import item_stream, run_tasks
from ...core import Init, Usage, Result, Status, TextDelta, ToolResult, scoped_writes_approve, deny_all
from ...core import artifacts as _arts
from ...core import autopilot as _autopilot
from ...core.autopilot import PROMPT_EXTRACTION_FEATURE
from ...core import git_layer, kernel_speech, kind_profiles
from ...core import sandbox as _sandbox
from ...core.faults import RETRY_LADDER
from ...core.models import MODEL_TIERS
from ...harness.tools.dev_tools import make_dev_mcp_server
from ...harness.tools.run_tools import make_run_report_server
from .turns import ResilientTurn

log = logging.getLogger("superme-agent")

# Concrete id, not the `sonnet` alias, which lags behind the latest release.
DEFAULT_RUN_MODEL = MODEL_TIERS["sonnet"]


def stop_item_work(context_id: str, item_id: str, *, expect_live: bool = False) -> tuple[int, bool]:
    """End an item's work for real — cancel the TASK, then close its run ROWS.

    Cancel first: releasing rows alone leaves the coroutine writing into a doomed folder. Rows
    close unconditionally, since cancellation lands only at a suspension point."""
    cancelled = run_tasks.cancel(context_id, item_id, expect_live=expect_live)
    freed = _spine.release_item_runs(context_id, item_id)
    if cancelled:
        log.info("stopped live task for %s/%s before releasing %d run row(s)",
                 context_id, item_id, freed)
    return freed, cancelled


def mark_item_error(ctx, context_id: str, item_id: str, reason: str, *, phase: str = "") -> bool:
    """Stop an item at `error` — the one writer of that status.

    The item stays where it died rather than claiming a decision is wanted or that work is running.
    Never terminal: `error` is what Resume and re-run read."""
    if not (item_id and getattr(ctx, "internal_root", None)):
        return False
    try:
        line = " ".join(str(reason or "").split()) or "the work stopped unexpectedly"
        if not _dev.set_work_item_error(ctx.internal_root / "dev", item_id, line):
            return False
        _dev_store.log_event(
            context_id, "run.error",
            f"Work stopped{f' during {phase}' if phase else ''} — {line[:160]}",
            item_id=item_id, actor="daemon", meta={"phase": phase, "reason": line})
        log.warning("item %s stopped at error%s: %s", item_id,
                    f" ({phase})" if phase else "", line[:160])
        return True
    except Exception:
        log.exception("could not mark %s as error", item_id)
        return False


def retry_notice(context_id: str, item_id: str, phase: str):
    """The trail a retry leaves: a run asleep on the backoff ladder is indistinguishable from a
    hung one."""
    def _notify(fault, attempt: int, delay: int) -> None:
        try:
            _dev_store.log_event(
                context_id, "run.retry",
                f"{phase.capitalize()} paused — {fault.reason}. Retry {attempt} of "
                f"{len(RETRY_LADDER)} in {max(1, delay // 60)} min",
                item_id=item_id, actor="daemon",
                meta={"phase": phase, "kind": fault.kind, "attempt": attempt, "delay": delay})
        except Exception:
            log.exception("retry notice failed for %s", item_id)
    return _notify


def _set_status(ctx, item_id: str, status: str) -> None:
    """Set a work-item's run-state status (orchestrator-owned). Best-effort; logs on failure.

    Never overwrites a typed pause: resting an `awaiting_child` item back to `active` at turn end
    would silently un-pause it. Only the status router may resume a paused parent."""
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
    """Open a run row only if the item isn't already running, then rest it `active` and log.

    That row is both the live state and the per-item run-lock, so a check-then-start race cannot
    lose it."""
    run_id = _spine.start_item_run(context_id, mode=ctx.mode, feature=kind,
                                   item_id=item_id, model=model, phase=phase)
    if run_id is None:
        return None  # already running — no status flip, no event
    # A starting run means the item is being worked; "running now" is derived from the live run
    # row.
    _set_status(ctx, item_id, "active")
    _dev_store.log_event(context_id, f"{kind}.start", f"Started {kind} run",
                         item_id=item_id, actor="daemon", meta={"model": model})
    return run_id  # the live run id — the caller keys its per-run event trail on it


class _LiveTokens:
    """Per-run token tally, deduped by `message_id` — the run's authoritative total.

    The SDK emits one Usage step per content block, so summing steps over-counts; latest-per-id
    leaves one entry per API call. `Result.usage` is parent-only and misses every subagent call."""

    _KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
             "output_tokens")

    def __init__(self) -> None:
        self._by_msg: dict[str, dict] = {}
        self._legacy: list[dict] = []      # steps from an SDK build with no message_id

    def bump(self, context_id: str, item_id: str, ev) -> None:
        """Record one step. NEVER raises: this runs inside the run's own task, so anything escaping kills
        the WORK."""
        try:
            mid = getattr(ev, "message_id", None)
            step = dict(getattr(ev, "usage", None) or {})
            if mid:
                self._by_msg[mid] = step   # latest wins — usage can grow within one message
            else:
                self._legacy.append(step)
            _spine.set_item_run_tokens(
                context_id, item_id, tokens=self.tokens(), ctx_pct=ev.ctx_pct,
            )
        except Exception:
            log.exception("live token bump failed for %s — the run continues, uncounted", item_id)

    @staticmethod
    def _num(value) -> int:
        """A usage field as an int, or 0 for anything else. The SDK's usage dict is external data and this
        runs on every step, so an unexpected value must never stop the work."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if value not in (None, ""):
                log.warning("unusable token value in SDK usage (%r) — counted as 0", value)
            return 0
        return int(value)

    def usage(self, final: dict | None = None) -> dict | None:
        """The whole turn's usage — parent and subagents — or None if none arrived.

        `final` is `Result.usage`, read for one field: output_tokens, which per-message usage only
        reports as a placeholder. Subagent output stays uncounted, deliberately."""
        steps = list(self._by_msg.values()) + self._legacy
        if not steps:
            return None
        summed = {k: sum(self._num(s.get(k)) for s in steps) for k in self._KEYS}
        summed["output_tokens"] = max(summed["output_tokens"],
                                      self._num((final or {}).get("output_tokens")))
        return summed

    def tokens(self) -> int:
        """The 3-type scalar (input + cache_creation + output; cache_read excluded) — what the running row
        and the card footer show."""
        u = self.usage() or {}
        return (self._num(u.get("input_tokens")) + self._num(u.get("cache_creation_input_tokens"))
                + self._num(u.get("output_tokens")))


def _end_run(ctx, context_id: str, item_id: str, tokens: int | None,
             status: str = "active", usage: dict | None = None,
             ctx_pct: int | None = None, outcome: str | None = None,
             session_id: str | None = None, summary: str = "") -> None:
    """Finalize a run's spine row and rest the work-item.

    Interactive turns rest `active`; a background run ending at a human gate passes
    `awaiting_human`, the only status that pages the owner. `ctx_pct` is the authoritative
    end-of-turn fill."""
    info = _spine.live_run(context_id, item_id)
    kind = (info or {}).get("feature", "plan")
    # The run's PHASE, not its feature — route on this, label the surface with `kind`.
    run_phase = str((info or {}).get("phase") or kind)
    rid = _spine.finish_item_run(context_id, item_id, fallback_tokens=tokens, usage=usage,
                                 ctx_pct=ctx_pct, outcome=outcome, session_id=session_id)
    # The authoritative 3-type total finish just reconciled onto the row; the pre-finish snapshot
    # over-counts.
    total = _spine.run_tokens(rid) if rid else (tokens or 0)
    _set_status(ctx, item_id, status)
    # Every run is offered `scratch/` and most never write there; sweeping must never block
    # finishing a run.
    if (root := getattr(ctx, "internal_root", None)) is not None:
        _sandbox.prune_scratch(Path(root) / "dev" / "work-items" / item_id)
    _dev_store.log_event(context_id, f"{kind}.end", f"Finished {kind} run · Σ {total} tok",
                         item_id=item_id, actor="daemon", meta={"tokens": total})
    # Scheduled, not inline: this run's lock must be fully released before the next phase begins.
    if kind != "compact" and session_id:
        from . import compaction
        compaction.note_turn_start(session_id)
    # Only a review run routes here — a build's `revise` belongs to its own driver.
    if outcome == "revise" and run_phase == "review":
        try:
            fire_phase_feedback(context_id, item_id, phase="review",
                                feedback=summary or "the review concluded this needs re-planning",
                                by="owner")
        except Exception:
            log.exception("revise routing failed for %s (item stays at review)", item_id)
        return
    if outcome == "revise":
        return   # the phase's driver routes it — see loop.background_build_cycle
    if status == "awaiting_human" and outcome != "needs_user":
        try:
            from .gates import maybe_autopilot_advance
            asyncio.get_running_loop().call_soon(
                maybe_autopilot_advance, context_id, item_id)
        except RuntimeError:
            pass  # no running loop (sync/test context) — driver is tested directly


# Tool-use Status events map to (kind, head, detail). The UI shows "head - detail", like the CLI's
# call lines.
def _short_path(p, keep: int = 4) -> str:
    """The last `keep` segments of a path, `…/`-prefixed when elided. The UI truncates the tail, so
    keep the meaningful end."""
    parts = [x for x in str(p or "").split("/") if x]
    if not parts:
        return ""
    return "/".join(parts) if len(parts) <= keep else "…/" + "/".join(parts[-keep:])


def _int_or_none(value) -> int | None:
    """An int, or None for anything else a tool's arguments hold — agent-written input is untrusted
    shape."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _artifact_payload(tool_name: str, ti: dict) -> str | None:
    """The row's full text, for rows where the TEXT is audited — only a sub-agent's brief today,
    because a spawned worker inherits nothing. `None` elsewhere: a row's description is its content."""
    if tool_name in ("Task", "Agent"):
        return str(ti.get("prompt") or "") or None
    return None


def _artifact_desc(tool_name: str, ti: dict) -> tuple[str, str, str]:
    """Map a tool-use to (kind, head, detail). kind ∈ tool|subagent|skill|mcp."""
    ti = ti or {}
    base = _short_path
    # A spawn arrives as `Task` or `Agent` depending on SDK build; render which agent ran, not a
    # generic "Agent".
    if tool_name in ("Task", "Agent"):
        # Name the worker when the spawn said which; the bare form is the honest fallback.
        who = (ti.get("subagent_type") or ti.get("subagentType") or ti.get("agent_type")
               or ti.get("agentType") or ti.get("name") or ti.get("description") or "")
        # The model, when the spawn overrode it — otherwise the trail cannot show whether a per-
        # phase instruction was followed.
        model = ti.get("model") or ti.get("modelName")
        # A subagent inherits nothing, so whatever the brief omits is worked without. Size is all
        # a row can hold.
        brief = str(ti.get("prompt") or "")
        parts = [str(who).strip(), str(model).strip() if model else "",
                 f"brief {len(brief)}" if brief else ""]
        inner = " · ".join(x for x in parts if x)
        return "subagent", "Agent", (f"Subagent ({inner[:48]})" if inner else "Subagent")
    if tool_name == "Skill":
        # The skill identity may arrive under any of several keys depending on the SDK build.
        name = (ti.get("command") or ti.get("name") or ti.get("skill")
                or ti.get("skill_name") or ti.get("skillName") or "")
        return "skill", "skill", str(name).lstrip("/")
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        return "mcp", "mcp", parts[-1] if parts else tool_name
    if tool_name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = base(ti.get("file_path") or ti.get("notebook_path"))
        # A read's span, when it named one: the path alone cannot distinguish 20 lines from a
        # whole 46 KB file.
        if tool_name == "Read":
            # `offset`/`limit` are whatever the agent typed, not a validated shape; a label must
            # never crash the run it describes.
            off, lim = _int_or_none(ti.get("offset")), _int_or_none(ti.get("limit"))
            if off is not None or lim is not None:
                start = off or 0
                span = f"{start}-{start + lim}" if lim is not None else f"{start}+"
                return "tool", tool_name, f"{path} [{span}]"
            return "tool", tool_name, f"{path} [whole]"
        return "tool", tool_name, path
    if tool_name == "Bash":
        # 1200 characters, so two commands differing only in a path argument still render
        # differently. The UI truncates the row.
        return "tool", "Bash", (str(ti.get("command") or "") or str(ti.get("description", "")))[:1200]
    if tool_name in ("Grep", "Glob"):
        return "tool", tool_name, str(ti.get("pattern", ""))
    if tool_name in ("WebFetch", "WebSearch"):
        return "tool", tool_name, str(ti.get("url") or ti.get("query") or "")[:60]
    if tool_name == "ToolSearch":
        # what it searched for — the deferred-tool query (e.g. "select:Read,Edit" or keywords).
        return "tool", "ToolSearch", str(ti.get("query", ""))[:60]
    return "tool", tool_name, ""


# --- per-run trail caps ---
_PROMPT_CAP = 4000   # a run's trigger prompt, trimmed
_REPLY_CAP = 8000    # one assistant text block, trimmed
_RESULT_CAP = 1200   # a tool's output, trimmed — enough to show what it returned without bloating the trail


def _result_row(ev: ToolResult) -> tuple[str, str]:
    """Map a ToolResult to (name, description). Its `tool_id` pairs it back to the call, since
    concurrent tools return out of order."""
    _, head, detail = _artifact_desc(ev.tool_name, {})
    name = detail or head
    body = (ev.content or "").strip()
    if ev.is_error:
        body = "[error] " + body
    return name, body[:_RESULT_CAP]


# `run_event` is the one trail; `run_artifact` is frozen history with no writer.


def capture_prompt(repo_id: str, prompt: str, *, run_id: int | None = None,
                   item_id: str | None = None) -> None:
    """Record the prompt that opened a run as the first entry of its trail."""
    _spine.log_run_event(repo_id=repo_id, kind="prompt", name="prompt",
                         description=(prompt or "").strip()[:_PROMPT_CAP], run_id=run_id, item_id=item_id)


def turn_surface(*, model: str | None = None, effort: str | None = None,
                 mcp: list[str] | None = None, write_boundary: list | None = None,
                 sandbox_writes: list | None = None, read_only: bool = False,
                 approve: str = "denied", resumes: bool = False) -> dict:
    """What the turn is ALLOWED to do — a run's input that isn't prose.

    Two runs can carry the same words and behave differently because one could run a shell. Nothing
    here is read back."""
    return {"model": model or "", "effort": effort or "",
            "mcp": sorted(mcp or []),
            "write_boundary": [str(p) for p in (write_boundary or [])],
            "sandbox_writes": [str(p) for p in (sandbox_writes or [])],
            "read_only": bool(read_only), "approve": approve, "resumes": bool(resumes)}


def surface_from_turn(turn_kwargs: dict, *, mcp: list[str] | None = None) -> dict:
    """`turn_surface` read off the kwargs a turn is actually sent with.

    A restatement can be wrong: an intake run recorded a `write_boundary` the stream below it never
    passed, so every shell command was refused and the capture said otherwise."""
    return turn_surface(
        model=turn_kwargs.get("model"),
        effort=turn_kwargs.get("effort"),
        mcp=mcp if mcp is not None else sorted(turn_kwargs.get("extra_mcp_servers") or {}),
        write_boundary=turn_kwargs.get("write_boundary"),
        sandbox_writes=turn_kwargs.get("sandbox_writes"),
        read_only=bool(turn_kwargs.get("deny_write_tools")),
        resumes=bool(turn_kwargs.get("resume")),
    )


def _authored_extras(ctx, item: dict, phase: str | None, mcp: list[str]) -> dict:
    """The prompt text SuperMe authors OUTSIDE the system append: the phase SKILL.md and the mounted
    MCP tool docs.

    Both ride in every request, so they belong on the same page as the prose. A missing one drops
    only its fragment."""
    from ...harness.tools.base_tools import BASE_TOOLS
    from ...harness.tools.dev_tools import dev_tool_specs
    from ...harness.tools.registry import describe_specs
    from ...harness.tools.run_tools import DEPUTY_VERDICT_TOOL, REPORT_COMPLETION_TOOL
    from ...paths import DEV_PLUGIN_DIR

    skills: list[dict] = []
    contract = kernel_speech.phase_contract(item.get("kind"), str(phase or ""))
    name = contract.get("skill")
    if name:
        path = DEV_PLUGIN_DIR / "skills" / name / "SKILL.md"
        if path.is_file():
            skills.append({"name": f"superme-dev:{name} — SKILL.md",
                           "location": f"plugins/superme-dev/skills/{name}/SKILL.md",
                           "text": path.read_text()})
    # `superme` is always mounted; the rest come from the run's own `extra_mcp_servers`. An
    # unrecognised one drops the fragment.
    try:
        dev_specs = dev_tool_specs(name or str(phase or ""))
    except KeyError:
        dev_specs = []
    by_server = {"superme": BASE_TOOLS, "dev": dev_specs,
                 "run": [REPORT_COMPLETION_TOOL], "deputy": [DEPUTY_VERDICT_TOOL]}
    tools: list[dict] = []
    for server in sorted(set(mcp) | {"superme"}):
        specs = by_server.get(server)
        if not specs:
            continue
        tools.append({"name": f"mcp__{server}__* — {len(specs)} tools",
                      "location": f"harness/tools · the `{server}` MCP server",
                      "text": describe_specs(specs)})
    return {"skills": skills, "tools": tools}


def capture_run_input(context_id: str, item_id: str, *, ctx, system_append: str | None,
                      prompt: str, background: bool, phase: str | None,
                      surface: dict | None = None) -> None:
    """Persist the ACTUAL input a run is about to send: the assembled system prompt plus the prompt
    body, keyed to the live run.

    Called only for throwaway prompt-extraction items, so `run_input` stops growing per-run.
    Best-effort — never breaks a turn."""
    try:
        info = _spine.live_run(context_id, item_id)
        rid = (info or {}).get("id")
        if rid is None:
            return
        _spine.set_run_feature(rid, PROMPT_EXTRACTION_FEATURE)
        # `item_bound=True` changes what the operating-context fragment renders; without it the
        # preview shows a prompt no run sent.
        system_prompt = _agent.assemble_system_append(ctx, system_append=system_append,
                                                      item_bound=True)
        # Provenance breakdown from the same builder, so the fragments sum to `system_prompt`.
        # Guarded: a hiccup must not lose the capture.
        try:
            frags = _agent.assemble_system_fragments(ctx, system_append=system_append,
                                                     item_bound=True)
            fragments_json = _json.dumps(frags, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            fragments_json = None
        # Same guard: losing the skill body must never cost us the prose.
        try:
            item = (_dev.read_work_item(ctx.internal_root / "dev", item_id) or {}) \
                if getattr(ctx, "internal_root", None) else {}
            extras_json = _json.dumps(
                _authored_extras(ctx, item, phase, list((surface or {}).get("mcp") or [])),
                ensure_ascii=False)
        except Exception:  # noqa: BLE001
            extras_json = None
        _spine.record_run_input(rid, repo_id=context_id, item_id=item_id, phase=phase,
                                feature=PROMPT_EXTRACTION_FEATURE, background=background,
                                system_prompt=system_prompt, prompt_body=prompt,
                                system_fragments=fragments_json, authored_extras=extras_json,
                                turn_surface=(_json.dumps(surface, ensure_ascii=False)
                                              if surface else None))
    except Exception:
        log.exception("capture_run_input failed for %s", item_id)


def capture_event(repo_id: str, ev, *, run_id: int | None = None, item_id: str | None = None,
                  publish_live: bool = True) -> None:
    """Record one turn event onto a run's trail and publish it to any panel watching the item.
    `publish_live=False` for the ws turn, which streams itself.

    NEVER RAISES: this runs inside the run's own task, so anything escaping kills the WORK."""
    try:
        _capture_event(repo_id, ev, run_id=run_id, item_id=item_id, publish_live=publish_live)
    except Exception:
        log.exception("trail capture failed for %s — the run continues, this event unrecorded",
                      item_id or repo_id)


def _capture_event(repo_id: str, ev, *, run_id: int | None = None, item_id: str | None = None,
                   publish_live: bool = True) -> None:
    # Resolve once here — a live frame with `run_id=None` cannot be matched to the history the
    # browser already holds.
    if run_id is None and item_id is not None:
        run_id = _spine.running_run_id(repo_id, item_id)
    if isinstance(ev, Status):
        kind, head, detail = _artifact_desc(ev.tool_name, ev.tool_input or {})
        _spine.log_run_event(repo_id=repo_id, kind=kind, name=head, description=detail,
                             run_id=run_id, item_id=item_id, tool_id=ev.tool_id,
                             parent_tool_id=ev.parent_tool_id,
                             payload=_artifact_payload(ev.tool_name, ev.tool_input or {}))
        if publish_live:
            _publish_timeline(item_id, run_id, kind, head, detail, ev.tool_id, ev.parent_tool_id)
    elif isinstance(ev, ToolResult):
        name, desc = _result_row(ev)
        # The tool_use id pairs result to call exactly; concurrent tools return out of order.
        _spine.log_run_event(repo_id=repo_id, kind="result", name=name, description=desc,
                             run_id=run_id, item_id=item_id, tool_id=ev.tool_id,
                             parent_tool_id=ev.parent_tool_id)
        if publish_live:
            _publish_timeline(item_id, run_id, "result", name, desc, ev.tool_id, ev.parent_tool_id)
    elif isinstance(ev, TextDelta):
        txt = (ev.text or "").strip()
        if txt:
            _spine.log_run_event(repo_id=repo_id, kind="reply", name="reply", description=txt[:_REPLY_CAP],
                                 run_id=run_id, item_id=item_id)
            if publish_live:
                _publish_timeline(item_id, run_id, "reply", "reply", txt[:_REPLY_CAP], None, None)


def _publish_timeline(item_id: str | None, run_id: int | None, kind: str, name: str,
                      description: str, tool_id: str | None,
                      parent_tool_id: str | None) -> None:
    """Fan a captured event out to any panel watching this item. No-op when nobody is watching.
    Never raises."""
    if not item_id or not item_stream.has_subscribers(item_id):
        return
    try:
        item_stream.publish(item_id, {
            "type": "timeline", "item_id": str(item_id), "run_id": run_id,
            "kind": kind, "name": name, "description": description, "tool_id": tool_id,
            "parent_tool_id": parent_tool_id,
        })
    except Exception:
        log.debug("item_stream publish failed for %s", item_id, exc_info=True)


def bank_auto_checkpoint(ctx, item_id: str, *, since: float | None = None) -> bool:
    """Mechanical fallback for the session-end checkpoint hook, so the orient block always has one.

    Skipped when the item is terminal or a checkpoint newer than `since` exists; the agent's own is
    better."""
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
            role=kind_profiles.session_slot(str(item.get("phase") or "triage")),
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

    Owed only while the session's newest finished run IS the compaction, and resolved via the role
    stamp: three threads bank into one folder."""
    if not (session_id and ctx.internal_root and item):
        return None
    if not _spine.session_compacted_pending(session_id):
        return None
    role = kind_profiles.session_slot(str(item.get("phase") or "triage"))
    item_dir = ctx.internal_root / "dev" / "work-items" / str(item.get("id") or "")
    cp = _arts.latest_checkpoint(item_dir, char_cap=1, role=role)
    return cp["path"] if cp else None


def compacted_session_memory(ctx, session_id: str | None) -> str | None:
    """The `session-memory/` path this thread is owed a pointer to, or None. Same self-clearing gate
    as `compacted_checkpoint`; a general session has one thread, so no role scoping."""
    if not (session_id and ctx.internal_root):
        return None
    if not _spine.session_compacted_pending(session_id):
        return None
    mem = _arts.read_session_memory(ctx.internal_root / ctx.mode, session_id, char_cap=1)
    return mem["path"] if mem else None


def reset_vet_thread(ctx, item: dict, *, dev=None, sessions=None) -> bool:
    """Retire the previous cycle's vet session and clear the item's vet slot, so the next vet turn
    mints.

    Called on every transition into vet, so each cycle gets a fresh vetter — prior findings arrive
    as reports, never as memory."""
    d, s = dev or _dev, sessions or _sessions
    prev = (item.get("sessions") or {}).get("vet")
    if not prev:
        return False
    s.delete(ctx, prev, cause="retired")
    d.set_work_item_session(ctx.internal_root / "dev", str(item["id"]), None, slot="vet")
    return True


def read_completion(context_id: str, item_id: str, sink: dict,
                    run_id: int | None = None) -> dict | None:
    """A run's completion payload out of its `report_completion` sink, persisted as the `run.report`
    event. None when the run never reported.

    `run_id` names the run this report ENDS, resolved from the item's live row. Stored alongside
    the payload, never inside it."""
    report = sink.get("report")
    if report:
        rid = run_id if run_id is not None else _spine.running_run_id(context_id, item_id)
        _dev_store.log_event(context_id, "run.report",
                             f"{report['outcome']}: {report['summary'][:160]}",
                             item_id=item_id, actor="agent", meta={**report, "run_id": rid})
    return report


UNREPORTED = "unreported"   # a run that finished but declared nothing, even after the backstop


async def ensure_completion(ctx, context_id: str, item_id: str, sink: dict, *, skill: str,
                            session_id: str | None, model: str | None, effort: str | None,
                            run_id: int | None = None) -> dict | None:
    """`read_completion` with a BACKSTOP: when the run ended without declaring, spend one short turn
    asking it to.

    The nudge resumes the run's own session, keeping the work in context. Never inferred:
    `outcome` encodes judgment only the agent holds."""
    report = read_completion(context_id, item_id, sink, run_id=run_id)
    if report or not session_id:
        if not report:
            log.warning("%s run for %s ended undeclared with no session to resume", skill, item_id)
        return report
    log.info("%s run for %s ended undeclared — asking for its outcome", skill, item_id)
    # `retry=False` deliberately — the work is done and only its label is missing; backoff is the
    # wrong trade.
    turn = ResilientTurn("completion-backstop", item_id=item_id, retry=False)
    try:
        async for _ev in turn.stream(
            _agent, ctx, kernel_speech.completion_nudge(skill),
            resume=session_id, model=model, effort=effort, approve=deny_all,
            extra_mcp_servers={"run": make_run_report_server(sink)},
            item_bound=True,
        ):
            pass
    except Exception:   # noqa: BLE001 — the backstop must never turn a finished run into a failure
        log.exception("completion backstop turn failed for %s (%s)", item_id, skill)
    report = read_completion(context_id, item_id, sink, run_id=run_id)
    if not report:
        log.warning("%s run for %s stayed undeclared after the backstop", skill, item_id)
    return report


def _dev_mcp(ctx, repo_dir: Path, item_id: str, *, scope: str) -> dict:
    """The dev MCP server for a background intake/resolve run.

    `repo_dir` is where evidence fingerprints — the worktree when one exists, else the repo root.
    `scope` names which tools this run sees, so a close run cannot hold plan's pens."""
    return {"dev": make_dev_mcp_server(
        _dev_store, ctx.id, spine=_spine, scope=scope,
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
    """The generic phase-entry runner for any auto-fired item skill that is not plan: `review`,
    `investigate`, `itemize`.

    All carry the item's INTAKE role — one thread end to end — so only the skill differs. Thin
    wrapper over `_background_intake_run`."""
    await _background_intake_run(ctx, context_id, item_id, item_dir,
                                 skill=skill, model=model, effort=effort)


async def _run_background_triage(ctx, context_id: str, item_id: str, item_dir: Path,
                                 model: str | None = None, effort: str | None = None) -> None:
    """Auto-triage on push: one triage turn, no surface, fired when an inbox item is pushed to the
    workspace.

    The item lands at `awaiting_human` with its classification recorded, so the owner glances and
    approves."""
    await _background_intake_run(ctx, context_id, item_id, item_dir,
                                 skill="triage", model=model, effort=effort)


def fire_review_entry(context_id: str, item_id: str, spine) -> bool:
    """Fire the review-entry run for an item that just landed at review.

    Shared by `advance_item`'s auto-fire and the loop's vet→review hop. Flips the item `active`
    while it runs, because `active` with no run is an idle stall."""
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
        run_tasks.track(asyncio.create_task(
            _run_background_item_skill(ctx, context_id, item_id,
                                       dev_root / "work-items" / item_id, "review", model, effort)))
        return True
    except Exception:
        log.exception("review-entry run failed to start for %s", item_id)
        try:
            _dev.set_work_item_status(ctx.internal_root / "dev", item_id, "awaiting_human")
        except Exception:
            pass
        return False


def fire_first_investigate(context_id: str, item_id: str, spine) -> bool:
    """Kick a BUTTON-LAUNCHED sweep into its first investigate run.

    The sibling of `fire_auto_triage`: an item created by pressing a button should not wait for a
    second click. A standing sweep enters already classified, so triage never runs."""
    from ...gateway import contexts   # lazy: avoid an import cycle at module load
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if str(item.get("status")) != "active" or str(item.get("phase")) != "investigate":
            return False
        model = spine.effective_model(context_id, item_model=item.get("model"))
        effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
        if _begin_run(ctx, context_id, item_id, "investigate", model, phase="investigate") is None:
            return False   # a run is already in flight — don't double-fire
        run_tasks.track(asyncio.create_task(
            _run_background_item_skill(ctx, context_id, item_id,
                                       dev_root / "work-items" / item_id,
                                       "investigate", model, effort)))
        return True
    except Exception:
        log.exception("first investigate run failed to start for %s", item_id)
        return False


def fire_auto_triage(context_id: str, item_id: str, spine) -> bool:
    """Kick a freshly-active item into its first triage run — the autopilot chain's shared first push.

    Fires only for an item `active` at `triage` with no run in flight. A failure leaves the item
    resting there for a chat-driven pass."""
    from ...gateway import contexts   # lazy: avoid an import cycle at module load
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if str(item.get("status")) != "active" or str(item.get("phase")) != "triage":
            return False   # only a fresh, active, un-triaged item is kicked
        # Resolve with the item's locked config, so the first kick honours the model chosen at
        # capture.
        model = spine.effective_model(context_id, item_model=item.get("model"))
        effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
        if _begin_run(ctx, context_id, item_id, "triage", model, phase="triage") is None:
            return False   # a run is already in flight — don't double-fire
        run_tasks.track(asyncio.create_task(
            _run_background_triage(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                   model, effort)))
        return True
    except Exception:
        log.exception("auto-triage failed to start for %s", item_id)
        return False


# --------------------------------------------------------------------------- deputy send-back re-run

# Which skill a phase re-runs with — its own. One entry per phase that can be re-run in place.
_PHASE_FEEDBACK_SKILL = {"triage": "triage", "plan": "plan", "investigate": "investigate"}

# Phases whose feedback lands somewhere else, because the work must change: `review` is a send-
# back, `build` a `revise`.
_ROUTES_BACK = ("review", "build")

# Per-kind, because the destination must be a phase the kind actually has — research has no plan
# phase.
_SEND_BACK_TARGET = {"implementation": "plan", "research": "investigate"}


def _send_back_phase(item: dict) -> str:
    """Where a `revise` from review or build re-enters, for this item's kind. Unknown kinds route to
    the implementation path rather than raise — losing recourse is the worse failure."""
    return _SEND_BACK_TARGET.get(str(item.get("kind") or ""), "plan")


def fire_phase_feedback(context_id: str, item_id: str, *, phase: str, feedback: str,
                        digest: str | None = None, by: str = "deputy") -> bool:
    """Deliver gate feedback as a REAL turn on the item's own session, so the agent re-runs the phase
    in-thread.

    `by` sets attribution only; routing is identical whoever gives it. Fires only at that gate,
    with no run in flight."""
    from ...gateway import contexts   # lazy: avoid an import cycle at module load
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if not item or item.get("done_at"):
            return False
        if phase not in _PHASE_FEEDBACK_SKILL and phase not in _ROUTES_BACK:
            return False   # not a gate that negotiates via a phase re-run
        # Review feedback falls back into the kind's rework phase; flipping the phase is what
        # makes the rework re-run.
        run_phase = _send_back_phase(item) if phase in _ROUTES_BACK else phase
        skill = _PHASE_FEEDBACK_SKILL.get(run_phase)
        if skill is None:
            return False   # the kind routes somewhere that cannot re-run — never guess a skill
        if phase in _ROUTES_BACK:
            if phase == "review":
                # A send-back spends the approval, so the PR closes with it — the diff it
                # described is about to change.
                from .git_ops import close_pr
                close_pr(_dev, dev_root, item_id)
            _dev.set_work_item_phase(dev_root, item_id, run_phase)
            # One event per speaker: `gate_briefs` counts these as revision rounds, so a build's
            # conclusion must not log a review.
            kind, said = (("review.route", f"Review feedback routed back to {run_phase}")
                          if phase == "review" else
                          ("revise.route",
                           "Build concluded the plan must change — routed to " + run_phase))
            _dev_store.log_event(context_id, kind, f"{said}: {feedback[:160]}",
                                 item_id=item_id, actor=by,
                                 meta={"from": phase, "to": run_phase, "feedback": feedback[:400],
                                       "by": by})
        # Resume the TARGET phase's own thread — resuming the entering phase's re-plans in the
        # reviewer's head.
        slots = item.get("sessions") or {}
        session_id = (slots.get(kind_profiles.session_slot(run_phase))
                      or slots.get(kind_profiles.LEGACY_INTAKE_SLOT)
                      or item.get("session_id") or None)
        # Claude Code stores sessions per project path, so a detached checkout's session cannot be
        # resumed from the repo root.
        from .git_ops import ensure_scratch_worktree
        repo_dir = ensure_scratch_worktree(ctx, context_id, item,
                                           dev=_dev, dev_store=_dev_store, spine=_spine)
        if repo_dir != ctx.cwd:
            ctx = replace(ctx, cwd=repo_dir)
        model = _spine.effective_model(context_id, item_model=item.get("model"))
        effort = _spine.effective_effort(context_id, item_effort=item.get("effort"))
        if _begin_run(ctx, context_id, item_id, skill, model, phase=run_phase) is None:
            return False   # a run is already in flight — don't double-fire
        # The marker the FE matches to attribute the turn to `<by>`; `speech` is the bubble,
        # `text` is what gets sent.
        title = item.get("title") or item_id
        prompt = kernel_speech.phase_feedback_trigger(item_id, title, run_phase, skill, feedback, digest)
        _dev_store.log_event(context_id, f"{by}.query",
                             f"{by.title()} sent feedback into the {run_phase} thread: {feedback[:160]}",
                             item_id=item_id, actor=by,
                             meta={"phase": run_phase, "origin_gate": phase, "speech": feedback,
                                   "text": prompt, "by": by})
        run_tasks.track(asyncio.create_task(
            _run_deputy_feedback_turn(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                      session_id=session_id, phase=run_phase, prompt=prompt,
                                      model=model, effort=effort)))
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
    """Run one deputy send-back re-run: RESUME the item's session and let the agent re-run the phase
    against the feedback.

    Ends at `awaiting_human`, which re-fires the gate seam so the deputy re-judges the result. That
    chaining IS the negotiation loop."""
    dev_root = ctx.internal_root / "dev"
    capture_prompt(context_id, prompt, item_id=item_id)
    # Re-read the item after any phase flip so the pointer names the phase this re-run actually
    # works.
    live_item = _dev.read_work_item(dev_root, item_id) or {"id": item_id, "phase": phase}
    focus = kernel_speech.work_item_preamble(
        item_id, live_item, str(item_dir), interactive=False,
        compacted_checkpoint=compacted_checkpoint(ctx, live_item, session_id))
    final_tokens = final_usage = final_session = None
    run_started = time.time()
    live = _LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    turn = ResilientTurn("deputy feedback", item_id=item_id,
                         notify=retry_notice(context_id, item_id, phase))
    async for ev in turn.stream(
        _agent, ctx, prompt,
        resume=session_id,   # RESUME — the deputy's turn lands in the item's own transcript
        model=model,
        effort=effort or _spine.effective_effort(context_id),
        approve=scoped_writes_approve(item_dir, deny_all),
        sandbox_writes=[item_dir],   # sandboxed shell; the item folder is its one outside write
        extra_mcp_servers={**_dev_mcp(ctx, ctx.cwd, item_id,
                                      scope=str(live_item.get("phase") or phase)),
                           "run": make_run_report_server(sink)},
        system_append=focus,
        item_bound=True,       # one item is this run's subject — no board-wide in-progress list
    ):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens, final_usage, final_session = (ev.tokens, ev.usage, ev.session_id)
            _sessions.record(ctx, ev.session_id)
            if ev.session_id:
                try:
                    # The feedback re-run belongs to the phase it re-runs, so it lands in that
                    # phase's slot.
                    _dev.set_work_item_session(dev_root, item_id, ev.session_id,
                                               slot=kind_profiles.session_slot(phase))
                    _spine.stamp_session_item(ev.session_id, item_id)
                except Exception:
                    log.exception("deputy feedback: failed to persist session to %s", item_id)
        elif isinstance(ev, Init):
            _cache_slash(ctx.id, ev.slash_commands)
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    # `ResilientTurn` returns rather than raises, so the closing block below is reached on every
    # path.
    report = await ensure_completion(ctx, context_id, item_id, sink, skill=str(phase),
                                     session_id=final_session, model=model, effort=effort)
    stopped = turn.fault.failed and not report
    if stopped:
        mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase=phase)
    _end_run(ctx, context_id, item_id, final_tokens,
             "error" if stopped else "awaiting_human", final_usage,
             outcome="blocked" if stopped else ((report or {}).get("outcome") or UNREPORTED),
             session_id=final_session)
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after deputy feedback failed")
    log.info("deputy feedback re-run: done for %s (%s%s)", item_id, phase,
             f", {turn.fault.kind}" if turn.fault.failed else "")


def fire_close_run(context_id: str, item_id: str, spine) -> bool:
    """Fire the ONE closing run of the CLOSE phase — the workflow's only knowledge write.

    Fires only for an item resting at `close` with no run in flight; when none can start it clears
    anyway, with the gap on record."""
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
        run_tasks.track(asyncio.create_task(
            _run_background_close(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                  model, effort)))
        return True
    except Exception:
        log.exception("auto-close failed to start for %s", item_id)
        return False
    finally:
        # No run started and the item still `active` means nothing will move it; clear it, gap
        # recorded.
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
    """Drive the item's ONE closing turn: RESUME its intake thread and let the close skill reflect the
    locked changes into the anchor docs and the change log.

    The kernel clears the item from there — the run never completes it."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    session_id = ((item.get("sessions") or {}).get("intake") or item.get("session_id") or None)
    title = item.get("title") or item_id
    prompt = kernel_speech.close_trigger(item_id, title)
    capture_prompt(context_id, prompt, item_id=item_id)
    # The resumed transcript carries the bulk; this is the standing phase/role pointer every
    # runner sends.
    focus = kernel_speech.work_item_preamble(
        item_id, item, str(item_dir), interactive=False,
        compacted_checkpoint=compacted_checkpoint(ctx, item, session_id))
    final_tokens = final_usage = final_session = None
    run_started = time.time()
    live = _LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    turn = ResilientTurn("auto-close", item_id=item_id,
                         notify=retry_notice(context_id, item_id, "close"))
    # Built once, then both SNAPSHOTTED and SENT — see `surface_from_turn`.
    turn_kwargs = dict(
        resume=session_id,   # RESUME the intake thread — the closeout narrates the whole item
        model=model,
        effort=effort or _spine.effective_effort(context_id),
        approve=scoped_writes_approve(item_dir, deny_all),
        write_boundary=[item_dir],   # the shell boundary, matching the sandbox beside it
        sandbox_writes=[item_dir],   # sandboxed shell; the item folder is its one outside write
        extra_mcp_servers={**_dev_mcp(ctx, ctx.cwd, item_id, scope="close"),
                           "run": make_run_report_server(sink)},
        system_append=focus,
        item_bound=True,       # one item is this run's subject — no board-wide in-progress list
    )
    # Prompt inspector "A" — throwaway probes ONLY: capture matches the real send exactly.
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=ctx, system_append=focus, prompt=prompt,
                          phase="close",
                          surface=surface_from_turn(turn_kwargs, mcp=["dev", "run"]),
                          background=True)
    async for ev in turn.stream(_agent, ctx, prompt, **turn_kwargs):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens, final_usage, final_session = (ev.tokens, ev.usage, ev.session_id)
            _sessions.record(ctx, ev.session_id)
            if ev.session_id:
                try:
                    _dev.set_work_item_session(dev_root, item_id, ev.session_id,
                                               slot="close")
                    _spine.stamp_session_item(ev.session_id, item_id)
                except Exception:
                    log.exception("auto-close: failed to persist session to %s", item_id)
        elif isinstance(ev, Init):
            _cache_slash(ctx.id, ev.slash_commands)
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    report = await ensure_completion(ctx, context_id, item_id, sink, skill="close",
                                     session_id=final_session, model=model, effort=effort)
    outcome = str((report or {}).get("outcome") or "")
    # `active`, never `awaiting_human`: nobody is being paged. Clearance decides next,
    # mechanically.
    stopped = turn.fault.failed and not outcome
    if stopped:
        mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase="close")
    _end_run(ctx, context_id, item_id, final_tokens, "error" if stopped else "active", final_usage,
             outcome="blocked" if stopped else (outcome or None), session_id=final_session)
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after auto-close failed")
    # A stopped close run must not spend the clearance retry budget, which exists for unreported
    # finishes.
    if not stopped:
        _clear_or_retry(context_id, item_id, outcome)


def _clear_or_retry(context_id: str, item_id: str, outcome: str) -> None:
    """The post-CLOSE kernel hook: reported ⇒ clear the item; not ⇒ re-fire, and once the budget is
    spent clear it anyway with the gap recorded.

    Clearance always completes — a closing run that cannot finish is a SuperMe fault."""
    from . import clearance
    try:
        if outcome:
            # Close has no authority to change anything, so a non-success outcome is a knowledge
            # gap to record, not a hold.
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
    """Drive one background intake-phase turn with no surface attached, then clear run-state. Only the
    skill and trigger differ.

    RESUMES THIS PHASE'S OWN THREAD, or mints when it has none: re-entering a phase is one agent
    looking at a changed tree."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    # A read-only kind reads its own detached checkout; swapping here keeps every phase on one
    # cwd.
    from .git_ops import ensure_scratch_worktree
    repo_dir = ensure_scratch_worktree(ctx, context_id, item,
                                       dev=_dev, dev_store=_dev_store, spine=_spine)
    if repo_dir != ctx.cwd:
        ctx = replace(ctx, cwd=repo_dir)
        item = _dev.read_work_item(dev_root, item_id) or item   # re-read: the git record moved
    # The phase this run IS, not `skill` — `itemize` is a research item's closing run.
    run_phase = str(item.get("phase") or "triage")
    # Resuming this phase's own thread continues where it left off; a first entry has no slot, so
    # the CLI mints.
    prev_session = item.get("session_id") or None
    # Bank before the thread dies — this runner mints a fresh session and deletes the previous
    # one.
    if prev_session:
        try:
            bank_auto_checkpoint(ctx, item_id)
        except Exception:
            log.exception("pre-replace checkpoint failed for %s", item_id)
    title = item.get("title") or item_id
    # A research worktree is a detached scratch tree — the only one this run reads or may destroy.
    wt = item.get("git_worktree")
    scratch_tree = ([Path(wt)]
                    if wt and kind_profiles.get_profile(
                        str(item.get("kind") or "implementation")).scratch_worktree
                    else [])
    # A resumed agent believes its memory over the folder, so a re-entry is told what changed.
    changed: list[str] = []
    if prev_session:
        try:
            since = _spine.last_phase_run_end(context_id, item_id, phase=run_phase)
            changed = _arts.changed_since(item_dir, since)
        except Exception:
            log.exception("re-entry delta failed for %s at %s", item_id, run_phase)
    trigger = kernel_speech.intake_trigger(skill, item_id, title, changed)
    prompt = trigger
    capture_prompt(context_id, trigger, item_id=item_id)
    focus = kernel_speech.work_item_preamble(item_id, item, str(item_dir), interactive=False)
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = _LiveTokens()   # dedupes the Usage stream by message_id for an accurate live estimate
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    turn = ResilientTurn(f"background {skill}", item_id=item_id,
                         notify=retry_notice(context_id, item_id, skill))
    # Built once, then both SNAPSHOTTED and SENT — see `surface_from_turn`.
    turn_kwargs = dict(
        resume=prev_session,   # this PHASE's own thread; None the first time it is entered
        model=model,
        effort=effort or _spine.effective_effort(context_id),  # item → repo → system → medium
        approve=scoped_writes_approve(item_dir, deny_all),
        # Without a shell boundary every command the read-only classifier cannot prove goes to
        # `deny_all`, with no path to allow.
        write_boundary=[item_dir],
        # One path outside the boundary refuses the whole command, so the shell may name the
        # scratch worktree.
        shell_roots=scratch_tree,
        sandbox_writes=[item_dir, *scratch_tree],   # the kernel holds the same two roots
        extra_mcp_servers={**_dev_mcp(ctx, ctx.cwd, item_id, scope=skill),
                           "run": make_run_report_server(sink)},
        system_append=focus,
        item_bound=True,       # one item is this run's subject — no board-wide in-progress list
    )
    # Throwaway probes only — capture matches the real send exactly. Normal items skip it.
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=ctx, system_append=focus, prompt=prompt,
                          phase=skill,
                          surface=surface_from_turn(turn_kwargs, mcp=["dev", "run"]),
                          background=True)
    async for ev in turn.stream(_agent, ctx, prompt, **turn_kwargs):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens = ev.tokens
            # Accumulated per-message usage (parent + subagents), not the parent-only
            # `Result.usage`; falls back when no Usage step arrived.
            final_usage = live.usage(ev.usage) or ev.usage
            final_session = ev.session_id
            _sessions.record(ctx, ev.session_id)
            if ev.session_id:
                try:
                    _dev.set_work_item_session(dev_root, item_id, ev.session_id,
                                               slot=kind_profiles.session_slot(run_phase))
                    # Reverse stamp: a background session born here gets its durable work-item
                    # identity and its spine kind.
                    _spine.stamp_session_item(ev.session_id, item_id)
                    _spine.stamp_session_kind(ev.session_id,
                                              kind_profiles.session_role(run_phase))
                except Exception:
                    log.exception("background %s: failed to persist session to %s" % (skill, item_id))
                # The replaced thread is superseded — delete it so the picker stays clean; its run
                # trace is preserved.
                if prev_session and prev_session != ev.session_id:
                    _sessions.delete(ctx, prev_session, cause="retired")
        elif isinstance(ev, Init):
            _cache_slash(ctx.id, ev.slash_commands)
        # Per-run trail for the Activity trace: the reply text, each call and its output, keyed to
        # this run.
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    # `ensure_completion` asks once more when a run ended undeclared; an intake phase ends at
    # someone's approval, so agents skip it.
    report = await ensure_completion(ctx, context_id, item_id, sink, skill=skill,
                                     session_id=final_session, model=model, effort=effort)
    # Finished ⇒ the item sits at the owner's gate; died ⇒ `error`, because `awaiting_human` would
    # claim a decision is wanted.
    stopped = turn.fault.failed and not report
    if stopped:
        mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase=skill)
    _end_run(ctx, context_id, item_id, final_tokens,
             "error" if stopped else "awaiting_human", final_usage,
             outcome="blocked" if stopped else ((report or {}).get("outcome") or UNREPORTED),
             session_id=final_session, summary=str((report or {}).get("summary") or ""))
    # Session-end checkpoint hook: a background session ends here — bank the fallback if the
    # run didn't write its own checkpoint.
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after background %s failed", skill)
    # Itemize is research's closing run, so it owes clearance — otherwise the item rests at close
    # forever.
    if skill == "itemize" and not stopped:
        _clear_or_retry(context_id, item_id,
                        str((report or {}).get("outcome") or UNREPORTED))
    log.info("background %s: done for %s%s", skill, item_id,
             f" ({turn.fault.kind})" if turn.fault.failed else "")


async def _run_background_resolve(ctx, context_id: str, item_id: str, worktree: Path,
                                conflicts: list[str], model: str | None = None,
                                  effort: str | None = None) -> None:
    """Drive one background turn that edits a conflicted merge's markers, then COMPLETE the merge
    mechanically daemon-side — the agent never commits.

    Success re-enters `vet`; failure pages the owner with the merge still in the tree."""
    dev_root = ctx.internal_root / "dev"
    # No `report_completion` mount: the outcome is mechanical (did the merge finish), never the
    # agent's claim.
    prompt = kernel_speech.resolve_trigger(worktree, item_id, conflicts)
    capture_prompt(context_id, prompt, item_id=item_id)
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = _LiveTokens()
    turn = ResilientTurn("background resolve", item_id=item_id,
                         notify=retry_notice(context_id, item_id, "resolve"))
    async for ev in turn.stream(
        _agent, ctx, prompt,
        resume=None,
        model=model,
        effort=effort or _spine.effective_effort(context_id),
        approve=scoped_writes_approve(worktree, deny_all),
        sandbox_writes=[worktree],   # resolving a conflict is git + edits inside the tree, nothing more
        extra_mcp_servers=_dev_mcp(ctx, worktree, item_id, scope="resolve"),  # Dev tools mounted so a background planner can read the log, roadmap and inbox.
    ):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens = ev.tokens
            # Accumulated per-message usage (parent + subagents), not the parent-only
            # `Result.usage`; falls back when no Usage step arrived.
            final_usage = live.usage(ev.usage) or ev.usage
            final_session = ev.session_id
            _sessions.record(ctx, ev.session_id)
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
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
        if revet:  # Re-vet before re-presenting: the merge changed the diff the owner already saw.
            reset_vet_thread(ctx, item)         # vet forgets — fresh vetter for the re-entry
            _dev.set_work_item_phase(dev_root, item_id, "vet")
            # Every phase move lands in the trail — this is the one non-gate transition.
            _dev_store.log_event(context_id, "phase.advance",
                                 "Conflict resolved — re-entering vet before re-presenting",
                                 item_id=item_id, actor="daemon",
                                 meta={"from": "review", "to": "vet"})
        _end_run(ctx, context_id, item_id, final_tokens, "active", final_usage, outcome=outcome,
                 session_id=final_session)
        # Something must run behind that `active`. Fired after `_end_run`, because `start_vet_run`
        # refuses while a run holds the lock.
        if revet:
            from .loop import start_vet_run
            started, why = start_vet_run(ctx, context_id, item_id)
            if not started:
                # Never leave `active` with no run: rest it where the owner can see it instead.
                _dev.set_work_item_status(dev_root, item_id, "awaiting_human")
                log.warning("resolve: vet re-entry did not start for %s (%s)", item_id, why)
    elif turn.fault.failed:
        # The resolver never finished: an outage, not a hard conflict. The merge is still in the
        # tree either way.
        mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase="resolve")
        _end_run(ctx, context_id, item_id, final_tokens, "error", final_usage,
                 outcome=outcome, session_id=final_session)
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
