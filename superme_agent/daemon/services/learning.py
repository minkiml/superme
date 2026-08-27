"""Learning pipeline — the capture, distill and write runners plus the capture-sweep machinery.

Three disposable, sessionless background runners and the sweep triggers. The learning and work-item
routes call these; `lifespan` launches `idle_sweep_loop`.
"""

import time
import shutil
import asyncio
import logging
import tempfile
from pathlib import Path

from ..app_state import agent as _agent, dev_store as _dev_store, spine as _spine, \
    sessions as _sessions
from ..deps import cache_slash as _cache_slash, proposal_slug as _proposal_slug
from .runs import capture_prompt, capture_event
from .turns import ResilientTurn
from ...core import kernel_speech
from ...core import Init, Usage, Result, Status, TextDelta, ToolResult, deny_all, learning_write_approve
from ...gateway import contexts
from ...harness.tools.dev_tools import make_dev_mcp_server
from ...paths import DEV_PLUGIN_DIR, CONSTITUTION_DIR

log = logging.getLogger("superme-agent")

FORGE_KIT = DEV_PLUGIN_DIR / "scripts" / "forge_kit"   # the forge agent's lint + eval toolkit


# --- DISTILL phase: process the candidate pool into proposals -----------------------------------

async def run_background_distill(ctx, context_id: str, run_id: int) -> None:
    """Drive one background distill pass over the un-processed candidate pool.

    Disposable and sessionless, so it cannot pollute the resumable picker. Nothing is applied."""
    # Thin trigger: name the agent + the job, nothing else. The steps live in the distill agent;
    prompt = kernel_speech.distill_trigger()
    # These transcripts are disposed, so the trail's first entry is the only record of the ask.
    capture_prompt(context_id, prompt, run_id=run_id)
    # Snapshot the pool so the end event can report what the pass produced.
    cands_before = len(_dev_store.list_memory_candidates(context_id, status="candidate"))
    props_before = len(_dev_store.list_memory_proposals(context_id, status="proposed"))
    _dev_store.log_event(context_id, "distill.start", f"Started distill · {cands_before} candidate(s)",
                         scope="dev", actor="daemon", meta={"candidates": cands_before})
    turn_mcp = {"dev": make_dev_mcp_server(_dev_store, ctx.id, scope="distill")}
    run_status = "done"
    session_id = None
    run_model = None
    run_usage = None
    turn = ResilientTurn("distill")
    async for ev in turn.stream(
        _agent, ctx, prompt,
        resume=None,
        model=_spine.resolve_agent_model("distill"),   # its .md tier → latest concrete (never the lagging CLI alias)
        effort=_spine.resolve_agent_effort("distill"),  # its .md effort field (default medium)
        approve=deny_all,                 # distill writes only via DB tools (pre-approved), not files
        extra_mcp_servers=turn_mcp,
    ):
        if isinstance(ev, Usage):
            _spine.bump_run(run_id, add_tokens=ev.total_tokens, ctx_pct=ev.ctx_pct)
        elif isinstance(ev, Result):
            session_id = ev.session_id    # captured ONLY to dispose the throwaway transcript
            run_model = ev.model          # the model the SDK resolved for this background run
            run_usage = ev.usage          # whole-turn usage — typed-column fallback at finish
        elif isinstance(ev, Init):
            _cache_slash(ctx.id, ev.slash_commands)
        # The throwaway transcript is disposed, so this trail is the run's only record.
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, run_id=run_id)
        # NB: never _sessions.record — distill is sessionless; its transcript is disposed below.
    if turn.fault.failed:
        run_status = "aborted"
    # The run row is kept as durable telemetry, but the session is disposable — delete its
    # transcript.
    _spine.finish_run(run_id, status=run_status, model=run_model, usage=run_usage)
    if session_id:
        _sessions.discard_transcript(ctx, session_id)
    props_after = len(_dev_store.list_memory_proposals(context_id, status="proposed"))
    filed = max(0, props_after - props_before)
    _dev_store.log_event(context_id, "distill.end",
                         f"Finished distill ({run_status}) · {filed} proposal(s) filed",
                         scope="dev", actor="daemon",
                         meta={"status": run_status, "proposals_filed": filed})
    log.info("background distill: %s for %s (%d proposals filed)", run_status, context_id, filed)


