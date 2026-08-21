"""FastAPI app for the SuperMe Core daemon.

Thin assembly: build the `app` with its lifespan and mount the routers. Handlers live under
`routers/`, orchestration in `services/`, the shared singletons in `app_state`.
"""

import logging

from fastapi import FastAPI

from .lifespan import lifespan

log = logging.getLogger("superme-agent")

# Swagger is off: the daemon is internal, and `/docs` belongs to the SuperMe Docs API.
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
    git as _r_dev_git, gates as _r_dev_gates, sweeps as _r_dev_sweeps,
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
app.include_router(_r_dev_sweeps.router)
app.include_router(_r_dev_learning.router)
app.include_router(_r_dev_general.router)
app.include_router(_r_ws.router)
