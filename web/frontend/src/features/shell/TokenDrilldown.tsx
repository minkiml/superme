import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { fmtTokens } from '@/lib/format'
import { featureColor } from '@/lib/palette'
import { getTokens, getTokenTimeseries, type TokenUsage, type TokenTimeseries } from '@/lib/api'
import type { CommandStats } from './useCommandStats'

// The Tokens tile drill-in — full-visibility accounting (token-usage-tracking-spec). Four lenses
// over the SAME real total: Per repo, By operation (flat per-feature — every feature incl. chat +
// any unregistered one, summing to the total), By token type (systematic split), and Over time.
// cache_read is counted in full and shown as a muted slice (cheap re-reads, never hidden).

type Row = { key: string; label: string; sub?: string; value: number; color: string }

// The token-type palette: cache_read + legacy are deliberately muted (cheap / historical), so a
// large cache_read reads as re-reads, not new spend.
const TYPE_META: { key: string; label: string; color: string; muted?: boolean }[] = [
  { key: 'input', label: 'Input', color: '#6ea8fe' },
  { key: 'cache_creation', label: 'Cache write', color: '#e0a35a' },
  { key: 'cache_read', label: 'Cache read', color: '#8b93a7', muted: true },
  { key: 'output', label: 'Output', color: '#5fe3b3' },
  { key: 'legacy', label: 'Legacy (unsplit)', color: '#4b5563', muted: true },
]

function Bars({ rows }: { rows: Row[] }) {
  const max = rows.length ? Math.max(...rows.map((r) => r.value)) || 1 : 1
  return (
    <div className="space-y-2.5">
      {rows.map((r) => (
        <div key={r.key} className="flex items-center gap-3 text-[14px]">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: r.color }} />
          <span className="w-32 shrink-0 truncate font-mono text-fg">{r.label}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-hover">
            <div className="h-full rounded-full" style={{ width: `${(r.value / max) * 100}%`, backgroundColor: r.color }} />
          </div>
          <span className="w-14 shrink-0 text-right font-mono text-muted">{fmtTokens(r.value)}</span>
          {r.sub && <span className="w-24 shrink-0 truncate text-[13px] text-faint">{r.sub}</span>}
        </div>
      ))}
    </div>
  )
}

// Breakdown 1 — a FLAT per-operation (per-feature) split: every feature (chat, plan, distill, sweep,
// write, + any unregistered one under its own name) as one bar, summing to the total. Mirrors the
// per-repo inspector's "By operation" so the two read identically. cache_read is included in each
// feature's amount (the accounting is per row, not per token-type here).
function FeatureBars({ tokens }: { tokens: TokenUsage }) {
  const rows: Row[] = Object.entries(tokens.global?.by_feature ?? {})
    .map(([f, v]) => ({ key: f, label: f, value: v, color: featureColor(f) }))
    .sort((a, b) => b.value - a.value)
  if (!rows.length) return <Empty />
  return <Bars rows={rows} />
}

// Breakdown 2 — the systematic token-type split (input / cache write / cache read / output / legacy).
function TypeSplit({ tokens }: { tokens: TokenUsage }) {
  const bt = tokens.global?.by_type
  const rows: Row[] = TYPE_META.map((t) => ({
    key: t.key,
    label: t.label,
    value: (bt?.[t.key as keyof typeof bt] as number) ?? 0,
    color: t.color,
  })).filter((r) => r.value > 0)
  if (!rows.length) return <Empty />
  return (
    <div className="space-y-3">
      <Bars rows={rows} />
      <p className="pt-1 text-[12px] leading-relaxed text-faint">
        Cache read is cheap re-reads of already-cached context — counted in full, but muted so a large
        value reads as re-reads, not new spend.
      </p>
    </div>
  )
}

