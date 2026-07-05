---
name: superme-context-model
description: How SuperMe composes a hosted agent's context (the four sources + always-on-vs-on-demand loading) and the invariants any change to the harness, context assembly, or artifact model must preserve. Pull before editing context assembly, the harness, constitutions/skills/subagents, or the knowledge layout.
scope: repo_dev
source: system
category: reference
created: 2026-07-05
updated: 2026-07-05
---

The design of SuperMe's **own** context system, and the invariants a change to it must not break.
This is the model you are working *inside* whenever you touch the harness or context assembly.

## The Context stack — four sources

For a host `(id, mode)`, context is composed in layers:

1. **Universal harness** — `harness/`: `SELF.md` (identity), `{core,dev}-charter.md` (the mode's
   role), `constitution/{core,dev}/` + `plugins/` (artifacts). Shared by *every* host.
2. **Local harness** — `local-harness/<id>/<mode>/`: this host's own artifacts + optional
   `charter.local.md`. Committed with the code.
3. **Working knowledge** — `superme-knowledge/<id>-knowledge/<mode>/`: this host's gitignored
   knowledge tree, reached by absolute path (not under cwd).
4. **Claude-native** — the owner's `~/.claude` + the host dir's `.claude/` via `setting_sources`.
   (SuperMe disables Claude Code's native auto-memory; it owns its own.)

## The one loading rule

**Only** `SELF.md` + the mode charter + a local charter (if present) are flat-dumped every turn.
**Everything else** — every skill, subagent, and constitution — is **catalog-then-pull**: the
always-on context carries only a lightweight catalog (each item's name + a one-line "when this
applies"); a body loads on demand (`pull_constitution(name)`, or skill/subagent invocation). The
always-on layer is the only permanent token cost, so keep it **ruthlessly tight** — no knowledge that
belongs in a constitution, no restating another entry.

## One home per fact

Every fact has exactly one context entry point — identity → `SELF.md`; mode role → the charter; deep
/ system knowledge → a constitution; a procedure → a skill; a delegated capability → a subagent; a
host's own facts → its working knowledge. **READMEs are directory docs, never context-stack
artifacts** (not loaded, not in the catalog).

## Two tiers, identical form

Skills, subagents, and constitutions differ **only by scope**: **universal** (`harness/`, applies to
every host) vs **host-local** (`local-harness/<id>/<mode>/`, only that host). The catalog spans both,
filtered to the current host.

## Hosting-scope isolation

A host draws on — and answers within — **only its own scope**: its `cwd` + its `<id>-knowledge/`
tree + the universal harness + its own `local-harness/<id>/<mode>`, and nothing else. This is enforced
on the read path: out-of-scope `Read`/`Grep`/`Glob` are denied against the per-Context allowlist
(`core/permissions.py`; Bash is the accepted ceiling). A repo host must never read another repo's
knowledge or SuperMe's own source.

## Two rules that protect isolation

- **Hub-is-a-repo.** SuperMe-internals knowledge (this constitution, `superme-architecture`) is
  **hub-local**, *not* universal — so it never leaks into a repo host's catalog. Only truly
  cross-cutting expertise (e.g. `sql-expert`) is universal.
- **No outward pointers in universal content.** Universal content must never name an out-of-scope file
  — a repo-specific file, or a general doc (`README.md`, `model.yaml`, …). A repo host either lacks
  that file or would have to look outside its scope to resolve it. Inline the essential digest, or
  reify it as a constitution the agent discovers via the catalog. (This binds *universal* content;
  host-local content may reference its own scope.)
