// Cache keys — the identity of a cached endpoint, and what `invalidate()` matches on.
//
// A key encodes every param, and keys NEST by topic, so invalidating a repo reaches every view
// under it.

export const K = {
  // ── system ───────────────────────────────────────────────────────────────────────────────────
  tokens: 'sys:tokens',
  repos: 'sys:repos',
  // Not folded into `repos`, which every surface polls: the anchor picker is its only reader.
  repoBranches: (repoId: string) => `sys:repo:${repoId}:branches`,
  runs: (limit: number) => `sys:runs:${limit}`,
  systemAttention: 'sys:attention',
  systemOverview: 'sys:overview',
  contexts: 'sys:contexts',

  // ── one repo's dev surface ───────────────────────────────────────────────────────────────────
  dev: (ctx: string) => `dev:${ctx}:glance`,
  devAttention: (ctx: string) => `dev:${ctx}:attention`,
  projectStatus: (ctx: string) => `dev:${ctx}:project-status`,
  memoryStats: (ctx: string) => `dev:${ctx}:memory-stats`,
  // The params are in the key, so an item log and a repo log stay distinct entries.
  devLog: (ctx: string, itemId?: string | null, limit = 50) =>
    `dev:${ctx}:log:${itemId ?? '-'}:${limit}`,

  // ── one work-item ────────────────────────────────────────────────────────────────────────────
  itemDetail: (ctx: string, id: string) => `dev:${ctx}:item:${id}:detail`,
  itemArtifacts: (ctx: string, id: string) => `dev:${ctx}:item:${id}:artifacts`,
  itemDrilldown: (ctx: string, id: string) => `dev:${ctx}:item:${id}:drilldown`,
  itemReport: (ctx: string, id: string, phase: string) =>
    `dev:${ctx}:item:${id}:report:${phase}`,
  itemGit: (ctx: string, id: string) => `dev:${ctx}:item:${id}:git`,
  // Its own key: the report is a rendered blob everyone polls, this is an editable form.
  itemOwnerInput: (ctx: string, id: string) => `dev:${ctx}:item:${id}:from-you`,
} as const

/** The invalidation topic covering everything about one repo (board, attention, all its items). */
export const topicRepo = (ctx: string) => `dev:${ctx}:`
/** …and every system-wide number. */
export const topicSystem = 'sys:'
