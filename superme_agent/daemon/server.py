"""FastAPI app for the SuperMe Core daemon.

Thin assembly module: builds the `app` with its lifespan and mounts the resource routers. Every
route handler lives under `routers/` (the HTTP surfaces + the agent WebSocket in `routers/ws.py`);
orchestration lives in `services/`; the shared singletons in `app_state` (injected via `Depends`).
`spine.reconcile()` + the idle-sweep heartbeat run in the `lifespan` (lifespan.py), not at import.
`uvicorn` imports `server:app`.
"""

import logging

from fastapi import FastAPI

from .lifespan import lifespan

log = logging.getLogger("superme-agent")

# docs_url/redoc_url disabled: the daemon is internal (the BFF is its only client) and we want the
# `/docs` path for the SuperMe Docs API, not FastAPI's Swagger UI.
app = FastAPI(title="SuperMe Core daemon", docs_url=None, redoc_url=None, lifespan=lifespan)


# --- router assembly: each surface is an APIRouter mounted behind its real path ------------------
from .routers import (  # noqa: E402
    health as _r_health, docs as _r_docs, knowledge as _r_knowledge,
    sessions as _r_sessions, system as _r_system, ws as _r_ws, fs as _r_fs,
    inventory as _r_inventory,
)
from .routers.dev import (  # noqa: E402
    inbox as _r_dev_inbox, meta as _r_dev_meta, harness as _r_dev_harness,
    work_items as _r_dev_work_items, learning as _r_dev_learning, general as _r_dev_general,
    git as _r_dev_git, gates as _r_dev_gates,
)

app.include_router(_r_health.router)
app.include_router(_r_docs.router)
app.include_router(_r_knowledge.router)
app.include_router(_r_sessions.router)
app.include_router(_r_system.router)
app.include_router(_r_inventory.router)  # TEMPORARY internals inventory (Internals tab) — deletable
app.include_router(_r_fs.router)
app.include_router(_r_dev_inbox.router)
app.include_router(_r_dev_meta.router)
app.include_router(_r_dev_harness.router)
app.include_router(_r_dev_work_items.router)
app.include_router(_r_dev_git.router)
app.include_router(_r_dev_gates.router)
app.include_router(_r_dev_learning.router)
app.include_router(_r_dev_general.router)
app.include_router(_r_ws.router)
