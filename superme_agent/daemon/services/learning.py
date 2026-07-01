"""Learning pipeline — the capture/distill/write runners + the capture-sweep machinery (WI-8).

Three disposable, sessionless headless runners (distill the candidate pool, write an approved
proposal, capture-sweep a session's un-swept tail) plus the sweep triggers (single-flight guard,
fire-and-forget, the idle-scan pass, and the daemon's idle heartbeat). The learning + work-item
routes call these; the daemon `lifespan` launches `_idle_sweep_loop`.

Imports singletons from `app_state` (never from server.py) so there's no import cycle.
"""

import json
import time
import shutil
import asyncio
import logging
import tempfile
from pathlib import Path

from ..app_state import agent as _agent, dev_store as _dev_store, spine as _spine, \
    sessions as _sessions
from ..deps import cache_slash as _cache_slash, proposal_slug as _proposal_slug
from ...core import Init, Usage, Result, deny_all, learning_write_approve
from ...gateway import contexts
from ...harness.tools.dev_tools import make_dev_mcp_server
from ...runtime.config import HARNESS_DIR, CONSTITUTION_DIR

log = logging.getLogger("superme-agent")

FORGE_KIT = HARNESS_DIR / "forge_kit"   # the forge agent's lint + behavioural-eval toolkit


# --- DISTILL phase: process the candidate pool into proposals -----------------------------------

async def _run_headless_distill(ctx, context_id: str, run_id: int) -> None:
    """Drive one headless distill pass: a dev-mode turn whose only job is to invoke the `distill`
    sub-agent over the un-processed candidate pool. The sub-agent files proposals via the dev MCP
    (review_candidates / propose_memory), so we inject that server for this turn. Fire-and-forget.

    The distill pass is a standalone, DISPOSABLE spine Run (started by the caller, finished here):
    it has NO session — its `ev.session_id` is deliberately NOT recorded, so it can't pollute the
    resumable picker. A clean finish purges the run row; an error keeps it as `aborted` for audit.
    Nothing is applied — the owner still gates every proposal."""
    # Thin trigger: name the agent + the job, nothing else. The steps live in the distill agent.
    prompt = (
        "Use the `distill` sub-agent (superme-dev:distill) to process the un-distilled memory "
        "candidate pool for this context. This is an autonomous headless run — there is no human "
        "in this chat, so do not ask questions; invoke the agent and let it file its proposals."
    )
    # Snapshot the pool so the end event can report what the pass produced.
    cands_before = len(_dev_store.list_memory_candidates(context_id, status="candidate"))
    props_before = len(_dev_store.list_memory_proposals(context_id, status="proposed"))
    _dev_store.log_event(context_id, "distill.start", f"Started distill · {cands_before} candidate(s)",
                         scope="dev", actor="daemon", meta={"candidates": cands_before})
    turn_mcp = {"dev": make_dev_mcp_server(_dev_store, ctx.id)}
    run_status = "done"
    session_id = None
    try:
        async for ev in _agent.run_turn(
            ctx, prompt,
            resume=None,
            approve=deny_all,                 # distill writes only via DB tools (pre-approved), not files
            extra_mcp_servers=turn_mcp,
        ):
            if isinstance(ev, Usage):
                _spine.bump_run(run_id, add_tokens=ev.total_tokens, ctx_pct=ev.context_pct)
            elif isinstance(ev, Result):
                session_id = ev.session_id    # captured ONLY to dispose the throwaway transcript
            elif isinstance(ev, Init):
                _cache_slash(ctx.id, ev.slash_commands)
            # NB: never _sessions.record — distill is sessionless; its transcript is disposed below.
    except Exception:
        log.exception("headless distill run failed for %s", context_id)
        run_status = "aborted"
    finally:
        # The run is LOGGED (the spine run row is kept as durable telemetry + the event below),
        # but the session is fully DISPOSABLE — delete its throwaway transcript so nothing
        # resumable lingers on disk.
        _spine.finish_run(run_id, status=run_status)
        if session_id:
            _sessions.discard_transcript(ctx, session_id)
        props_after = len(_dev_store.list_memory_proposals(context_id, status="proposed"))
        filed = max(0, props_after - props_before)
        _dev_store.log_event(context_id, "distill.end",
                             f"Finished distill ({run_status}) · {filed} proposal(s) filed",
                             scope="dev", actor="daemon",
                             meta={"status": run_status, "proposals_filed": filed})
        log.info("headless distill: %s for %s (%d proposals filed)", run_status, context_id, filed)


# --- WRITE phase (WI-8 §Phase 3 / 4c) — gate-1 approval → headless per-item authoring -----------

