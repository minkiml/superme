import { useCallback, useEffect, useRef, useState } from 'react'
import { Sparkles, Brain } from 'lucide-react'
import Modal from '@/ui/Modal'
import { getProposals, getMemoryStats, runDistill, type MemoryProposal, type MemoryStats } from '@/lib/api'
import { fmtLocalDate } from '@/lib/format'
import { Empty } from '@/features/dev/common'
import { ReviewQueue } from './ReviewQueue'
import { AgentWorking } from './bits'

// The memory surface: what has been learned, and what is waiting to be judged.

export function MemoryGovernance({ contextId }: { contextId: string }) {
  const [props, setProps] = useState<MemoryProposal[]>([])
  const [stats, setStats] = useState<MemoryStats | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [distilling, setDistilling] = useState(false)
  const [popup, setPopup] = useState<null | 'candidates' | 'knowledge'>(null)
  const poll = useRef<ReturnType<typeof setInterval> | null>(null)

  // The poll loop is created before `load` is defined, so a ref breaks that cycle.
  const loadRef = useRef<() => void>(() => {})

  // Guarded so a remount cannot spawn a second interval.
  const startPolling = useCallback(() => {
    if (poll.current) return
    const started = Date.now()
    poll.current = setInterval(async () => {
      try {
        const s = await getMemoryStats(contextId)
        setStats(s)
        if (!s.distilling || Date.now() - started > 180_000) {
          if (poll.current) { clearInterval(poll.current); poll.current = null }
          setDistilling(false)
          loadRef.current()
        }
      } catch {
        /* transient — keep polling until the timeout */
      }
    }, 1500)
  }, [contextId])

  const load = useCallback(() => {
    Promise.all([getProposals(contextId), getMemoryStats(contextId)])
      .then(([p, s]) => {
        // Keep every OPEN proposal in the queue: at gate 1, mid-write, or at gate 2. Terminal ones
        // drop off.
        setProps(p.proposals.filter((x) => ['proposed', 'writing', 'drafted'].includes(x.status)))
        setStats(s)
        setErr(null)
        // Re-sync to server truth: a remount mid-run restores the spinner and resumes polling.
        if (s.distilling) { setDistilling(true); startPolling() }
        else setDistilling(false)
      })
      .catch((e) => setErr(String(e)))
  }, [contextId, startPolling])

  useEffect(() => { loadRef.current = load }, [load])

  useEffect(() => {
    load()
  }, [load])

  // Stop polling if the component unmounts mid-distill.
  useEffect(() => () => { if (poll.current) { clearInterval(poll.current); poll.current = null } }, [])

  // Fire the background pass, then let the server-truth poll drive the spinner until it clears.
  const onDistill = async () => {
    try {
      const r = await runDistill(contextId)
      if (r.status === 'no_candidates') return
      setDistilling(true)
      startPolling()
    } catch (e) {
      setErr(String(e))
    }
  }

  // Proposals land from a background turn while this stays mounted, and there is no manual refresh.
  useEffect(() => {
    const refetch = () => load()
    const onVisible = () => document.visibilityState === 'visible' && load()
    window.addEventListener('focus', refetch)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.removeEventListener('focus', refetch)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load])

  // While any proposal is mid-forge, poll so its card narrates the live phase and flips when it
  // finishes.
  const anyWriting = props.some((p) => p.status === 'writing')
  useEffect(() => {
    if (!anyWriting) return
    const t = setInterval(() => loadRef.current(), 2500)
    return () => clearInterval(t)
  }, [anyWriting])

  const candCount = stats?.candidates.total ?? 0
  const knowCount = (stats?.knowledge.facts_total ?? 0) + (stats?.knowledge.artifacts_total ?? 0)

  return (
    <div>
      {/* Stat tiles — pipeline gauges. Click a tile for the drill-down popup. */}
      {/* Stacked when the pane cannot hold both: a tile squeezed until its label truncates is not
          a smaller tile. */}
      <div className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(210px,1fr))] gap-3">
        <StatTile
          icon={Sparkles}
          label="Candidates to distill"
          value={candCount}
          sub={`${stats?.candidates.pending_proposals ?? 0} pending proposal(s)`}
          onClick={() => stats && setPopup('candidates')}
        />
        <StatTile
          icon={Brain}
          label="Learned knowledge"
          value={knowCount}
          sub={`${stats?.knowledge.facts_enabled ?? 0} on · ${stats?.knowledge.facts_disabled ?? 0} off · ${stats?.knowledge.artifacts_total ?? 0} artifacts`}
          onClick={() => stats && setPopup('knowledge')}
        />
      </div>

      <div className="mb-4 flex items-center gap-2">
        <span className="text-xs text-faint">{props.length} pending</span>
        <button
          onClick={onDistill}
          disabled={distilling || candCount === 0}
          title={candCount === 0 ? 'No candidates to distill' : 'Run distill over the candidate pool'}
          className="ml-auto inline-flex items-center gap-1 rounded-md border border-accent/40 bg-accent/10 px-2 py-1 text-[12px] text-accent-text transition hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {distilling ? (
            <AgentWorking size={13}>Distilling…</AgentWorking>
          ) : (
            <><Sparkles size={13} /> Run distill</>
          )}
        </button>
      </div>

      {popup && stats && (
        <StatPopup kind={popup} stats={stats} onClose={() => setPopup(null)} />
      )}

      {err && (
        <div className="mb-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {err}
        </div>
      )}

      {/* The review queue is the whole surface; the live inventory lives in the Published tab. */}
      {props.length > 0 ? (
        <ReviewQueue proposals={props} contextId={contextId} onChange={load} />
      ) : (
        <Empty>No proposals in review. Run distill over the candidate pool to generate some.</Empty>
      )}
    </div>
  )
}

