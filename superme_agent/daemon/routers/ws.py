"""The bidirectional agent WebSocket (`/ws/agent`).

Turns run sequentially per connection; a concurrent reader task lets approval frames arrive
while a turn is paused, which would otherwise deadlock the approval round-trip.

Imports singletons from `app_state` to avoid an import cycle.
"""

import uuid
import asyncio
import logging
import time
from dataclasses import replace
from pathlib import Path

from claude_agent_sdk import HookMatcher
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..app_state import (
    agent as _agent, dev as _dev, dev_store as _dev_store, spine as _spine,
    sessions as _sessions, commands as _commands,
)
from ..deps import load_slash_cache as _load_slash_cache, cache_slash as _cache_slash
from ..protocol import (
    event_to_frame, init_frame, result_frame, approval_request_frame, error_frame, parse_inbound,
)
from ..schemas.ws import TurnFrame, ApprovalResponseFrame, WatchFrame, DashboardHelloFrame
from ..services import compaction, dashboard_stream, item_stream
from ..services.runs import (
    begin_run, end_run, LiveTokens,
    bank_auto_checkpoint, capture_prompt, capture_event, compacted_checkpoint,
    compacted_session_memory, fire_auto_triage, read_completion,
)
from ...core.auth import auth_status
from ...core import kernel_speech
from ...core.vocab import kind_profiles
from ...core import (
    Init, Usage, Result, Status, TextDelta, ToolResult, scoped_writes_approve,
    PLAN_READONLY_NUDGE,
    VET_READONLY_NUDGE,
)
from ...core.faults import classify
from ...core.permissions import APPROVAL_UNANSWERED, approval_signature
from ...gateway import contexts
from ...harness.tools.dev_tools import make_dev_mcp_server
from ...core.kernel_speech import (
    work_item_preamble, general_preamble, onboarding_preamble, diagnosis_preamble,
    diagnosis_trace_block, compaction_notice,
)
from ...paths import DAEMON_APPROVAL_TIMEOUT

log = logging.getLogger("superme-agent")

router = APIRouter()


def _norm_cwd(p) -> str:
    """A cwd normalized for equality checks (resolves symlinks/relative parts, tolerates junk)."""
    try:
        return str(Path(str(p)).resolve()) if p else ""
    except OSError:
        return str(p or "")


def resolve_item_session(item: dict, *, worktree, repo_dir, get_session, adopt) -> tuple[str, str | None]:
    """The explicit phase-to-session map: which session a bound turn runs in.

    None means this turn mints. Other phases' threads are left alone."""
    phase = str(item.get("phase") or "triage")
    slot = kind_profiles.session_slot(phase)
    slots = item.get("sessions") or {}
    sid = slots.get(slot)
    if not sid and phase in kind_profiles.INTAKE_PHASES:
        shared = slots.get(kind_profiles.LEGACY_INTAKE_SLOT)
        if shared:
            adopt(str(shared), slot)
            sid = str(shared)
    if not sid and not slots and item.get("session_id"):
        legacy_sid = str(item["session_id"])
        lcwd = _norm_cwd((get_session(legacy_sid) or {}).get("cwd"))
        # The cwd names a family, not a phase: worktree means build, repo means some intake phase.
        legacy_slot = ("build" if worktree and lcwd == _norm_cwd(worktree)
                       else slot if (lcwd == _norm_cwd(repo_dir)
                                     and phase in kind_profiles.INTAKE_PHASES) else None)
        if legacy_slot:
            adopt(legacy_sid, legacy_slot)
            if legacy_slot == slot:
                sid = legacy_sid
    return slot, sid


def _live_resume(msg_resume: str | None, resumed: dict | None) -> str | None:
    """The resume sid a turn may actually use.

    A client resume resolving to no stored row is DANGLING, and handing it to the CLI hard-fails the
    turn."""
    return msg_resume if (msg_resume and resumed is not None) else None


