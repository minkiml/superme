"""Canonical model catalog — the one source of truth for the CONCRETE model each tier runs.

The CLI's aliases LAG the newest release, so an alias silently runs an older model than its
label. Mirror the FE labels in `lib/format.ts`.
"""

# Tier alias → the concrete id that runs the intended newest version.
MODEL_TIERS: dict[str, str] = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}

# The concrete ids the pickers offer / the system stores.
CANONICAL_MODELS: tuple[str, ...] = tuple(MODEL_TIERS.values())

# The floor when nothing more specific is set. Concrete, so a turn never hits the opaque CLI
# default.
DEFAULT_MODEL: str = MODEL_TIERS["sonnet"]

# Presets for background agents that take no user pick. Cost-aware: sweep is high-volume
# extraction.
AGENT_MODELS: dict[str, str] = {
    "sweep": "haiku",
    "distill": "sonnet",
    "write": "sonnet",
    "plan": "sonnet",
}
_AGENT_MODEL_FLOOR = "sonnet"

# Owner-tunable from Quick config. Each sub-agent's `.md` frontmatter is the SOURCE OF TRUTH; the
# config UI two-way-syncs it.
AGENT_MODEL_FEATURES: tuple[str, ...] = ("sweep", "distill", "write")
AGENT_MD_NAME: dict[str, str] = {"sweep": "capture", "distill": "distill", "write": "forge"}
AGENT_MODEL_LABELS: dict[str, str] = {
    "sweep": "Capture",
    "distill": "Distill",
    "write": "Forge",
    "plan": "Plan",
}
AGENT_MODEL_SCOPE = "dev"  # the learning sub-agents are all universal dev-scope

# Reset tokens that clear an override (→ None).
_RESET = ("reset", "default", "clear")


def model_family(m: str | None) -> str | None:
    """The tier FAMILY of any model value — the name that survives version bumps."""
    if not m:
        return None
    m = m.strip().lower()
    if m in MODEL_TIERS:
        return m
    parts = m.split("-")  # claude-<family>-<version…>
    if len(parts) >= 2 and parts[0] == "claude":
        return parts[1]
    return None


def track_to_latest(m: str | None) -> str | None:
    """Resolve a model value to its tier's CURRENT concrete id. Pick a tier once, get the newest forever."""
    fam = model_family(m)
    if fam and fam in MODEL_TIERS:
        return MODEL_TIERS[fam]
    return normalize_model(m)


def normalize_model(m: str | None) -> str | None:
    """Map any accepted value to the canonical CONCRETE id. Unknown strings pass through; the SDK validates."""
    if not m:
        return None
    m = m.strip().lower()
    if m in _RESET:
        return None
    return MODEL_TIERS.get(m, m)


def agent_model(feature: str) -> str:
    """The concrete, LATEST model for a background agent. Never None, so it never hits the CLI default."""
    return normalize_model(AGENT_MODELS.get(feature, _AGENT_MODEL_FLOOR))


def is_valid_model(m: str | None) -> bool:
    """True for a tier alias, a canonical concrete id, or a reset token."""
    if not m:
        return False
    m = m.strip().lower()
    return m in MODEL_TIERS or m in CANONICAL_MODELS or m in _RESET
