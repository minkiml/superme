"""The bidirectional agent WebSocket (`/ws/agent`).

Runs turns sequentially per connection; a concurrent reader task lets approval_response frames
arrive *while* a turn is paused awaiting an approval (otherwise the turn loop would block the
receive path and deadlock the approval round-trip). A work-item-bound turn opens a run (live
telemetry + run-lock) and sandboxes its writes to the item's folder.

Imports singletons from `app_state` (never from server.py) so there's no import cycle.
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
from ..schemas.ws import TurnFrame, ApprovalResponseFrame
from ..services import compaction
from ..services.runs import (
    _begin_run, _end_run, _LiveTokens, _log_artifact,
    bank_auto_checkpoint, capture_prompt, capture_event,
)
from ...core import session_contract, status_router
from ...core import Init, Usage, Result, Status, TextDelta, ToolResult, scoped_writes_approve
from ...gateway import contexts
from ...harness.tools.dev_tools import make_dev_mcp_server
from ..session_agents import (
    work_item_preamble, general_preamble, onboarding_preamble, diagnosis_preamble,
    diagnosis_trace_block,
)
from ...runtime.config import DAEMON_APPROVAL_TIMEOUT

log = logging.getLogger("superme-agent")

router = APIRouter()


# --- session-aware per-turn append (work-item-session-recognition + session-kinds-diagnose) ----
# A dev session runs as one of several AGENTS (session_agents.py owns each one's identity preamble):
# work_item (the builder, centered on its item) · general (the advisor, discussion-only) · onboarding
# (general's establish-memory persona while the project has no memory) · diagnosis (read-only, pointed
# at a subject run, its trace auto-injected). Pointer-only by design — no artifact contents are inlined,
# keeping ctx% honest. Assembled here (the daemon knows the stamp); Core just appends what it's handed.


@router.websocket("/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    await ws.accept()
    # Seed the client's "/" palette from the last-known list (web is the global context),
    # so it's usable before the first turn reveals the live list.
    cached = _load_slash_cache().get("global")
    if cached:
        await ws.send_json(init_frame(cached))

    pending: dict[str, asyncio.Future] = {}   # approval_id -> Future[bool]
    inbox: asyncio.Queue = asyncio.Queue()     # queued TurnFrames (None = disconnect)

    async def reader() -> None:
        """Pump incoming frames (typed via protocol.parse_inbound): resolve approvals immediately,
        queue turns."""
        try:
            while True:
                frame = parse_inbound(await ws.receive_json())
                if isinstance(frame, ApprovalResponseFrame):
                    fut = pending.get(frame.id)
                    if fut and not fut.done():
                        fut.set_result(frame.approved)
                elif isinstance(frame, TurnFrame):
                    await inbox.put(frame)
                else:
                    log.warning("ignoring unknown client frame")
        except WebSocketDisconnect:
            await inbox.put(None)
        except Exception:
            log.exception("ws reader failed")
            await inbox.put(None)

    async def approve(tool_name: str, tool_input: dict) -> bool:
        """The daemon's ApproveFn — round-trip the decision to this client."""
        aid = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        pending[aid] = fut
        await ws.send_json(approval_request_frame(aid, tool_name, tool_input))
        try:
            return await asyncio.wait_for(fut, timeout=DAEMON_APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("approval %s for %s timed out -> deny", aid[:8], tool_name)
            return False
        finally:
            pending.pop(aid, None)

    reader_task = asyncio.create_task(reader())
    # Session-end checkpoint hook (S5): remember the last work-item this connection worked, so the
    # disconnect path can bank a mechanical fallback checkpoint (skipped when the agent banked its
    # own during the session) — the orient block always has a latest checkpoint to cold-start from.
    conn_started = time.time()
    last_bound: tuple | None = None   # (ctx, work_item_id)
    try:
        while True:
            msg = await inbox.get()
            if msg is None:
                break  # client disconnected
            ctx = contexts.resolve(msg.context_id, msg.mode or "core")
            prompt = msg.prompt
            # Work-item identity is SERVER-AUTHORITATIVE (work-item-session-recognition-prd Q7): the
            # session's durable `item_id` stamp — NOT the client payload — decides whether this turn
            # is a work-item turn, and drives ALL of centering + write-sandbox + run-lock + telemetry.
            # Rule: if the resumed session already EXISTS, its stamp wins (a work-item session stays
            # centered even when reopened straight from the picker with no payload; a general session
            # can't be spoofed into item behavior by a stale/rogue payload). Only at a session's BIRTH
            # (no resume row yet) do we trust the opening workflow's payload — that first turn is what
            # mints + stamps the session. Dev surface only; core turns never bind.
            resumed = _spine.get_session(msg.resume) if msg.resume else None
            work_item_id = (resumed.get("item_id") or None) if resumed is not None else msg.work_item_id
            if ctx.mode != "dev":
                work_item_id = None

            # Is this dev repo still unestablished (no PRD deliverables ⇒ no project memory)? That
            # is the ONE fact both the session kind and the onboarding behaviour below derive from —
            # onboarding is a repo STATE, not a launched action, so nothing in the payload decides it.
            unestablished = bool(
                ctx.mode == "dev" and ctx.internal_root
                and not _dev.project_established(ctx.internal_root / "dev")
            )
            # Session KIND (session-kinds-diagnose) — server-authoritative like item_id: a resumed
            # session's STORED kind wins; only at BIRTH do we infer. Inference: item_id ⇒ work_item ·
            # an unestablished repo ⇒ onboarding (every session there IS the onboarding workflow) ·
            # else general. The payload may still name a kind explicitly (`diagnosis`, which carries
            # a read-only `subject_run_id` — the Activity run it inspects). Dev surface only.
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

            # Shared command layer: non-native commands (/model) are handled here and
            # answered directly — no agent turn. Everything else (incl. native /compact,
            # /clear, skills) falls through to the CLI below.
            cmd_reply = _commands.handle(ctx, prompt)
            if cmd_reply is not None:
                await ws.send_json(result_frame(cmd_reply))
                continue

            # The bound work-item (if any): its configured model/effort feed resolution, and its
            # folder sandboxes writes below. Empty dict when unbound. Read ONCE here.
            item = ((_dev.read_work_item(ctx.internal_root / "dev", work_item_id) or {})
                    if (work_item_id and ctx.internal_root) else {})

            # Model + effort resolved through the ONE precedence helper (session-model-precedence):
            # per-turn/session pick (`msg.model` — the surface's runtime override; it NEVER persists
            # to the repo default) → the work-item's configured model → this repo's default override
            # → the system default. Effort mirrors it; the "medium" floor is applied at the run call.
            model = _spine.effective_model(ctx.id, per_call=msg.model, item_model=item.get("model"))
            effort = _spine.effective_effort(ctx.id, per_call=msg.effort, item_effort=item.get("effort"))

            # Run-lock: an item runs ONE agent at a time. If something is already working it
            # (a headless plan, or another bound turn), refuse rather than let two agents write
            # the same files concurrently. The owner waits for the in-flight run to finish.
            if work_item_id and _spine.is_item_running(ctx.id, work_item_id):
                await ws.send_json(result_frame(
                    "⏳ This work-item already has a run in progress — wait for it to "
                    "finish before sending another turn."))
                continue

            # A turn bound to a work-item IS that item being worked on: open a run (flips it to
            # in_progress now + tracks live time/tokens/model) and sandbox its writes to the
            # item's own folder so planning is autonomous within its dev-knowledge — writes to
            # anything else (real code) still prompt the human. (plan/design-era policy.)
            turn_approve = approve
            began_run = False
            item_run_id = None  # the run id when bound to a work-item (for the per-run event trail)
            # Session-aware per-turn append (work-item-session-recognition-prd): a work-item session
            # gets a Focus block (centered on its item); a general dev session gets a Guard block
            # (discussion-only). Assembled here (the daemon knows the session's stamp) and handed to
            # the agent. `gate_general` hard-gates mutating tools for a general dev session.
            session_append = None
            # The prompt actually SENT to the agent. Defaults to the user's message; a diagnosis birth
            # turn prepends the subject-run trace here (once) so it lands in the transcript and caches,
            # instead of re-sending it in the per-turn system prompt (token-inefficiency-per-turn-append).
            # The ORIGINAL `prompt` is what gets captured for the Activity trail — kept clean of the trace.
            agent_prompt = prompt
            # `gate_general` hard-gates mutating tools for any non-work-item dev session — general,
            # onboarding, AND diagnosis (a diagnosis session is strictly read-only).
            gate_general = ctx.mode == "dev" and not work_item_id
            # The one write a general/onboarding dev session may make: authoring/maintaining this
            # project's `general/` memory. Auto-allowed by the guardrail; real-code writes stay
            # denied+nudged. Diagnosis gets NO write exception (fully read-only) → root stays None.
            general_write_root = (
                (ctx.internal_root / "dev" / "general")
                if gate_general and session_kind != "diagnosis" and ctx.internal_root else None
            )
            # Is this turn an ONBOARDING turn? (session-agent-lifecycle-prd, Bug 3.) An unestablished
            # dev repo's general session IS the onboarding workflow — establishing the project's
            # memory — so its runs are tagged `onboarding` (a workflow agent that counts in the
            # AGENTS `running` metric), vs plain `chat` which never counts.
            # NOTE this is the LIVE state, deliberately distinct from `session_kind`: a session BORN
            # during onboarding keeps `kind=onboarding` forever (that's what it was), but the moment
            # the docs land it stops BEHAVING as one — general preamble, onboarding skills blocked.
            # Identity is durable; behaviour auto-retires.
            is_onboarding = bool(gate_general and session_kind != "diagnosis" and unestablished)
            # NOTE: the agent no longer writes `memory/` files during a turn — capture (automatic
            # sweeps) and processing (`distill`) write DB rows, and apply/publish are owner-gated
            # daemon-side writes. So the old `scoped_writes_approve(memory/)` sandbox here is gone
            # (PRD §4.10). Only the work-item folder is auto-write below.
            # S4 git layer: a work-item with a live worktree record (build+ of a worktree kind)
            # WORKS IN ITS WORKTREE — the turn's cwd swaps to it (reads/writes/Bash all resolve
            # there), and the freeze boundary confines Edit/Write to worktree + item folder
            # (hard-deny outside, auto-allow inside). Cleared at terminal (record's dir is gone).
            write_boundary = None
            item_worktree = None
            turn_resume = msg.resume
            rotated_from = None   # the pre-worktree session this turn replaces (retired on success)
            main_repo_dir = ctx.cwd   # the REAL repo root, captured before any worktree swap
            if work_item_id and item.get("git_worktree"):
                wt = Path(str(item["git_worktree"]))
                if wt.is_dir():
                    item_worktree = wt
            if work_item_id and ctx.internal_root:
                item_dir = ctx.internal_root / "dev" / "work-items" / work_item_id
                # Auto-allow item-folder writes (autonomous planning within its own dir); anything
                # else still defers to the surface approval. (`item` + `model`/`effort` were resolved
                # above — the item's configured model already factored into `model`.)
                turn_approve = scoped_writes_approve(item_dir, turn_approve)
                if item_worktree:
                    ctx = replace(ctx, cwd=item_worktree)
                    write_boundary = [item_worktree, item_dir]
                    # Session-cwd boundary: the CLI stores transcripts PER-CWD, so a session born
                    # pre-build (cwd = the repo) cannot be resumed from the worktree — the CLI
                    # exits 1 with "No conversation found". Entering build is a natural session
                    # boundary anyway: mint a FRESH session here (the orient block below carries
                    # continuity from plan.md + the banked checkpoint); the replaced thread is
                    # retired once the new session records.
                    def _norm_cwd(p) -> str:
                        try:
                            return str(Path(str(p)).resolve()) if p else ""
                        except OSError:
                            return str(p or "")
                    if resumed is not None and _norm_cwd(resumed.get("cwd")) != _norm_cwd(item_worktree):
                        rotated_from = msg.resume
                        turn_resume = None
                        resumed = None   # birth semantics: orient block injects below
                item_run_id = _begin_run(ctx, ctx.id, work_item_id, "chat", model, phase=item.get("phase"))
                began_run = item_run_id is not None
                last_bound = (ctx, work_item_id)
                session_append = work_item_preamble(work_item_id, item, item_dir)
                # Cold-start orient block (S5/D11 §3): kernel-assembled, injected ONCE at session
                # BIRTH into the transcript (later turns cache-read it; never re-sent per turn).
                # `resumed is None` = no stored session row = this turn mints the session.
                if resumed is None:
                    try:
                        siblings = _dev.read_all(ctx.internal_root / "dev")["work_items"]
                        kids = status_router.children_of(siblings, work_item_id)
                    except Exception:
                        kids = []
                    orient = session_contract.render_orient_block(item, item_dir, children=kids)
                    agent_prompt = f"{orient}\n\n---\n\n{prompt}" if prompt else orient
            elif session_kind == "diagnosis":
                # Read-only inspection of a subject run (session-kinds-diagnose). The IDENTITY/behaviour
                # header rides the per-turn system prompt (small, stable → caches). The large subject-run
                # TRACE is injected ONCE at session birth into the prompt below, so resumed turns read it
                # from the transcript cache instead of re-writing ~20k every turn
                # (token-inefficiency-per-turn-append). Missing subject_run_id ⇒ header only, no trace.
                subj_run = _spine.get_run(subject_run_id) if subject_run_id else None
                session_append = diagnosis_preamble(subj_run, subject_run_id or 0)
                if resumed is None and subject_run_id:
                    subj_events = _spine.events_for_run(subject_run_id)
                    trace = diagnosis_trace_block(subj_run, subj_events, subject_run_id)
                    agent_prompt = f"{trace}\n\n---\n\n{prompt}" if prompt else trace
            elif ctx.mode == "dev":
                # The general agent — or its onboarding persona while the project has no memory yet
                # (transient: the session stays stamped `general`; only the preamble differs).
                # The onboarding preamble carries the skill directive itself (naming the repo's
                # connect-time choice), so the kickoff is SILENT: the owner's first message is their
                # project description and nothing else — no canned prompt in their transcript.
                session_append = (
                    onboarding_preamble((_spine.repo(ctx.id).onboarding if _spine.repo(ctx.id) else None))
                    if is_onboarding else general_preamble()
                )
            # Onboarding skills are ONE-SHOT per repo: they exist to establish project memory, so
            # once it IS established they can only do harm — `retrofit` re-derives the anchor docs
            # from the code and would overwrite the owner's approved ones. Nothing in the skills'
            # own prose can be trusted to hold that line across every phrasing ("go read the
            # codebase and write up what's there" reads exactly like a retrofit request), so the
            # kernel blocks the category once the project is established. Onboarding turns keep
            # them; other modes never load them, so the block is a no-op there.
            block_categories = None if is_onboarding else {"onboarding"}
            # UNBOUND (general/diagnosis) chat still spends tokens — record a lightweight run so it is
            # fully accounted (Interactive category), never silent. No item-status flip / no run-lock;
            # just telemetry + the authoritative per-type usage written at finish. session_id is
            # attached at finish so per-session grouping works.
            # S8 checkpoint-first safety net: if the CLI ever compacts this bound session on its
            # own (mid-turn auto-compaction), the PreCompact hook banks the derived checkpoint
            # BEFORE the compaction event — the D11 run order holds no matter who compacts.
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
            chat_feature = ("onboarding" if is_onboarding
                            else "diagnosis" if session_kind == "diagnosis" else "chat")
            chat_run_id = None if began_run else _spine.start_run(
                ctx.id, mode=ctx.mode, feature=chat_feature)
            # The run this turn is accounted to — the per-run event trail (prompt · reply · calls)
            # keys on this id so each Activity row has its own thread (Activity trace popup).
            active_run_id = item_run_id if began_run else chat_run_id
            if active_run_id:
                capture_prompt(ctx.id, prompt, run_id=active_run_id, item_id=work_item_id)
            # Dev-mode turns get the dev MCP server: the `read_*` reads (event log · inbox · learning
            # pool) + the inbox itemize writes. The learning WRITE pens stay learning-run-only. Capture is
            # fully automatic — there is no chat-side capture tool; the idle + phase-advance sweeps own it.
            # Folder-as-scope: absent in core mode. PRD §4.9.
            turn_mcp = (
                {"dev": make_dev_mcp_server(
                    _dev_store, ctx.id, spine=_spine,
                    # S2 artifact tools: the item folders + the repo tree for git-state / evidence
                    # freshness fingerprints. With a live worktree, ctx.cwd IS the worktree (the
                    # S4 swap above) — evidence fingerprints the tree actually being validated.
                    # `main_repo_dir` stays the real repo for sync_from_main's trunk resolution.
                    dev_root=(ctx.internal_root / "dev") if ctx.internal_root else None,
                    repo_dir=ctx.cwd,
                    main_repo_dir=main_repo_dir,
                    # Worker tool-scoping (S5/D9): the item write-tools operate ONLY this
                    # session's bound item; a general session gets refusals, not silence.
                    bound_item_id=work_item_id,
                )}
                if ctx.mode == "dev" else None
            )
            final_tokens = None
            final_usage = None
            final_session = None
            final_model = None
            final_ctx = None   # the turn's end-of-turn context-window fill (persisted so the chat
                               # header can show a session's ctx% on reopen, not just live)
            live = _LiveTokens()   # dedupes the Usage stream by message_id for an accurate live estimate
            try:
                async for ev in _agent.run_turn(
                    ctx,
                    agent_prompt,
                    resume=turn_resume,
                    model=model,
                    effort=effort or _spine.DEFAULT_EFFORT,   # final "medium" floor
                    approve=turn_approve,
                    extra_mcp_servers=turn_mcp,
                    enforce_silent=True,   # user-facing chat: hide+block internal `access: silent` skills
                    scope_reads=True,      # L2 read-guard: keep reads inside the host's scope
                    system_append=session_append,       # Focus (work-item) / Guard (general) block
                    gate_general_mutations=gate_general, # hard-gate mutations in a general dev session
                    general_write_root=general_write_root,  # …except writing this project's general/ memory
                    write_boundary=write_boundary,  # S4 freeze: build writes stay in worktree+item dir
                    hooks=turn_hooks,               # S8: PreCompact checkpoint-first safety net
                    block_categories=block_categories,  # onboarding skills die once memory exists
                ):
                    if isinstance(ev, Usage) and began_run:
                        live.bump(ctx.id, work_item_id, ev)
                    elif isinstance(ev, (Status, ToolResult)) and began_run:
                        _log_artifact(ctx.id, work_item_id, ev)
                    # Claim the session id so it shows up in this context's picker
                    # (and stays distinct from the owner's own Claude Code sessions).
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
                                    ctx.internal_root / "dev", work_item_id, ev.session_id
                                )
                                # Reverse stamp: the session now durably KNOWS its work-item
                                # (work-item-session-recognition-prd). Write-once/immutable — this is
                                # a work-item session's birth. From here on the daemon reads this stamp
                                # (not the client payload) to center + sandbox + lock + telemetry.
                                _spine.stamp_session_item(ev.session_id, work_item_id)
                                # A worktree-entry rotation replaced the pre-build thread — retire
                                # it now that the new session is recorded (trace preserved+labeled;
                                # same pattern as the headless-plan replacement).
                                if rotated_from and rotated_from != ev.session_id:
                                    _sessions.delete(ctx, rotated_from, cause="retired")
                            except Exception:
                                log.exception("failed to persist session to work-item %s", work_item_id)
                        elif ev.session_id and ctx.mode == "dev":
                            # Non-work-item dev session: stamp its KIND at birth (write-once) so a
                            # resume reads the stored kind — critical for diagnosis, which would
                            # otherwise derive back to 'general' and lose its subject pointer +
                            # read-only identity (session-kinds-diagnose).
                            try:
                                _spine.stamp_session_kind(ev.session_id, session_kind, subject_run_id)
                            except Exception:
                                log.exception("failed to stamp session kind %s", session_kind)
                    elif isinstance(ev, Init):
                        _cache_slash(ctx.id, ev.slash_commands)
                    # Per-run observability trail (any run, bound or unbound): the assistant's reply
                    # text + each tool/skill/agent call + that call's (capped) output, keyed to this
                    # run for the Activity trace and the diagnosis agent.
                    if active_run_id and isinstance(ev, (Status, TextDelta, ToolResult)):
                        capture_event(ctx.id, ev, run_id=active_run_id, item_id=work_item_id)
                    # ToolResult is trail-only (persisted above); it has no live frame and
                    # event_to_frame would reject it, so never send it down the socket.
                    if not isinstance(ev, ToolResult):
                        await ws.send_json(event_to_frame(ev))
                # Turn done — an interactive (bound-chat) turn rests the item at `active`
                # (the conversation continues; gates set awaiting_human, not chat turns).
                if began_run:
                    _end_run(ctx, ctx.id, work_item_id, final_tokens, "active", final_usage, final_ctx)
                    # S8 compaction trigger: a REAL bound turn just finished with fresh usage —
                    # clear the defer latch / attempt budget, then evaluate the fill against the
                    # (floor-aware) trigger. Fire-and-forget: the compact run takes the item's
                    # run-lock itself and never blocks this reply.
                    if final_session and final_ctx is not None:
                        try:
                            compaction.note_turn_start(final_session)
                            compaction.maybe_compact(
                                ctx, ctx.id, work_item_id, final_session,
                                ctx_pct=final_ctx, kind=(item or {}).get("kind"), model=model)
                        except Exception:
                            log.exception("compaction trigger evaluation failed")
                elif chat_run_id:
                    _spine.finish_run(chat_run_id, usage=final_usage, session_id=final_session,
                                      model=final_model, ctx_pct=final_ctx)
                # No chat-side capture: the conversation is swept automatically (idle-timeout +
                # phase-advance/completion), so nothing fires here per-turn.
            except Exception as e:
                if began_run:
                    _end_run(ctx, ctx.id, work_item_id, final_tokens, "active", final_usage, final_ctx)
                elif chat_run_id:
                    _spine.finish_run(chat_run_id, status="aborted", usage=final_usage,
                                      session_id=final_session, model=final_model, ctx_pct=final_ctx)
                log.exception("turn failed")
                try:
                    await ws.send_json(error_frame(str(e)))
                except Exception:
                    break  # socket is gone
    finally:
        reader_task.cancel()
        # Bank the session-end fallback checkpoint (no-op if the agent banked its own since
        # `conn_started`, or the item is terminal). Best-effort — never blocks the close.
        if last_bound:
            try:
                bank_auto_checkpoint(last_bound[0], last_bound[1], since=conn_started)
            except Exception:
                log.exception("session-end auto-checkpoint failed")
