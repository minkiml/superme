import { useEffect, useState } from 'react'
import { Loader2, AlertTriangle } from 'lucide-react'
import { getRoadmap, type RoadmapBoard as Board, type BoardWave, type BoardItem } from '@/lib/api'
import { fmtLocal } from '@/lib/format'
import { STATUS_COLOR, STATUS_LABEL, Empty } from './common'

// The Roadmap board (S4) — GET /dev/roadmap rendered as a vertical spine: deliverables are SQUARE
// nodes, their waves are CIRCLE nodes strung on a connecting line top-to-bottom, each wave's live
// work-items listed beside it. Wave status is the curated glyph from roadmap.md; the rollup is
// computed live from the items. First-cut; iterate on feedback.

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

export default function RoadmapTab({ contextId }: { contextId: string }) {
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
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl p-6">
        <header className="mb-1 flex items-center gap-2.5">
          <h1 className="text-[17px] font-semibold text-fg">Roadmap</h1>
          <span className="text-[13px] text-faint">deliverable → wave → its work-items</span>
        </header>
        <p className="mb-6 text-[12px] text-faint">
          The plan and what's in motion — waves come from roadmap.md; progress is live from the work-items.
        </p>
        {err && <div className="mb-3 text-sm text-danger">Couldn’t load — {err}</div>}

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
    </div>
  )
}
