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
from ...core.faults import RETRY_LADDER
from ...core.models import MODEL_TIERS
from ...harness.tools.dev_tools import make_dev_mcp_server
from ...harness.tools.run_tools import make_run_report_server
from .turns import ResilientTurn

log = logging.getLogger("superme-agent")

# Work-items default to the latest Sonnet (concrete id — the `sonnet` alias lags; see core/models.py).
DEFAULT_RUN_MODEL = MODEL_TIERS["sonnet"]


def mark_item_error(ctx, context_id: str, item_id: str, reason: str, *, phase: str = "") -> bool:
    """Stop an item at `error` with the reason its work stopped (recovery-resilience R2).

    The ONE writer of that status. Called where a run died — after R1's ladder is spent, or on a
    fault that was never retryable — so the item stays at the phase it died in, labelled, instead
    of resting `awaiting_human` (which claims a decision is wanted) or `active` (which claims
    something is working). Both of those lies were live: an outage left items reading IN PROGRESS
    with a frozen timer, and the owner had no way to tell them from real work.

    Not terminal, ever: `error` is the entry point to Resume (R4) and re-run (R5). Best-effort —
    a failure to LABEL a failure must not itself raise inside a runner's tail."""
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
    """The trail a retry leaves (R1). A run asleep on the ladder for five minutes is externally
    indistinguishable from a hung one, so every wait writes a dev event naming what failed and how
    long we are giving it — the difference between "SuperMe is stuck" and "SuperMe is waiting out
    an outage". Every background runner passes one of these to its `ResilientTurn`."""
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
    """Per-run token tally, DEDUPED by message_id — and, since 2026-08-14, the run's AUTHORITATIVE
    total rather than a live estimate of one.

    The SDK emits several Usage steps per API call sharing one message_id (one per content block),
    so summing every STEP over-counts. Keeping the latest per message_id fixes that, and what
    remains is one entry per API call — which summed IS the turn's real spend, because that is how
    the calls are billed.

    **Why this is now the authority.** `Result.usage` covers the PARENT conversation's calls only.
    Measured on the hub (2026-08-14): a turn whose subagent did all the work reported 75,857 here
    against 9,150 there — 8.3x — and `Result.usage.output_tokens` was 219, the parent's two-line
    reply. Two fresh research sweeps reproduced it at 3.0x and 3.3x. Subagent messages arrive on
    the same stream (tagged `parent_tool_use_id`) and are counted here; they are absent there. So
    the figure that was being called authoritative was the smaller, partial one, and every fan-out
    run — the most expensive thing SuperMe does — was under-reported in exactly proportion to how
    much it delegated.

    `usage()` returns the summed 4-type dict for `finish_item_run`, which writes the typed columns
    from it. The shape matches a raw SDK usage dict so every existing reader is unchanged."""

    _KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
             "output_tokens")

    def __init__(self) -> None:
        self._by_msg: dict[str, dict] = {}
        self._legacy: list[dict] = []      # steps from an SDK build with no message_id

    def bump(self, context_id: str, item_id: str, ev) -> None:
        """Record one step. NEVER raises — this is telemetry, and it runs inside the run's own task,
        so anything that escapes here kills the WORK. That is not hypothetical: on 2026-08-14 an
        unexpected usage value raised out of this method, the task died mid-investigation, and the
        run row stayed `running` until the watchdog stopped it 20 minutes later. A counter that can
        stop a run is worse than no counter."""
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
        """A usage field as an int, or 0 for anything that isn't one.

        A usage dict is EXTERNAL DATA from the SDK, and this accumulator runs on every step of every
        run — so an unexpected value must never be able to stop the work. It could, until
        2026-08-14: a step arrived with a LIST where an int was expected, `int()` raised inside
        `bump`, the whole run task died mid-flight, and the row sat `running` until the stall
        watchdog closed it 20 minutes later. 486k tokens of real investigation, lost to a token
        COUNTER. The shape is logged once so the surprise is still learnable."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if value not in (None, ""):
                log.warning("unusable token value in SDK usage (%r) — counted as 0", value)
            return 0
        return int(value)

    def usage(self, final: dict | None = None) -> dict | None:
        """The whole turn's usage — parent AND subagents — as a raw-shaped dict, or None if no
        Usage step ever arrived (then the caller keeps `Result.usage` as the only thing it has).

        `final` is `Result.usage`, and it is read for ONE field: **output_tokens**. A per-message
        usage dict carries the API's `message_start` numbers, where the input and cache figures are
        final but the output count is a PLACEHOLDER — measured 2026-08-15 on a 3-tool-call probe:
        every message reported 1–2 output tokens while the Result reported 188. Summed over a
        700-call sweep that placeholder produced 1,805 output tokens for run 1402, which is off by
        roughly two orders of magnitude. The input and cache sides are unaffected (the probe's
        per-message cache_creation summed to exactly the Result's), so only this one field is
        taken from the smaller-but-real aggregate.

        The residual, deliberately left: `Result.usage` is parent-only, so **subagent output is
        still uncounted**. It is the cheapest slice of a fan-out by volume and there is no other
        signal for it on the stream — a known undercount of one field beats an invented one. Total
        spend is dominated by cache_creation and cache_read, both of which DO include subagents."""
        steps = list(self._by_msg.values()) + self._legacy
        if not steps:
            return None
        summed = {k: sum(self._num(s.get(k)) for s in steps) for k in self._KEYS}
        summed["output_tokens"] = max(summed["output_tokens"],
                                      self._num((final or {}).get("output_tokens")))
        return summed

    def tokens(self) -> int:
        """The 3-type scalar (input + cache_creation + output; cache_read excluded, as everywhere
        else) — what the running row shows and what the card footer counts up."""
        u = self.usage() or {}
        return (self._num(u.get("input_tokens")) + self._num(u.get("cache_creation_input_tokens"))
                + self._num(u.get("output_tokens")))


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
    # The run's PHASE, which is not its feature. They coincide for every background phase run
    # (`feature='review'`, `phase='review'`) and DIVERGE for an interactive turn bound to the item
    # (`feature='chat'`, `phase='review'`) — so anything routing on "which phase concluded this"
    # must read this, and anything labelling "which surface ran" must read `kind`. See the revise
    # branch below, which read `kind` for eleven weeks while its own comment said phase.
    run_phase = str((info or {}).get("phase") or kind)
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
    # ONLY a review run routes from here. `revise` from anywhere else — a build cycle concluding
    # the plan it was handed is wrong — belongs to that phase's own driver, because the driver is
    # already going to decide where the item goes next. Routing here as well put TWO writers on one
    # transition: on f0bda271d766 this flipped the item to `plan` and started a plan run, and the
    # loop's build-cycle branch then logged `build → vet` on top of the running plan, saved from
    # actually starting a vet only by the one-run-at-a-time lock. It also attributed the whole thing
    # to the OWNER, who had done nothing. `decide_after_build` now returns klass `revise` and the
    # loop routes it, attributed to the agent that actually concluded it.
    # `run_phase` is THIS RUN's own phase, read off its spine row — not the item's phase on disk,
    # which by now may already have been moved by whatever else is reacting to the same report.
    #
    # IT MUST BE THE PHASE, NOT THE FEATURE. This branch read `kind` (the feature) while this
    # comment said phase, so it fired only for the BACKGROUND review run and never for an owner
    # talking to the review thread — which is the one way an owner can send work back at all
    # ("`revise` is the ONE way back to re-work — there is no send-back button anywhere", §2.1).
    # The turn concluded `revise`, the outcome was persisted, the agent told the owner what would
    # happen next, and nothing moved: the item sat at `review · active` with no run. Measured live
    # on 3ae839ce7fd7 (run 1309, `feature='chat'` `phase='review'`), 2026-08-13.
    #
    # Feature-blindness is the point. Whoever concluded it — the background run, the owner in chat,
    # the deputy — the item goes back the same way, because the conclusion is about the WORK.
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


def _int_or_none(value) -> int | None:
    """An int, or None for anything a tool's arguments might hold instead. Tool input is written by
    an AGENT, so every field in it is untrusted shape — and the trail formatter below is not a
    place that may raise (see the Read span)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _artifact_desc(tool_name: str, ti: dict) -> tuple[str, str, str]:
    """Map a tool-use to (kind, head, detail). kind ∈ tool|subagent|skill|mcp."""
    ti = ti or {}
    base = _short_path
    # Sub-agent spawn — arrives as either the `Task` or the `Agent` tool depending on SDK build. Render
    # it "Agent - <type>" (same label·detail shape as skills: "skill - <name>"), so the trace says WHICH
    # agent ran (e.g. `superme-dev:capture`) instead of a generic "Agent". kind=subagent → the Bot icon.
    if tool_name in ("Task", "Agent"):
        # The row always reads "Agent - Subagent", and NAMES the worker in parentheses when the
        # spawn said which: `Subagent (superme-dev:capture)`. The bare form is the honest fallback
        # for a spawn whose input carried no recognizable type key — it says a sub-agent ran, which
        # is true, instead of the old "Agent - agent", which said nothing twice.
        who = (ti.get("subagent_type") or ti.get("subagentType") or ti.get("agent_type")
               or ti.get("agentType") or ti.get("name") or ti.get("description") or "")
        # The MODEL, when the spawn overrode it. `vet` fans out on haiku and `plan` on sonnet by
        # skill instruction — without this the trail cannot show whether that instruction was
        # actually followed, which is the whole question a delegation review asks.
        model = ti.get("model") or ti.get("modelName")
        # The BRIEF's size, because a subagent inherits nothing: it cannot see the skill, the family
        # guide or the plan, so whatever the brief does not carry, the work is done without. The
        # prompt itself is far too long for a trace row (and would put a whole pasted bar on screen),
        # so what is kept is the one fact a row can hold and a gate can read — how much was sent.
        # Size is a PROXY for content and is treated as one: it can only prove a brief too short to
        # have carried a bar, never that a long one carried the right bar. Added 2026-08-14, after
        # the fan-out instruction shipped for weeks with no contract for what a brief must contain.
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
        # A READ's SPAN, when it named one. The row used to carry the path alone, which makes the
        # trail unable to distinguish "read 20 lines of a 46 KB file" from "read the whole 46 KB
        # file" — and that difference is most of what a phase run costs, because everything read
        # early is re-sent on every later step. The run-protocol rule that asks for spans was
        # therefore unobservable by the very trace meant to check it (2026-08-10).
        if tool_name == "Read":
            # `offset`/`limit` are whatever the AGENT typed, not a validated shape. A list here
            # raised TypeError out of this formatter, out of `capture_event`, and out of the run's
            # own task — killing a live investigation and leaving its row `running` until the stall
            # watchdog closed it 20 minutes later. Seen three times on 2026-08-14 (runs 1399-1401,
            # ~1.4M tokens) and almost certainly the original 24-minute incident too. A LABEL for a
            # trace row must never be able to stop the work it is describing.
            off, lim = _int_or_none(ti.get("offset")), _int_or_none(ti.get("limit"))
            if off is not None or lim is not None:
                start = off or 0
                span = f"{start}-{start + lim}" if lim is not None else f"{start}+"
                return "tool", tool_name, f"{path} [{span}]"
            return "tool", tool_name, f"{path} [whole]"
        return "tool", tool_name, path
    if tool_name == "Bash":
        # 200, not 60: at 60 characters two shell commands that differ only in their path argument
        # render identically, so the trail cannot answer "why was this one refused and that one
        # allowed" — the question the tab exists for. Prefer the raw command over the model's own
        # `description`, which is prose about the command rather than the command.
        return "tool", "Bash", (str(ti.get("command") or "") or str(ti.get("description", "")))[:200]
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


# `_log_artifact` is GONE (2026-07-31). Every runner that called it also called `capture_event` on
# the same event stream, so it wrote a second, POORER copy of the same trail: `run_artifact` skipped
# prompt/reply rows and was never written by deputy or compaction runs at all. The drilldown's Runs
# pane happened to read that copy and was silently showing 17 of one item's 34 runs. `run_event` is
# the one trail now. The TABLE and its rows stay (never-delete-logs) — frozen history, no new writes.


def capture_prompt(repo_id: str, prompt: str, *, run_id: int | None = None,
                   item_id: str | None = None) -> None:
    """Record the prompt that opened a run as the first entry of its trail."""
    _spine.log_run_event(repo_id=repo_id, kind="prompt", name="prompt",
                         description=(prompt or "").strip()[:_PROMPT_CAP], run_id=run_id, item_id=item_id)


def turn_surface(*, model: str | None = None, effort: str | None = None,
                 mcp: list[str] | None = None, write_boundary: list | None = None,
                 sandbox_writes: list | None = None, read_only: bool = False,
                 approve: str = "denied", resumes: bool = False) -> dict:
    """What the turn is ALLOWED to do — the half of a run's input that isn't prose.

    Two runs can carry the same words and behave differently because one of them could run a shell
    inside the worktree and the other could not. This is that difference, recorded in the same row
    as the prompt so the inspector can show them together. Paths are stringified; nothing here is
    read back by the runtime, so it is a faithful snapshot rather than a second source of truth.

    `resumes` is the other invisible half for a REMEMBERING thread (build, intake): the model's real
    context is this input PLUS a transcript the capture doesn't hold, and a reader who doesn't know
    that will mis-read a short prompt as a short context."""
    return {"model": model or "", "effort": effort or "",
            "mcp": sorted(mcp or []),
            "write_boundary": [str(p) for p in (write_boundary or [])],
            "sandbox_writes": [str(p) for p in (sandbox_writes or [])],
            "read_only": bool(read_only), "approve": approve, "resumes": bool(resumes)}


def _authored_extras(ctx, item: dict, phase: str | None, mcp: list[str]) -> dict:
    """The prompt text SuperMe authors OUTSIDE the system append — the two channels a capture of the
    append alone silently omits, each as ready-to-render fragments.

    1. The phase SKILL.md the trigger tells this run to invoke. It is the largest single artifact we
       write, and it is the one the model actually follows; a prompt page without it is a page about
       the smaller half. (Its `references/` guides are pulled on demand and are NOT included — they
       cost nothing unless read.)
    2. The mounted MCP tools' own descriptions + parameter docs. Every byte rides in every request
       of the turn, so they belong on the same page as the prose, measured the same way.

    Best-effort by construction: a missing skill file or an unknown server name drops that fragment
    rather than the capture."""
    from ...harness.tools.base_tools import BASE_TOOLS
    from ...harness.tools.dev_tools import dev_tool_specs
    from ...harness.tools.registry import describe_specs
    from ...harness.tools.run_tools import DEPUTY_VERDICT_TOOL, REPORT_COMPLETION_TOOL
    from ...runtime.config import DEV_PLUGIN_DIR

    skills: list[dict] = []
    contract = kernel_speech.phase_contract(item.get("kind"), str(phase or ""))
    name = contract.get("skill")
    if name:
        path = DEV_PLUGIN_DIR / "skills" / name / "SKILL.md"
        if path.is_file():
            skills.append({"name": f"superme-dev:{name} — SKILL.md",
                           "location": f"plugins/superme-dev/skills/{name}/SKILL.md",
                           "text": path.read_text()})
    # Server → the spec list that server mounts. `superme` is always present (agent_service builds
    # it); the rest come from the run's own `extra_mcp_servers`, recorded in the turn surface.
    # The dev server is SCOPED (dev_tools.TOOL_SCOPES), so the capture asks for the same scope the
    # run mounted — the skill this phase fires. An unrecognised one drops the fragment, per the
    # best-effort contract above, rather than showing a surface no run carried.
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
        # `item_bound=True` — this function is only ever called for a work-item run, and the flag
        # changes what the operating-context fragment renders, so a preview without it would show a
        # prompt no run ever sent.
        system_prompt = _agent.assemble_system_append(ctx, system_append=system_append,
                                                      item_bound=True)
        # Also capture the system prompt's provenance breakdown (ordered [{name,location,text}]) so
        # the inspector renders per-fragment sub-cards. Same builder as `system_prompt` above, so the
        # fragments sum to it. Guarded: a serialization hiccup must never lose the whole capture.
        try:
            frags = _agent.assemble_system_fragments(ctx, system_append=system_append,
                                                     item_bound=True)
            fragments_json = _json.dumps(frags, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            fragments_json = None
        # The other authored channels (phase skill + mounted tool docs) — same guard: losing the
        # skill body must never cost us the prose.
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
    """Record one turn event onto a run's trail: a Status → its tool/skill/agent call, a ToolResult →
    that call's (capped) output, a TextDelta → an assistant reply block. Anything else is ignored.
    Best-effort (log_run_event never raises).

    ALSO the single publish point for the F2 live timeline: the same row is fanned out to any panel
    watching this item (`item_stream.publish`), so build/vet/intake turns stream live. Run-lock means
    one live run per item, so the FE attributes each live frame to the item's CURRENT phase lane.
    `publish_live=False` for the INTERACTIVE ws turn — it already streams itself directly to the panel
    that fired it, so broadcasting would double it there (run-lock means no other panel needs the echo).

    **NEVER RAISES.** This is called from inside the run's own task for every event, so anything that
    escapes here does not lose a trail row — it kills the WORK. That is not theoretical: a `Read`
    whose `limit` arrived as a list raised out of `_artifact_desc` and took three hub investigations
    with it on 2026-08-14 (~1.4M tokens), each leaving its row `running` until the watchdog closed
    it. The specific shape is fixed at the source; this is the wall that makes the class of bug
    survivable, because the trail is a description of the work and must never outrank it."""
    try:
        _capture_event(repo_id, ev, run_id=run_id, item_id=item_id, publish_live=publish_live)
    except Exception:
        log.exception("trail capture failed for %s — the run continues, this event unrecorded",
                      item_id or repo_id)


def _capture_event(repo_id: str, ev, *, run_id: int | None = None, item_id: str | None = None,
                   publish_live: bool = True) -> None:
    # Resolve the run ONCE, here. Every background phase caller passes only `item_id` and lets
    # `log_run_event` resolve the run for the DB row — but the live frame was being published with
    # the caller's `run_id`, i.e. None, so the browser could not match it to the history it already
    # held and rendered every streaming reply a second time on top (owner, 2026-08-02: duplicated
    # bubbles for the length of a run, correct the instant it ended and the live buffer cleared).
    if run_id is None and item_id is not None:
        run_id = _spine.running_run_id(repo_id, item_id)
    if isinstance(ev, Status):
        kind, head, detail = _artifact_desc(ev.tool_name, ev.tool_input or {})
        _spine.log_run_event(repo_id=repo_id, kind=kind, name=head, description=detail,
                             run_id=run_id, item_id=item_id, tool_id=ev.tool_id,
                             parent_tool_id=ev.parent_tool_id)
        if publish_live:
            _publish_timeline(item_id, run_id, kind, head, detail, ev.tool_id, ev.parent_tool_id)
    elif isinstance(ev, ToolResult):
        name, desc = _result_row(ev)
        # Record with the tool_use id so the FE pairs result→call exactly (concurrent tools return
        # out of order). Empty output stays empty (the call just won't be expandable).
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
    """Fan a captured event out to any panel watching this item (F2). No-op when nobody's watching
    (the common background-autopilot case) so we skip the frame build entirely. Never raises."""
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

    Owed only while the session's newest finished run IS the compaction (spine —
    self-clearing, restart-proof), and resolved to THIS THREAD's newest checkpoint via the role
    stamp: three threads bank into one folder, and handing a compacted intake thread the build
    thread's checkpoint would present work it never did as recovered memory."""
    if not (session_id and ctx.internal_root and item):
        return None
    if not _spine.session_compacted_pending(session_id):
        return None
    role = kind_profiles.session_slot(str(item.get("phase") or "triage"))
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
    d.set_work_item_session(ctx.internal_root / "dev", str(item["id"]), None, slot="vet")
    return True


def read_completion(context_id: str, item_id: str, sink: dict,
                    run_id: int | None = None) -> dict | None:
    """A run's completion payload out of its `report_completion` sink (run_tools) — None when the
    run never called the tool (a legacy/unstructured ending; callers warn so drift is visible).
    Persists the payload as the run.report event, the DB record keyed to this item's run trail.

    `run_id` names the run this report ENDS — resolved from the item's live row when the caller
    doesn't already hold it (every background runner logs before `_end_run`, so the row is still
    open). The timeline needs it to place the card inside its run and to drop anything the agent
    says after its own ending. Stored ALONGSIDE the payload, never inside it: `meta` stays the
    verbatim `report_completion` call plus this one kernel-added key."""
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
    """`read_completion` with a BACKSTOP: when the run ended without declaring, spend one short
    turn asking it to. Returns the payload, or None if even that failed.

    Why a kernel-side check rather than firmer wording in the skill: measured, prose does not carry
    this. The same instruction lands at a 4% miss where the loop breaks without it and 18-36% where
    nothing downstream notices — compliance tracks enforcement, not emphasis. So the contract stays
    where it was and the guarantee moves here, identically for every phase.

    The nudge RESUMES the run's own session, so the agent still has its work in context and the
    call costs one small turn; without a session id there is nothing to resume and we accept the
    gap. Deliberately NOT inferred: `outcome` encodes judgment (`partial` / `blocked` /
    `needs_user`) that only the agent holds, so a kernel-stamped `success` would be a claim nobody
    made — see [[no-estimated-usage]] for the same rule about measurements."""
    report = read_completion(context_id, item_id, sink, run_id=run_id)
    if report or not session_id:
        if not report:
            log.warning("%s run for %s ended undeclared with no session to resume", skill, item_id)
        return report
    log.info("%s run for %s ended undeclared — asking for its outcome", skill, item_id)
    # `retry=False` deliberately, though every other background turn climbs the ladder: the work is
    # already done and only its label is missing, so holding the item for up to ~29 minutes of
    # backoff to win a label is the wrong trade. One attempt, classified; then `unreported`.
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
    """The dev MCP server for a background intake/resolve run (F8-residual, playground-e2e-blockers:
    these runners went tool-less while the loop runners got the mount in step 5) — same shape as
    loop.py's `_dev_mcp`. A background planner can now read the dev log / roadmap / inbox and use
    the item pens instead of planning blind. `repo_dir` = where evidence fingerprints (the
    worktree when one exists, else the repo root).

    `scope` names which tools this run may see (dev_tools.TOOL_SCOPES) — for a phase run it is the
    skill being fired, so a close run cannot hold plan's pens."""
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

    The sibling of `fire_auto_triage`, and for the same reason: an item the owner just created by
    pressing a button should not sit waiting for a second click. It differs only in where the item
    was born — a standing sweep enters at `investigate` already classified (the button IS the
    classification), so there is no ticket for triage to read and no triage run to fire.

    Fires ONLY for an `active` item at `investigate` whose phase run isn't already in flight. Best
    effort: returns True iff a run started, and a failure leaves the sweep resting at investigate
    for a chat-driven pass rather than raising into the owner's click."""
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
        run_tasks.track(asyncio.create_task(
            _run_background_triage(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                   model, effort)))
        return True
    except Exception:
        log.exception("auto-triage failed to start for %s", item_id)
        return False


# --------------------------------------------------------------------------- deputy send-back re-run

# Which skill a phase's re-run fires (the phase that OWNS the fix; deputy-live-turns-design §"New
# model"). triage feedback re-triages, plan/review feedback re-plans (review flips review→plan first).
# The skill a phase RE-RUNS with — its own, one entry per phase that can be re-run in place. It used
# to carry `review: plan` and `build: plan` too, which conflated two questions: "can this gate
# negotiate" and "what runs when it does". Once the destination became per-kind those values were
# dead weight that still looked authoritative — a `review` entry saying `plan` is exactly the stale
# lookup someone would later trust.
_PHASE_FEEDBACK_SKILL = {"triage": "triage", "plan": "plan", "investigate": "investigate"}

# The phases whose feedback lands somewhere OTHER than where it was given: the work must change, and
# a document has to say how. `review` is the owner/deputy's send-back; `build` is the build cycle
# concluding that the plan it was handed is wrong (a `revise` outcome) — a different speaker with the
# same destination, which is why they share a path and NOT an event.
_ROUTES_BACK = ("review", "build")

# …and WHERE it lands is per-KIND, because the destination has to be a phase the kind actually has.
# Implementation re-plans (`plan.md` is the only document that can change the work, and forward flow
# plan→build→vet→review re-vets by construction). Research has no plan phase at all any more, so its
# send-back re-enters INVESTIGATE — which is the same shape of act: the phase that produced the
# record is asked to produce a better one, and it resumes its own thread to do it.
#
# THIS IS THE FOURTH INSTANCE of the pattern the 2026-08-13 E2E kept surfacing: a router with one
# destination, and that destination belonged to the implementation path. Left unfixed alongside the
# phase change it is not a degradation but a hard failure — `next_phase` raises loud on a phase the
# profile does not contain, which is exactly what a research send-back would have set.
_SEND_BACK_TARGET = {"implementation": "plan", "research": "investigate"}


def _send_back_phase(item: dict) -> str:
    """Where a `revise` from review/build re-enters, for this item's kind. Unknown kinds fall back to
    the implementation route rather than raising: a send-back is recourse, and losing recourse is a
    worse failure than routing a novel kind to its planning phase."""
    return _SEND_BACK_TARGET.get(str(item.get("kind") or ""), "plan")


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
        if phase not in _PHASE_FEEDBACK_SKILL and phase not in _ROUTES_BACK:
            return False   # not a gate that negotiates via a phase re-run
        # Review feedback FALLS BACK INTO THE KIND'S REWORK PHASE (deputy-live-turns Q1-B; #178 owner
        # too) — `plan` for implementation, `investigate` for research. Flipping the phase is what
        # makes the rework re-run, and forward flow from there re-checks by construction
        # (plan→build→vet→review, or investigate→review). The run + trigger then speak in the
        # DESTINATION's terms — hence the skill swap — while the `digest` carries the review/build/vet
        # context so the rework knows it is answering feedback on its own earlier output.
        run_phase = _send_back_phase(item) if phase in _ROUTES_BACK else phase
        skill = _PHASE_FEEDBACK_SKILL.get(run_phase)
        if skill is None:
            return False   # the kind routes somewhere that cannot re-run — never guess a skill
        if phase in _ROUTES_BACK:
            if phase == "review":
                # `strict` (§2.2): a send-back spends the approval, so the PR closes with it — the
                # diff it described is about to change. See git_ops.close_pr. No-op in `fast` repos
                # and on any item that never had one open. A BUILD-originated revise has no
                # approval to spend: nothing was ever put to the owner.
                from .git_ops import close_pr
                close_pr(_dev, dev_root, item_id)
            _dev.set_work_item_phase(dev_root, item_id, run_phase)
            # ONE EVENT PER SPEAKER. `review.route` used to be logged for a build's `revise` too,
            # so the permanent record said a review had concluded something when no review had run
            # — and `gate_briefs` counts these events as REVISION ROUNDS, so a build's own
            # conclusion inflated the owner's round count. Seen live on f0bda271d766 (2026-08-07).
            kind, said = (("review.route", f"Review feedback routed back to {run_phase}")
                          if phase == "review" else
                          ("revise.route",
                           "Build concluded the plan must change — routed to " + run_phase))
            _dev_store.log_event(context_id, kind, f"{said}: {feedback[:160]}",
                                 item_id=item_id, actor=by,
                                 meta={"from": phase, "to": run_phase, "feedback": feedback[:400],
                                       "by": by})
        # RESUME THE TARGET PHASE'S OWN THREAD (per-phase sessions). Before the split this read the
        # single `intake` slot, which belonged to whichever phase entered last — so a send-back to
        # plan resumed REVIEW's thread and the re-plan ran in the reviewer's head. Falls back to the
        # retired shared slot for items in flight at the cutover, then to the pre-roles bare key.
        slots = item.get("sessions") or {}
        session_id = (slots.get(kind_profiles.session_slot(run_phase))
                      or slots.get(kind_profiles.LEGACY_INTAKE_SLOT)
                      or item.get("session_id") or None)
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
        system_append=focus,   # Current-focus pointer incl. the run protocol
        item_bound=True,       # one item is this run's subject — no board-wide in-progress list
    ):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens, final_usage, final_session = (ev.tokens, ev.usage, ev.session_id)
            _sessions.record(ctx, ev.session_id)
            if ev.session_id:
                try:
                    # The feedback re-run belongs to the phase it re-runs, so it lands in
                    # THAT phase's slot — the same one it resumed a moment ago.
                    _dev.set_work_item_session(dev_root, item_id, ev.session_id,
                                               slot=kind_profiles.session_slot(phase))
                    _spine.stamp_session_item(ev.session_id, item_id)
                except Exception:
                    log.exception("deputy feedback: failed to persist session to %s", item_id)
        elif isinstance(ev, Init):
            _cache_slash(ctx.id, ev.slash_commands)
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    # The `finally` this replaces guaranteed the run row closed even on a crash. `ResilientTurn`
    # returns rather than raises, so the closing block below is reached on every path — including
    # a spent retry ladder, where `turn.fault` names what beat us.
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
        run_tasks.track(asyncio.create_task(
            _run_background_close(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                  model, effort)))
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
                          phase="close",
                          surface=turn_surface(model=model, effort=effort, mcp=["dev", "run"],
                                               write_boundary=[item_dir],
                                               sandbox_writes=[item_dir],
                                               resumes=bool(session_id)),
                          background=True)
    final_tokens = final_usage = final_session = None
    run_started = time.time()
    live = _LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    turn = ResilientTurn("auto-close", item_id=item_id,
                         notify=retry_notice(context_id, item_id, "close"))
    async for ev in turn.stream(
        _agent, ctx, prompt,
        resume=session_id,   # RESUME the intake thread — the closeout narrates the whole item
        model=model,
        effort=effort or _spine.effective_effort(context_id),
        approve=scoped_writes_approve(item_dir, deny_all),
        sandbox_writes=[item_dir],   # sandboxed shell; the item folder is its one outside write
        extra_mcp_servers={**_dev_mcp(ctx, ctx.cwd, item_id, scope="close"),
                           "run": make_run_report_server(sink)},
        system_append=focus,   # Current-focus pointer incl. the run protocol
        item_bound=True,       # one item is this run's subject — no board-wide in-progress list
    ):
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
    # `active`, never `awaiting_human`: nobody is being paged. Clearance decides what happens
    # next, and it is mechanical (the item is terminal a moment later, or retried).
    stopped = turn.fault.failed and not outcome
    if stopped:
        mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase="close")
    _end_run(ctx, context_id, item_id, final_tokens, "error" if stopped else "active", final_usage,
             outcome="blocked" if stopped else (outcome or None), session_id=final_session)
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after auto-close failed")
    # A STOPPED close run must not feed the clearance retry: that budget exists for a run that
    # finished without reporting, and spending it on an outage would burn the item's last chances
    # and then clear it with a knowledge gap nobody chose. `error` holds it for Resume instead.
    if not stopped:
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
    """Drive one background intake-phase turn (triage, plan, investigate, review, close) for
    `item_id` with no surface attached, then clear run-state. They share this driver — only the
    skill + trigger differ.

    RESUMES THIS PHASE'S OWN THREAD, or mints when the phase has none yet (per-phase sessions).
    The phase has already been flipped by the time this runs (`gates.autopilot_advance` sets it and
    then fires), so the item's computed `session_id` IS this phase's slot. Entering a phase for the
    first time therefore mints; entering it again — a review after a revise round — continues.

    It used to always pass `resume=None`, on the reasoning that a re-run must not resume an agent
    that says "already done". That reasoning covered a re-run of a DIFFERENT phase, which still
    mints; it never covered a phase re-entered with new feedback, which is one agent looking at a
    changed tree and had no business forgetting its own last pass.

    The run protocol (kernel-fired, report_completion ending) rides the per-turn Current-focus
    background variant — never the durable transcript, which this session keeps for later
    interactive chat. Persists the session onto this phase's slot, sandboxes writes to the item
    folder; status is owned here: active while running → awaiting_human (the item sits at the
    owner's gate) on finish."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    # A READ-ONLY kind reads from its own detached checkout, not the working tree (kind_profiles:
    # `scratch_worktree`). The swap is here, at the one door every intake phase comes through, so
    # all of an item's phases share one cwd — a thread that read the repo at triage and the tree at
    # investigate would be one agent watching its own paths move. No-op for every other kind.
    from .git_ops import ensure_scratch_worktree
    repo_dir = ensure_scratch_worktree(ctx, context_id, item,
                                       dev=_dev, dev_store=_dev_store, spine=_spine)
    if repo_dir != ctx.cwd:
        ctx = replace(ctx, cwd=repo_dir)
        item = _dev.read_work_item(dev_root, item_id) or item   # re-read: the git record moved
    # The phase this run IS — read off the item, which `gates.autopilot_advance` already flipped.
    # NOT `skill`: they coincide for triage/plan/investigate/review/close but not for `itemize`,
    # which is a research item's closing run and belongs in the `close` slot.
    run_phase = str(item.get("phase") or "triage")
    # THIS PHASE'S OWN THREAD (per-phase sessions). Resuming it is the point: re-entering a phase —
    # a review after a revise round, a triage the owner re-ran — continues where that phase left off
    # instead of re-deriving it. A phase entered for the FIRST time has no slot, so `resume` is None
    # and the CLI mints; that is the mint-on-phase-change half of the rule.
    #
    # `prev_session` is still read, because resuming can FORK: the CLI may hand back a new id, and
    # the thread it forked from is then superseded and retired below. `_session_fields` supplies the
    # legacy `session_intake` (pre-split items) / bare `session_id` (pre-roles items) fallbacks, so
    # an item in flight at the cutover resumes its real thread rather than starting blank.
    prev_session = item.get("session_id") or None
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
    # …with ONE exception, and only when this phase is being RE-ENTERED on its own thread: what
    # changed on disk since that thread last ran. Resuming buys the phase its own memory, and a
    # resumed agent believes its memory over the folder unless the turn says otherwise (measured:
    # a review that answered "nothing has changed" over a rewritten investigation). Computed from
    # the phase's last FINISHED run, so the first entry — and any phase whose thread was adopted
    # from a legacy slot with no run history — says nothing.
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
    # Prompt inspector "A" — throwaway probes ONLY: capture matches the real send exactly.
    # Normal items skip capture (the run_input table no longer grows per-run).
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=ctx, system_append=focus, prompt=prompt,
                          phase=skill,
                          surface=turn_surface(model=model, effort=effort, mcp=["dev", "run"],
                                               write_boundary=[item_dir],
                                               sandbox_writes=[item_dir],
                                               # this runner is ALWAYS a fresh pass (resume=None):
                                               # a re-plan must not read as "already done"
                                               resumes=False),
                          background=True)
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = _LiveTokens()   # dedupes the Usage stream by message_id for an accurate live estimate
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    turn = ResilientTurn(f"background {skill}", item_id=item_id,
                         notify=retry_notice(context_id, item_id, skill))
    async for ev in turn.stream(
        _agent, ctx, prompt,
        resume=prev_session,   # this PHASE's own thread; None the first time it is entered
        model=model,
        effort=effort or _spine.effective_effort(context_id),  # item → repo → system → medium
        approve=scoped_writes_approve(item_dir, deny_all),
        sandbox_writes=[item_dir],   # sandboxed shell; the item folder is its one outside write
        extra_mcp_servers={**_dev_mcp(ctx, ctx.cwd, item_id, scope=skill),
                           "run": make_run_report_server(sink)},
        system_append=focus,   # Current-focus pointer incl. the run protocol
        item_bound=True,       # one item is this run's subject — no board-wide in-progress list
    ):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens = ev.tokens
            # The turn total is the accumulated per-message usage (parent + subagents), NOT
            # `Result.usage`, which covers the parent conversation only — measured 3-8x smaller
            # on fan-out runs (see _LiveTokens). Falls back to the Result when no Usage step ever
            # arrived, which is the only case where it is the fuller of the two.
            final_usage = live.usage(ev.usage) or ev.usage
            final_session = ev.session_id
            _sessions.record(ctx, ev.session_id)
            if ev.session_id:
                try:
                    _dev.set_work_item_session(dev_root, item_id, ev.session_id,
                                               slot=kind_profiles.session_slot(run_phase))
                    # Reverse stamp: this background session is a work-item session, born here —
                    # stamp its durable identity (work-item-session-recognition-prd) + its spine
                    # KIND, which stays `intake` for every intake phase (the slot is per-phase; the
                    # kind is what SORT of thread it is, and that did not change).
                    _spine.stamp_session_item(ev.session_id, item_id)
                    _spine.stamp_session_kind(ev.session_id,
                                              kind_profiles.session_role(run_phase))
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
    # Structured completion (renovation §3.2): the run's report_completion payload, delivered
    # through the sink and persisted onto the run row. `ensure_completion` asks once more when the
    # run ended undeclared — the intake phases are where that used to happen most (2026-08-11:
    # plan 36/100, triage 24/111), because the PHASE ends at someone's approval and "completion"
    # read like a claim the agent could not make.
    report = await ensure_completion(ctx, context_id, item_id, sink, skill=skill,
                                     session_id=final_session, model=model, effort=effort)
    # Background intake finished (or died). Finished ⇒ the item sits at the owner's pre-main gate
    # (triage exit or plan), the one status that pages them (D2 typed awaiting). DIED ⇒ `error`
    # (R2): resting a stopped run at `awaiting_human` claims a decision is wanted, and the owner
    # would open the gate to find no report and no explanation.
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
    # ITEMIZE IS RESEARCH'S CLOSING RUN, so it owes what every closing run owes: clearance.
    # Research fires it on close ENTRY instead of the close skill (a research close writes no
    # knowledge — it exists to record the itemization decision), but clearance hung off the close
    # runner alone, so the item rested at close forever (live, 2026-08-13): Approve 409s ("phase
    # close has no next phase"), Run refuses ("waiting on your decision, not on a run"), and only
    # Drop moved it — filing finished research as `abandoned`.
    if skill == "itemize" and not stopped:
        _clear_or_retry(context_id, item_id,
                        str((report or {}).get("outcome") or UNREPORTED))
    log.info("background %s: done for %s%s", skill, item_id,
             f" ({turn.fault.kind})" if turn.fault.failed else "")


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
    turn = ResilientTurn("background resolve", item_id=item_id,
                         notify=retry_notice(context_id, item_id, "resolve"))
    async for ev in turn.stream(
        _agent, ctx, prompt,
        resume=None,
        model=model,
        effort=effort or _spine.effective_effort(context_id),
        approve=scoped_writes_approve(worktree, deny_all),
        sandbox_writes=[worktree],   # resolving a conflict is git + edits inside the tree, nothing more
        extra_mcp_servers=_dev_mcp(ctx, worktree, item_id, scope="resolve"),  # F8-residual: dev tools mounted
    ):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens = ev.tokens
            # The turn total is the accumulated per-message usage (parent + subagents), NOT
            # `Result.usage`, which covers the parent conversation only — measured 3-8x smaller
            # on fan-out runs (see _LiveTokens). Falls back to the Result when no Usage step ever
            # arrived, which is the only case where it is the fuller of the two.
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
    elif turn.fault.failed:
        # The resolver never ran to completion — an outage, not a hard conflict. Same distinction
        # as everywhere else (R2): "I tried and the conflict beat me" is a decision for the owner,
        # "I never got to try" is a stop to resume. The merge is still in the tree either way.
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
    # The item's whole trail, minus the turn TEXT — this snapshot is the call trail, and the
    # prompt/reply rows belong to the conversation (the Trace tab renders them the same way).
    # Reading `run_event` rather than the retired `run_artifact` copy also means the snapshot now
    # includes the deputy's and the loop's runs, which the old feed dropped entirely.
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
            # A call made INSIDE a sub-agent is indented under its spawn, so the snapshot keeps
            # the same shape the live trace shows — a flat list would read as the parent doing work
            # three delegated readers actually did.
            indent = "    " if a.get("parent_tool_id") else ""
            out.append(f"{indent}{a['seq']}. **{a['name']}**{d}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
