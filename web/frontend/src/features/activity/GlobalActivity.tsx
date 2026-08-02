import { useCallback, useMemo, useRef, useState } from 'react'
import { Activity, RefreshCw, Loader2, ChevronDown, Link2Off } from 'lucide-react'
import { colorFor, featureColor, featureLabel } from '@/lib/palette'
import { RepoIcon } from '@/lib/repoIcons'
import { fmtTokens, fmtLocal, fmtModel, fmtDuration } from '@/lib/format'
import { getRuns, type Run } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
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

export default function GlobalActivity({
  stats,
  onDiagnose,
}: {
  stats: CommandStats
  onDiagnose?: (run: Run, query: string) => void
}) {
  const [limit, setLimit] = useState(PAGE) // grows by PAGE each "Load more"; the fetch pulls this many
  const [openRun, setOpenRun] = useState<Run | null>(null)

  // `limit` is part of the cache key: growing the page is a different request, and the previous
  // page's data stays cached under its own key (so collapsing back is instant).
  const feed = useLive(K.runs(limit), () => getRuns(undefined, limit))
  const loading = feed.loading
  const err = feed.data ? null : feed.error ? String(feed.error) : null

  // `history` (recent_runs) already includes in-flight runs, and `live` repeats them — so the naive
  // [...live, ...history] double-lists a running run (same id → duplicate React key, which also
  // breaks reconciliation so the stale row lingered until a full reload). Dedup by id, live first
  // so the running row wins.
  const rows: Run[] | null = useMemo(() => {
    const d = feed.data
    if (!d) return null
    const seen = new Set<number>()
    return [...d.live, ...d.history].filter((r) => !seen.has(r.id) && seen.add(r.id))
  }, [feed.data])
  // KEEP THE LAST GOOD PAGE ON SCREEN while a bigger one loads. `limit` is part of the cache key, so
  // growing the page is a cache MISS: `feed.data` went undefined, the whole table unmounted for the
  // "Loading…" spinner, and remounting reset the scroll container to the top — the owner clicked
  // "Load 30 more" and got thrown back to row 1. Rows are keyed by id, so React reuses the existing
  // DOM and only appends; the button's own spinner is the loading signal.
  const lastGood = useRef<Run[] | null>(null)
  if (rows) lastGood.current = rows
  const runs = rows ?? lastGood.current
  // A full page of history back means there may be more to fetch.
  const hasMore = (feed.data?.history.length ?? 0) >= limit

  // Live roster first; a run whose repo was disconnected falls through to its tombstoned label
  // (so history keeps reading "Dummy Project", not the bare id) and is marked as gone.
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

  const load = feed.refresh

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

      {openRun && (
        <RunTraceModal
          run={openRun}
          meta={metaFor(openRun.repo_id)}
          onClose={() => setOpenRun(null)}
          onDiagnose={
            // A diagnosis run is already a read-only investigation — offering to diagnose it
            // recursively nests without adding signal, so suppress the affordance on those rows.
            onDiagnose && openRun.feature !== 'diagnosis'
              ? (query) => { onDiagnose(openRun, query); setOpenRun(null) }
              : undefined
          }
        />
      )}
    </div>
  )
}

function RunRow({ r, meta, last, onOpen }: { r: Run; meta: { label: string; color: string; icon: string | null; archived?: boolean }; last: boolean; onOpen: () => void }) {
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
        <span
          className={`truncate text-[13px] ${meta.archived ? 'text-muted italic' : 'text-fg'}`}
          title={meta.archived ? `${meta.label} — disconnected project` : meta.label}
        >
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
      </span>
      {/* Op = the feature chip + the work-item PHASE beneath it. An interactive triage/build/review
          turn is feature `chat`; the phase is the real signal, so surface it (else the feed reads
          all-`chat` and hides the pipeline work). Phase is absent on non-item runs. */}
      <span className="flex min-w-0 flex-col items-start gap-0.5">
        <span
          className="rounded px-1.5 py-0.5 text-[11px] font-medium"
          style={{ color: featureColor(r.feature), backgroundColor: 'rgb(var(--c-hover))' }}
        >
          {featureLabel(r.feature)}
        </span>
        {r.phase && <span className="px-0.5 text-[10px] lowercase text-faint">{r.phase}</span>}
      </span>
      <span className="text-[11px] text-faint">{r.mode}</span>
      <span className="truncate text-[11px] text-muted" title={r.model ?? undefined}>{r.model ? fmtModel(r.model) : '—'}</span>
      {/* `r.tokens` is ALREADY the 3-type display amount — `spine._run_dict` overrides it through
          `_display_tokens` (input + cache_write + output, excluding cache_read) so every
          run-returning surface reconciles with the token dashboard's default. Do NOT recompute it
          here from typed columns: they are not on the wire, and a second definition of one number is
          how the two surfaces drift apart. */}
      <span className="text-right font-mono text-[11px] text-muted">{fmtTokens(r.tokens)}</span>
      <span className="text-right font-mono text-[11px] text-faint">{took(r)}</span>
      <span className="text-right font-mono text-[11px] text-faint">{fmtLocal(r.started_at)}</span>
    </button>
  )
}
