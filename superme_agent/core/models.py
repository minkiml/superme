"""Canonical model catalog — the ONE backend source of truth for which CONCRETE model each tier runs.

Why this exists: the CLI's bare tier aliases LAG behind the newest release. On the installed CLI,
`sonnet` resolves to `claude-sonnet-4-6` (NOT Sonnet 5) and `opus` resolves to `claude-opus-4-7`
(NOT Opus 4.8) — so selecting an alias silently runs an OLDER model than its label implies. We
therefore pin each tier to a concrete id that actually resolves to the intended newest version, and
normalize every model value (alias OR concrete) through here before it reaches the SDK.

Bump these ids when a newer model per tier ships (and mirror the FE labels in web .../lib/format.ts).
"""

# Tier alias → the concrete model id that runs the intended newest version.
MODEL_TIERS: dict[str, str] = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}

# The concrete ids the pickers offer / the system stores.
CANONICAL_MODELS: tuple[str, ...] = tuple(MODEL_TIERS.values())

# The built-in SYSTEM default model — the concrete floor SuperMe runs when nothing more specific is
# set (no per-turn pick, no repo override, no configured system default). Always concrete + known,
# so a turn never falls through to the opaque CLI default.
DEFAULT_MODEL: str = MODEL_TIERS["sonnet"]

# Per-agent model PRESET (by run feature) for the autonomous background agents that don't take a
# user pick. A tier alias here → the latest concrete via agent_model(), so these agents always run
# the newest version of their tier and NEVER fall through to the opaque CLI default. Cost-aware:
# sweep is high-volume extraction (cheap Haiku); distill/write author memory + skills (Sonnet).
# Editable here (a code-level config today; a per-agent UI knob is a later hook).
AGENT_MODELS: dict[str, str] = {
    "sweep": "haiku",
    "distill": "sonnet",
    "write": "sonnet",
    "plan": "sonnet",
}
_AGENT_MODEL_FLOOR = "sonnet"

# Reset tokens that clear an override (→ None).
_RESET = ("reset", "default", "clear")


def normalize_model(m: str | None) -> str | None:
    """Map any accepted model value to the canonical CONCRETE id the SDK should run:
      • a tier alias (`sonnet`) → its pinned concrete id (`claude-sonnet-5`)
      • an already-concrete id (`claude-sonnet-5`) → itself
      • a reset token / empty → None (use the default)
    Unknown strings pass through untouched (the SDK does the final validation)."""
    if not m:
        return None
    m = m.strip().lower()
    if m in _RESET:
        return None
    return MODEL_TIERS.get(m, m)


def agent_model(feature: str) -> str:
    """The concrete, LATEST model an autonomous agent (by run feature) should run on — its preset
    tier resolved to the newest concrete id. Never None, so these agents don't fall to the CLI
    default. Unknown features get the floor tier."""
    return normalize_model(AGENT_MODELS.get(feature, _AGENT_MODEL_FLOOR))


def is_valid_model(m: str | None) -> bool:
    """True for a tier alias, a canonical concrete id, or a reset token — the values a picker/command
    may legitimately send. (Unknown concrete ids are rejected by the API but still run if forced.)"""
    if not m:
        return False
    m = m.strip().lower()
    return m in MODEL_TIERS or m in CANONICAL_MODELS or m in _RESET