# Forms × scopes that can actually be written today (core is reserved, §4.11.6 #3).
_WRITE_FORMS = {"constitution", "skill", "agent"}
_WRITE_SCOPES = {"repo_dev", "universal_dev"}


def _has_answer(answers, question) -> bool:
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
    """Where this proposal WILL publish to — computed up front so it can be staged + shown at gate 2.
    Best-effort: returns None if the home can't be resolved (e.g. reserved scope)."""
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
    """For a constitution proposal, write the scope's currently-in-force rules into the workspace so
    the eval can run a real conflict check. Returns the file path, or None when there's nothing to
    compare against (non-constitution, or no rules yet)."""
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
    path.write_text("\n\n".join(bodies) + "\n")
    return str(path)


def _write_prompt(prop: dict, *, slug: str, workspace: Path, existing_path: str | None) -> str:
    """The thin trigger for the write run: name the agent + hand it the full proposal plus the
    tooling it authors with. The authoring + validation steps live in the `forge` agent and its
    per-form skills; here we just lay out the spec and where the toolkit / scratch space are."""
    fields = prop.get("fields")
    answers = prop.get("clarification_answers")
    parts = [
        "Use the `forge` sub-agent (superme-dev:forge) to author the final artifact for this approved "
        "proposal, validate it with the forge_kit, then stage it via `stage_artifact`. This is an "
        "autonomous headless run — there is no human in this chat, so do not ask questions.",
        "",
        f"forge_kit: {FORGE_KIT}   (run: python <forge_kit>/lint.py … and python <forge_kit>/eval.py …)",
        f"scratch workspace: {workspace}   (draft + run the toolkit here; do not write anywhere else)",
        f"publish slug: {slug}   (the artifact's on-disk name — frontmatter `name` must match it)",
        "",
        f"PROPOSAL #{prop['id']}",
        f"output_form: {prop['output_form']}",
        f"target_scope: {prop['target_scope']}",
        f"title: {prop['title']}",
    ]
    if prop.get("summary"):
        parts.append(f"summary: {prop['summary']}")
    if prop.get("body"):
        parts += ["body:", prop["body"]]
    if fields:
        parts += ["fields (the spec):", json.dumps(fields, indent=2)]
    if answers:
        parts += ["owner's answers to the clarifying questions (binding):",
                  json.dumps(answers, indent=2)]
    if existing_path:
        parts.append(f"existing rules in this scope (for the eval conflict check): {existing_path}")
    return "\n".join(parts)


async def _run_headless_write(ctx, context_id: str, proposal_id: int, run_id: int) -> None:
    """Drive one headless WRITE pass for a single approved proposal: a dev-mode turn whose only job
    is to invoke the `write` sub-agent, which authors the final artifact and stages it (→ drafted)
    via `stage_artifact`. Per-item isolated — one proposal per run, no context mixing.

    Disposable spine Run (started by the caller, finished here): sessionless, transcript discarded,
    run row kept as telemetry. On a clean finish the proposal is `drafted`; if the agent failed to
    stage, we revert it to `proposed` so the owner can re-approve."""
    prop = _dev_store.get_memory_proposal(proposal_id)
    repo_id = context_id if prop["target_scope"] == "repo_dev" else None
    staged_path = _intended_path(prop, repo_id)
    slug = _proposal_slug(prop)
    # Disposable scratch space: forge drafts + runs the forge_kit here, nowhere else (writes are
    # scoped to it). Removed in the finally. For a constitution, drop the scope's in-force rules in
    # so the eval can do a real conflict check.
    workspace = Path(tempfile.mkdtemp(prefix=f"forge-{proposal_id}-"))
    existing_path = _existing_rules_file(prop, repo_id, workspace)
    _dev_store.log_event(context_id, "write.start",
                         f"Started write · proposal #{proposal_id} ({prop['output_form']}/{prop['target_scope']})",
                         scope="dev", actor="daemon",
                         meta={"proposal_id": proposal_id, "staged_path": staged_path})
    turn_mcp = {"dev": make_dev_mcp_server(_dev_store, ctx.id,
                                           proposal_id=proposal_id, staged_path=staged_path)}
    run_status = "done"
    session_id = None
    try:
        async for ev in _agent.run_turn(
            ctx, _write_prompt(prop, slug=slug, workspace=workspace, existing_path=existing_path),
            resume=None,
            # forge needs Bash (forge_kit) + Write (draft into the scratch workspace); both are
            # auto-allowed for this hermetic, disposable run. stage_artifact stays DB-only (safe).
            approve=learning_write_approve(workspace),
            extra_mcp_servers=turn_mcp,
        ):
            if isinstance(ev, Usage):
                _spine.bump_run(run_id, add_tokens=ev.total_tokens, ctx_pct=ev.context_pct)
            elif isinstance(ev, Result):
                session_id = ev.session_id
            elif isinstance(ev, Init):
                _cache_slash(ctx.id, ev.slash_commands)
    except Exception:
        log.exception("headless write run failed for proposal %s", proposal_id)
        run_status = "aborted"
    finally:
        _spine.finish_run(run_id, status=run_status)
        if session_id:
            _sessions.discard_transcript(ctx, session_id)
        after = _dev_store.get_memory_proposal(proposal_id)
        status_now = after.get("status") if after else None
        # Moved past `writing` (→ drafted, or already published by a fast gate-2) ⇒ the agent staged.
        # Still `writing` ⇒ it never staged (failed/declined) — revert so the owner can re-approve.
        staged = status_now in ("drafted", "published")
        if status_now == "writing":
            _dev_store.set_proposal_status(proposal_id, "proposed")
        _dev_store.log_event(context_id, "write.end",
                             f"Finished write ({run_status}) · proposal #{proposal_id} "
                             f"{'staged → drafted' if staged else 'not staged (reverted)'}",
                             scope="dev", actor="daemon",
                             meta={"proposal_id": proposal_id, "status": run_status, "staged": staged})
        log.info("headless write: %s for proposal %s (staged=%s)", run_status, proposal_id, staged)
        shutil.rmtree(workspace, ignore_errors=True)


