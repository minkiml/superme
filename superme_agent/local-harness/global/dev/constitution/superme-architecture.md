---
name: superme-architecture
description: How SuperMe's own system fits together (Core → daemon → BFF → cockpit) and the invariants that keep the FE↔BE contract intact. Pull before changing the API surface, the daemon, the persistence stores, or the frontend contract.
scope: repo_dev
source: system
category: reference
created: 2026-07-05
updated: 2026-07-05
---

The map of SuperMe's own codebase and the rules that keep a change from silently breaking the
contract. (You can read any of these files directly — this is the cross-cutting model that no single
file shows.) Keep this in sync as the architecture evolves — bump `updated` when you do.

## Project structure

```
superme_agent/
├─ core/            host-agnostic agent engine (no transport)
│                     agent_service · context · sessions · operational · commands · permissions
│                     spine · dev_store · knowledge_service · events · models · token_taxonomy
├─ daemon/          FastAPI service (:8787): server · app_state · lifespan · deps · protocol
│                     routers/ (incl. dev/) · schemas/ · services/
├─ gateway/         contexts.py — (repo, mode) → Context resolution
├─ harness/         the UNIVERSAL portable brain
│                     SELF.md · {core,dev}-charter.md · constitution/{core,dev}/
│                     plugins/ (skills + subagents) · tools/ · policy.py · forge_kit/
├─ local-harness/   per-host local harness — <id>/<mode>/ (skills · constitution · charter.local.md)
├─ config/          repos.yaml (registered repos) · system.yaml
├─ scripts/         check_fast.sh (the gate) · parity.py · sweep/E2E tests
├─ validators/
└─ runtime/         LEGACY Slack-era app (unreferenced; kept only as future Slack-integration reference)
web/
├─ bff/             generic reverse proxy (:8000)
└─ frontend/        React + Vite cockpit (:5173); transport types generated from the daemon OpenAPI
superme-knowledge/  the knowledge repo (sibling, gitignored) — <id>-knowledge/{core,dev}/
```

## The four layers

Each layer is ignorant of the one above it:

- **Core** — `superme_agent/core/`. The whole agent with **no transport in it**: turn loop +
  system-prompt assembly (`agent_service`), `context`, `sessions`, `operational` (the
  harness/constitution model), `spine` + `dev_store` + `knowledge_service` (persistence), `commands`,
  `permissions`. Keep Core transport-agnostic — no FastAPI / HTTP / WebSocket concepts leak in here.
- **Daemon** — `superme_agent/daemon/` (FastAPI, :8787). Wraps Core in a **typed** HTTP + WS surface:
  `routers/` + `schemas/` (a `response_model` on every route) + `services/`.
- **BFF** — `web/bff/` (:8000). A **generic reverse proxy** to the daemon. Never add per-route logic
  here — it stays dumb.
- **Cockpit** — `web/frontend/` (React + Vite, :5173). Its transport types are **generated** from the
  daemon's OpenAPI, not hand-written.

## The contract invariant (the one that bites)

The FE↔BE contract is **generated + gated**, not hand-synced. Any change to a route or a response
shape means:

1. restart the daemon (it caches module imports at startup — an edit to `core/` or `daemon/` is
   invisible until restart);
2. re-baseline route parity — `python -m scripts.parity snapshot`;
3. regenerate FE types — `npm --prefix web/frontend run gen:api` (and `gen:ws` for WS frame changes);
4. gate — `STRICT=1 bash scripts/check_fast.sh` (route parity + OpenAPI shape drift + `tsc`) must be
   green.

Run the gate between edits. Re-baseline **deliberately** — an unexpected parity diff means you
changed the surface without meaning to.

## Persistence — two SQLite stores

- **Spine** — `.system.db`, via `core/spine.py`. The system source of truth: runs, sessions,
  settings, the repo registry, per-repo meta/learning flags, agent model/effort config.
- **Dev store** — `.dev.db`, via `core/dev_store.py`. Dev-knowledge that isn't files: work-items’
  DB-side, the inbox queue, and the append-only events log. The dev-knowledge **tree** schema (what
  lives on disk) is its own constitution — pull `dev-knowledge-structure`.

## Hosts

A **host** = a `(repo, mode)` pair, resolved to a `Context` by `gateway/contexts.resolve()`. Repos
are registered in `config/repos.yaml`; the hub is the `global` context (cwd = this repo root). How a
host's *context* is assembled — and the isolation rules — is the `superme-context-model` constitution.
