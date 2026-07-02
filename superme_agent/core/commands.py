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
from .models import MODEL_TIERS, is_valid_model, normalize_model
from .spine import SystemSpine, get_spine

log = logging.getLogger("superme-agent")

# Tier aliases the picker/command accept — each pinned to a concrete newest id (see models.py).
MODEL_ALIASES = tuple(MODEL_TIERS)
# Reasoning-effort levels exposed to the owner (the SDK also accepts xhigh/max).
EFFORT_LEVELS = ("low", "medium", "high")


class CommandLayer:
    """Surface-neutral dispatch for non-native slash commands. Per-repo model overrides are
    persisted in the system spine (the `model_override` table, keyed by repo id) — WI-3."""

    def __init__(self, spine: SystemSpine | None = None):
        self._spine = spine or get_spine()

    def model_override(self, ctx: Context) -> str | None:
        """The model alias chosen for this context via /model, or None for default."""
        return self._spine.get_model_override(ctx.id)

    def effort_override(self, ctx: Context) -> str | None:
        """The reasoning effort chosen for this context via /effort, or None for default."""
        return self._spine.get_effort_override(ctx.id)

    def handle(self, ctx: Context, prompt: str) -> str | None:
        """Run a shared command, or return None to let native dispatch handle it."""
        if not prompt.startswith("/"):
            return None
        name, _, arg = prompt[1:].partition(" ")
        if name.lower() == "model":
            return self._model(ctx, arg.strip().lower())
        return None  # not ours — /compact, /clear, skills, … pass through natively

    def _model(self, ctx: Context, arg: str) -> str:
        """`/model` — set the model and (optionally) the reasoning effort together:
        `/model <opus|sonnet|haiku> [low|medium|high]`. Either token may be `reset`/`default` to
        clear that override. Effort is folded in here so the chat has one picker, not two."""
        mopts, eopts = " | ".join(MODEL_ALIASES), " | ".join(EFFORT_LEVELS)
        parts = arg.split()
        marg = parts[0] if parts else ""
        earg = parts[1] if len(parts) > 1 else ""
        if not marg or marg in ("show", "status", "?"):
            cur, cure = self._spine.get_model_override(ctx.id), self._spine.get_effort_override(ctx.id)
            return (f"Model: **{cur or 'default'}**, effort: **{cure or 'default'}**. "
                    f"Set with `/model <{mopts}> [{eopts}]`.")
        # Model half (required token).
        msgs: list[str] = []
        if marg in ("reset", "default", "clear"):
            self._spine.set_model_override(ctx.id, None); msgs.append("Model **default**")
        elif is_valid_model(marg):
            # Store the CONCRETE id (alias → its pinned newest) so what's picked is what runs.
            concrete = normalize_model(marg)
            self._spine.set_model_override(ctx.id, concrete); msgs.append(f"Model **{concrete}**")
        else:
            return f"Unknown model `{marg}`. Choose one of: {mopts}."
        # Effort half (optional token).
        if earg:
            if earg in ("reset", "default", "clear"):
                self._spine.set_effort_override(ctx.id, None); msgs.append("effort **default**")
            elif earg in EFFORT_LEVELS:
                self._spine.set_effort_override(ctx.id, earg); msgs.append(f"effort **{earg}**")
            else:
                msgs.append(f"(ignored unknown effort `{earg}`)")
        return ", ".join(msgs) + " for this session."
