import { useEffect, useMemo, useState } from 'react'
import { fmtTokens } from '@/lib/format'
import { operationRows } from '@/lib/tokens'
import Modal from '@/ui/Modal'
import TabBar from '@/ui/TabBar'
import Toggle from '@/ui/Toggle'
import Bars, { type BarRow as Row } from '@/ui/Bars'
import Empty from '@/ui/Empty'
import { getTokens, getTokenTimeseries, type TokenUsage, type TokenTimeseries } from '@/lib/api'
import type { CommandStats } from './useCommandStats'

// The Tokens tile drill-in — full-visibility accounting (token-usage-tracking-spec). Three lenses over
// the SAME total: Per repo, By operation, Over time. The systematic token-type split is not a fourth
// lens: it is ONE number per type with no parts to compare, so it reads as a figure line under the
// trend that already plots those types, and a tab holding three bars is a tab holding a caption.
//
// A single MODE toggle governs the whole popup (2026-07-07): OFF = 3-type (input + cache write +
// output — the "new work" the tile shows, default); ON = 4-type (also counts cache_read, the cheap
// re-reads of already-cached context = the full window volume). Every breakdown value — headline,
// per-repo, per-operation, per-day, and the type split — is computed w.r.t. the active mode. In
// 3-type mode the cache_read row/layer simply isn't shown.

// The token-type palette. `cache_read` only appears in 4-type mode. There is no fifth bucket: a run
// with no typed usage (aborted, killed, errored) contributes nothing — see spine.token_timeseries.
const TYPE_META: { key: string; label: string; color: string }[] = [
  { key: 'input', label: 'Input', color: '#6ea8fe' },
  { key: 'cache_creation', label: 'Cache write', color: '#e0a35a' },
  { key: 'cache_read', label: 'Cache read', color: '#8b93a7' },
  { key: 'output', label: 'Output', color: '#5fe3b3' },
]
// Types visible in the active mode: cache_read is 4-type-only.
const typesFor = (full: boolean) => TYPE_META.filter((t) => full || t.key !== 'cache_read')

// Breakdown 1 — per-operation. The shape comes from the payload's category tree (see lib/tokens),
// shared with every other surface that lists operations, so they cannot drift apart.
function OperationBars({ tokens, full }: { tokens: TokenUsage; full: boolean }) {
  const rows = operationRows(
    tokens.global?.by_category,
    full ? tokens.global?.by_feature_cache_read : undefined,
  )
  if (!rows.length) return <NoUsage />
  return <Bars rows={rows} />
}

// Over time — per-day usage. Two modes: a stacked by-type breakdown (each day's bar split by token
// type, with a hover popover of the exact values) and a cumulative line. Both respect the 3/4-type
// mode. ("Per day" was dropped — the stacked by-type view already IS the per-day total, split.)
function OverTime({ ts, tokens, full }: { ts: TokenTimeseries; tokens: TokenUsage | null; full: boolean }) {
  const [mode, setMode] = useState<'type' | 'cumulative'>('type')
  const days = ts.days ?? []
  if (!days.length) return <NoUsage />
  const types = typesFor(full)
  // Each day's mode-aware total = sum of its visible type values (= 3-type total, +cache_read in 4-type).
  const dayTotal = (d: TokenTimeseries['days'][number]) =>
    types.reduce((s, t) => s + ((d[t.key as keyof typeof d] as number) ?? 0), 0)
  const series = days.map((d) => ({ d, total: dayTotal(d) }))
  let run = 0
  const cum = series.map((s) => (run += s.total))
  const dayMax = Math.max(1, ...series.map((s) => s.total))
  const cumMax = Math.max(1, run)
  const heightOf = (v: number, max: number) => `${Math.max(2, (v / max) * 100)}%`
  return (
    <div className="space-y-3">
      <TabBar size="sm" value={mode} onChange={setMode} tabs={[['type', 'By type'], ['cumulative', 'Cumulative']] as const} />
      <div className="flex h-40 items-end gap-1 border-b border-line pb-0">
        {series.map(({ d, total }, i) => {
          // Edge-aware popover anchor so it never clips out of the modal: left third opens rightward,
          // right third opens leftward, the middle stays centered over the column.
          const f = series.length <= 1 ? 0.5 : i / (series.length - 1)
          const anchorX = f < 0.34 ? 'left-0' : f > 0.66 ? 'right-0' : 'left-1/2 -translate-x-1/2'
          return (
          <div key={d.day} className="group relative flex h-full flex-1 flex-col justify-end" style={{ minWidth: 6 }}>
            {mode === 'type'
              ? types.map((t) => {
                  const v = (d[t.key as keyof typeof d] as number) ?? 0
                  return v ? <div key={t.key} style={{ height: heightOf(v, dayMax), backgroundColor: t.color }} /> : null
                })
              : <div className="rounded-t-sm bg-iris" style={{ height: heightOf(cum[i], cumMax) }} />}
            {/* hover popover — over the hovered column, edge-anchored. In `type` mode: the day's
                per-type breakdown + day total. In `cumulative` mode: the running total the bar shows
                (cum through this day) + that day's own contribution. */}
            <div className={`pointer-events-none absolute top-1/2 z-20 hidden -translate-y-1/2 whitespace-nowrap rounded-md border border-line bg-surface px-2.5 py-1.5 text-[11px] shadow-lg group-hover:block ${anchorX}`}>
              <div className="mb-1 font-medium text-fg">{d.day}</div>
              {mode === 'type' ? (
                <>
                  {types.map((t) => {
                    const v = (d[t.key as keyof typeof d] as number) ?? 0
                    return v ? (
                      <div key={t.key} className="flex items-center justify-between gap-3">
                        <span className="flex items-center gap-1.5 text-muted">
                          <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: t.color }} /> {t.label}
                        </span>
                        <span className="font-mono text-fg">{fmtTokens(v)}</span>
                      </div>
                    ) : null
                  })}
                  <div className="mt-1 flex items-center justify-between gap-3 border-t border-line pt-1">
                    <span className="text-muted">Total</span>
                    <span className="font-mono text-fg">{fmtTokens(total)}</span>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-muted">Cumulative</span>
                    <span className="font-mono text-fg">{fmtTokens(cum[i])}</span>
                  </div>
                  <div className="mt-0.5 flex items-center justify-between gap-3">
                    <span className="text-faint">This day</span>
                    <span className="font-mono text-faint">+{fmtTokens(total)}</span>
                  </div>
                </>
              )}
              {d.runs ? <div className="mt-0.5 text-faint">{d.runs} run{d.runs > 1 ? 's' : ''}</div> : null}
            </div>
          </div>
          )
        })}
      </div>
      <div className="flex justify-between text-[11px] text-faint">
        <span>{days[0].day}</span>
        <span>{days[days.length - 1].day}</span>
      </div>
      {/* The legend carries each type's RUNNING TOTAL, not just its colour. It is the same key the
          chart needs, and the systematic split has no parts to compare within a type — one figure
          per line says everything a bar chart of three bars was saying, in the place the reader is
          already looking. Shown in both modes: the totals are the period's, which the cumulative
          view is also about. */}
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 border-t border-line pt-2.5 text-[11px]">
        {types.map((t) => (
          <span key={t.key} className="flex items-center gap-1.5">
            <span className="h-2 w-2 shrink-0 rounded-sm" style={{ backgroundColor: t.color }} />
            <span className="text-faint">{t.label}</span>
            <span className="font-mono tabular-nums text-fg">
              {fmtTokens((tokens?.global?.by_type?.[t.key as 'input'] as number) ?? 0)}
            </span>
          </span>
        ))}
      </div>

    </div>
  )
}

