"""FastAPI app for the SuperMe Core daemon.

Exposes a health check and the bidirectional agent WebSocket. The socket runs turns
sequentially per connection; a concurrent reader task lets approval_response frames
arrive *while* a turn is paused awaiting an approval (otherwise the turn loop would
block the receive path and deadlock the approval round-trip).
"""

import asyncio
import uuid
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from ..core import AgentService, KnowledgeService
from ..gateway import contexts
from ..runtime.config import DAEMON_APPROVAL_TIMEOUT
from .protocol import event_to_frame

log = logging.getLogger("superme-agent")

app = FastAPI(title="SuperMe Core daemon")

# One brain + one knowledge service, shared across connections.
_agent = AgentService()
_knowledge = KnowledgeService()


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
            try:
                async for ev in _agent.run_turn(
                    ctx,
                    msg.get("prompt", ""),
                    resume=msg.get("resume"),
                    model=msg.get("model"),
                    approve=approve,
                    extra_mcp_servers=None,     # web has no surface tools; Slack joins in B2
                ):
                    await ws.send_json(event_to_frame(ev))
            except Exception as e:
                log.exception("turn failed")
                try:
                    await ws.send_json({"type": "error", "message": str(e)})
                except Exception:
                    break  # socket is gone
    finally:
        reader_task.cancel()
