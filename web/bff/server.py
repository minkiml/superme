"""FastAPI BFF: same-origin `/api` for the frontend, forwarding to the Core daemon.

A pure boundary with no brain of its own. Two generic forwarders, HTTP and WebSocket, cover
the whole surface — a new daemon route needs no BFF work.
"""

import os
import asyncio
import logging

from . import _env  # noqa: F401  (loads the repo-root .env before reading os.environ)

import httpx
import websockets
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect

log = logging.getLogger("superme-web-bff")
logging.basicConfig(level=logging.INFO)

DAEMON_HTTP = os.environ.get("SUPERME_DAEMON_URL", "http://127.0.0.1:8787")
DAEMON_WS_BASE = DAEMON_HTTP.replace("http", "ws", 1)

# Hop-by-hop headers: forwarding them passes stale framing through instead of recomputing it.
_DROP_REQUEST_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
_DROP_RESPONSE_HEADERS = {
    "content-length", "content-encoding", "transfer-encoding", "connection",
}

app = FastAPI(title="SuperMe web BFF")


@app.get("/api/health")
async def health() -> dict:
    """The BFF's own liveness, independent of the daemon, so the boundary reports up
    even when the daemon is down."""
    return {"status": "ok", "service": "superme-web-bff", "daemon": DAEMON_HTTP}


# Name-agnostic, so a send-only daemon socket relays through the same code as a duplex one.
@app.websocket("/api/ws/{name}")
async def ws_relay(name: str, browser: WebSocket) -> None:
    await browser.accept()
    try:
        async with websockets.connect(f"{DAEMON_WS_BASE}/ws/{name}") as daemon:

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


@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(path: str, request: Request) -> Response:
    """Forward any `/api/*` request to the daemon verbatim: method, query, body, status."""
    url = f"{DAEMON_HTTP}/{path}"
    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _DROP_REQUEST_HEADERS
    }
    try:
        async with httpx.AsyncClient() as c:
            r = await c.request(
                request.method,
                url,
                params=request.query_params.multi_items(),
                content=body,
                headers=headers,
                timeout=httpx.Timeout(300.0, connect=10.0),
            )
    except httpx.RequestError as e:
        log.warning("proxy error %s %s: %s", request.method, url, e)
        return Response(
            content=b'{"detail":"daemon unreachable"}',
            status_code=502,
            media_type="application/json",
        )
    resp_headers = {
        k: v for k, v in r.headers.items()
        if k.lower() not in _DROP_RESPONSE_HEADERS
    }
    return Response(content=r.content, status_code=r.status_code, headers=resp_headers)
