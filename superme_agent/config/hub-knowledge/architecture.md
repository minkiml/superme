# SuperMe — architecture

## Orient
SuperMe is a local dashboard that runs Claude Code agents against the owner's own repos, one process tree per machine. Its shape is a chain: a **daemon** (`superme_agent/daemon/`) holds all runtime state and drives agent turns through a **core** domain layer (`superme_agent/core/`), which in turn composes every turn's prompt and tools from a **harness** (`superme_agent/harness/`); a thin **web BFF** (`web/bff/`) proxies a **frontend** (`web/frontend/`) to the daemon; durable knowledge lives outside all of it, in per-repo markdown under `superme-knowledge/`.

**Read before you touch**
- The daemon is the only process that touches state directly — the BFF has no logic and the frontend never calls the daemon itself.
- `core` is framework-agnostic and does not know FastAPI exists; if you're adding daemon-only concerns (HTTP, WS), they belong in `daemon/`, not `core/`.
- Every agent tool call passes through one approval gate (`core/permissions.py`) before it runs — there is no second path that bypasses it.
- Durable knowledge (markdown under `superme-knowledge/`) and operational content (prompts/constitutions/skills under `harness/`) are never the same file, and nothing merges them.
- A work item builds in its own git worktree and only reaches `main` through an owner-approved merge — no phase writes to `main` directly.

## Stack
- **Backend**: Python 3.11+, FastAPI + uvicorn for the daemon (`:8787`) and the web BFF (`:8000`).
- **Agent runtime**: the Claude Agent SDK, driving the Claude Code CLI — Claude-plan/OAuth credential only, no `ANTHROPIC_API_KEY` billing path.
- **Frontend**: React + TypeScript + Vite (`:5173`) — a hand-rolled router and a hand-rolled polling/push data cache in place of a routing or state-management library.
- **Storage**: SQLite for runtime/operational state, no ORM; markdown + YAML for everything durable (knowledge, work-items, config).
- **Process orchestration**: `run_superme.py` boots the daemon, then the BFF, then the frontend, each gated on the previous one answering healthy.

## Context & externals
The daemon is the only process with a network surface a human or the frontend touches; the BFF is a transparent proxy in front of it; the frontend never reaches the daemon directly.

| External | Role | Integration point |
|----------|------|-------------------|
| Claude Agent SDK / Claude Code CLI | drives every agent turn (chat, phase runs, deputy, learning pipeline) | `superme_agent/core/agent_service.py` |
| Anthropic, via the owner's Claude plan | the underlying model calls | `superme_agent/core/auth.py`, `superme_agent/paths.py` (drops `ANTHROPIC_API_KEY` from the process) |
| The owner's connected repos | the hosts SuperMe reads/writes as projects | `superme_agent/config/repos.yaml`, `superme_agent/gateway/contexts.py` |
| Git | per-work-item worktrees, branches, squash-merge to main | `superme_agent/core/git_layer.py` |
| Claude Code's plugin/skill/subagent system | supplies the prompts, MCP tools, skills and subagents a turn runs with | `superme_agent/harness/` |

## Components
| Component | Responsibility | Location | Depends on |
|-----------|----------------|----------|------------|
| core | domain brain: turn execution, run/session/repo state, work-item knowledge, git worktrees, gate mechanics | `superme_agent/core/` | harness |
| daemon | HTTP/WS API; phase-transition, autopilot and background-run orchestration | `superme_agent/daemon/` | core, harness, gateway |
| harness | persona/charter prompts, constitutions, MCP tool surface, Claude Code plugins (skills, agents) | `superme_agent/harness/` | core (mutates state core owns) |
| local-harness overlay | per-repo operational overlay: local constitution, charter append, local plugin | `superme_agent/local-harness/` | harness |
| gateway | resolves a `context_id` + mode into a repo's cwd, knowledge root, persona append | `superme_agent/gateway/` | core (spine) |
| web BFF | transparent HTTP + WebSocket proxy between the browser and the daemon | `web/bff/` | daemon |
| web frontend | the dashboard UI | `web/frontend/src/` | web BFF |
| knowledge store | durable per-repo knowledge, gitignored, never a DB row | `superme-knowledge/` | core (knowledge + dev-knowledge services) |

