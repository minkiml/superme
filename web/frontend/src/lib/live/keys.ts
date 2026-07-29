// Cache keys — the identity of a cached endpoint, and the thing `invalidate()` matches on.
//
// Two rules make the whole layer work, and both are why these live in one file rather than as
// template strings scattered across components:
//
//   1. A key encodes the endpoint AND every param. Two components that build the same key get one
//      request; two that build different keys get two. Drift here silently un-dedups a screen.
//   2. Keys NEST by topic, most general segment first. `invalidate('dev:test-playground')` must
//      reach that repo's board, its attention feed, and every open item under it — so an item's
//      keys live *under* its repo's prefix rather than in a sibling namespace.
//
// Topic prefixes, in widening order:
//   `sys:`                    system-wide (tokens, roster, runs, attention)
//   `dev:<ctx>:`              one repo's dev surface
//   `dev:<ctx>:item:<id>:`    one work-item inside it

export const K = {
  // ── system ───────────────────────────────────────────────────────────────────────────────────
  tokens: 'sys:tokens',
  repos: 'sys:repos',
  runs: (limit: number) => `sys:runs:${limit}`,
  systemAttention: 'sys:attention',
  systemOverview: 'sys:overview',
  contexts: 'sys:contexts',

  // ── one repo's dev surface ───────────────────────────────────────────────────────────────────
  dev: (ctx: string) => `dev:${ctx}:glance`,
  devAttention: (ctx: string) => `dev:${ctx}:attention`,
  projectStatus: (ctx: string) => `dev:${ctx}:project-status`,
  memoryStats: (ctx: string) => `dev:${ctx}:memory-stats`,
  // The dev event log is param-heavy and several surfaces want different slices of it; the params
  // are in the key so a 50-row item log and a 200-row repo log stay distinct entries.
  devLog: (ctx: string, itemId?: string | null, limit = 50) =>
    `dev:${ctx}:log:${itemId ?? '-'}:${limit}`,

  // ── one work-item ────────────────────────────────────────────────────────────────────────────
  itemDetail: (ctx: string, id: string) => `dev:${ctx}:item:${id}:detail`,
  itemArtifacts: (ctx: string, id: string) => `dev:${ctx}:item:${id}:artifacts`,
  itemGateBrief: (ctx: string, id: string) => `dev:${ctx}:item:${id}:gate-brief`,
  itemGit: (ctx: string, id: string) => `dev:${ctx}:item:${id}:git`,
} as const

/** The invalidation topic covering everything about one repo (board, attention, all its items). */
export const topicRepo = (ctx: string) => `dev:${ctx}:`
/** …and every system-wide number. */
export const topicSystem = 'sys:'
