"""Shared command layer — the few slash commands with no headless SDK path.

Native commands and skills (`/compact`, `/clear`, `/superme-harness:*`, your skills)
pass straight through to the CLI on every surface, so they need no code here. This
layer handles only the non-native ones we still want — today just `/model` — in ONE
place, so web and Slack behave identically once Slack becomes a daemon client (B2).

`handle()` returns a reply string if it owned the command (the surface shows it, no
agent turn runs); it returns None for anything else, which falls through to native
dispatch. Per-context model overrides are persisted and applied to later turns.
"""

import json
import logging

from .context import Context
from ..runtime.config import CONTEXT_MODELS_FILE

log = logging.getLogger("superme-agent")

# Aliases the SDK/CLI accepts (resolved to the latest concrete model per tier).
MODEL_ALIASES = ("haiku", "sonnet", "opus")


class CommandLayer:
    """Surface-neutral dispatch for non-native slash commands (state per context)."""

    def __init__(self, path=CONTEXT_MODELS_FILE):
        self._path = path

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        try:
            self._path.write_text(json.dumps(data, indent=2))
        except OSError as e:
            log.warning("could not persist %s: %s", self._path.name, e)

    def model_override(self, ctx: Context) -> str | None:
        """The model alias chosen for this context via /model, or None for default."""
        return self._load().get(ctx.id)

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
        data = self._load()
        if not arg or arg in ("show", "status", "?"):
            cur = data.get(ctx.id)
            return f"Model: **{cur or 'default'}**. Set with `/model <{opts}>` or `/model reset`."
        if arg in ("reset", "default", "clear"):
            if data.pop(ctx.id, None) is not None:
                self._save(data)
            return "Model reset to the **default**."
        if arg not in MODEL_ALIASES:
            return f"Unknown model `{arg}`. Choose one of: {opts}."
        data[ctx.id] = arg
        self._save(data)
        return f"Model set to **{arg}** for this session."