# --- WRITE phase: gate-1 approval → background per-item authoring ---

# Forms and scopes that can actually be written today; core is reserved.
WRITE_FORMS = {"constitution", "skill", "agent"}
WRITE_SCOPES = {"repo_dev", "universal_dev"}


def has_answer(answers, question) -> bool:
    """Did the owner answer this clarifying question? Tolerates {question: answer} dicts or a list
    of {question, answer} entries."""
    if not answers or not question:
        return False
    if isinstance(answers, dict):
        return bool(str(answers.get(question, "")).strip())
    if isinstance(answers, list):
        for a in answers:
            if isinstance(a, dict) and a.get("question") == question:
                return bool(str(a.get("answer", "")).strip())
    return False


def _intended_path(prop: dict, repo_id: str | None) -> str | None:
    """Where this proposal WILL publish to, computed up front so it can be staged and shown at the gate.
    None when the home cannot be resolved."""
    from ...core import operational as ops
    slug = _proposal_slug(prop)
    try:
        if prop["output_form"] == "constitution":
            return str(ops.constitution_home(prop["target_scope"], repo_id) / f"{slug}.md")
        root = ops.plugin_root(prop["target_scope"], repo_id)
        if prop["output_form"] == "skill":
            return str(root / "skills" / slug / "SKILL.md")
        return str(root / "agents" / f"{slug}.md")
    except Exception:
        return None


def _existing_rules_file(prop: dict, repo_id: str | None, workspace: Path) -> str | None:
    """For a constitution proposal, write the scope's in-force rules into the workspace so the eval can
    run a real conflict check. None when there is nothing to compare against."""
    if prop["output_form"] != "constitution":
        return None
    from ...core import operational as ops
    try:
        items = ops.list_constitution(
            "dev", CONSTITUTION_DIR / "dev",
            (ops.constitution_home("repo_dev", repo_id) if repo_id else None),
        )
    except Exception:
        return None
    bodies = [it["body"].strip() for it in items if it.get("enabled")]
    if not bodies:
        return None
    path = workspace / "_existing_rules.md"
    path.write_text("\n\n".join(bodies) + "\n", encoding="utf-8")
    return str(path)


