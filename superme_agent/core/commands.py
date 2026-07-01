"""Shared command layer — the few slash commands with no headless SDK path.

Native commands and skills (`/compact`, `/clear`, `/superme-harness:*`, your skills)
pass straight through to the CLI on every surface, so they need no code here. This
layer handles only the non-native ones we still want — today just `/model` — in ONE
place, so web and Slack behave identically once Slack becomes a daemon client (B2).

`handle()` returns a reply string if it owned the command (the surface shows it, no
agent turn runs); it returns None for anything else, which falls through to native
dispatch. Per-context model overrides are persisted and applied to later turns.
"""

import logging

from .context import Context
from .spine import SystemSpine, get_spine

log = logging.getLogger("superme-agent")

# Aliases the SDK/CLI accepts (resolved to the latest concrete model per tier).
MODEL_ALIASES = ("haiku", "sonnet", "opus")


class CommandLayer:
    """Surface-neutral dispatch for non-native slash commands. Per-repo model overrides are
    persisted in the system spine (the `model_override` table, keyed by repo id) — WI-3."""

    def __init__(self, spine: SystemSpine | None = None):
        self._spine = spine or get_spine()

    def model_override(self, ctx: Context) -> str | None:
        """The model alias chosen for this context via /model, or None for default."""
        return self._spine.get_model_override(ctx.id)

    def handle(self, ctx: Context, prompt: str) -> str | None:
        """Run a shared command, or return None to let native dispatch handle it."""
        if not prompt.startswith("/"):
            return None
        name, _, arg = prompt[1:].partition(" ")
        if name.lower() != "model":
            return None  # not ours — /compact, /clear, skills, … pass through natively
        return self._model(ctx, arg.strip().lower())

    def _model(self, ctx: Context, arg: str) -> str:
        opts = " | ".join(MODEL_ALIASES)
        if not arg or arg in ("show", "status", "?"):
            cur = self._spine.get_model_override(ctx.id)
            return f"Model: **{cur or 'default'}**. Set with `/model <{opts}>` or `/model reset`."
        if arg in ("reset", "default", "clear"):
            self._spine.set_model_override(ctx.id, None)
            return "Model reset to the **default**."
        if arg not in MODEL_ALIASES:
            return f"Unknown model `{arg}`. Choose one of: {opts}."
        self._spine.set_model_override(ctx.id, arg)
        return f"Model set to **{arg}** for this session."
