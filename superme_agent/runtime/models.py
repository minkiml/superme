"""Channel → model override.

A tiny, persisted store mapping a channel to a model alias ("haiku" / "sonnet" /
"opus"). Set live from Slack via `@bot /model <name>`. Unlike a workspace (which
locks on first use), a channel's model can be changed at any time. Channels with
no override use the CLI's default model.

State lives in `.channel_models.json` (per-environment, not committed).
"""

import json
import logging

from .config import MODELS_FILE

log = logging.getLogger("superme-agent")

# Aliases the SDK/CLI accepts (resolved to the latest concrete model per tier).
ALLOWED = ("haiku", "sonnet", "opus")


def _load() -> dict:
    try:
        return json.loads(MODELS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    try:
        MODELS_FILE.write_text(json.dumps(data, indent=2))
    except OSError as e:
        log.warning("Could not persist %s: %s", MODELS_FILE.name, e)


# channel -> model alias
_STATE: dict = _load()


def allowed() -> tuple[str, ...]:
    return ALLOWED


def get(channel_id: str) -> str | None:
    """The channel's model override, or None to use the CLI default."""
    return _STATE.get(channel_id)


def set_model(channel_id: str, name: str) -> tuple[bool, str]:
    """Set this channel's model (changeable any time). Returns (ok, message)."""
    name = name.strip().lower()
    if name not in ALLOWED:
        opts = ", ".join(f"`{m}`" for m in ALLOWED)
        return False, f"Unknown model `{name}`. Choose one of: {opts}."
    _STATE[channel_id] = name
    _save(_STATE)
    return True, f"🧠 This channel now uses *{name}*."


def clear(channel_id: str) -> str:
    """Drop this channel's override → back to the default model."""
    existed = _STATE.pop(channel_id, None) is not None
    if existed:
        _save(_STATE)
    return "🧠 This channel is back to the *default* model."