# --- capture sweep: deterministic trigger → agentic remembering (WI-8) ---------------------
# The learning pipeline's front door. A sweep is DETERMINISTIC code (this function) that decides
# WHEN + WHAT-IS-NEW (read the transcript slice past the watermark, single-flight, advance the
# watermark) wrapped around AGENTIC remembering (the `capture` sub-agent decides what, if anything,
# in that slice is a durable operational learning and files candidates). Capture is FULLY AUTOMATIC:
# the idle-timeout and phase-advance/completion triggers both bottom out here — there is no chat-side
# capture surface. The candidate pool then flows to `distill` exactly as before.

# Single-flight guard, keyed by the ORIGIN session id (in-memory: a restart means nothing is
# actually sweeping, so the set is correctly empty). The spine run row stays sessionless telemetry.
_sweeping: set[str] = set()


def _render_slice(messages: list[dict]) -> str:
    """Render the swept chat messages as a plain, readable transcript for the capture agent."""
    out = []
    for m in messages:
        who = "Owner" if m.get("role") == "you" else "SuperMe"
        out.append(f"{who}: {m.get('text', '').strip()}")
    return "\n\n".join(out)


async def run_sweep(ctx, session_id: str, focus: str | None = None) -> dict:
    """Run ONE capture sweep over a session's un-swept conversation tail. The keystone of the
    learning workflow's capture end (WI-8):

      1. read the transcript slice `watermark → head` (content-level idempotency — never re-sweep);
      2. if nothing new, no-op;
      3. else launch a DISPOSABLE headless run of the `capture` sub-agent over that slice, which
         files candidates via `mcp__dev__file_candidate` (provenance bound here, not by the agent);
      4. advance the watermark to head on a clean pass (an abort leaves it, so the slice re-sweeps).

    Disposable like distill: the sweep run is a sessionless spine Run (telemetry kept); its throwaway
    sub-transcript is deleted on finish. `focus` is the owner's optional steer (manual sweep)."""
    context_id = ctx.id
    if session_id in _sweeping:
        return {"status": "already_running", "session_id": session_id}
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
    # A `focus` is an explicit steer (debug-only now — supplied solely by the `/dev/sweep` ops hook;
    # the automatic triggers pass None). When present, treat it as a directive: file it unless it's
    # genuinely non-operational. Redundancy ("already covered") is decided downstream by distill +
    # the owner, NOT suppressed here — else an explicit steer silently keeps nothing.
    focus_line = (f"\n\n**A capture steer was supplied for this sweep — treat it as a directive, not "
                  f"a hint.** Prioritize it: file it as a candidate unless it is genuinely "
                  f"non-operational (a pure fact/reference with no effect on how SuperMe behaves). "
                  f"The steer:\n{focus}\n") if focus else ""
    prompt = (
        "Use the `capture` sub-agent (superme-dev:capture) to sweep the conversation slice below "
        "for durable OPERATIONAL learnings and file each as a candidate. This is an autonomous "
        "headless run — there is no human in this chat, so do not ask questions; invoke the agent "
        "and let it file what it finds (filing nothing is a valid result)."
        f"{focus_line}\n\n--- conversation slice (oldest first) ---\n{_render_slice(slice_msgs)}\n"
        "--- end slice ---"
    )
    # Bind provenance server-side: the agent supplies substance, we stamp which session it came from.
    turn_mcp = {"dev": make_dev_mcp_server(_dev_store, context_id, origin_session_id=session_id)}
    run_status = "done"
    sub_session = None
    filed = 0
    try:
        async for ev in _agent.run_turn(
            ctx, prompt, resume=None,
            approve=deny_all,                 # capture writes only via the file_candidate DB tool
            extra_mcp_servers=turn_mcp,
        ):
            if isinstance(ev, Usage):
                _spine.bump_run(run_id, add_tokens=ev.total_tokens, ctx_pct=ev.context_pct)
            elif isinstance(ev, Result):
                sub_session = ev.session_id   # captured ONLY to dispose the throwaway transcript
            elif isinstance(ev, Init):
                _cache_slash(ctx.id, ev.slash_commands)
            # NB: never _sessions.record — the sweep is sessionless; its transcript is disposed below.
    except Exception:
        log.exception("capture sweep run failed for %s (session %s)", context_id, session_id)
        run_status = "aborted"
    finally:
        _spine.finish_run(run_id, status=run_status)
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


