"""The Gateway — normalizes an access point and binds it to a Core Context.

Surfaces never reach into the Core directly: they name a context, and the Gateway maps
that surface-facing id to a harness layer.
"""

from .contexts import resolve, GLOBAL_ID

__all__ = ["resolve", "GLOBAL_ID"]
