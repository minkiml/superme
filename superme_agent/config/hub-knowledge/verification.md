# Verification library

The checks this repo has already proven, kept so the next item inherits them instead of re-deriving them.

## Standing
None promoted yet. Every implementation plan in this repo starts with an empty standing set until the owner promotes an entry from Available, in the dashboard (Artifacts → Verification).

## Available

### import-loads-clean
- proves: the daemon's app imports cleanly on a fresh process
- traces: nothing accidentally breaks the daemon's ability to start
- mode: command
- scenario: import the daemon's FastAPI app fresh, nothing running
- run: PYTHONPATH=. python -c "from superme_agent.daemon import server; assert server.app"
- expect: exit 0

### routes-match-declared-surface
- proves: the daemon's live route inventory and its OpenAPI shape match what the code on disk declares
- traces: a refactor never silently drops, renames or reshapes a route
- mode: command
- scenario: run the parity check against the code on disk (no running daemon)
- run: PYTHONPATH=. python -m scripts.parity check
- expect: exit 0, reports "PARITY OK"

### import-surface-stable
- proves: the app's importable module and symbol surface hasn't lost anything, only gained
- traces: nothing depends on a module or symbol that quietly stopped existing
- mode: command
- scenario: snapshot the import surface and diff it against the committed baseline
- run: PYTHONPATH=. python -m scripts.api_snapshot check
- expect: exit 0, reports "API SNAPSHOT OK"

### import-layering-acyclic
- proves: the module dependency graph stays acyclic and carries no new cross-layer violation beyond the pinned, acknowledged ones
- traces: core/gateway/harness/daemon keep their one-directional dependency order
- mode: command
- scenario: walk the import graph and check it against the layering rule and the pinned allowlist
- run: PYTHONPATH=. python -m scripts.layers
- expect: exit 0, reports "LAYERS OK"

### text-io-declares-encoding
- proves: every text read, write and subprocess decode in the codebase names its encoding explicitly
- traces: behaviour never silently depends on the host OS's default encoding
- mode: command
- scenario: statically scan the codebase for text I/O calls with no explicit encoding
- run: PYTHONPATH=. python -m scripts.encoding_gate
- expect: exit 0

### frontend-typechecks
- proves: the frontend's TypeScript compiles with no type errors
- traces: the frontend and the API types it's generated against stay in agreement
- mode: command
- scenario: typecheck the frontend with no build step
- run: cd web/frontend && npx tsc --noEmit
- expect: exit 0
