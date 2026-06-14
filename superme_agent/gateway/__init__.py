"""The Gateway — normalizes an access point and binds it to a Core Context.

Surfaces don't reach into the Core directly; they name a *context* (e.g. "global",
or later a project id) and the Gateway resolves it to a concrete Context the Core
runs against. This is the one place that knows the map from a surface-facing id to a
harness layer (global root vs. a local/project sub-SuperMe).
"""

from .contexts import resolve, GLOBAL_ID

__all__ = ["resolve", "GLOBAL_ID"]
