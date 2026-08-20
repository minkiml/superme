import { useMemo } from 'react'
import { getTokens, getRepos, getMemoryStats, type RepoOverview, type ScopeStatus } from '@/lib/api'
import { colorFor } from '@/lib/palette'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'

// One repo as an orbit node: identity + its token signal (total + core/dev split) plus the
// richer per-scope + per-feature detail the inspector popup needs.
export type OrbitRepo = {
  id: string
  label: string
  layer: string
  tokens: number
  dev: number
  core: number
  running: number
  agents: number
  // The per-repo category tree — the operation list reads it through lib/tokens, the same
  // way the token drill-in does, so the two can never disagree about one repo's split.
  byCategory: Record<string, { total?: number; features?: Record<string, number>; label?: string; collapsed?: boolean }>
  scopes: Record<string, ScopeStatus>
  modelOverride: string | null
  effortOverride: string | null
  // The `vet` role's own tier for this repo (null = unset → the floor). Separate from the two
  // above on purpose: vet checks what build produced and does not inherit the project's tier.
  vetModel: string | null
  vetEffort: string | null
  learningEnabled: boolean
  reviewMode: string // 'fast' | 'strict' — whether the diff gets its own review gate before landing
  anchorBranch: string | null // the branch every git site targets (null = the repo's own default)
  resolvedAnchor: string | null // what anchorBranch resolves to now (null = not a git repo / broken)
  anchorError: string | null // set when the configured branch is missing — a refusal, not a fallback
  color: string // the resolved visual-tag color (owner override, else hashed palette)
  icon: string | null // owner-set icon (emoji) shown in place of the color swatch
}

export type CommandStats = {
  loading: boolean
  tokensTotal: number
  tokensByFeature: Record<string, number> // global per-operation split (for the token drill-in)
  projects: number // connected repos, Me (global) excluded
  opsRunning: number
  opsLive: number
  learn: { candidates: number; pending: number; drafted: number; learned: number }
  hub: OrbitRepo | null // Me / global — the orbit center
  nodes: OrbitRepo[] // connected projects orbiting the hub
  // Disconnected projects, by former id → label. They have no orbit node, but their runs live on,
  // so the activity log and the token drill-in can still name them ("Old projects").
  archived: Record<string, string>
}

const EMPTY: CommandStats = {
  loading: true,
  tokensTotal: 0,
  tokensByFeature: {},
  projects: 0,
  opsRunning: 0,
  opsLive: 0,
  learn: { candidates: 0, pending: 0, drafted: 0, learned: 0 },
  hub: null,
  nodes: [],
  archived: {},
}

// Composes the command-centre's live numbers from three real sources: token aggregation
// (/tokens), the repo roster (/repos), and global learning stats (/dev/memory/stats).
//
// Each is its OWN cache key rather than one fused `load()` — the roster and the token aggregate are
// wanted independently all over the app (the inspector, the drilldowns, the config surface), and
// keying them separately is what lets those views cost zero extra requests. The three land at
// different moments, so this composes over whatever has arrived: a partial view renders with the
// pieces it has, exactly as the old single-shot version rendered placeholders before its first
// response.
//
// No longer fails silently: a transport failure marks the app disconnected (see `useConnection` /
// StatusBar). The numbers still hold their last good values — the difference is that the owner is
// now told they are last-known rather than current.
export function useCommandStats(pollMs = 5000): CommandStats {
  const tokensQ = useLive(K.tokens, getTokens, pollMs)
  const reposQ = useLive(K.repos, getRepos, pollMs)
  const memQ = useLive(K.memoryStats('global'), () => getMemoryStats('global'), pollMs)

  const tokens = tokensQ.data
  const repos = reposQ.data
  const mem = memQ.data

  return useMemo(() => {
    if (!tokens || !repos) return EMPTY
    const toRepo = (r: RepoOverview): OrbitRepo => {
      const t = tokens.by_repo?.[r.id]
      const scopeList = Object.values(r.scopes ?? {})
      return {
        id: r.id,
        label: r.label,
        layer: r.layer,
        tokens: t?.total ?? 0,
        dev: t?.by_scope?.dev ?? 0,
        core: t?.by_scope?.core ?? 0,
        running: scopeList.reduce((n, s) => n + (s?.running ?? 0), 0),
        agents: scopeList.reduce((n, s) => n + (s?.agents ?? 0), 0),
        byCategory: t?.by_category ?? {},
        scopes: r.scopes ?? {},
        modelOverride: r.model_override ?? null,
        effortOverride: r.effort_override ?? null,
        vetModel: r.vet_model ?? null,
        vetEffort: r.vet_effort ?? null,
        learningEnabled: r.learning_enabled ?? true,
        reviewMode: r.review_mode ?? 'fast',
        anchorBranch: r.anchor_branch ?? null,
        resolvedAnchor: r.resolved_anchor ?? null,
        anchorError: r.anchor_error ?? null,
        color: r.tag_color || colorFor(r.id),
        icon: r.icon ?? null,
      }
    }
    const all = repos.map(toRepo)
    const hub = all.find((r) => r.id === 'global') ?? null
    const nodes = all.filter((r) => r.id !== 'global')
    const cand = mem?.candidates
    const know = mem?.knowledge
    return {
      loading: false,
      tokensTotal: tokens.global?.total ?? 0,
      tokensByFeature: tokens.global?.by_feature ?? {},
      projects: nodes.length,
      opsRunning: all.reduce((n, r) => n + r.running, 0),
      opsLive: all.reduce((n, r) => n + r.agents, 0),
      learn: {
        candidates: cand?.total ?? 0,
        pending: cand?.pending_proposals ?? 0,
        drafted: cand?.drafted_proposals ?? 0,
        learned: know?.facts_total ?? 0,
      },
      hub,
      nodes,
      archived: Object.fromEntries((tokens.archived?.repos ?? []).map((r) => [r.id, r.label])),
    }
  }, [tokens, repos, mem])
}