async def run_background_write(ctx, context_id: str, proposal_id: int, run_id: int) -> None:
    """Drive one background WRITE pass for a single approved proposal, so no context mixes.

    A clean finish leaves it `drafted`; if the agent never staged, it reverts to `proposed`."""
    prop = _dev_store.get_memory_proposal(proposal_id)
    repo_id = context_id if prop["target_scope"] == "repo_dev" else None
    staged_path = _intended_path(prop, repo_id)
    slug = _proposal_slug(prop)
    # Disposable scratch space, removed in the `finally`. A constitution gets its scope's in-force
    # rules for a real conflict check.
    workspace = Path(tempfile.mkdtemp(prefix=f"forge-{proposal_id}-"))
    existing_path = _existing_rules_file(prop, repo_id, workspace)
    _dev_store.log_event(context_id, "write.start",
                         f"Started write · proposal #{proposal_id} ({prop['output_form']}/{prop['target_scope']})",
                         scope="dev", actor="daemon",
                         meta={"proposal_id": proposal_id, "staged_path": staged_path})
    turn_mcp = {"dev": make_dev_mcp_server(_dev_store, ctx.id, scope="write",
                                           proposal_id=proposal_id, staged_path=staged_path)}
    run_status = "done"
    session_id = None
    run_model = None
    run_usage = None
    write_prompt = kernel_speech.write_trigger(prop, slug=slug, workspace=workspace,
                                               existing_path=existing_path, forge_kit=FORGE_KIT)
    capture_prompt(context_id, write_prompt, run_id=run_id)
    turn = ResilientTurn("write")
    async for ev in turn.stream(
        _agent, ctx, write_prompt,
        resume=None,
        model=_spine.resolve_agent_model("write"),   # its .md tier → latest concrete (never the lagging CLI alias)
        effort=_spine.resolve_agent_effort("write"),  # its .md effort field (default medium)
        # Bash and Write are auto-allowed for this hermetic, disposable run; `stage_artifact`
        # stays DB-only.
        approve=learning_write_approve(workspace),
        extra_mcp_servers=turn_mcp,
        enforce_silent=False,   # the ONE run entitled to the forge-* skills

    ):
        if isinstance(ev, Usage):
            _spine.bump_run(run_id, add_tokens=ev.total_tokens, ctx_pct=ev.ctx_pct)
        elif isinstance(ev, Result):
            session_id = ev.session_id
            run_model = ev.model          # the model the SDK resolved for this background run
            run_usage = ev.usage          # whole-turn usage — typed-column fallback at finish
        elif isinstance(ev, Init):
            _cache_slash(ctx.id, ev.slash_commands)
        # The throwaway transcript is disposed, so this trail is the run's only record.
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, run_id=run_id)
    if turn.fault.failed:
        run_status = "aborted"
    _spine.finish_run(run_id, status=run_status, model=run_model, usage=run_usage)
    if session_id:
        _sessions.discard_transcript(ctx, session_id)
    after = _dev_store.get_memory_proposal(proposal_id)
    status_now = after.get("status") if after else None
    # Past `writing` means the agent staged; still `writing` means it never did, so revert for re-
    # approval.
    staged = status_now in ("drafted", "published")
    if status_now == "writing":
        _dev_store.set_proposal_status(proposal_id, "proposed")
    _dev_store.log_event(context_id, "write.end",
                         f"Finished write ({run_status}) · proposal #{proposal_id} "
                         f"{'staged → drafted' if staged else 'not staged (reverted)'}",
                         scope="dev", actor="daemon",
                         meta={"proposal_id": proposal_id, "status": run_status, "staged": staged})
    log.info("background write: %s for proposal %s (staged=%s)", run_status, proposal_id, staged)
    shutil.rmtree(workspace, ignore_errors=True)


# Deterministic code decides WHEN and WHAT-IS-NEW; the `capture` sub-agent decides what in that
# slice is a learning.

# Keyed by origin session id, in memory: a restart means nothing is sweeping, so an empty set is
# correct.
_sweeping: set[str] = set()


def _render_slice(messages: list[dict]) -> str:
    """Render the swept chat messages as a plain, readable transcript for the capture agent."""
    out = []
    for m in messages:
        who = "Owner" if m.get("role") == "you" else "SuperMe"
        out.append(f"{who}: {m.get('text', '').strip()}")
    return "\n\n".join(out)