const NoUsage = () => <Empty>No usage recorded yet.</Empty>

type Tab = 'repo' | 'category' | 'time'

export default function TokenDrilldown({ stats, onClose }: { stats: CommandStats; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('repo')
  const [tokens, setTokens] = useState<TokenUsage | null>(null)
  const [ts, setTs] = useState<TokenTimeseries | null>(null)
  // Mode: off = 3-type (default), on = 4-type (adds cache_read across every breakdown).
  const [full, setFull] = useState(false)

  useEffect(() => {
    getTokens().then(setTokens).catch(() => {})
    getTokenTimeseries().then(setTs).catch(() => {})
  }, [])

  const total3 = tokens?.global?.total ?? 0
  const cacheRead = tokens?.global?.by_type?.cache_read ?? 0
  const headlineTotal = full ? total3 + cacheRead : total3

  // Per-repo rows: 3-type from the orbit stats; 4-type adds each repo's cache_read from the token API.
  // Disconnected repos have no orbit node, so their preserved spend rolls into one "Old projects" bar
  // — otherwise these rows wouldn't sum to the headline total.
  const repoRows: Row[] = useMemo(() => {
    const live = [stats.hub, ...stats.nodes]
      .filter((r): r is NonNullable<typeof r> => !!r)
      .map((r) => {
        const cr = full ? tokens?.by_repo?.[r.id]?.by_type?.cache_read ?? 0 : 0
        return { key: r.id, label: r.id === 'global' ? 'SuperMe Hub' : r.label, value: r.tokens + cr, color: r.color }
      })
    const old = tokens?.archived
    if (old && old.total > 0) {
      const cr = full
        ? old.repos.reduce((s, m) => s + (tokens?.by_repo?.[m.id]?.by_type?.cache_read ?? 0), 0)
        : 0
      live.push({ key: '__archived__', label: 'Old projects', value: old.total + cr, color: '#8b93a7' })
    }
    return live.sort((a, b) => b.value - a.value)
  }, [stats, tokens, full])

  const oldRepos = tokens?.archived?.repos ?? []

  const TABS: [Tab, string][] = [
    ['repo', 'Per repo'],
    ['category', 'By operation'],
    ['time', 'Over time'],
  ]

  return (
    <Modal onClose={onClose} title="Token usage">
      {/* Headline total + the 3/4-type mode toggle (off = 3-type default, on = adds cache read). */}
      <div className="flex items-center justify-between px-5 pt-4">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-semibold tabular-nums text-fg">{fmtTokens(headlineTotal)}</span>
          <span className="text-[12px] text-faint">tokens</span>
        </div>
        <label className="flex items-center gap-2 text-[12px] text-muted" title="Off: input + cache write + output (new work). On: also count cache read (full window volume).">
          <span>Cache read</span>
          <Toggle on={full} onChange={setFull} onColor="bg-iris" />
        </label>
      </div>
      <div className="px-5 pt-3">
        <TabBar tabs={TABS} value={tab} onChange={setTab} />
      </div>
      <div className="max-h-[60vh] overflow-y-auto p-5">
        {tab === 'repo' && (
          <div className="space-y-3">
            <Bars rows={repoRows} />
            {oldRepos.length > 0 && (
              <p className="pt-1 text-[12px] leading-relaxed text-faint">
                <span className="text-muted">Old projects</span> — every project you no longer have:
                disconnected, dropped, or deleted.
              </p>
            )}
          </div>
        )}
        {tab === 'category' && (tokens ? <OperationBars tokens={tokens} full={full} /> : <NoUsage />)}
        {tab === 'time' && (ts ? <OverTime ts={ts} tokens={tokens} full={full} /> : <NoUsage />)}
      </div>
    </Modal>
  )
}
