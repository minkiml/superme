import { useCallback, useMemo, useRef, useState } from 'react'
import { Activity, Loader2, ChevronDown, Link2Off } from 'lucide-react'
import { colorFor, featureColor, featureLabel } from '@/lib/palette'
import { RepoIcon } from '@/lib/repoIcons'
import { fmtTokens, fmtLocal, fmtAge, fmtModel, fmtDuration } from '@/lib/format'
import { useContainerWidth } from '@/lib/layout'
import { getRuns, type Run } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import type { CommandStats, OrbitRepo } from '@/features/shell/useCommandStats'
import { Empty } from '@/features/dev/common'
import RunTraceModal from './RunTraceModal'

// The command-centre run feed across ALL repos — one row per spine run, newest first, paged on
// demand.
//
// SuperMe's own agent work only; the owner's external sessions are excluded. Click a row for its
// trace.

const PAGE = 30
// Shared column template, so the header and every row align exactly.
//
// The table SHEDS COLUMNS as it narrows and never scrolls sideways. What never goes: which repo,
// what ran, what it cost, and when.
type Density = 'full' | 'mid' | 'tight'

// A bare `fr` floors at its content, so a long name pushes the grid wider and CLIPS the right-hand
// columns.
const COLS: Record<Density, string> = {
  full: 'grid grid-cols-[minmax(0,1.4fr)_72px_48px_minmax(0,1fr)_84px_64px_112px] items-center gap-3',
  mid: 'grid grid-cols-[minmax(0,1.4fr)_72px_minmax(0,1fr)_84px_112px] items-center gap-3',
  tight: 'grid grid-cols-[20px_minmax(0,1fr)_60px_52px] items-center gap-2',
}

function densityFor(w: number): Density {
  if (w === 0 || w >= 720) return 'full'
  return w >= 560 ? 'mid' : 'tight'
}

// How long a run took (start→end). Live/unfinished runs have no end yet.
const took = (r: Run) => (r.ended_at ? fmtDuration(Date.parse(r.ended_at) - Date.parse(r.started_at)) : '—')

