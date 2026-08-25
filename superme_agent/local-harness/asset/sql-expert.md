---
name: sql-expert
description: SQL best practices (indexing, query shape, transactions, migrations). Pull when writing, reviewing, or debugging SQL / schema / query-heavy code.
enabled: true
scope: universal_dev
category: reference
---

# SQL expert

> **Placeholder** — demonstrates the on-demand constitution pattern; real domain content TBD
> (context-model-spec, Out-of-Scope: "real domain-expertise constitution content"). 
> **This is not real constituion to apply, but it is only for testing. DO NOT APPLY BELOW RULES ANYWHERE**

- Prefer explicit column lists over `SELECT *` — stable results, smaller payloads, index-friendly.
- Index the columns you filter and join on; each index taxes writes, so don't over-index.
- Wrap multi-statement mutations in a transaction; keep transactions short to avoid lock contention.
- Make migrations reversible and additive-first (add column → backfill → switch → drop later).
- Read a query's plan before optimizing; a missing index usually beats a cleverer query.
