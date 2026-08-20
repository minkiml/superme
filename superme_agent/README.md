# SuperMe

A hosted, self-developing Claude agent — a growing **digital twin of its owner** — driven through a
web cockpit. Its brain is the **Claude Agent SDK**; everything around it (the context model, the
knowledge store, the learning loop, the dashboards) is SuperMe.

The same agent runs in two **modes** over any registered repo: **core** (the twin — represents the
owner and their domains) and **dev** (builds — operates on a repo's own development). The **hub** is
SuperMe working on *itself* (the `global` context, shown as "SuperMe Hub").

## The stack

Four layers, each ignorant of the one above it:

```
Cockpit    web/frontend/     React + Vite SPA            :5173   the UI
BFF        web/bff/          generic reverse proxy       :8000   browser → daemon
Daemon     superme_agent/daemon/   FastAPI over Core     :8787   typed routers + schemas + WS chat
Core       superme_agent/core/     host-agnostic engine  —       the agent, no HTTP/transport
```

**Core** is the whole agent with no transport in it: `agent_service` (turn loop + system-prompt
assembly + ctx% accounting), `context`, `sessions`, `operational` (the harness/constitution model),
`spine` + `dev_store` + `knowledge_service` (persistence), `commands`, `permissions`. The **daemon**
wraps Core in a typed HTTP + WebSocket surface (route parity is gated — see below). The **BFF** is a
thin generic proxy. The **cockpit** is a codegen-typed SPA (`web/frontend`, types generated from the
daemon's OpenAPI).

## Hosts, modes, contexts

A **host** is a `(repo, mode)` pair, resolved to a `Context` by `gateway/contexts.resolve()`. Repos are
registered in `config/repos.yaml`; each gets a knowledge home at `superme-knowledge/<id>-knowledge/`.
The `global` context is the hub (this repo, cwd = repo root).

## The context stack (what one host sees)

Five channels compose a host's context. Only the **always-dumped** layer is loaded every turn; the
rest is catalog-then-pull.

1. **Universal harness** — `harness/`: the portable brain, identical for every host.
   - `SELF.md` (persona) + the per-mode charter (`core-charter.md` / `dev-charter.md`) are the
     **always-dumped** layer, plus an optional `charter.local.md` from the host's local harness.
   - `constitution/` — operational intelligence, **frontmatter-first**: the catalog (name +
     description) is always on; a body is pulled on demand (`pull_constitution`).
   - `plugins/` (skills + subagents, likewise frontmatter-first), `tools/`, `policy.py`, `forge_kit/`.
2. **Local harness** — `local-harness/<id>/<mode>/`: this host's own operational content.
3. **Working knowledge** — `superme-knowledge/<id>-knowledge/` (pulled on demand; see its README).
4. **DB-backed knowledge** — the inbox + activity log, read through context-scoped tools.
5. **Working root** — the host's cwd.

Out-of-scope reads (a repo host reaching into SuperMe's code or another host's knowledge) are denied by
a per-Context read guard.

## Layout

```
superme_agent/
├─ core/            host-agnostic agent engine (no transport)
├─ daemon/          FastAPI service (:8787) — routers/ schemas/ services/
├─ gateway/         contexts.py — (repo, mode) → Context resolution
├─ harness/         the UNIVERSAL portable brain (SELF, charters, constitution, plugins, tools, policy, forge_kit)
├─ local-harness/   per-host local harness — <id>/<mode>/
├─ config/          repos.yaml (registered repos) · system.yaml
└─ validators/
scripts/            check_fast.sh (the gate) · parity · sweep/E2E tests (gitignored)
web/                bff/ (reverse proxy) · frontend/ (cockpit) · dev.sh (launch all three)
superme-knowledge/  the knowledge repo (sibling, gitignored) — see its README
```

> **Legacy:** `runtime/` and `__main__.py` are the retired Slack-era app (`python -m superme_agent`),
> superseded by the daemon and unreferenced by it. Pending removal.

## Run

```bash
conda activate my-agent
bash web/dev.sh          # daemon (:8787) + BFF (:8000) + Vite (:5173); Ctrl-C stops all three
```

Then open http://localhost:5173. To run a layer alone: `python -m superme_agent.daemon` /
`python -m web.bff` / `npm --prefix web/frontend run dev`.

**Auth (OAuth, Claude Max):** `claude setup-token` → put the `sk-ant-oat01-…` token in the repo-root
`.env` as `CLAUDE_CODE_OAUTH_TOKEN`. Keep `ANTHROPIC_API_KEY` unset (it silently takes precedence).

## The gate

```bash
STRICT=1 bash scripts/check_fast.sh      # route-parity + OpenAPI shape drift + frontend tsc
```

Run it between edits. Re-baseline deliberately (`python -m scripts.parity snapshot`) only when a route
change is intended.
