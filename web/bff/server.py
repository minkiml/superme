"""FastAPI BFF: same-origin /api for the frontend, forwarding to the Core daemon.

- Knowledge endpoints are plain HTTP passthroughs to the daemon.
- The chat WebSocket is a bidirectional relay: browser <-> BFF <-> daemon, so the
  frontend only ever opens /api/ws/agent and never knows the daemon exists.

The BFF holds no brain and no Slack creds; it's purely a client of the daemon.
"""

import os
import asyncio
import logging

from . import _env  # noqa: F401  (loads the repo-root .env before reading os.environ)

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

log = logging.getLogger("superme-web-bff")
logging.basicConfig(level=logging.INFO)

DAEMON_HTTP = os.environ.get("SUPERME_DAEMON_URL", "http://127.0.0.1:8787")
DAEMON_WS = DAEMON_HTTP.replace("http", "ws", 1) + "/ws/agent"

app = FastAPI(title="SuperMe web BFF")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "superme-web-bff", "daemon": DAEMON_HTTP}


# --- contexts (global + connected domains) --------------------------------------
@app.get("/api/contexts")
async def contexts_list():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{DAEMON_HTTP}/contexts")
    return JSONResponse(r.json(), status_code=r.status_code)


# --- knowledge HTTP passthrough -------------------------------------------------
@app.get("/api/knowledge/tree")
async def tree(context_id: str = "global"):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{DAEMON_HTTP}/knowledge/tree", params={"context_id": context_id})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/api/knowledge/file")
async def read_file(path: str, context_id: str = "global"):
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{DAEMON_HTTP}/knowledge/file",
            params={"path": path, "context_id": context_id},
        )
    return JSONResponse(r.json(), status_code=r.status_code)


@app.put("/api/knowledge/file")
async def write_file(req: Request):
    body = await req.json()
    async with httpx.AsyncClient() as c:
        r = await c.put(f"{DAEMON_HTTP}/knowledge/file", json=body)
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/knowledge/inject")
async def inject(req: Request):
    body = await req.json()
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{DAEMON_HTTP}/knowledge/inject", json=body)
    return JSONResponse(r.json(), status_code=r.status_code)


# --- sessions HTTP passthrough --------------------------------------------------
@app.get("/api/sessions")
async def sessions_list(context_id: str = "global"):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{DAEMON_HTTP}/sessions", params={"context_id": context_id})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/api/sessions/{session_id}")
async def session_read(session_id: str, context_id: str = "global"):
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{DAEMON_HTTP}/sessions/{session_id}", params={"context_id": context_id}
        )
    return JSONResponse(r.json(), status_code=r.status_code)


@app.delete("/api/sessions/{session_id}")
async def session_delete(session_id: str, context_id: str = "global"):
    async with httpx.AsyncClient() as c:
        r = await c.delete(
            f"{DAEMON_HTTP}/sessions/{session_id}", params={"context_id": context_id}
        )
    return JSONResponse(r.json(), status_code=r.status_code)


# --- chat WebSocket relay -------------------------------------------------------
@app.websocket("/api/ws/agent")
async def ws_relay(browser: WebSocket) -> None:
    await browser.accept()
    try:
        async with websockets.connect(DAEMON_WS) as daemon:

            async def browser_to_daemon() -> None:
                try:
                    while True:
                        await daemon.send(await browser.receive_text())
                except WebSocketDisconnect:
                    pass

            async def daemon_to_browser() -> None:
                async for msg in daemon:
                    await browser.send_text(msg)

            t1 = asyncio.create_task(browser_to_daemon())
            t2 = asyncio.create_task(daemon_to_browser())
            _, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except Exception as e:
        log.warning("ws relay error: %s", e)
    finally:
        try:
            await browser.close()
        except Exception:
            pass
