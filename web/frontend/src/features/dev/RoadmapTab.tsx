import { useEffect, useState } from 'react'
import { Loader2, AlertTriangle } from 'lucide-react'
import {
  getRoadmap,
  type RoadmapBoard as Board, type BoardWave, type BoardItem,
} from '@/lib/api'
import ProjectPortrait from './ProjectPortrait'
import { fmtLocal } from '@/lib/format'
import { STATUS_COLOR, STATUS_LABEL, Empty } from './common'

// The roadmap as a vertical spine: deliverables are square nodes, their waves circles strung on a
// line, with each wave's live items beside it.
//
// Wave status is the curated glyph from the doc; the rollup is computed live.

// Curated wave-status → the circle's fill/outline.
const WAVE_DOT: Record<string, string> = {
  done: 'bg-success',
  active: 'bg-dev',
  planned: 'bg-app border-2 border-line', // hollow = not started
}

// A node on the spine — square for a deliverable, circle for a wave. `ring-app` masks the line
// behind the marker so the spine reads as connecting between nodes, not through them.
function SquareNode() {
  return <span className="relative z-10 block h-4 w-4 rounded-[3px] bg-dev ring-4 ring-app" />
}
function CircleNode({ status }: { status?: string | null }) {
  return <span className={`relative z-10 block h-2.5 w-2.5 rounded-full ring-4 ring-app ${WAVE_DOT[status ?? 'planned'] ?? WAVE_DOT.planned}`} />
}

function Rollup({ done, total }: { done: number; total: number }) {
  if (!total) return <span className="text-[11px] text-faint">—</span>
  const pct = Math.round((done / total) * 100)
  const complete = done >= total
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-faint" title={`${done} of ${total} done`}>
      <span className="h-1.5 w-14 overflow-hidden rounded-full bg-hover">
        <span className={`block h-full rounded-full ${complete ? 'bg-success' : 'bg-dev'}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="tabular-nums">{done}/{total}</span>
    </span>
  )
}

// One work-item beside its wave — the "…." detail line.
function ItemLine({ it }: { it: BoardItem }) {
  const status = it.done_at ? 'done' : (it.status ?? '')
  return (
    <div className="mt-1 flex items-center gap-2 text-[12px]">
      <span className="min-w-0 truncate text-muted">{it.title || it.id}</span>
      <span className={`shrink-0 text-[11px] ${STATUS_COLOR[status] ?? 'text-faint'}`}>{STATUS_LABEL[status] ?? status}</span>
      {it.date && <span className="shrink-0 text-[11px] text-faint tabular-nums">{fmtLocal(String(it.date))}</span>}
    </div>
  )
}

function WaveRow({ w }: { w: BoardWave }) {
  return (
    <div className="flex items-start gap-3 py-1.5">
      <div className="flex w-[26px] shrink-0 justify-center pt-1"><CircleNode status={w.status} /></div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="min-w-0 truncate text-[13px] font-medium text-fg">{w.title}</span>
          <span className="shrink-0 font-mono text-[10px] text-faint">{w.id}</span>
          <span className="ml-auto shrink-0"><Rollup done={w.rollup.done} total={w.rollup.total} /></span>
        </div>
        {w.items.map((it) => <ItemLine key={it.id} it={it} />)}
      </div>
    </div>
  )
}

// The Roadmap board proper — the forward-only plan, one of the two views under Project.
function RoadmapBoardView({ contextId }: { contextId: string }) {
  const [board, setBoard] = useState<Board | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setBoard(null)
    getRoadmap(contextId)
      .then((b) => alive && setBoard(b))
      .catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [contextId])

  return (
      <div className="mx-auto max-w-2xl p-6">
        {err && <div className="mb-3 text-sm text-danger">Couldn’t load — {err}</div>}

        <h2 className="mb-2 text-[13px] font-semibold uppercase tracking-wider text-muted">Roadmap</h2>

        {board === null ? (
          <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>
        ) : board.deliverables.length === 0 ? (
          <Empty>No project memory yet — establish it first (project-init for a new repo, retrofit for existing code).</Empty>
        ) : (
          <div className="relative pl-1">
            {/* the continuous spine */}
            <div className="absolute bottom-4 left-[14px] top-4 w-px bg-line" />
            {board.deliverables.map((d) => (
              <div key={d.id}>
                {/* deliverable — square node */}
                <div className="flex items-center gap-3 py-2">
                  <div className="flex w-[26px] shrink-0 justify-center"><SquareNode /></div>
                  <span className="min-w-0 truncate text-[14px] font-semibold text-fg">{d.title}</span>
                  <span className="shrink-0 font-mono text-[10px] text-faint">{d.id}</span>
                  <span className="ml-auto shrink-0"><Rollup done={d.rollup.done} total={d.rollup.total} /></span>
                </div>
                {/* what the owner GETS from this deliverable — the reason it exists, when declared */}
                {d.value && (
                  <div className="flex gap-3 pb-1">
                    <div className="w-[26px] shrink-0" />
                    <span className="text-[12px] italic text-muted">{d.value}</span>
                  </div>
                )}
                {d.waves.length === 0 && d.items.length === 0 ? (
                  <div className="flex gap-3 pb-1">
                    <div className="w-[26px] shrink-0" />
                    <span className="text-[12px] italic text-faint">no SuperMe-tracked work yet</span>
                  </div>
                ) : (
                  <>
                    {d.waves.map((w) => <WaveRow key={w.id} w={w} />)}
                    {d.items.map((it) => (
                      <div key={it.id} className="flex items-start gap-3 py-1.5">
                        <div className="flex w-[26px] shrink-0 justify-center pt-1"><CircleNode status={null} /></div>
                        <div className="min-w-0 flex-1"><ItemLine it={it} /></div>
                      </div>
                    ))}
                  </>
                )}
              </div>
            ))}
          </div>
        )}

        {board && board.orphans.length > 0 && (
          <div className="mt-6 rounded-lg border border-warn/40 bg-warn/5 p-3">
            <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-medium text-warn">
              <AlertTriangle size={13} /> Referential-integrity issues
            </div>
            <ul className="space-y-1 text-[11.5px] text-muted">
              {board.orphans.map((o, i) => (
                <li key={i} className="font-mono">
                  {o.reason}: {o.wave ? `wave ${o.wave}` : ''}{o.deliverable ? ` → deliverable ${o.deliverable}` : ''}
                  {o.items?.length ? ` (${o.items.join(', ')})` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}

      </div>
  )
}

// The general knowledge this project runs on, in two views: the PORTRAIT is the front door, the
// ROADMAP sits beside it.
const VIEWS = [
  { key: 'portrait', label: 'Overview', hint: 'what this project is' },
  { key: 'roadmap', label: 'Roadmap', hint: "what's coming" },
] as const

export default function RoadmapTab({ contextId }: { contextId: string }) {
  const [view, setView] = useState<'portrait' | 'roadmap'>('portrait')

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-6 py-2.5">
        <div className="ml-auto flex gap-1 rounded-md border border-line bg-surface p-0.5">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              onClick={() => setView(v.key)}
              className={`rounded px-2.5 py-1 text-[11.5px] font-medium transition ${
                view === v.key ? 'bg-hover text-fg' : 'text-muted hover:text-fg'}`}
            >
              {v.label}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {view === 'portrait'
          ? <ProjectPortrait contextId={contextId} />
          : <RoadmapBoardView contextId={contextId} />}
      </div>
    </div>
  )
}