async def run_sweep(ctx, session_id: str, focus: str | None = None) -> dict:
    """Run ONE capture sweep over a session's un-swept conversation tail.

    The watermark advances only on a clean pass, so an abort leaves the slice to be re-swept."""
    context_id = ctx.id
    if session_id in _sweeping:
        return {"status": "already_running", "session_id": session_id}
    # Onboarding is SuperMe reciting its own guides, with nothing owner-originated to mine. One
    # choke: every trigger lands here.
    if _spine.session_is_onboarding(session_id):
        return {"status": "skipped_onboarding", "session_id": session_id}
    # Diagnosis is read-only observation ABOUT a run; mining it would feed diagnosis-of-diagnosis
    # recursion.
    if _spine.session_is_diagnosis(session_id):
        return {"status": "skipped_diagnosis", "session_id": session_id}
    try:
        messages = _sessions.transcript_messages(ctx, session_id)
    except Exception:
        log.exception("capture sweep: could not read transcript for %s", session_id)
        messages = []
    head = len(messages)
    mark = _spine.get_sweep_watermark(session_id)
    slice_msgs = messages[mark:head]
    if not slice_msgs:
        return {"status": "no_new", "session_id": session_id, "watermark": mark}

    _sweeping.add(session_id)
    run_id = _spine.start_run(context_id, mode="dev", feature="sweep")
    cands_before = len(_dev_store.list_memory_candidates(context_id, status="candidate"))
    _dev_store.log_event(context_id, "sweep.start",
                         f"Started capture sweep · {len(slice_msgs)} new message(s)",
                         scope="dev", actor="daemon",
                         meta={"session_id": session_id, "messages": len(slice_msgs)})
    # `focus` is the owner's explicit steer (debug-only — the `/dev/sweep` ops hook); the trigger
    # renders it as a directive (kernel_speech.capture_trigger).
    prompt = kernel_speech.capture_trigger(_render_slice(slice_msgs), focus)
    capture_prompt(context_id, prompt, run_id=run_id)  # trail head (capped; the slice is trimmed)
    # Bind provenance server-side: the agent supplies substance, we stamp which session it came from.
    turn_mcp = {"dev": make_dev_mcp_server(_dev_store, context_id, scope="capture",
                                           origin_session_id=session_id)}
    run_status = "done"
    sub_session = None
    run_model = None
    run_usage = None
    filed = 0
    turn = ResilientTurn("capture sweep")
    async for ev in turn.stream(
        _agent, ctx, prompt, resume=None,
        model=_spine.resolve_agent_model("sweep"),   # its .md tier → latest concrete (never the lagging CLI alias)
        effort=_spine.resolve_agent_effort("sweep"),  # its .md effort field (default medium)
        approve=deny_all,                 # capture writes only via the file_candidate DB tool
        extra_mcp_servers=turn_mcp,
    ):
        if isinstance(ev, Usage):
            _spine.bump_run(run_id, add_tokens=ev.total_tokens, ctx_pct=ev.ctx_pct)
        elif isinstance(ev, Result):
            sub_session = ev.session_id   # captured ONLY to dispose the throwaway transcript
            run_model = ev.model          # the model the SDK resolved for this background run
            run_usage = ev.usage          # whole-turn usage — typed-column fallback at finish
        elif isinstance(ev, Init):
            _cache_slash(ctx.id, ev.slash_commands)
        # The throwaway transcript is disposed, so this trail is the run's only record.
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, run_id=run_id)
        # NB: never _sessions.record — the sweep is sessionless; its transcript is disposed below.
    if turn.fault.failed:
        run_status = "aborted"
    _spine.finish_run(run_id, status=run_status, model=run_model, usage=run_usage)
    if sub_session:
        _sessions.discard_transcript(ctx, sub_session)
    # Advance the watermark ONLY on a clean pass — an aborted sweep must re-sweep the same slice.
    if run_status == "done":
        _spine.set_sweep_watermark(session_id, head)
    cands_after = len(_dev_store.list_memory_candidates(context_id, status="candidate"))
    filed = max(0, cands_after - cands_before)
    _dev_store.log_event(context_id, "sweep.end",
                         f"Finished capture sweep ({run_status}) · {filed} candidate(s) filed",
                         scope="dev", actor="daemon",
                         meta={"session_id": session_id, "status": run_status,
                               "candidates_filed": filed,
                               "watermark": head if run_status == "done" else mark})
    _sweeping.discard(session_id)
    log.info("capture sweep: %s for %s (session %s, %d filed)",
             run_status, context_id, session_id, filed)
    return {"status": run_status, "session_id": session_id, "filed": filed,
            "watermark": head if run_status == "done" else mark}


# Both auto triggers bottom out at `run_sweep`, like the manual path. The watermark makes every
# trigger safe to fire freely.
SWEEP_IDLE_SECONDS = 15 * 60     # a dev session quiet this long, with un-swept content, gets swept
SWEEP_POLL_SECONDS = 5 * 60      # how often the idle loop scans (the watermark is the real gate, so
                                 # Built-in fallbacks; the live values come from the spine's sweep
                                 # tuning.