def _latest_report_outcome(context_id: str, item_id: str) -> str | None:
    """The item's newest `run.report` outcome, or None. The grill detector keys on this: `needs_user`
    means the plan agent is parked on its questions."""
    try:
        for e in _dev_store.list_events(context_id, item_id=item_id, limit=25):
            if str(e.get("kind")) == "run.report":
                return str((e.get("meta") or {}).get("outcome") or "") or None
    except Exception:
        log.exception("latest report outcome read failed for %s", item_id)
    return None


def _compact_reply(verdict: dict | None) -> str:
    """The owner's answer to a manual `/compact` — the one owner-facing sentence compaction has. The
    numbers are the verdict event's own."""
    if not verdict:
        return ("Nothing to compact — this session has no fill on record yet, or it has already "
                "backed off from compacting (two ineffective attempts).")
    if verdict.get("skipped"):
        return f"Compaction skipped — {verdict['skipped']}."
    if verdict.get("error"):
        return "Compaction failed — see the daemon log; the session is unchanged."
    pre, post = verdict.get("pre_tokens"), verdict.get("post_tokens")
    if not pre:
        return "Compaction ran but recorded no boundary — the session may be too small to compact."
    return (f"Compacted: {pre:,} → {post:,} tokens ({round(verdict.get('gain_pct') or 0)}% "
            f"smaller). A checkpoint was banked first, so what only this conversation knew is "
            f"on disk.")


# A dev session runs as one of several agents. Pointer-only, so no artifact contents inflate ctx%.


