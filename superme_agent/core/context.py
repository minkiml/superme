"""Context — the surface-agnostic binding for one agent run.

A Context says *who/where* a turn runs: the global root ("Me") or a specific
local/project (sub-SuperMe). It generalizes the Slack-era "workspace" concept so the
same Core code path serves every surface and both harness layers.

  layer          "global" (the root SuperMe) | "local" (a project sub-SuperMe)
  mode           "core" (the twin — lives the owner's work) | "dev" (builds SuperMe
                 itself, anchored to dev-knowledge). ORTHOGONAL to layer: every
                 (layer, mode) pair is valid (global-core, global-dev, …). Supplied by
                 the surface, not the registry. Selects the system-prompt charter and
                 which harness plugins load; it is NOT a knowledge sandbox.
  id             stable identifier ("global", or a project/workspace name)
  cwd            working directory the agent runs in
  knowledge_root the dashboard-consumed knowledge (the `core/` sub-root of this
                 context's knowledge home) — Me/Domains/Manage-Knowledge read this
  internal_root  internal knowledge (the `internal/` sub-root) — dev-knowledge etc.,
                 not browsable in the knowledge dashboards (consumed by Development)
  persona_append extra persona text layered on top of the global persona
  extra_mcp      names of surface/workspace-specific MCP servers to attach
  label          human-facing display name
  setting_sources which Claude Code setting layers to load — all three, so SuperMe keeps
                 the owner's full NATIVE environment (commands, skills, MCP, memory):
                   "user"    → the owner's global ~/.claude (native commands/skills, MCP, memory)
                   "project" → the hosting dir's project-level cwd/.claude (resolves vs cwd)
                   "local"   → cwd/.claude/*.local overrides
                 SuperMe's OWN skills are mode-scoped separately by the per-mode harness
                 plugins (see config.plugins_for) — dev vs core load different plugins — so the
                 slash palette = SuperMe's mode-scoped skills + the native environment above.
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