// Over time — per-day usage, with three modes: per-day total (bars), cumulative (rising bars), and
// a stacked-by-type breakdown. Same per-day rows drive all three.
function OverTime({ ts }: { ts: TokenTimeseries }) {
  const [mode, setMode] = useState<'day' | 'cumulative' | 'type'>('day')
  const days = ts.days ?? []
  if (!days.length) return <Empty />
  const heightOf = (v: number, max: number) => `${Math.max(2, (v / (max || 1)) * 100)}%`
  const dayMax = Math.max(1, ...days.map((d) => d.total))
  const cumMax = ts.total || 1
  return (
    <div className="space-y-3">
      <div className="inline-flex rounded-lg bg-hover p-0.5 text-[12px]">
        {([['day', 'Per day'], ['cumulative', 'Cumulative'], ['type', 'By type']] as const).map(([m, lbl]) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`rounded-md px-2.5 py-1 ${mode === m ? 'bg-surface text-fg' : 'text-muted hover:text-fg'}`}
          >
            {lbl}
          </button>
        ))}
      </div>
      <div className="flex h-40 items-end gap-1 border-b border-line pb-0">
        {days.map((d) => {
          const title = `${d.day} · ${fmtTokens(d.total)} tok${d.runs ? ` · ${d.runs} run${d.runs > 1 ? 's' : ''}` : ''}`
          if (mode === 'type') {
            return (
              <div key={d.day} title={title} className="flex h-full flex-1 flex-col justify-end" style={{ minWidth: 6 }}>
                {TYPE_META.map((t) => {
                  const v = (d[t.key as keyof typeof d] as number) ?? 0
                  return v ? <div key={t.key} style={{ height: heightOf(v, dayMax), backgroundColor: t.color }} /> : null
                })}
              </div>
            )
          }
          const v = mode === 'cumulative' ? d.cumulative : d.total
          const max = mode === 'cumulative' ? cumMax : dayMax
          return (
            <div key={d.day} title={title} className="flex h-full flex-1 flex-col justify-end" style={{ minWidth: 6 }}>
              <div className="rounded-t-sm bg-iris" style={{ height: heightOf(v, max) }} />
            </div>
          )
        })}
      </div>
      <div className="flex justify-between text-[11px] text-faint">
        <span>{days[0].day}</span>
        <span>{days[days.length - 1].day}</span>
      </div>
      {mode === 'type' && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
          {TYPE_META.map((t) => (
            <span key={t.key} className="flex items-center gap-1 text-faint">
              <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: t.color }} /> {t.label}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function Empty() {
  return <p className="py-8 text-center text-[13px] text-faint">No usage recorded yet.</p>
}

type Tab = 'repo' | 'category' | 'type' | 'time'

export default function TokenDrilldown({ stats, onClose }: { stats: CommandStats; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('category')
  const [tokens, setTokens] = useState<TokenUsage | null>(null)
  const [ts, setTs] = useState<TokenTimeseries | null>(null)

  useEffect(() => {
    getTokens().then(setTokens).catch(() => {})
    getTokenTimeseries().then(setTs).catch(() => {})
  }, [])

  const repoRows: Row[] = useMemo(
    () =>
      [stats.hub, ...stats.nodes]
        .filter((r): r is NonNullable<typeof r> => !!r)
        .map((r) => ({ key: r.id, label: r.id === 'global' ? 'SuperMe' : r.label, value: r.tokens, color: r.color }))
        .sort((a, b) => b.value - a.value),
    [stats],
  )

  const TABS: [Tab, string][] = [
    ['repo', 'Per repo'],
    ['category', 'By operation'],
    ['type', 'By token type'],
    ['time', 'Over time'],
  ]

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-6 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-app shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <span className="text-[15px] font-semibold text-fg">Token usage</span>
          <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-hover hover:text-fg">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 pt-4">
          <div className="inline-flex rounded-lg bg-hover p-0.5 text-[13px]">
            {TABS.map(([t, lbl]) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded-md px-3 py-1 ${tab === t ? 'bg-surface text-fg' : 'text-muted hover:text-fg'}`}
              >
                {lbl}
              </button>
            ))}
          </div>
        </div>

        <div className="max-h-[60vh] overflow-y-auto p-5">
          {tab === 'repo' && <Bars rows={repoRows} />}
          {tab === 'category' && (tokens ? <FeatureBars tokens={tokens} /> : <Empty />)}
          {tab === 'type' && (tokens ? <TypeSplit tokens={tokens} /> : <Empty />)}
          {tab === 'time' && (ts ? <OverTime ts={ts} /> : <Empty />)}
        </div>
      </div>
    </div>
  )
}
