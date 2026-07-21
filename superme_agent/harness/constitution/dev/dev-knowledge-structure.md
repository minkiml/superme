---
name: dev-knowledge-structure
description: The dev-knowledge contract — the general/ anchor-doc set, the two-tier deliverable→wave scaffold, the work-item frontmatter schema (fields · phase/status enums · the wave/deliverable pointer), the roadmap/PRD list conventions, and the inbox row shape. Pull when you read or write a general/ doc or an item.md, or need exact fields/enum values.
enabled: true
foundational: true
scope: universal_dev
category: reference
---

# Dev-knowledge structure

Your **dev-knowledge root** holds `general/` (anchor docs) and `work-items/<id>/` (all work-items, live
and completed). Two stores are **not files** — the **inbox** (`read_inbox`) and activity **log**
(`read_dev_log`) live in the ops DB, reached by tool.

## general/ — the anchor docs
Your durable model of THIS project. A **cache of understanding, not an archive**: essential-only; long
research or external files go under `resources/` or a work-item's `artifacts/`.

```
general/
├─ project-prd.md    what & why — identity · goals · non-goals · deliverables as value · success
├─ architecture.md   how it's built NOW — stack · invariants · what's deliberately not here ·
│                    components · flows · data (current-state, mutable)
├─ capabilities.md   what it can do RIGHT NOW — present tense only, never the plan
├─ decisions.md      what we chose and why — the append-only D-NNN ledger (history, never edited)
├─ roadmap.md        the within-project index of SuperMe-tracked dev work (deliverable → wave → items)
└─ resources/        if any, external refs + an index .md (optional)
```

**Each doc answers ONE question, and owns that answer.** State a fact in the doc that owns it and
reference it from anywhere else (`see D-012`) — never restate it. Two homes for one fact means one of
them silently stops being updated.

**Docs are split by LIFECYCLE, and the split is load-bearing:** `project-prd.md` + `architecture.md` +
`capabilities.md` are mutable current-state (edit in place); `decisions.md` is append-only history
(never edit a past entry's body — reverse by appending and marking the old one superseded);
`roadmap.md` is forward-only (no history). Putting a fact in the wrong lifecycle is how these docs rot.

**The tense rule:** `capabilities.md` is present tense only — what works today. `roadmap.md` is future
tense. A capability that hasn't shipped belongs in the roadmap; the moment the two mix, neither can be
trusted. (`spec.md` was retired into `architecture.md` — stack and constraints ARE current-state
architecture, and keeping both invited the same fact in two places.)

Each doc's field-level authoring contract — sections, shape, templates — is in the `superme-dev` plugin at
`general-dev-knowledge-asset/<doc>.md`; follow it when you write.

## The two-tier scaffold
Two curated tiers sit above the items:
- **Deliverable** — a chunk of value the owner can RECEIVE (not a component you must build) — in
  `project-prd.md` as `- **<id>** — Title`, optionally carrying indented `- **Value**:` and
  `- **Needs**:` sub-fields (the full set, including planned or externally-built). The test: if
  *"once this lands, I can ___"* can't be finished without naming another unfinished deliverable,
  it's a task inside one, not a deliverable of its own.
- **Wave** — a step toward a deliverable — under it in `roadmap.md` as `**<id>** — Title`, indexing only
  pipeline work driven through SuperMe (inbox → push → work-item).

**The link points UP**: a root item declares `wave: <id>` (resolves its deliverable) or `deliverable:
<id>`. The roadmap lists no item ids — the board groups items by their `wave:`. A wave may hold **zero**
items (planned, or work done outside SuperMe — that lives in `architecture.md` + git, not here).

A wave's **status** is a curated glyph (`✓ done · ▸ active · · planned`) — intent, not derived; the
**rollup** (3/5 done) is derived when the wave has items. **Referential integrity: every roadmap entry
names a deliverable in `project-prd.md`** (never the reverse).

## Work-items & inbox items
Every set of dev work is the **instance of** an item: an **inbox item** (intake row) is **pushed** into a
**work-item**, the instance the work runs in.

```
work-items/<id>/
├─ item.md        frontmatter (machine contract) + a 1–3 line body
├─ artifacts/     anything longer than the body
└─ <child-id>/    a dependent branch-off child — itself a full work-item (same shape); folder name = id
   ├─ item.md
   ├─ artifacts/
   └─ <grandchild-id>/ …
```

Read fields from frontmatter, never prose; `parent_id`/`root_id` derive from the path.

```yaml
id: 1.5k              # = folder name
root_id: 1.5k         # top of this branch-off tree
parent_id: null       # null = a root; set = a branch-off
wave: null            # (root only) the wave this item instances → resolves its deliverable
deliverable: null     # …or a deliverable directly when no wave applies
title: ...
kind: implementation  # implementation | research — picks the phase pipeline (KIND_PROFILES)
phase: build          # impl: triage|plan|build|vet|review|close · research: triage|plan|investigate|report|close
status: active        # active | awaiting_child | awaiting_human | done
outcome: null         # set with status done: completed | abandoned | superseded
spawned_from: null    # branch-off provenance {item, relation: blocking|parallel|spawn, note?}
superseded_by: null   # set when outcome = superseded
inbox_id: null        # originating inbox row (trace)
done_at: null         # terminal stamp → item leaves the board
artifacts: []         # files under this item's artifacts/
session_id: null      # the session this item originated in
created_at: 2026-06-16
updated_at: 2026-06-16
```

The kernel also stamps fields not shown above (all kernel-owned — read, never write): the git
record (`git_branch` · `git_worktree` · `git_base` · `git_merge_commit` · `git_merged_at` ·
`git_backup_ref`), the per-item run config (`model` · `effort`), and the read receipt (`seen_at`).

Rules an example won't show:
- **Every item enters at `triage`/`active`** — `kind` is a PROPOSAL until the triage-exit gate;
  phase sequencing is per-kind (the system's KIND_PROFILES), never skipped.
- **Terminal = a status change, never a delete** — `status: done` + `outcome` + `done_at`;
  `superseded` requires `superseded_by`.
- **`awaiting_human` is the only status that pages the owner**; `awaiting_child` auto-resumes when
  the last blocking child closes; running-right-now is derived from live runs, not a status.
- **Dependency between items is expressed ONE way** — a branch-off with `spawned_from.relation:
  blocking`, which pauses the parent at `awaiting_child`. There is no dependency-id list.
- **Who sets them** — the system sets `kind`/`phase`/`status`/`outcome` and inbox rows;
  phase advances and terminal outcomes are **human-gated** (the agent NEVER sets an item done).
  Unguided work owns only `artifacts` (+ `updated_at`).

**Inbox row:** `kind ∈ note|idea|todo|question` · `status ∈ open|pushed` · `origin ∈ user|agent` ·
optional `spawned_from` (carried onto the item at push). **Push** promotes an `open` row into
`work-items/<id>/` (at `triage`/`active`); **drop** is a hard delete.