// --- stat tiles + drill-down popup ----------------------------------------------------------

function StatTile({
  icon: Icon,
  label,
  value,
  sub,
  onClick,
}: {
  icon: typeof Brain
  label: string
  value: number
  sub: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="group flex items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-left shadow-sm transition hover:border-accent hover:bg-hover"
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent-text">
        <Icon size={17} />
      </div>
      <div className="min-w-0">
        <div className="flex items-baseline gap-1.5">
          <span className="text-xl font-semibold text-fg">{value}</span>
          <span className="truncate text-[12px] text-muted">{label}</span>
        </div>
        <div className="truncate text-[11px] text-faint">{sub}</div>
      </div>
    </button>
  )
}

function Chips({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, n]) => n > 0)
  if (!entries.length) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([k, n]) => (
        <span key={k} className="rounded-full bg-hover px-2 py-0.5 text-[11px] text-muted">
          {k} <span className="text-faint">· {n}</span>
        </span>
      ))}
    </div>
  )
}

function StatPopup({
  kind,
  stats,
  onClose,
}: {
  kind: 'candidates' | 'knowledge'
  stats: MemoryStats
  onClose: () => void
}) {
  const isCand = kind === 'candidates'
  return (
    <Modal
      onClose={onClose}
      maxW="max-w-2xl"
      title={
        <span className="flex items-center gap-2">
          {isCand ? <Sparkles size={16} className="text-accent-text" /> : <Brain size={16} className="text-accent-text" />}
          {isCand ? 'Candidates to distill' : 'Learned knowledge'}
        </span>
      }
    >
        <div className="max-h-[70vh] space-y-3 overflow-y-auto px-4 py-3">
          {isCand ? (
            <>
              <div className="flex items-center gap-3 text-[12px] text-muted">
                <span><b className="text-fg">{stats.candidates.total}</b> un-distilled</span>
                <span>·</span>
                <span><b className="text-fg">{stats.candidates.pending_proposals}</b> pending proposal(s)</span>
              </div>
              <Chips counts={stats.candidates.by_form} />
              {stats.candidates.items.length === 0 ? (
                <Empty>The capture pool is empty. Nothing to distill.</Empty>
              ) : (
                <div className="space-y-1.5">
                  {stats.candidates.items.map((c) => (
                    <div key={c.id} className="rounded-lg bg-surface px-3 py-2 shadow-sm">
                      <div className="text-[13px] text-fg">{c.signal}</div>
                      <div className="mt-1 flex items-center gap-2 text-[10px] text-faint">
                        {c.form_hint && <span className="rounded bg-hover px-1.5 py-0.5">{c.form_hint}</span>}
                        {c.scope_hint && <span className="rounded bg-hover px-1.5 py-0.5">{c.scope_hint}</span>}
                        {c.source && <span>{c.source}</span>}
                        {c.captured_at && <span>· {fmtLocalDate(c.captured_at)}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3 text-[12px] text-muted">
                <span><b className="text-fg">{stats.knowledge.facts_total}</b> facts</span>
                <span>·</span>
                <span>{stats.knowledge.facts_enabled} on · {stats.knowledge.facts_disabled} off</span>
                <span>·</span>
                <span>{stats.knowledge.artifacts_total} artifacts {stats.knowledge.artifacts_reserved && <span className="text-faint">(reserved)</span>}</span>
              </div>
              <Chips counts={stats.knowledge.facts_by_type} />
              {stats.knowledge.items.length === 0 ? (
                <Empty>No applied facts yet.</Empty>
              ) : (
                <div className="space-y-1.5">
                  {stats.knowledge.items.map((f) => (
                    <div key={f.name} className={`rounded-lg bg-surface px-3 py-2 shadow-sm ${f.enabled ? '' : 'opacity-50'}`}>
                      <div className="flex items-baseline gap-2">
                        <span className="truncate text-[13px] text-fg">{f.description || f.name}</span>
                        <span className="ml-auto shrink-0 rounded bg-hover px-1.5 py-0.5 text-[10px] text-muted">{f.type}</span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 font-mono text-[10px] text-faint">
                        <span>{f.name}</span>
                        {f.source && <span>· {f.source}</span>}
                        {!f.enabled && <span>· disabled</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
    </Modal>
  )
}

// --- the tier-C review queue (moved from DevMemory — the gate lives in Manage Harness) -------
