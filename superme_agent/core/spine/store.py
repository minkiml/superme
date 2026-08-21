"""The assembled spine, and the one instance the process shares."""

from .base import Base
from .events import EventOps
from .models import ModelOps
from .repos import RepoOps
from .runs import RunOps
from .sessions import SessionOps
from .settings import SettingsOps
from .tokens import TokenOps


class SystemSpine(RepoOps, SessionOps, RunOps, EventOps, TokenOps, ModelOps, SettingsOps, Base):
    """The authoritative spine: loads static config + owns the live `.system.db`."""


_SPINE: SystemSpine | None = None


def get_spine() -> SystemSpine:
    """The per-process spine singleton. Short-lived connections over one `.system.db` make
    it multi-process-safe."""
    global _SPINE
    if _SPINE is None:
        _SPINE = SystemSpine()
    return _SPINE
