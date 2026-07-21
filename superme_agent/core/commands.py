"""Shared command layer — the few slash commands with no non-interactive SDK path.

Native commands and skills (`/compact`, `/clear`, `/superme-harness:*`, your skills)
pass straight through to the CLI on every surface, so they need no code here. This
layer handles only the non-native ones we still want — today just `/model` — in ONE
place, so web and Slack behave identically once Slack becomes a daemon client (B2).

`handle()` returns a reply string if it owned the command (the surface shows it, no
agent turn runs); it returns None for anything else, which falls through to native
dispatch. `/model` is now informational only — the model/effort for a chat are a
per-session runtime override set from the composer's picker (sent on the socket frame),
never a persisted default (session-model-precedence).
"""

import logging

from .context import Context
from .models import MODEL_TIERS
from .spine import SystemSpine, get_spine

log = logging.getLogger("superme-agent")

# Tier aliases the picker/command accept — each pinned to a concrete newest id (see models.py).
MODEL_ALIASES = tuple(MODEL_TIERS)
# Reasoning-effort levels exposed to the owner (the SDK also accepts xhigh/max).
EFFORT_LEVELS = ("low", "medium", "high")


class CommandLayer:
    """Surface-neutral dispatch for non-native slash commands. Today only `/model`, which is
    informational (the picker sets a per-session runtime model/effort; repo/system defaults are
    set in Quick config)."""

    def __init__(self, spine: SystemSpine | None = None):
        self._spine = spine or get_spine()

    def handle(self, ctx: Context, prompt: str) -> str | None:
        """Run a shared command, or return None to let native dispatch handle it."""
        if not prompt.startswith("/"):
            return None
        name, _, arg = prompt[1:].partition(" ")
        if name.lower() == "model":
            return self._model(ctx)
        return None  # not ours — /compact, /clear, skills, … pass through natively

    def _model(self, ctx: Context) -> str:
        """`/model` — informational only. The chat model + effort are set from the composer's model
        picker and apply to THIS session as a runtime override (sent per-turn on the socket frame);
        they NEVER change a persisted default. The repo default and system default are set in Quick
        config. Kept as an intercepted command so a typed `/model` gets this pointer instead of
        silently writing a persisted override (session-model-precedence)."""
        mopts, eopts = " | ".join(MODEL_ALIASES), " | ".join(EFFORT_LEVELS)
        repo_default = self._spine.get_model_override(ctx.id)
        return (f"Set the model for **this chat** with the model picker next to the composer — it "
                f"applies to this session only (never the repo default). This repo's default is "
                f"**{repo_default or 'the system default'}**; change repo/system defaults in Quick "
                f"config. Models: {mopts} · effort: {eopts}.")
