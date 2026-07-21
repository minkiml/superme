import { useEffect, useState } from 'react'
import { getTokens, getRepos, getMemoryStats, type RepoOverview, type ScopeStatus } from '@/lib/api'
import { colorFor } from '@/lib/palette'

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
  byFeature: Record<string, number>
  scopes: Record<string, ScopeStatus>
  modelOverride: string | null
  effortOverride: string | null
  learningEnabled: boolean
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
// (/tokens), the repo roster (/repos), and global learning stats (/dev/memory/stats). Polls
// so the strip + orbit stay live. Fails soft — a down daemon just leaves the placeholders.
export function useCommandStats(pollMs = 5000): CommandStats {
  const [stats, setStats] = useState<CommandStats>(EMPTY)

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const [tokens, repos, mem] = await Promise.all([getTokens(), getRepos(), getMemoryStats('global')])
        if (!alive) return
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
            byFeature: t?.by_feature ?? {},
            scopes: r.scopes ?? {},
            modelOverride: r.model_override ?? null,
            effortOverride: r.effort_override ?? null,
            learningEnabled: r.learning_enabled ?? true,
            color: r.tag_color || colorFor(r.id),
            icon: r.icon ?? null,
          }
        }
        const all = repos.map(toRepo)
        const hub = all.find((r) => r.id === 'global') ?? null
        const nodes = all.filter((r) => r.id !== 'global')
        const cand = mem.candidates
        const know = mem.knowledge
        setStats({
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
        })
      } catch {
        /* daemon down — keep whatever we have (placeholders) */
      }
    }
    load()
    const t = setInterval(load, pollMs)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [pollMs])

  return stats
}
