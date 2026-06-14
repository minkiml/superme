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
  setting_sources which Claude Code setting layers to load. Default layers all three,
                 so SuperMe — wherever it's hosted — combines:
                   "user"    → the owner's global ~/.claude artifacts (skills/commands;
                               note: also brings the user's MCP servers, hooks, perms)
                   "project" → the hosting dir's project-level cwd/.claude (resolves
                               against `cwd`; none yet, but auto-used when present)
                   "local"   → cwd/.claude/*.local overrides
                 …on top of SuperMe's own carried harness (the plugin, cwd-independent).
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
    setting_sources: list[str] = field(default_factory=lambda: ["user", "project", "local"])

    def __post_init__(self):
        if not self.label:
            self.label = self.id