# --- capture-sweep TRIGGERS (WI-8 build ②) ------------------------------------------------
# Both auto triggers bottom out at run_sweep, identical to the manual path. The watermark makes
# every trigger safe to fire freely — content is never swept twice. v1 triggers (owner-locked):
#   • PHASE-ADVANCE / COMPLETION — the work-item lifecycle gates (fired from those endpoints);
#   • IDLE-TIMEOUT — a periodic loop sweeps dev sessions that went quiet with un-swept content.
# Deferred: every-N-turn cadence, pre-compaction.
SWEEP_IDLE_SECONDS = 15 * 60     # a dev session quiet this long, with un-swept content, gets swept
SWEEP_POLL_SECONDS = 5 * 60      # how often the idle loop scans (the watermark is the real gate, so
                                 # this only sets latency, not how often anything actually sweeps)


def _fire_sweep_bg(ctx, session_id: str | None, *, focus: str | None = None,
                   then_purge: bool = False) -> None:
    """Fire-and-forget a sweep so the triggering request returns immediately. `then_purge` chains a
    transcript purge AFTER the sweep — for completion, where the transcript is reclaimed but the
    sweep must read it first (so the purge can't happen synchronously in the endpoint)."""
    if not session_id:
        return

    async def _job():
        try:
            await run_sweep(ctx, session_id, focus=focus)
        except Exception:
            log.exception("background sweep failed for session %s", session_id)
        finally:
            if then_purge:
                _sessions.purge(ctx, session_id)

    asyncio.create_task(_job())


async def _sweep_idle_sessions(idle_seconds: int = SWEEP_IDLE_SECONDS) -> dict:
    """One idle-scan pass: across every repo, sweep each dev session that (a) has gone quiet for
    `idle_seconds` and (b) has un-swept content past its watermark. Eligible sessions are swept
    CONCURRENTLY (each has its own single-flight guard). Returns a small summary for logging/tests.

    VISIBLE-ONLY (token safety): the scan walks `sessions_for_cwd` (resumable_only=True) — exactly
    the recorded, dashboard-visible dev sessions. Disposable sub-runs (distill/sweep) are never
    recorded, and the owner's own Claude-Code transcript is never recorded, so neither is ever swept."""
    now = time.time()
    eligible: list[tuple] = []   # (ctx, repo_id, session_id)
    scanned = 0
    for repo_id in _spine.repos():
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
            scanned += 1
            mtime = _sessions.transcript_mtime(ctx, sid)
            if mtime is None or (now - mtime) < idle_seconds:
                continue  # missing or still active
            msgs = _sessions.transcript_messages(ctx, sid)
            if _spine.get_sweep_watermark(sid) >= len(msgs):
                continue  # nothing new since the last sweep
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


async def _idle_sweep_loop() -> None:
    """The daemon's idle-sweep heartbeat: every SWEEP_POLL_SECONDS, IF auto-learning is enabled,
    scan + sweep quiet dev sessions. When the master switch is off the loop just idles (cheap)."""
    while True:
        try:
            await asyncio.sleep(SWEEP_POLL_SECONDS)
            if _spine.get_learning_enabled():
                await _sweep_idle_sessions()
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("idle sweep loop iteration failed")
