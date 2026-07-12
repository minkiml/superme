---
name: dev-knowledge-structure
description: The dev-knowledge contract — the general/ anchor-doc set, the two-tier deliverable→wave scaffold, the work-item frontmatter schema (fields · phase/status enums · the wave/deliverable pointer), the roadmap/PRD list conventions, and the inbox row shape. Pull when you read or write a general/ doc or an item.md, or need exact fields/enum values.
enabled: true
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
├─ project-prd.md    what & why — problem · users · deliverables · success · non-goals · open questions
├─ spec.md           decisions — stack · approach · constraints · append-only key decisions
├─ roadmap.md        the within-project index of SuperMe-tracked dev work (deliverable → wave → items)
├─ architecture.md   current system truth — components · flows · data · constraints & debt
└─ resources/        if any, external refs + an index .md (optional)
```

Each doc's field-level authoring contract — sections, shape, templates — is in the `superme-dev` plugin at
`general-dev-knowledge-asset/<doc>.md`; follow it when you write.

## The two-tier scaffold
Two curated tiers sit above the items:
- **Deliverable** — a chunk of intended value — in `project-prd.md` as `- **<id>** — Title` (the full
  set, including planned or externally-built).
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
phase: build_eval     # triage | plan_design | build_eval | done
status: in_progress   # queued | in_progress | waiting | dropped  (null when completed)
done_at: null         # set = completed → item leaves the board
artifacts: []         # files under this item's artifacts/
blocked_by: []        # work-item ids this depends on
session_id: null      # the session this item originated in
created_at: 2026-06-16
updated_at: 2026-06-16
```

Rules an example won't show:
- **phase `triage`** — intake/classification before `plan_design`; lands with the workspace workflow
  (today `push` enters at `plan_design`).
- **status has no `done`** — completion = phase `done` **plus** `done_at`; `dropped` is the only terminal
  status; **`blocked` is derived** (an unresolved `blocked_by`), never stored.
- **Who sets them** — workflows and the system set `phase`/`status`/`blocked_by` and inbox rows; phase
  advances and drops are **human-gated**. Unguided work owns only `artifacts` (+ `updated_at`).

**Inbox row:** `kind ∈ note|idea|todo|question` · `status ∈ open|pushed` · `origin ∈ user|agent`. **Push**
promotes an `open` row into `work-items/<id>/` (at `plan_design`/`queued`); **drop** is a hard delete.