export default function GlobalActivity({
  stats,
  onDiagnose,
}: {
  stats: CommandStats
  onDiagnose?: (run: Run, query: string) => void
}) {
  const [tableRef, tableW] = useContainerWidth<HTMLDivElement>()
  const density = densityFor(tableW)
  const [limit, setLimit] = useState(PAGE) // grows by PAGE each "Load more"; the fetch pulls this many
  const [openRun, setOpenRun] = useState<Run | null>(null)

  // `limit` is part of the cache key, so the previous page stays cached under its own.
  const feed = useLive(K.runs(limit), () => getRuns(undefined, limit))
  const loading = feed.loading
  const err = feed.data ? null : feed.error ? String(feed.error) : null

  // History already includes in-flight runs and `live` repeats them, so dedup by id, live first.
  const rows: Run[] | null = useMemo(() => {
    const d = feed.data
    if (!d) return null
    const seen = new Set<number>()
    return [...d.live, ...d.history].filter((r) => !seen.has(r.id) && seen.add(r.id))
  }, [feed.data])
  // KEEP THE LAST GOOD PAGE: a cache miss would unmount the table and reset the scroll.
  const lastGood = useRef<Run[] | null>(null)
  if (rows) lastGood.current = rows
  const runs = rows ?? lastGood.current
  // A full page of history back means there may be more to fetch.
  const hasMore = (feed.data?.history.length ?? 0) >= limit

  // Live roster first; a disconnected repo falls through to its tombstoned label rather than a bare
  // id.
  const metaFor = useCallback(
    (id: string): { label: string; color: string; icon: string | null; archived?: boolean } => {
      const r = ([stats.hub, ...stats.nodes].filter(Boolean) as OrbitRepo[]).find((x) => x.id === id)
      const gone = !r && !!stats.archived[id]
      return {
        label: id === 'global' ? 'SuperMe Hub' : r?.label ?? stats.archived[id] ?? id,
        color: r?.color ?? colorFor(id),
        icon: r?.icon ?? null,
        archived: gone,
      }
    },
    [stats.hub, stats.nodes, stats.archived],
  )


  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl p-6">
        <header className="mb-4 flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <Activity size={18} className="text-dev" />
          <h1 className="text-[17px] font-semibold text-fg">Activity</h1>
          <span className="text-[13px] text-faint">SuperMe agent runs · all repos · both scopes</span>
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
          <div ref={tableRef} className="overflow-hidden rounded-xl border border-line">
            {/* column header */}
            <div className={`${COLS[density]} border-b border-line bg-surface/60 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-faint`}>
              {density === 'tight' ? <span /> : <span>Repo</span>}
              <span>Op</span>
              {density === 'full' && <span>Scope</span>}
              {density !== 'tight' && <span>Model</span>}
              <span className="text-right">Tokens</span>
              {density === 'full' && <span className="text-right">Took</span>}
              <span className="text-right">When</span>
            </div>
            {runs.map((r, i) => (
              <RunRow key={r.id} r={r} meta={metaFor(r.repo_id)} density={density} last={i === runs.length - 1 && !hasMore} onOpen={() => setOpenRun(r)} />
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

      {openRun && (
        <RunTraceModal
          run={openRun}
          meta={metaFor(openRun.repo_id)}
          onClose={() => setOpenRun(null)}
          onDiagnose={
            // A diagnosis run is already an investigation, so diagnosing it nests without adding
            // signal.
            onDiagnose && openRun.feature !== 'diagnosis'
              ? (query) => { onDiagnose(openRun, query); setOpenRun(null) }
              : undefined
          }
        />
      )}
    </div>
  )
}

function RunRow({ r, meta, density, last, onOpen }: { r: Run; meta: { label: string; color: string; icon: string | null; archived?: boolean }; density: Density; last: boolean; onOpen: () => void }) {
  const isHub = r.repo_id === 'global'
  return (
    <button
      type="button"
      onClick={onOpen}
      title="View trace"
      className={`${COLS[density]} w-full bg-surface px-4 py-2.5 text-left transition hover:bg-hover ${last ? '' : 'border-b border-line'}`}
    >
      {/* Tight keeps the MARK and drops the name: a label truncated to one letter identifies
          nothing, the colour still does. */}
      <span
        className="flex min-w-0 items-center gap-2"
        title={meta.archived ? `${meta.label} — disconnected project` : meta.label}
      >
        {meta.icon && !isHub ? (
          <RepoIcon name={meta.icon} size={14} color={meta.color} className="shrink-0" />
        ) : (
          <span
            className="h-4 w-4 shrink-0 rounded-[4px]"
            style={isHub ? { backgroundImage: 'var(--grad-iris)' } : { backgroundColor: meta.color }}
          />
        )}
        {density !== 'tight' && (
          <>
            <span className={`truncate text-[13px] ${meta.archived ? 'text-muted italic' : 'text-fg'}`}>
              {meta.label}
            </span>
            {r.session_fate && (
              <span
                className="shrink-0 text-faint"
                title={`Origin session ${r.session_fate} — this run's trace is preserved`}
              >
                <Link2Off size={12} />
              </span>
            )}
          </>
        )}
      </span>
      {/* The feature chip plus the work-item PHASE: an interactive turn's feature is `chat`, so
          the phase is the real signal */}
      <span className="flex min-w-0 flex-col items-start gap-0.5">
        <span
          className="rounded px-1.5 py-0.5 text-[11px] font-medium"
          style={{ color: featureColor(r.feature), backgroundColor: 'rgb(var(--c-hover))' }}
        >
          {featureLabel(r.feature)}
        </span>
        {r.phase && <span className="px-0.5 text-[10px] lowercase text-faint">{r.phase}</span>}
      </span>
      {density === 'full' && <span className="text-[11px] text-faint">{r.mode}</span>}
      {density !== 'tight' && (
        <span className="truncate text-[11px] text-muted" title={r.model ?? undefined}>{r.model ? fmtModel(r.model) : '—'}</span>
      )}
      {/* `r.tokens` is ALREADY the display amount, so do not recompute it from typed columns that
          are not on the wire */}
      <span className="text-right font-mono text-[11px] text-muted">{fmtTokens(r.tokens)}</span>
      {density === 'full' && <span className="text-right font-mono text-[11px] text-faint">{took(r)}</span>}
      {/* Tight trades the stamp for an AGE; the stamp stays in the tooltip */}
      <span className="truncate text-right font-mono text-[11px] text-faint" title={fmtLocal(r.started_at)}>
        {density === 'tight' ? fmtAge(r.started_at) : fmtLocal(r.started_at)}
      </span>
    </button>
  )
}
