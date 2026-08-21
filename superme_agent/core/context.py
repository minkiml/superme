"""Context — the surface-agnostic binding for one agent run: who runs, and where.

  layer           "global" (the root SuperMe) | "local" (a project sub-SuperMe)
  mode            "core" (the twin) | "dev" (builds SuperMe itself). ORTHOGONAL to layer, and
                  not a knowledge sandbox — it selects the charter and which plugins load.
  cwd             working directory the agent runs in
  knowledge_root  dashboard-browsable knowledge
  internal_root   dev-knowledge; never browsable in the knowledge dashboards
  setting_sources which Claude Code setting layers load, so the owner keeps their native
                  environment (commands, skills, MCP, memory)
"""

from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Context:
    layer: str
    id: str
    cwd: Path
    mode: str = "core"
    knowledge_root: Path | None = None
    internal_root: Path | None = None
    persona_append: str = ""
    extra_mcp: list = field(default_factory=list)
    label: str = ""
    setting_sources: list[str] = field(default_factory=lambda: ["user", "project", "local"])

    def __post_init__(self):
        if not self.label:
            self.label = self.id
