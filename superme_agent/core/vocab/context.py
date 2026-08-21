"""Context — the surface-agnostic binding for one agent run: who runs, and where.
"""

from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Context:
    layer: str
    id: str
    cwd: Path
    # ORTHOGONAL to layer, and not a knowledge sandbox: it picks the charter and the plugins.
    mode: str = "core"
    knowledge_root: Path | None = None
    # Dev-knowledge. Never browsable in the knowledge dashboards.
    internal_root: Path | None = None
    persona_append: str = ""
    extra_mcp: list = field(default_factory=list)
    label: str = ""
    setting_sources: list[str] = field(default_factory=lambda: ["user", "project", "local"])

    def __post_init__(self):
        if not self.label:
            self.label = self.id