def _fire_sweep_bg(ctx, session_id: str | None, *, focus: str | None = None,
                   then_delete: str | None = None) -> None:
    """Fire-and-forget a sweep so the triggering request returns immediately.

    `then_delete` chains a session delete after it, when a transcript must be read before it goes."""
    if not session_id:
        return

    async def _job():
        try:
            await run_sweep(ctx, session_id, focus=focus)
        except Exception:
            log.exception("background sweep failed for session %s", session_id)
        finally:
            if then_delete:
                _sessions.delete(ctx, session_id, cause=then_delete)

    asyncio.create_task(_job())


async def sweep_idle_sessions(idle_seconds: int | None = None, min_user_msgs: int | None = None) -> dict:
    """One idle-scan pass: sweep every dev session quiet for `idle_seconds` with enough un-swept content.
    Eligible sessions sweep concurrently.

    VISIBLE-ONLY, for token safety: the scan walks only recorded, dashboard-visible sessions."""
    cfg = _spine.get_sweep_config()
    if idle_seconds is None:
        idle_seconds = cfg["idle_seconds"]
    if min_user_msgs is None:
        min_user_msgs = cfg["min_user_msgs"]
    now = time.time()
    eligible: list[tuple] = []   # (ctx, repo_id, session_id)
    scanned = 0
    for repo_id in _spine.repos():
        if not _spine.get_repo_learning(repo_id):
            continue  # this repo opted out of automatic capture
        try:
            ctx = contexts.resolve(repo_id, "dev")
        except Exception:
            continue
        if not ctx.internal_root:
            continue
        for rec in _spine.sessions_for_cwd(ctx.cwd):   # resumable_only ⇒ dashboard-visible only
            if rec.get("mode") != "dev":
                continue
            sid = rec["id"]
            if rec.get("kind") == "diagnosis":
                continue  # diagnosis sessions are never swept (read-only meta; run_sweep guards too)
            if _spine.session_is_onboarding(sid):
                continue  # onboarding sessions are never swept (run_sweep guards too; skip early)
            scanned += 1
            mtime = _sessions.transcript_mtime(ctx, sid)
            if mtime is None or (now - mtime) < idle_seconds:
                continue  # missing or still active
            msgs = _sessions.transcript_messages(ctx, sid)
            watermark = _spine.get_sweep_watermark(sid)
            if watermark >= len(msgs):
                continue  # nothing new since the last sweep
            # Count NEW user turns past the watermark — sweep only once enough has accumulated.
            new_user = sum(1 for m in msgs[watermark:] if m.get("role") == "you")
            if new_user < min_user_msgs:
                continue
            eligible.append((ctx, repo_id, sid))

    if not eligible:
        return {"scanned": scanned, "swept": []}
    results = await asyncio.gather(*(run_sweep(c, s) for c, _, s in eligible),
                                   return_exceptions=True)
    swept = []
    for (ctx, repo_id, sid), res in zip(eligible, results):
        if isinstance(res, dict) and res.get("status") == "done":
            swept.append({"session_id": sid, "repo_id": repo_id, "filed": res.get("filed", 0)})
    if swept:
        log.info("idle sweep: swept %d session(s) concurrently: %s", len(swept), swept)
    return {"scanned": scanned, "swept": swept}


async def idle_sweep_loop() -> None:
    """The daemon's idle-sweep heartbeat: every `poll_seconds`, if auto-learning is on, scan and sweep
    quiet dev sessions.

    Reading the cadence each iteration lets a config change take effect without a restart."""
    while True:
        try:
            poll = max(30, _spine.get_sweep_config()["poll_seconds"])
            await asyncio.sleep(poll)
            if _spine.get_learning_enabled():
                await sweep_idle_sessions()
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("idle sweep loop iteration failed")
