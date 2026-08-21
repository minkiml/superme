"""Daemon API schemas — Pydantic models, one module per resource, plus `common.py` for shared bits.

Every route declares `response_model=`, so the OpenAPI is the single source the FE generates its
transport types from. `core/` stays dataclass-pure.
"""
