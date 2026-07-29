"""FastAPI BFF: same-origin `/api` for the frontend, forwarding to the Core daemon.

The BFF is a pure boundary — it holds no brain and no Slack creds; it's a client of
the daemon. Two forwarders cover the whole surface:

- A **generic HTTP reverse proxy**: `/api/{path}` → `{daemon}/{path}`, forwarding method,
  query, body and status verbatim. Adding a daemon route needs zero BFF work.
- A **generic WebSocket relay**: `/api/ws/{name}` → `{daemon}/ws/{name}`, browser <-> BFF <->
  daemon, so the frontend only ever opens `/api/ws/*` and never knows the daemon exists. Adding a
  daemon socket needs zero BFF work either (this was hard-coded to `agent` until the dashboard
  invalidation channel arrived and made the second one real).

The web ingress/boundary role (port separation, a future home for CORS/auth/rate-limiting)
stays here; the 1:1 route mirroring is gone.
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

# Hop-by-hop headers must not be forwarded; let httpx / the response layer recompute
# framing and content negotiation rather than passing stale values through.
_DROP_REQUEST_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
_DROP_RESPONSE_HEADERS = {
    "content-length", "content-encoding", "transfer-encoding", "connection",
}

app = FastAPI(title="SuperMe web BFF")


@app.get("/api/health")
async def health() -> dict:
    """The BFF's own liveness — independent of the daemon, so the boundary reports up
    even when the daemon is down. (The daemon's own health is reachable via the proxy.)"""
    return {"status": "ok", "service": "superme-web-bff", "daemon": DAEMON_HTTP}


# --- generic WebSocket relay: /api/ws/{name} -> {daemon}/ws/{name} ---------------
# Bidirectional and name-agnostic, so a send-only daemon socket (the dashboard's invalidation
# channel) relays through the same code as the fully duplex chat one — the browser-to-daemon leg
# simply never carries anything, and the pair-of-tasks shape still notices the close.
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


# --- generic HTTP reverse proxy: /api/{path} -> {daemon}/{path} ------------------
@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(path: str, request: Request) -> Response:
    """Forward any `/api/*` HTTP request to the daemon verbatim — method, query string,
    body, status and content type pass straight through. New daemon endpoints are served
    here with no BFF change."""
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
