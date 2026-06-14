"""Context — the surface-agnostic binding for one agent run.

A Context says *who/where* a turn runs: the global root ("Me") or a specific
local/project (sub-SuperMe). It generalizes the Slack-era "workspace" concept so the
same Core code path serves every surface and both harness layers.

  layer          "global" (the root SuperMe) | "local" (a project sub-SuperMe)
  id             stable identifier ("global", or a project/workspace name)
  cwd            working directory the agent runs in
  knowledge_root the file-based knowledge home for this layer (None until Stage C
                 wires superme-global-knowledge/ and <project>/superme/)
  persona_append extra persona text layered on top of the global persona
  extra_mcp      names of surface/workspace-specific MCP servers to attach
  label          human-facing display name
"""

from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Context:
    layer: str
    id: str
    cwd: Path
    knowledge_root: Path | None = None
    persona_append: str = ""
    extra_mcp: list = field(default_factory=list)
    label: str = ""

    def __post_init__(self):
        if not self.label:
            self.label = self.id
