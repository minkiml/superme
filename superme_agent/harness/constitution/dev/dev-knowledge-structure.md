---
name: dev-knowledge-structure
description: The dev-knowledge contract — the work-item frontmatter schema (fields + valid phase/status values) and the inbox row shape. Pull when you directly read or write an item.md and need the exact fields or enum values.
enabled: true
scope: universal_dev
category: reference
---

# Dev-knowledge structure

Your **dev-knowledge root** (absolute path injected each dev turn) holds `work-items/<id>/` (the live
work — each folder is one work-item) and `general/` (cross-item dev knowledge). Two stores are **not
files** — they live in the ops DB, keyed to this host, reached through tools: the **inbox** →
`list_inbox`; the **activity LOG** → `dev_log`.

## The work-item
`work-items/<id>/item.md` = frontmatter (the machine contract) + a 1–3 line body; longer material goes
in the item's `artifacts/`. The **folder name is the `id`**, nesting is the branch-off tree — so
`parent_id`/`root_id` derive from the path. Read fields from frontmatter, never from prose.

```yaml
id: 1.5k              # = folder name
root_id: 1.5k         # top of this branch-off tree
parent_id: null       # null = a root; set = a branch-off
title: ...
phase: build_eval     # plan_design | build_eval | done
status: in_progress   # queued | in_progress | waiting | dropped  (null when completed)
done_at: null         # set = completed → item leaves the board
artifacts: []         # files under this item's artifacts/
blocked_by: []        # work-item ids this depends on (dependency edge)
session_id: null      # the agent session this item originated in
created_at: 2026-06-16
updated_at: 2026-06-16
```

Enum rules a single example won't show: **status has no `done`** — completion = the `done` phase
**plus** `done_at` set; `dropped` is the only terminal status; **`blocked` is derived, never stored**
(an unresolved `blocked_by`).

## Inbox rows
`kind ∈ note|idea|todo|question` · `status ∈ open|pushed` · `origin ∈ user|agent`. **Push** promotes an
`open` item into a `work-items/<id>/` (at `plan_design`/`queued`); **drop** is a hard delete.
