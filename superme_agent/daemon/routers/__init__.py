"""Resource-oriented API routers (Backend Refactor R2+).

One cohesive resource per module: health, system, sessions, knowledge, docs, ws — plus the `dev/`
subpackage for the 31 dev routes (meta, work_items, inbox, learning, harness). Each router is wired
with `app_state` singletons via `Depends` and included by `daemon/app.py`. Empty until R2 fills it.
"""
