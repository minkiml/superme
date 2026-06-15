"""FastAPI app for the SuperMe Core daemon.

Exposes a health check and the bidirectional agent WebSocket. The socket runs turns
sequentially per connection; a concurrent reader task lets approval_response frames
arrive *while* a turn is paused awaiting an approval (otherwise the turn loop would
block the receive path and deadlock the approval round-trip).
"""

import json
import asyncio
import uuid
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from ..core import AgentService, KnowledgeService, SessionStore, CommandLayer, Init, Result
from ..gateway import contexts
from ..runtime.config import DAEMON_APPROVAL_TIMEOUT, SLASH_COMMANDS_FILE
from .protocol import event_to_frame

log = logging.getLogger("superme-agent")

app = FastAPI(title="SuperMe Core daemon")

# One brain + knowledge + sessions + shared command layer, shared across connections.
_agent = AgentService()
_knowledge = KnowledgeService()
_sessions = SessionStore()
_commands = CommandLayer()


def _load_slash_cache() -> dict:
    try:
        return json.loads(SLASH_COMMANDS_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _cache_slash(context_id: str, slash_commands: list) -> None:
    """Remember a context's slash-command list so the "/" palette is ready on connect."""
    if not slash_commands:
        return
    cache = _load_slash_cache()
    if cache.get(context_id) != slash_commands:
        cache[context_id] = slash_commands
        try:
            SLASH_COMMANDS_FILE.write_text(json.dumps(cache, indent=2))
        except OSError as e:
            log.warning("could not persist slash cache: %s", e)


def _knowledge_root(context_id: str):
    """Resolve a context to its knowledge root, or 400 if it has none."""
    ctx = contexts.resolve(context_id)
    if not ctx.knowledge_root:
        raise HTTPException(status_code=400, detail="context has no knowledge root")
    return ctx.knowledge_root


class WriteBody(BaseModel):
    path: str
    content: str
    context_id: str = "global"


class InjectBody(BaseModel):
    title: str
    content: str
    folder: str = "knowledge"
    context_id: str = "global"


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "superme-core-daemon"}


# --- sessions: list + replay (history lives in the SDK transcripts) -------------
@app.get("/sessions")
async def sessions_list(context_id: str = "global") -> list[dict]:
    """SuperMe's own past sessions for a context, newest first."""
    return _sessions.list(contexts.resolve(context_id))


@app.get("/sessions/{session_id}")
async def session_read(session_id: str, context_id: str = "global") -> dict:
    """One session's title + replayable bubble history."""
    data = _sessions.read(contexts.resolve(context_id), session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    return data


@app.delete("/sessions/{session_id}")
async def session_delete(session_id: str, context_id: str = "global") -> dict:
    """Forget a session (removes it from the picker; transcript file is kept)."""
    _sessions.forget(contexts.resolve(context_id), session_id)
    return {"ok": True, "id": session_id}


@app.get("/contexts")
async def contexts_list() -> list[dict]:
    """Live contexts (global + connected domains) for the surfaces to render."""
    return contexts.list_all()


@app.get("/knowledge/tree")
async def knowledge_tree(context_id: str = "global") -> dict:
    """The folder/file tree of the context's knowledge layer."""
    return _knowledge.list_tree(_knowledge_root(context_id))


@app.get("/knowledge/file")
async def knowledge_read(path: str, context_id: str = "global") -> dict:
    """One knowledge file's text."""
    try:
        return {"path": path, "content": _knowledge.read(_knowledge_root(context_id), path)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/knowledge/file")
async def knowledge_write(body: WriteBody) -> dict:
    """Create or overwrite a knowledge file (in-place editing)."""
    try:
        _knowledge.write(_knowledge_root(body.context_id), body.path, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "path": body.path}


@app.post("/knowledge/inject")
async def knowledge_inject(body: InjectBody) -> dict:
    """Create a new note from {title, content} and link it in index.md."""
    rel = _knowledge.inject(
        _knowledge_root(body.context_id), body.title, body.content, body.folder
    )
    return {"ok": True, "path": rel}


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    await ws.accept()
    # Seed the client's "/" palette from the last-known list (web is the global context),
    # so it's usable before the first turn reveals the live list.
    cached = _load_slash_cache().get("global")
    if cached:
        await ws.send_json({"type": "init", "slash_commands": cached, "model": None})

    pending: dict[str, asyncio.Future] = {}   # approval_id -> Future[bool]
    inbox: asyncio.Queue = asyncio.Queue()     # queued turn frames (None = disconnect)

    async def reader() -> None:
        """Pump incoming frames: resolve approvals immediately, queue turns."""
        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type")
                if kind == "approval_response":
                    fut = pending.get(msg.get("id"))
                    if fut and not fut.done():
                        fut.set_result(bool(msg.get("approved")))
                elif kind == "turn":
                    await inbox.put(msg)
                else:
                    log.warning("ignoring unknown client frame: %r", kind)
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
        await ws.send_json({
            "type": "approval_request",
            "id": aid,
            "tool_name": tool_name,
            "tool_input": tool_input,
        })
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
            ctx = contexts.resolve(msg.get("context_id"))
            prompt = msg.get("prompt", "")

            # Shared command layer: non-native commands (/model) are handled here and
            # answered directly — no agent turn. Everything else (incl. native /compact,
            # /clear, skills) falls through to the CLI below.
            cmd_reply = _commands.handle(ctx, prompt)
            if cmd_reply is not None:
                await ws.send_json({
                    "type": "result", "text": cmd_reply,
                    "model": None, "context_pct": None,
                    "context_window": None, "session_id": None,
                })
                continue

            # Apply this context's persisted /model choice (the surface may still send an
            # explicit per-turn model, which wins).
            model = msg.get("model") or _commands.model_override(ctx)
            try:
                async for ev in _agent.run_turn(
                    ctx,
                    prompt,
                    resume=msg.get("resume"),
                    model=model,
                    approve=approve,
                    extra_mcp_servers=None,     # web has no surface tools; Slack joins in B2
                ):
                    # Claim the session id so it shows up in this context's picker
                    # (and stays distinct from the owner's own Claude Code sessions).
                    if isinstance(ev, Result):
                        _sessions.record(ctx, ev.session_id)
                    elif isinstance(ev, Init):
                        _cache_slash(ctx.id, ev.slash_commands)
                    await ws.send_json(event_to_frame(ev))
            except Exception as e:
                log.exception("turn failed")
                try:
                    await ws.send_json({"type": "error", "message": str(e)})
                except Exception:
                    break  # socket is gone
    finally:
        reader_task.cancel()
