import { useCallback, useEffect, useState } from 'react'
import { Activity, RefreshCw, Loader2, ChevronDown, Link2Off } from 'lucide-react'
import { colorFor, featureColor, featureLabel } from '@/lib/palette'
import { RepoIcon } from '@/lib/repoIcons'
import { fmtTokens, fmtLocal, fmtModel, fmtDuration } from '@/lib/format'
import { getRuns, type Run } from '@/lib/api'
import type { CommandStats, OrbitRepo } from '@/features/shell/useCommandStats'
import { Empty } from '@/features/dev/common'
import RunTraceModal from './RunTraceModal'

// Global Activity — the command-centre run feed across ALL repos (SuperMe's own agent work; the
// owner's external Claude Code sessions are excluded). One row per spine run: repo · feature ·
// scope · model · tokens · took · when. Newest-first, paged on demand ("Load 30 more") so
// history is always reachable regardless of recency. Click a row to open its trace (transcript +
// telemetry). Colored by repo (swatch) + feature (chip) so it stays scannable.

const PAGE = 30
// Shared column template so the header and every row align exactly.
const COLS = 'grid grid-cols-[1.4fr_72px_48px_1fr_84px_64px_100px] items-center gap-3'

// How long a run took (start→end). Live/unfinished runs have no end yet.
const took = (r: Run) => (r.ended_at ? fmtDuration(Date.parse(r.ended_at) - Date.parse(r.started_at)) : '—')

export default function GlobalActivity({ stats }: { stats: CommandStats }) {
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [limit, setLimit] = useState(PAGE) // grows by PAGE each "Load more"; the fetch pulls this many
  const [hasMore, setHasMore] = useState(false)
  const [openRun, setOpenRun] = useState<Run | null>(null)

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

  // `silent` polls in the background without flipping the spinner or clobbering an error toast.
  const load = useCallback((silent = false) => {
    if (!silent) { setLoading(true); setErr(null) }
    getRuns(undefined, limit)
      .then((d) => {
        // `history` (recent_runs) already includes in-flight runs, and `live` repeats them — so the
        // naive [...live, ...history] double-lists a running run (same id → duplicate React key,
        // which also breaks reconciliation so the stale row lingered until a full reload). Dedup by
        // id, live first so the running row wins.
        const seen = new Set<number>()
        const rows = [...d.live, ...d.history].filter((r) => !seen.has(r.id) && seen.add(r.id))
        setRuns(rows)
        // A full page of history back means there may be more to fetch.
        setHasMore(d.history.length >= limit)
      })
      .catch((e) => { if (!silent) setErr(String(e)) })
      .finally(() => { if (!silent) setLoading(false) })
  }, [limit])

  useEffect(() => {
    load()
  }, [load])

  // Auto-refresh: poll the run feed every 5s so live runs (and their completion) surface without a
  // manual click. Silent, so it never flashes the spinner or drops an open trace modal.
  useEffect(() => {
    const id = setInterval(() => load(true), 5000)
    return () => clearInterval(id)
  }, [load])

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl p-6">
        <header className="mb-4 flex items-center gap-2.5">
          <Activity size={18} className="text-dev" />
          <h1 className="text-[17px] font-semibold text-fg">Activity</h1>
          <span className="text-[13px] text-faint">SuperMe agent runs · all repos · both scopes</span>
          <button
            onClick={() => load()}
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
        ) : runs.length === 0 ? (
          <Empty>No runs recorded yet.</Empty>
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
              <span className="text-right">When</span>
            </div>
            {runs.map((r, i) => (
              <RunRow key={r.id} r={r} meta={metaFor(r.repo_id)} last={i === runs.length - 1 && !hasMore} onOpen={() => setOpenRun(r)} />
            ))}
            {hasMore && (
              <button
                onClick={() => setLimit((l) => l + PAGE)}
                disabled={loading}
                className="flex w-full items-center justify-center gap-1.5 bg-surface/40 px-4 py-2.5 text-[13px] text-muted transition hover:bg-hover hover:text-fg disabled:opacity-60"
              >
                {loading ? <Loader2 size={14} className="animate-spin" /> : <ChevronDown size={14} />} Load {PAGE} more
              </button>
            )}
          </div>
        )}
      </div>

      {openRun && <RunTraceModal run={openRun} meta={metaFor(openRun.repo_id)} onClose={() => setOpenRun(null)} />}
    </div>
  )
}

function RunRow({ r, meta, last, onOpen }: { r: Run; meta: { label: string; color: string; icon: string | null }; last: boolean; onOpen: () => void }) {
  const isHub = r.repo_id === 'global'
  return (
    <button
      type="button"
      onClick={onOpen}
      title="View trace"
      className={`${COLS} w-full bg-surface px-4 py-2.5 text-left transition hover:bg-hover ${last ? '' : 'border-b border-line'}`}
    >
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
        {r.session_fate && (
          <span
            className="shrink-0 text-faint"
            title={`Origin session ${r.session_fate} — this run's trace is preserved`}
          >
            <Link2Off size={12} />
          </span>
        )}
      </span>
      <span
        className="justify-self-start rounded px-1.5 py-0.5 text-[11px] font-medium"
        style={{ color: featureColor(r.feature), backgroundColor: 'rgb(var(--c-hover))' }}
      >
        {featureLabel(r.feature)}
      </span>
      <span className="text-[11px] text-faint">{r.mode}</span>
      <span className="truncate text-[11px] text-muted" title={r.model ?? undefined}>{r.model ? fmtModel(r.model) : '—'}</span>
      <span className="text-right font-mono text-[11px] text-muted">{fmtTokens(r.tokens)}</span>
      <span className="text-right font-mono text-[11px] text-faint">{took(r)}</span>
      <span className="text-right font-mono text-[11px] text-faint">{fmtLocal(r.started_at)}</span>
    </button>
  )
}