## Flows
### Interactive agent turn
1. Browser opens `/api/ws/agent` → 2. web BFF relays the socket byte-for-byte → 3. daemon's `ws_agent` resolves the context/session/work-item binding → 4. core's `AgentService` assembles the turn's system prompt from harness (persona, mode charter, constitution catalog, operating-context preamble) and mounts the scoped MCP tools → 5. the Claude Agent SDK runs the turn → 6. events stream back through the same chain to the browser.

### Work-item lifecycle
1. An inbox item is pushed into a work-item → 2. the daemon fires the item's current phase (triage → plan → build ⟷ vet → review → close) as a scoped agent turn inside a per-item git worktree → 3. mechanical gate checks are settled before the owner is asked anything → 4. the owner (or, inside bounds, a deputy judge-agent) approves the gate → 5. on review approval, `git_layer` squash-merges the item's branch to main with a backup ref.

### Live dashboard state
1. Daemon-side state changes are observed and coalesced → 2. topic-only invalidation frames push over `/api/ws/dashboard` → 3. the frontend refetches the affected data over plain HTTP. No value ever travels the push channel — only which topic changed — so push and the fallback poll can never disagree.

## Data
| Store | Purpose | Holds | Owner |
|-------|---------|-------|-------|
| system SQLite store | cross-repo runtime state | sessions, runs, token accounting, repo registry/live status | `core.spine` |
| dev SQLite store | per-repo dev-cockpit operational state | inbox queue, activity events, learning-pipeline candidates/proposals | `core.dev_store` |
| knowledge files (`superme-knowledge/<repo>-knowledge/`) | durable knowledge, never a DB row | `core/` (twin-facing identity/journal/knowledge) and `dev/` (`general/` anchor docs + `work-items/<id>/`) | `core` knowledge services |
| git worktrees (outside the repo tree) | isolated per-item build/vet workspace | branch, worktree path, backup ref | `core.git_layer` |

## Cross-cutting
- **Credential** — SuperMe runs on the owner's Claude plan/OAuth credential only; `ANTHROPIC_API_KEY` is stripped from the process on import so a stray key in the shell can never quietly bill instead (`superme_agent/paths.py`).
- **Tool approval** — every tool call an agent makes passes through one `can_use_tool` gate before it executes (`core/permissions.py`).
- **Tool scope** — which MCP tools a session can even see is fixed per session-kind, not decided by the agent (`harness/tools/dev_tools/scopes.py`).
- **Sandbox** — agent shell commands are held inside their working root at the OS level (macOS Seatbelt), not by prompt instruction alone.
- **Context management** — compaction fires only on a run boundary at an owner-set threshold, always checkpointed first, never mid-task.

## Invariants
- Nothing crosses a gate on its own — mechanical checks may clear before a gate, but only the owner (or a deputy acting within its bounds) advances one.
- A work-item's code changes happen in its own git worktree; nothing reaches `main` until the owner approves the merge.
- Operational content that governs agent behaviour (constitutions, charters, skills) is versioned with the code; knowledge about the world is pulled on demand from the knowledge store — the two never mix.
- SuperMe never bills through a bare API key; the Claude plan/OAuth credential is the only path.

## What's deliberately not here
- **No API-key billing path** — the process actively drops `ANTHROPIC_API_KEY`; the owner's own Claude plan is the only credential SuperMe will use.
- **No fully autonomous gate-crossing** — autopilot and the deputy agent can settle mechanical steps and, within bounds, judge a gate, but the human-in-the-loop principle means the owner stays the one who decides at every gate the system defines.
- **No shared multi-user workspace inside one instance** — SuperMe is built for a single owner; "others can run it" means a separate instance per owner, not several owners sharing one.
- **No OS-level sandbox outside macOS** — the shell-command sandbox uses macOS Seatbelt; other platforms rely on the tool-approval gate alone.

## Constraints & debt
- **Constraint:** SuperMe needs a working Claude Code credential to do anything agent-touching — without one the dashboard still opens, but every action that would start a run is greyed out.
- **Debt:** no test in the suite exercises a subagent fan-out path — the gap that let subagents recently stop returning their reports go unnoticed until it was hit and fixed by hand. Not yet scheduled.
- **Debt:** `superme_agent/local-harness/README.md` describes the local-harness overlay as an unused placeholder; the code (`paths.py`, `core/agent_service.py`) already loads it on every turn. The doc is stale against the code.