@router.websocket("/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    await ws.accept()
    # One serialized sender: the turn loop and the drain task both push, and Starlette's send
    # cannot interleave.
    send_lock = asyncio.Lock()

    async def send(frame: dict) -> None:
        async with send_lock:
            await ws.send_json(frame)

    # Seed the client's palette from the last-known list, so it is usable before the first turn.
    cached = _load_slash_cache().get("global")
    if cached:
        await send(init_frame(cached))

    pending: dict[str, asyncio.Future] = {}   # approval_id -> Future[bool]
    inbox: asyncio.Queue = asyncio.Queue()     # queued TurnFrames (None = disconnect)

    # This panel watches at most one work-item; a drain task forwards that item's frames
    # independent of any turn.
    watch: dict = {"item_id": None, "queue": None, "task": None}

    async def _drain(item_id: str, q: asyncio.Queue) -> None:
        """Forward one watched item's live broker frames to the client until cancelled."""
        try:
            while True:
                frame = await q.get()
                await send(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ws watch-drain failed for %s", item_id)

    async def set_watch(item_id: str | None) -> None:
        """(Re)point this panel's watch. Idempotent for the same id; tears down the prior subscription and
        drain first."""
        if watch["item_id"] == item_id:
            return
        if watch["task"]:
            watch["task"].cancel()
            item_stream.unsubscribe(watch["item_id"], watch["queue"])
        watch.update(item_id=None, queue=None, task=None)
        if item_id:
            q = item_stream.subscribe(item_id)
            watch.update(item_id=item_id, queue=q,
                         task=asyncio.create_task(_drain(item_id, q)))

    async def reader() -> None:
        """Pump incoming frames (typed via protocol.parse_inbound): resolve approvals immediately,
        (un)subscribe watches, queue turns."""
        try:
            while True:
                frame = parse_inbound(await ws.receive_json())
                if isinstance(frame, ApprovalResponseFrame):
                    fut = pending.get(frame.id)
                    if fut and not fut.done():
                        fut.set_result(frame.approved)
                elif isinstance(frame, WatchFrame):
                    await set_watch(frame.item_id)
                elif isinstance(frame, TurnFrame):
                    await inbox.put(frame)
                else:
                    log.warning("ignoring unknown client frame")
        except WebSocketDisconnect:
            await inbox.put(None)
        except Exception:
            log.exception("ws reader failed")
            await inbox.put(None)

    approved_sigs: set[str] = set()  # remember approval within a session — once the owner
    #                                   OK's a KIND of call, later calls of that kind don't re-ask.

    async def approve(tool_name: str, tool_input: dict) -> bool | str:
        """The daemon's ApproveFn: round-trip the decision to this client, but only once per kind of call —
        a previously-approved signature auto-allows."""
        sig = approval_signature(tool_name, tool_input)
        if sig in approved_sigs:
            return True
        aid = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        pending[aid] = fut
        await send(approval_request_frame(aid, tool_name, tool_input))
        try:
            granted = await asyncio.wait_for(fut, timeout=DAEMON_APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("approval %s for %s timed out -> deny", aid[:8], tool_name)
            # Denied, but by nobody. An agent told "the owner denied this" reasons from a false
            # premise.
            return APPROVAL_UNANSWERED
        finally:
            pending.pop(aid, None)
        if granted:
            approved_sigs.add(sig)
        return granted

    reader_task = asyncio.create_task(reader())
    # Remember the last work-item this connection worked, so the disconnect path can bank a
    # fallback checkpoint.
    conn_started = time.time()
    last_bound: tuple | None = None   # (ctx, work_item_id)
    try:
        while True:
            msg = await inbox.get()
            if msg is None:
                break  # client disconnected
            # Nothing downstream can run without a credential, and the SDK's own failure names
            # neither the cause nor the fix.
            if not (auth := auth_status())["ready"]:
                await send({"type": "error", "message": auth["detail"]})
                continue
            ctx = contexts.resolve(msg.context_id, msg.mode or "core")
            prompt = msg.prompt
            # The session's durable stamp decides whether this is a work-item turn; only at birth
            # is the payload trusted.
            resumed = _spine.get_session(msg.resume) if msg.resume else None
            work_item_id = (resumed.get("item_id") or None) if resumed is not None else msg.work_item_id
            if ctx.mode != "dev":
                work_item_id = None

            # Onboarding is a repo STATE, not a launched action, so nothing in the payload decides
            # it.
            unestablished = bool(
                ctx.mode == "dev" and ctx.internal_root
                and not _dev.project_established(ctx.internal_root / "dev")
            )
            # Session KIND is server-authoritative like `item_id`: a resumed session's stored kind
            # wins, and only birth infers.
            if ctx.mode != "dev":
                session_kind, subject_run_id = "general", None
            elif resumed is not None:
                session_kind = resumed.get("kind") or ("work_item" if work_item_id else "general")
                subject_run_id = resumed.get("subject_run_id")
            else:
                session_kind = msg.kind or ("work_item" if work_item_id else
                                            "onboarding" if unestablished else "general")
                subject_run_id = msg.subject_run_id
            if work_item_id:  # an item-bound session is a work_item session, whatever the payload said
                session_kind = "work_item"

            # Non-native commands are answered here with no agent turn; `/compact` is intercepted
            # further down instead.
            cmd_reply = _commands.handle(ctx, prompt)
            if cmd_reply is not None:
                await send(result_frame(cmd_reply))
                continue

            # The CLI's own compact skips everything the kernel puts around one — handoff turn,
            # banked checkpoint, verdict, notice.
            manual_compact = prompt.strip().lower() == "/compact"

            # The bound work-item, read once: its configured model and effort feed resolution, its
            # folder sandboxes writes.
            item = ((_dev.read_work_item(ctx.internal_root / "dev", work_item_id) or {})
                    if (work_item_id and ctx.internal_root) else {})

            # One precedence chain: per-turn pick → work-item → repo → system. A per-turn pick
            # never persists.
            model = _spine.effective_model(ctx.id, per_call=msg.model, item_model=item.get("model"))
            effort = _spine.effective_effort(ctx.id, per_call=msg.effort, item_effort=item.get("effort"))

            # An item runs ONE agent at a time; refuse rather than let two agents write the same
            # files concurrently.
            if work_item_id and _spine.is_item_running(ctx.id, work_item_id):
                await send(result_frame(
                    "⏳ This work-item already has a run in progress — wait for it to "
                    "finish before sending another turn."))
                continue

            # A turn bound to a work-item IS that item being worked: open a run and sandbox writes
            # to its folder.
            turn_approve = approve
            began_run = False
            item_run_id = None  # the run id when bound to a work-item (for the per-run event trail)
            # A work-item session gets a Focus block, a general one a Guard block; `gate_general`
            # gates mutating tools.
            session_append = None
            # A diagnosis birth turn prepends the subject-run trace here, so it caches instead of
            # re-sending every turn.
            agent_prompt = prompt
            # `gate_general` hard-gates mutating tools for any non-work-item dev session — general,
            # onboarding, AND diagnosis (a diagnosis session is strictly read-only).
            gate_general = ctx.mode == "dev" and not work_item_id
            # The one write a general or onboarding session may make. Diagnosis gets no write
            # exception at all.
            general_write_root = (
                (ctx.internal_root / "dev" / "general")
                if gate_general and session_kind != "diagnosis" and ctx.internal_root else None
            )
            # The LIVE state, deliberately distinct from `session_kind`: a session born during
            # onboarding stops behaving as one.
            is_onboarding = bool(gate_general and session_kind != "diagnosis" and unestablished)
            # Build and vet run at the worktree; intake stays at the repo, because the CLI stores
            # transcripts per cwd.
            write_boundary = None
            deny_write_tools = None  # vet turns set this: file-writes denied outright
            protected_paths = None    # review turns set this: plan.md is not review's to write
            item_worktree = None
            turn_resume = _live_resume(msg.resume, resumed)
            session_slot = None   # the bound turn's storage slot (triage|plan|build|vet|review|close)
            session_role = None   # its ROLE (intake|build|vet) — NOT the slot; only build and vet share a name
            handoff_mark = None   # step-6 watermark to persist at Result (a promotion rode this turn)
            main_repo_dir = ctx.cwd   # the REAL repo root, captured before any worktree swap
            grill_parked = False  # plan's grill: this bound chat is a Q&A round
            grill_sink: dict = {}  # this turn's report_completion payload (grill round OR a review `revise`)
            compact_verdict: dict | None = None  # set when a compaction fired at this run's start
            if work_item_id and item.get("git_worktree"):
                wt = Path(str(item["git_worktree"]))
                if wt.is_dir():
                    item_worktree = wt
            if work_item_id and ctx.internal_root:
                item_dir = ctx.internal_root / "dev" / "work-items" / work_item_id
                # Auto-allow item-folder writes for autonomous planning; anything else still
                # defers to the surface approval.
                turn_approve = scoped_writes_approve(item_dir, turn_approve)
                # …except plan.md at review, which is read-only there. Scoped to review only:
                # build legitimately ticks its task list.
                if str(item.get("phase")) == "review":
                    protected_paths = [item_dir / "artifacts" / "plan.md"]
                session_slot, slot_sid = resolve_item_session(
                    item, worktree=item_worktree, repo_dir=main_repo_dir,
                    get_session=_spine.get_session,
                    adopt=lambda sid, r: _dev.set_work_item_session(
                        ctx.internal_root / "dev", work_item_id, sid, slot=r),
                )
                # The slot says WHERE a turn runs, never which role it plays. No slot is named
                # `intake`.
                session_role = kind_profiles.session_role(str(item.get("phase") or "triage"))
                # The bound turn runs in the CURRENT role's slot; a resume naming another role's
                # thread is redirected.
                turn_resume = slot_sid
                resumed = _spine.get_session(slot_sid) if slot_sid else None
                # A phase session owning a worktree gets the freeze boundary: writes into the
                # worktree and item dir, denied on main.
                if item_worktree:
                    write_boundary = [item_worktree, item_dir]
                    if kind_profiles.role_uses_worktree(session_role, item.get("kind")):
                        ctx = replace(ctx, cwd=item_worktree)
                # Vet is READ-ONLY on files; the shell stays, because running checks IS the job.
                if session_role == "vet":
                    deny_write_tools = VET_READONLY_NUDGE
                # The item is parked on plan's questions, so mount the report pen — until it re-
                # reports, it stays parked.
                if (session_role == "intake" and str(item.get("phase")) == "plan"
                        and _latest_report_outcome(ctx.id, work_item_id) == "needs_user"):
                    grill_parked = True
                # The one compaction check, before the lock: a prompt sent mid-compaction is
                # exactly what this placement prevents.
                try:
                    compact_verdict = await compaction.compact_before_run(
                        ctx, ctx.id, work_item_id, turn_resume,
                        kind=(item or {}).get("kind"), model=model, force=manual_compact)
                except Exception:
                    log.exception("run-start compaction check failed")
                item_run_id = begin_run(ctx, ctx.id, work_item_id, "chat", model, phase=item.get("phase"))
                began_run = item_run_id is not None
                last_bound = (ctx, work_item_id)
                session_append = work_item_preamble(
                    work_item_id, item, item_dir, shell_cwd=ctx.cwd,
                    # Owed only while this thread's newest finished run is the compaction, so the
                    # very next turn carries it.
                    compacted_checkpoint=compacted_checkpoint(ctx, item, turn_resume))
                # Current focus carries the pointer per-turn; state is read on demand from the
                # item folder it names.
                prompt_prefixes: list[str] = []
                # New loop records inject once into the intake thread; the watermark advances on a
                # Result, so a failed turn re-injects.
                if session_role == "intake":
                    hb, hb_mark = kernel_speech.render_handoff_block(item, item_dir)
                    if hb:
                        prompt_prefixes.append(hb)
                        handoff_mark = hb_mark
                if prompt_prefixes:
                    agent_prompt = "\n\n---\n\n".join(
                        prompt_prefixes + ([prompt] if prompt else []))
            elif session_kind == "diagnosis":
                # The trace is injected once at session birth, so resumed turns read it from the
                # cache rather than re-sending.
                subj_run = _spine.get_run(subject_run_id) if subject_run_id else None
                session_append = diagnosis_preamble(subj_run, subject_run_id or 0)
                if resumed is None and subject_run_id:
                    subj_events = _spine.events_for_run(subject_run_id)
                    trace = diagnosis_trace_block(subj_run, subj_events, subject_run_id)
                    agent_prompt = f"{trace}\n\n---\n\n{prompt}" if prompt else trace
            elif ctx.mode == "dev":
                # The onboarding preamble carries the skill directive, so the kickoff is silent:
                # the owner's first message is their own.
                session_append = (
                    onboarding_preamble((_spine.repo(ctx.id).onboarding if _spine.repo(ctx.id) else None))
                    if is_onboarding else general_preamble()
                )
            # The handoff writes `session-memory/<sid>.md`, so the trigger needs a knowledge home.
            # Diagnosis is read-only and excluded.
            if (not work_item_id and ctx.mode == "dev" and session_kind != "diagnosis"
                    and ctx.internal_root):
                try:
                    compact_verdict = await compaction.compact_before_run(
                        ctx, ctx.id, None, turn_resume, kind=None, model=model,
                        force=manual_compact)
                except Exception:
                    log.exception("run-start compaction check failed (general session)")
            # All three unbound preambles want the identical line, so it is appended rather than
            # passed as a parameter.
            if session_append and not work_item_id:
                session_append += compaction_notice(
                    compacted_session_memory(ctx, turn_resume), has_artifacts=False)
            # A manual `/compact` IS the whole turn; sending the literal string on would compact a
            # session compacted seconds ago.
            if manual_compact:
                await send(result_frame(_compact_reply(compact_verdict)))
                continue
            # Onboarding skills are one-shot per repo: once memory is established, `retrofit`
            # would overwrite the owner's approved docs.
            block_categories = None if is_onboarding else {"onboarding"}
            # A phase skill outside a work-item has no item to read and no pen mounted.
            if not work_item_id:
                block_categories = (block_categories or set()) | {"workspace"}
            # Unbound chat still spends tokens, so record a lightweight run: telemetry only, no
            # status flip and no run-lock.
            turn_hooks = None
            if work_item_id:
                _hook_ctx, _hook_item = ctx, work_item_id

                async def _pre_compact(_input, _tool_use_id, _hctx):
                    try:
                        banked = compaction.bank_precompaction_checkpoint(_hook_ctx, _hook_item)
                        _dev_store.log_event(
                            _hook_ctx.id, "compaction.checkpoint",
                            "Pre-compaction checkpoint banked (PreCompact hook)" if banked
                            else "Pre-compaction checkpoint skipped (fresh one exists)",
                            item_id=_hook_item, actor="daemon", meta={"hook": True})
                    except Exception:
                        log.exception("PreCompact checkpoint hook failed")
                    return {}

                turn_hooks = {"PreCompact": [HookMatcher(hooks=[_pre_compact])]}
            elif ctx.mode == "dev" and session_kind != "diagnosis":
                # A general session can still hit the CLI's own autocompact mid-turn. Without this
                # log the boundary is invisible.
                _hook_ctx_id = ctx.id

                async def _pre_compact_general(_input, _tool_use_id, _hctx):
                    try:
                        _dev_store.log_event(
                            _hook_ctx_id, "compaction.checkpoint",
                            "The CLI compacted this general session on its own, mid-turn — "
                            "nothing was banked (no turn available to write one)",
                            actor="daemon", meta={"hook": True, "banked": False,
                                                  "by_agent": False, "cli_initiated": True})
                    except Exception:
                        log.exception("PreCompact general-session log failed")
                    return {}

                turn_hooks = {"PreCompact": [HookMatcher(hooks=[_pre_compact_general])]}
            chat_feature = ("onboarding" if is_onboarding
                            else "diagnosis" if session_kind == "diagnosis" else "chat")
            chat_run_id = None if began_run else _spine.start_run(
                ctx.id, mode=ctx.mode, feature=chat_feature)
            # The per-run event trail keys on this id, so each Activity row has its own thread.
            active_run_id = item_run_id if began_run else chat_run_id
            if active_run_id:
                capture_prompt(ctx.id, prompt, run_id=active_run_id, item_id=work_item_id)
            # A bound chat gets its item's phase toolset, so a phase's surface never depends on
            # who is driving it.
            tool_scope = ("diagnosis" if session_kind == "diagnosis"
                          else "onboarding" if is_onboarding
                          else str(item.get("phase") or "triage") if work_item_id
                          else "general")
            turn_mcp = (
                {"dev": make_dev_mcp_server(
                    _dev_store, ctx.id, spine=_spine, scope=tool_scope,
                    # With a live worktree `ctx.cwd` IS the worktree, so evidence fingerprints the
                    # validated tree.
                    dev_root=(ctx.internal_root / "dev") if ctx.internal_root else None,
                    repo_dir=ctx.cwd,
                    main_repo_dir=main_repo_dir,
                    # The item write-tools operate ONLY this session's bound item; a general
                    # session gets none.
                    bound_item_id=work_item_id,
                    # An auto-pushed child gets the same first kick the owner's push gives it.
                    fire_triage=lambda child_id: fire_auto_triage(ctx.id, child_id, _spine),
                )}
                if ctx.mode == "dev" else None
            )
            if work_item_id and ctx.mode == "dev":
                # Every bound chat turn gets the report pen, so a phase agent can declare its
                # outcome where it talks.
                from ...harness.tools.run_tools import make_run_report_server
                turn_mcp = {**(turn_mcp or {}), "run": make_run_report_server(grill_sink)}
            final_tokens = None
            final_usage = None
            final_session = None
            final_model = None
            final_ctx = None   # the turn's end-of-turn context-window fill (persisted so the chat
                               # header can show a session's ctx% on reopen, not just live)
            live = LiveTokens()   # dedupes the Usage stream by message_id for an accurate live estimate
            try:
                async for ev in _agent.run_turn(
                    ctx,
                    agent_prompt,
                    resume=turn_resume,
                    model=model,
                    effort=effort or _spine.DEFAULT_EFFORT,   # final "medium" floor
                    approve=turn_approve,
                    extra_mcp_servers=turn_mcp,
                    enforce_silent=True,   # default; stated because chat is where a silent skill tempts
                    # The ONE surface with a turn after this one: a person can read a subagent's
                    # late result. Every kernel-fired run is a single turn and defaults to False.
                    has_later_turn=True,
                    scope_reads=True,      # L2 read-guard: keep reads inside the host's scope
                    preamble=session_append,       # Focus (work-item) / Guard (general) block
                    # A bound chat already names its subject, so it skips the board-wide list; an
                    # unbound session keeps it.
                    item_bound=bool(work_item_id),
                    gate_general_mutations=gate_general, # hard-gate mutations in a general dev session
                    general_write_root=general_write_root,  # …except writing this project's general/ memory
                    write_boundary=write_boundary,  # build writes stay in the worktree and item dir
                    deny_write_tools=deny_write_tools,  # vet: no file-write capability at all
                    protected_paths=protected_paths,    # review: plan.md is not review's to write
                    protected_nudge=PLAN_READONLY_NUDGE,
                    hooks=turn_hooks,  # PreCompact checkpoint-first safety net
                    block_categories=block_categories,  # onboarding skills die once memory exists
                ):
                    if isinstance(ev, Usage) and began_run:
                        live.bump(ctx.id, work_item_id, ev)
                    # Claim the session id so it appears in this context's picker, distinct from
                    # the owner's own sessions.
                    elif isinstance(ev, Result):
                        final_tokens = ev.tokens
                        final_usage = ev.usage
                        final_session = ev.session_id
                        final_model = ev.model
                        final_ctx = ev.ctx_pct
                        _sessions.record(ctx, ev.session_id)
                        if work_item_id and ev.session_id and ctx.internal_root:
                            try:
                                _dev.set_work_item_session(
                                    ctx.internal_root / "dev", work_item_id, ev.session_id,
                                    slot=session_slot or "triage",
                                )
                                # Reverse stamp at a work-item session's birth: from here the
                                # daemon reads this, not the client payload.
                                _spine.stamp_session_item(ev.session_id, work_item_id)
                                # …and its role. Legacy item sessions keep kind NULL, which
                                # derives to `work_item` downstream.
                                _spine.stamp_session_kind(ev.session_id, session_role or "intake")
                                # The promotion block landed in this turn's transcript, so advance
                                # the watermark. Only on a successful Result.
                                if handoff_mark is not None:
                                    _dev.set_work_item_handoff_mark(
                                        ctx.internal_root / "dev", work_item_id, handoff_mark)
                            except Exception:
                                log.exception("failed to persist session to work-item %s", work_item_id)
                        elif ev.session_id and ctx.mode == "dev":
                            # Stamp the kind at birth so a resume reads it — diagnosis would
                            # otherwise derive back to `general`.
                            try:
                                _spine.stamp_session_kind(ev.session_id, session_kind, subject_run_id)
                            except Exception:
                                log.exception("failed to stamp session kind %s", session_kind)
                    elif isinstance(ev, Init):
                        _cache_slash(ctx.id, ev.slash_commands)
                    # Per-run trail for any run: the reply text, each call, and that call's capped
                    # output.
                    if active_run_id and isinstance(ev, (Status, TextDelta, ToolResult)):
                        # this interactive turn already streams to the firing panel, so a broker
                        # echo would double it
                        capture_event(ctx.id, ev, run_id=active_run_id, item_id=work_item_id,
                                      publish_live=False)
                    # ToolResult is trail-only: it has no live frame and `event_to_frame` would
                    # reject it.
                    if not isinstance(ev, ToolResult):
                        await send(event_to_frame(ev))
                # A chat turn rests a working item `active` but never clears a hold; only Approve
                # does.
                if began_run:
                    # One writer for the run.report event, so an interactive ending is recorded
                    # like a kernel-fired one.
                    report = read_completion(ctx.id, work_item_id, grill_sink, run_id=item_run_id)
                    outcome = report.get("outcome") if report else None
                    # `revise` is honoured only at review, because it crosses a phase boundary.
                    # Reported elsewhere it is just logged.
                    if outcome == "revise" and str((item or {}).get("phase")) != "review":
                        outcome = None
                    # `revise` is the one outcome that MOVES the item, so it alone drops the hold.
                    rest_status = ("awaiting_human"
                                   if str((item or {}).get("status")) == "awaiting_human"
                                   and outcome != "revise" else "active")
                    end_run(ctx, ctx.id, work_item_id, final_tokens, rest_status, final_usage,
                             final_ctx, outcome=outcome, session_id=final_session,
                             summary=str((report or {}).get("summary") or ""))
                    # A real turn just reported fresh usage, so release the defer latch. No
                    # compaction is evaluated here.
                    if final_session:
                        compaction.note_turn_start(final_session)
                elif chat_run_id:
                    _spine.finish_run(chat_run_id, usage=final_usage, session_id=final_session,
                                      model=final_model, ctx_pct=final_ctx)
                    # Same defer release as the bound branch: without it the seam above keeps
                    # reading the stale pre-compaction fill.
                    if final_session:
                        compaction.note_turn_start(final_session)
                # No chat-side capture: the conversation is swept automatically (idle-timeout +
                # phase-advance/completion), so nothing fires here per-turn.
            except Exception as e:
                if began_run:
                    end_run(ctx, ctx.id, work_item_id, final_tokens, "active", final_usage,
                             final_ctx, session_id=final_session)
                elif chat_run_id:
                    _spine.finish_run(chat_run_id, status="aborted", usage=final_usage,
                                      session_id=final_session, model=final_model, ctx_pct=final_ctx)
                # Classify, but do NOT climb the retry ladder — the owner is watching and would
                # rather be told.
                fault = classify(exc=e)
                log.warning("chat turn failed (%s): %s", fault.kind, fault.reason)
                try:
                    await send(error_frame(fault.reason if fault.retryable else str(e)))
                except Exception:
                    break  # socket is gone
    finally:
        reader_task.cancel()
        # tear down any live watch: cancel the drain, drop the broker subscription
        if watch["task"]:
            watch["task"].cancel()
            item_stream.unsubscribe(watch["item_id"], watch["queue"])
        # No-op if the agent banked its own since `conn_started`, or the item is terminal. Never
        # blocks the close.
        if last_bound:
            try:
                bank_auto_checkpoint(last_bound[0], last_bound[1], since=conn_started)
            except Exception:
                log.exception("session-end auto-checkpoint failed")


# Send-only, and it sends TOPICS, never values — the panel refetches over HTTP, so every number
# keeps one source.


@router.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket) -> None:
    await ws.accept()
    q = dashboard_stream.subscribe()

    async def pump() -> None:
        """Forward coalesced invalidation frames until cancelled."""
        while True:
            await ws.send_json(await q.get())

    # The hello IS the contract: its arrival tells the browser push is live, so it can slow its
    # polling.
    await ws.send_json(DashboardHelloFrame(coalesce_ms=dashboard_stream.COALESCE_MS).model_dump())
    task = asyncio.create_task(pump())
    try:
        # The client never speaks; this await is how a close is noticed at all.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.debug("dashboard socket closed: %s", e)
    finally:
        task.cancel()
        dashboard_stream.unsubscribe(q)
