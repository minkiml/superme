"""Daemon API schemas (Backend Refactor R3+).

Pydantic v2 request/response models, one module per resource (mirroring `routers/`), plus `common.py`
for shared bits. Requests keep the `*Body` suffix; responses are `*Response`. Every route declares
`response_model=` so the daemon's OpenAPI is the single source of truth (the FE generates its transport
types from it). `core/` stays dataclass-pure; routers map dataclass → `*Response` at the edge via
`from_attributes`. Empty until R3.
"""
