"""Shared command layer — the few slash commands with no non-interactive SDK path.

Native commands and skills pass straight through to the CLI, so only the non-native ones live
here. `handle()` returns a reply when it owned the command, else None to fall through.
"""

import logging

from .context import Context
from .models import MODEL_TIERS
from .spine import SystemSpine, get_spine

log = logging.getLogger("superme-agent")

# Tier aliases, each pinned to a concrete newest id (see models.py).
MODEL_ALIASES = tuple(MODEL_TIERS)
# Reasoning-effort levels exposed to the owner (the SDK also accepts xhigh/max).
EFFORT_LEVELS = ("low", "medium", "high")


class CommandLayer:
    """Surface-neutral dispatch for non-native slash commands. Today only `/model`, informational."""

    def __init__(self, spine: SystemSpine | None = None):
        self._spine = spine or get_spine()

    def handle(self, ctx: Context, prompt: str) -> str | None:
        """Run a shared command, or return None to let native dispatch handle it."""
        if not prompt.startswith("/"):
            return None
        name, _, arg = prompt[1:].partition(" ")
        if name.lower() == "model":
            return self._model(ctx)
        return None  # not ours — /compact, /clear and skills pass through natively

    def _model(self, ctx: Context) -> str:
        """`/model` — informational only. Model and effort are a per-session runtime override from
        the composer's picker, never a persisted default. Intercepted so a typed `/model` gets
        this pointer instead of silently writing one."""
        mopts, eopts = " | ".join(MODEL_ALIASES), " | ".join(EFFORT_LEVELS)
        repo_default = self._spine.get_model_override(ctx.id)
        return (f"Set the model for **this chat** with the model picker next to the composer — it "
                f"applies to this session only (never the repo default). This repo's default is "
                f"**{repo_default or 'the system default'}**; change repo/system defaults in Quick "
                f"config. Models: {mopts} · effort: {eopts}.")
