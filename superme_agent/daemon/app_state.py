"""Shared daemon singletons — built once, injected into routers.

Instantiated at import (cheap: open DB handles + load YAML config). The one startup SIDE EFFECT,
`spine.reconcile()` (flip runs orphaned by a previous daemon exit), lives in `lifespan.py` — NOT here —
so importing this module for introspection or tests never mutates the run table.

Routers take these via FastAPI `Depends(get_…)`, which returns the singleton (no per-request rebuild)
and gives a clean test seam (`app.dependency_overrides[get_spine] = lambda: fake`).
"""

from ..core import (
    AgentService, KnowledgeService, DevKnowledgeService, DevStore, SessionStore, CommandLayer,
)
from ..core.spine import get_spine as _spine_factory, SystemSpine
from ..paths import DEV_DB_FILE

# --- the singletons (shared across every connection) ---
agent: AgentService = AgentService()
knowledge: KnowledgeService = KnowledgeService()
dev: DevKnowledgeService = DevKnowledgeService()
dev_store: DevStore = DevStore(DEV_DB_FILE)
spine: SystemSpine = _spine_factory()
sessions: SessionStore = SessionStore(spine)
commands: CommandLayer = CommandLayer(spine)


# --- Depends providers (return the singleton; tests override these) ---
def get_knowledge() -> KnowledgeService: return knowledge
def get_dev() -> DevKnowledgeService: return dev
def get_dev_store() -> DevStore: return dev_store
def get_spine() -> SystemSpine: return spine
def get_sessions() -> SessionStore: return sessions
