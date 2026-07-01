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
from ..services.runs import (
    DEFAULT_RUN_MODEL, _begin_run, _end_run, _bump_run_tokens, _log_artifact,
)
from ...core import Init, Usage, Result, Status, scoped_writes_approve
from ...gateway import contexts
from ...harness.tools.dev_tools import make_dev_mcp_server
from ...runtime.config import DAEMON_APPROVAL_TIMEOUT

log = logging.getLogger("superme-agent")

router = APIRouter()


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
    try:
        while True:
            msg = await inbox.get()
            if msg is None:
                break  # client disconnected
            ctx = contexts.resolve(msg.context_id, msg.mode or "core")
            prompt = msg.prompt
            # When a turn is bound to a work-item (dev surface), the item owns its thread:
            # resume from it and persist the session back onto it.
            work_item_id = msg.work_item_id

            # Shared command layer: non-native commands (/model) are handled here and
            # answered directly — no agent turn. Everything else (incl. native /compact,
            # /clear, skills) falls through to the CLI below.
            cmd_reply = _commands.handle(ctx, prompt)
            if cmd_reply is not None:
                await ws.send_json(result_frame(cmd_reply))
                continue

            # Model resolution, most-specific first: an explicit per-turn pick → this repo's
            # persisted /model override → the system-wide default (Configure tab / system.yaml).
            # None at the end = the host/CLI default.
            model = msg.model or _commands.model_override(ctx) or _spine.effective_system_model()

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
            # NOTE: the agent no longer writes `memory/` files during a turn — capture (automatic
            # sweeps) and processing (`distill`) write DB rows, and apply/publish are owner-gated
            # daemon-side writes. So the old `scoped_writes_approve(memory/)` sandbox here is gone
            # (PRD §4.10). Only the work-item folder is auto-write below.
            if work_item_id and ctx.internal_root:
                item_dir = ctx.internal_root / "dev" / "work-items" / work_item_id
                item = _dev.read_work_item(ctx.internal_root / "dev", work_item_id) or {}
                # Auto-allow item-folder writes (autonomous planning within its own dir);
                # anything else still defers to the surface approval.
                turn_approve = scoped_writes_approve(item_dir, turn_approve)
                # The item's configured model (frontmatter) drives its bound-chat turns too,
                # unless the surface sent an explicit per-turn override.
                model = model or item.get("model") or DEFAULT_RUN_MODEL
                _begin_run(ctx, ctx.id, work_item_id, "chat", model)
                began_run = True
            # Dev-mode turns get the dev MCP server: `dev_log` (read the activity log on demand) +
            # the learning-pipeline tools (review_candidates / propose_memory). Capture is fully
            # automatic — there is no chat-side capture tool; the idle + phase-advance sweeps own it.
            # Folder-as-scope: absent in core mode. PRD §4.9.
            turn_mcp = (
                {"dev": make_dev_mcp_server(_dev_store, ctx.id)}
                if ctx.mode == "dev" else None
            )
            final_tokens = None
            try:
                async for ev in _agent.run_turn(
                    ctx,
                    prompt,
                    resume=msg.resume,
                    model=model,
                    approve=turn_approve,
                    extra_mcp_servers=turn_mcp,
                    enforce_silent=True,   # user-facing chat: hide+block internal `access: silent` skills
                ):
                    if isinstance(ev, Usage) and began_run:
                        _bump_run_tokens(ctx.id, work_item_id, ev.total_tokens, ev.context_pct)
                    elif isinstance(ev, Status) and began_run:
                        _log_artifact(ctx.id, work_item_id, ev)
                    # Claim the session id so it shows up in this context's picker
                    # (and stays distinct from the owner's own Claude Code sessions).
                    elif isinstance(ev, Result):
                        final_tokens = ev.tokens
                        _sessions.record(ctx, ev.session_id)
                        if work_item_id and ev.session_id and ctx.internal_root:
                            try:
                                _dev.set_work_item_session(
                                    ctx.internal_root / "dev", work_item_id, ev.session_id
                                )
                            except Exception:
                                log.exception("failed to persist session to work-item %s", work_item_id)
                    elif isinstance(ev, Init):
                        _cache_slash(ctx.id, ev.slash_commands)
                    await ws.send_json(event_to_frame(ev))
                # Turn done — the agent has stopped, so the item now awaits the owner.
                if began_run:
                    _end_run(ctx, ctx.id, work_item_id, final_tokens, "waiting")
                # No chat-side capture: the conversation is swept automatically (idle-timeout +
                # phase-advance/completion), so nothing fires here per-turn.
            except Exception as e:
                if began_run:
                    _end_run(ctx, ctx.id, work_item_id, final_tokens, "waiting")
                log.exception("turn failed")
                try:
                    await ws.send_json(error_frame(str(e)))
                except Exception:
                    break  # socket is gone
    finally:
        reader_task.cancel()
