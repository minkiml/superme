"""The assembled dev-knowledge service."""

from .fields import FieldOps
from .general import GeneralOps
from .lifecycle import LifecycleOps
from .read import ReadOps


class DevKnowledgeService(ReadOps, LifecycleOps, FieldOps, GeneralOps):
    """Parse + lightly mutate the templated dev-knowledge structure under a dev_root."""
