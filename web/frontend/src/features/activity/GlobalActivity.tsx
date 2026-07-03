import { useCallback, useEffect, useState } from 'react'
import { Activity, RefreshCw, Loader2, Loader, ChevronDown } from 'lucide-react'
import { colorFor, featureColor } from '@/lib/palette'
import { RepoIcon } from '@/lib/repoIcons'
import { fmtTokens, fmtLocal, fmtModel, fmtDuration } from '@/lib/format'
import { getRuns, type Run } from '@/lib/api'
import type { CommandStats, OrbitRepo } from '@/features/shell/useCommandStats'
import { Empty } from '@/features/dev/common'

// Global Activity — the command-centre run feed across ALL repos (SuperMe's own agent work; the
// owner's external Claude Code sessions are excluded). One row per spine run: repo · feature ·
// scope · model · tokens · took · status · when. Defaults to the last 2 days; "See more" loads
// the older history on demand. Colored by repo (swatch) + feature (chip) so it stays scannable.

const TWO_DAYS_MS = 2 * 24 * 60 * 60 * 1000
// Shared column template so the header and every row align exactly.
const COLS = 'grid grid-cols-[1.4fr_72px_48px_1fr_84px_64px_72px_100px] items-center gap-3'

// How long a run took (start→end). Live/unfinished runs have no end yet.
const took = (r: Run) => (r.ended_at ? fmtDuration(Date.parse(r.ended_at) - Date.parse(r.started_at)) : '—')

const STATUS_TINT: Record<string, string> = {
  running: 'text-dev',
  done: 'text-muted',
  aborted: 'text-danger',
  waiting: 'text-warn',
}

export default function GlobalActivity({ stats }: { stats: CommandStats }) {
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [showAll, setShowAll] = useState(false)

  const metaFor = useCallback(
    (id: string): { label: string; color: string; icon: string | null } => {
      const r = ([stats.hub, ...stats.nodes].filter(Boolean) as OrbitRepo[]).find((x) => x.id === id)
      return {
        label: id === 'global' ? 'SuperMe Hub' : r?.label ?? id,
        color: r?.color ?? colorFor(id),
        icon: r?.icon ?? null,
      }
    },
    [stats.hub, stats.nodes],
  )

  const load = useCallback(() => {
    setLoading(true)
    setErr(null)
    // Pull a bigger window once "See more" is on, so older history is actually available to show.
    getRuns(undefined, showAll ? 500 : 120)
      .then((d) => setRuns([...d.live, ...d.history]))
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false))
  }, [showAll])

  useEffect(() => {
    load()
  }, [load])

  const cutoff = Date.now() - TWO_DAYS_MS
  const recent = (runs ?? []).filter((r) => Date.parse(r.started_at) >= cutoff)
  const shown = showAll ? runs ?? [] : recent
  const hiddenOlder = (runs?.length ?? 0) - recent.length

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl p-6">
        <header className="mb-4 flex items-center gap-2.5">
          <Activity size={18} className="text-dev" />
          <h1 className="text-[17px] font-semibold text-fg">Activity</h1>
          <span className="text-[13px] text-faint">SuperMe agent runs · all repos · both scopes</span>
          <button
            onClick={load}
            title="Refresh"
            aria-label="Refresh"
            className="ml-auto rounded-md border border-line bg-surface p-1.5 text-muted hover:bg-hover hover:text-fg"
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          </button>
        </header>

        {err ? (
          <div className="text-sm text-danger">Couldn’t load activity — {err}</div>
        ) : runs === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Loading…
          </div>
        ) : shown.length === 0 ? (
          <Empty>No runs in the last 2 days.</Empty>
        ) : (
          <div className="overflow-hidden rounded-xl border border-line">
            {/* column header */}
            <div className={`${COLS} border-b border-line bg-surface/60 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-faint`}>
              <span>Repo</span>
              <span>Op</span>
              <span>Scope</span>
              <span>Model</span>
              <span className="text-right">Tokens</span>
              <span className="text-right">Took</span>
              <span>Status</span>
              <span className="text-right">When</span>
            </div>
            {shown.map((r, i) => (
              <RunRow key={r.id} r={r} meta={metaFor(r.repo_id)} last={i === shown.length - 1 && !(!showAll && hiddenOlder > 0)} />
            ))}
            {!showAll && hiddenOlder > 0 && (
              <button
                onClick={() => setShowAll(true)}
                className="flex w-full items-center justify-center gap-1.5 bg-surface/40 px-4 py-2.5 text-[13px] text-muted transition hover:bg-hover hover:text-fg"
              >
                <ChevronDown size={14} /> See {hiddenOlder} older {hiddenOlder === 1 ? 'run' : 'runs'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function RunRow({ r, meta, last }: { r: Run; meta: { label: string; color: string; icon: string | null }; last: boolean }) {
  const isHub = r.repo_id === 'global'
  return (
    <div className={`${COLS} bg-surface px-4 py-2.5 ${last ? '' : 'border-b border-line'}`}>
      <span className="flex min-w-0 items-center gap-2">
        {meta.icon && !isHub ? (
          <RepoIcon name={meta.icon} size={14} color={meta.color} className="shrink-0" />
        ) : (
          <span
            className="h-4 w-4 shrink-0 rounded-[4px]"
            style={isHub ? { backgroundImage: 'var(--grad-iris)' } : { backgroundColor: meta.color }}
          />
        )}
        <span className="truncate text-[13px] text-fg" title={meta.label}>{meta.label}</span>
      </span>
      <span
        className="justify-self-start rounded px-1.5 py-0.5 text-[11px] font-medium"
        style={{ color: featureColor(r.feature), backgroundColor: 'rgb(var(--c-hover))' }}
      >
        {r.feature}
      </span>
      <span className="text-[11px] text-faint">{r.mode}</span>
      <span className="truncate text-[11px] text-muted" title={r.model ?? undefined}>{r.model ? fmtModel(r.model) : '—'}</span>
      <span className="text-right font-mono text-[11px] text-muted">{fmtTokens(r.tokens)}</span>
      <span className="text-right font-mono text-[11px] text-faint">{took(r)}</span>
      <span className={`flex items-center gap-1 text-[11px] ${STATUS_TINT[r.status] ?? 'text-faint'}`}>
        {r.status === 'running' && <Loader size={11} className="animate-spin" />}
        {r.status}
      </span>
      <span className="text-right font-mono text-[11px] text-faint">{fmtLocal(r.started_at)}</span>
    </div>
  )
}
