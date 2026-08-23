import { useState, useEffect } from 'react'
import { CornerDownRight, GitBranch, Loader2 } from 'lucide-react'
import { type WorkItem } from '@/lib/api'
import { fmtTokens, fmtDuration, fmtModel } from '@/lib/format'
import { PHASE_VERB, STATUS_COLOR, STATUS_LABEL, STATUS_STRIPE, primaryStatus, agoLabel, researchKindLabel, workKindLabel, kindChipClass } from '../common'
import { useContainerWidth } from '@/lib/layout'
import { useAuthGate } from '@/lib/authGate'

// The board: every live work item as a card, grouped by what it is waiting on.

// The badge answers "what is this item doing", so a live run wins over the status word.
//
// `bucket` is the item's attention tier, which is where the parked-at-a-gate verdict comes from.
export function StatusBadge({ it, running, bucket }: { it: WorkItem; running?: boolean; bucket?: string }) {
  const s = primaryStatus(it, bucket)
  const live = running && s !== 'done' ? PHASE_VERB[it.phase ?? ''] : null
  return (
    <span className={`shrink-0 whitespace-nowrap rounded bg-hover px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
      live ? 'text-success' : STATUS_COLOR[s] ?? 'text-faint'
    }`}>
      {live ? `${live}…` : STATUS_LABEL[s] ?? '—'}
    </span>
  )
}

// Branch-off provenance: a child shows where it came from; a parent shows its branch count.
function BranchInfo({ it }: { it: WorkItem }) {
  return (
    <>
      {it.parent_id && (
        <span className="inline-flex items-center gap-0.5 text-[10px] text-muted" title={`branched off ${it.parent_id}`}>
          <CornerDownRight size={11} /> {it.parent_id}
        </span>
      )}
      {it.children.length > 0 && (
        <span className="inline-flex items-center gap-0.5 text-[10px] text-muted" title={`${it.children.length} branch-off(s)`}>
          <GitBranch size={11} /> {it.children.length}
        </span>
      )}
    </>
  )
}

// An item is on the active board until it terminates (status done / done_at — completed,
// abandoned, or superseded). Terminal items leave the interface; their trace stays.
export const isActive = (it: WorkItem) => !it.done_at && it.status !== 'done'

// Actions a board surface can offer per card. `bind` opens the item in the chat (dev);
// `plan` fires a background /plan turn; `running` is the set of ids currently planning.
export type WorkActions = {
  onOpen?: (it: WorkItem) => void // open the review popup (card click)
  onBind?: (it: WorkItem) => void // bind the chat to the item (review popup's "Discuss")
  onPlan?: (it: WorkItem, model?: string) => void
  // Re-fire a STOPPED item's run: its next act is unambiguous. Every other control lives in the
  // popup.
  onResume?: (it: WorkItem) => void
  running?: string[]
  boundItemId?: string | null
}

// --- workspace: kanban by phase group -------------------------------------------

// The union pipeline collapsed to FOUR columns, so the whole board fits one view.
//
// The middle column is labelled for the STAGE, not the machinery, and the lane dots carry NO
// COLOUR.
const KANBAN_GROUPS: { key: string; label: string; phases: string[] }[] = [
  { key: 'intake', label: 'Triage & Plan', phases: ['triage', 'plan'] },
  { key: 'work', label: 'Work', phases: ['build', 'investigate', 'vet', 'report'] },
  { key: 'review', label: 'Review', phases: ['review'] },
  { key: 'close', label: 'Close', phases: ['close'] },
]

export function WorkspaceKanban({ items, onOpen, onResume, running, boundItemId, buckets }: { items: WorkItem[]; buckets?: Record<string, string> } & WorkActions) {
  const visible = items.filter(isActive)
  const [boardRef, boardW] = useContainerWidth<HTMLDivElement>()
  // No whole-board empty state: the columns are the pipeline's shape, and every one already renders
  // its own dash.
  // Running first, then newest finish down: the top of a column is what moved last.
  const inGroup = (phases: string[]) =>
    visible
      .filter((it) => phases.includes(it.phase ?? 'triage'))
      .sort(
        (a, b) =>
          Number(running?.includes(b.id) ?? false) - Number(running?.includes(a.id) ?? false) ||
          (b.last_run?.ended_at ?? 0) - (a.last_run?.ended_at ?? 0),
      )
  // The lanes REFLOW: a lane past the edge is a stage the owner cannot see.
  const lanes = boardW === 0 || boardW >= 716 ? 4 : boardW >= 552 ? 2 : 1
  // The board knows how wide a lane came out, so it tells the card how much to say.
  const laneW = lanes ? (boardW - 12 * (lanes - 1)) / lanes : 0
  const tightCards = laneW > 0 && laneW < 215
  return (
    <div ref={boardRef}>
      <div
        className="grid gap-3"
        style={{ gridTemplateColumns: `repeat(${lanes}, minmax(0, 1fr))` }}
      >
        {KANBAN_GROUPS.map((g) => {
        const col = inGroup(g.phases)
        return (
          <div key={g.key} className="flex min-h-[7rem] flex-col rounded-xl bg-sunken p-2">
            <div className="mb-2 flex items-center gap-2 px-1">
              <span className="h-1.5 w-1.5 rounded-full bg-line" />
              <span className="truncate text-[11px] font-semibold uppercase tracking-wide text-fg" title={g.label}>{g.label}</span>
              <span className="ml-auto rounded-full bg-hover px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-muted">{col.length}</span>
            </div>
            <div className="space-y-2">
              {col.length === 0 ? (
                <div className="px-1.5 py-3 text-center text-[11px] text-faint">—</div>
              ) : (
                col.map((it) => (
                  <WorkCard
                    key={it.id}
                    it={it}
                    onOpen={onOpen}
                    planning={running?.includes(it.id)}
                    bound={boundItemId === it.id}
                    bucket={buckets?.[it.id]}
                    onResume={onResume}
                    tight={tightCards}
                  />
                ))
              )}
            </div>
          </div>
        )
      })}
      </div>
    </div>
  )
}

function WorkCard({
  it, onOpen, planning, bound, bucket, onResume, tight,
}: {
  it: WorkItem
  onOpen?: (it: WorkItem) => void
  planning?: boolean
  bound?: boolean
  bucket?: string // attention tier: error | needs_you | deputy_working | running | unread — tints the card ring
  onResume?: (it: WorkItem) => void // only ever called for a STOPPED card (see below)
  tight?: boolean // the lane is too narrow for the full four rows — see below
}) {
  // Resuming re-fires the run, so with no credential the card says why instead.
  const { reason: authReason } = useAuthGate()
  const clickable = !!onOpen
  const running = !!planning || !!it.running
  // A fixed four-row card; live values win while a run is in flight. A pure glance and entry point.
  const model = (running ? it.run_model : null) ?? it.model
  const ctx = running ? it.run_ctx_pct : it.ctx_pct
  const tokens = running ? it.run_tokens : it.total_tokens
  const hasTokens = (tokens ?? 0) > 0
  const settledTime = it.last_run?.duration_ms != null ? fmtDuration(it.last_run.duration_ms) : null
  const showMeter = running || hasTokens || !!settledTime
  // A live run wins over the status word, the same rule the badge and ring follow.
  const liveNow = running && primaryStatus(it, bucket) !== 'done'
  const stripe = liveNow ? 'border-l-success'
    : STATUS_STRIPE[primaryStatus(it, bucket)] ?? 'border-l-line'
  const stopped = primaryStatus(it, bucket) === 'error'
  // The card carries its bucket colour as a soft ring. Unread applies to terminal items, which live
  // off-board.
  const attnRing = bucket === 'error' ? 'ring-1 ring-danger/80'
    : bucket === 'needs_you' ? 'ring-1 ring-warn/70'
    : bucket === 'deputy_working' ? 'ring-1 ring-deputy/60'
    : bucket === 'running' ? 'ring-1 ring-success/60' : ''
  return (
    <div
      onClick={clickable ? () => onOpen!(it) : undefined}
      title={clickable ? 'Open review + chat for this work-item' : undefined}
      className={`flex flex-col gap-1 rounded-md border border-line border-l-2 bg-surface px-2.5 py-2 shadow-sm ${
        bound ? 'border-l-accent ring-2 ring-accent' : stripe
      } ${bound ? '' : attnRing} ${clickable ? 'cursor-pointer transition hover:border-accent hover:shadow-md' : ''}`}
    >
      {/* 1 · status (+ branch provenance) */}
      <div className="flex min-w-0 items-center gap-1.5">
        <StatusBadge it={it} running={running} bucket={bucket} />
        {/* One chip, one slot: the research FAMILY where there is one, else the work kind. */}
        {!tight && (researchKindLabel(it.research_kind) || workKindLabel(it.kind)) && (
          <span className={`min-w-0 truncate rounded px-1 py-px text-[9.5px] font-medium uppercase tracking-wide ${kindChipClass(it.kind)}`}>
            {researchKindLabel(it.research_kind) || workKindLabel(it.kind)}
          </span>
        )}
        <span className="ml-auto shrink-0 flex items-center gap-1.5"><BranchInfo it={it} /></span>
      </div>
      {/* 2 · name — one line, ellipsis when long */}
      <div className="truncate text-[12.5px] leading-snug text-fg" title={it.title}>{it.title}</div>
      {/* Narrow, one unlabelled row: the units are obvious from their shape, and every dropped
          label is in the drilldown. */}
      {tight && (ctx != null || hasTokens || agoLabel(it.last_run?.ended_at)) && (
        <div className="flex items-center gap-1.5 whitespace-nowrap text-[10.5px] text-muted">
          {running && <Loader2 size={10} className="animate-spin text-accent-text" />}
          {ctx != null && <span className="tabular-nums" title={`Context ${ctx}% full`}>{ctx}%</span>}
          {hasTokens && (
            <>
              {ctx != null && <span className="text-faint">·</span>}
              <span className="tabular-nums" title="Tokens used (3-type basis)">{fmtTokens(tokens ?? 0)}</span>
            </>
          )}
          {running
            ? <><span className="text-faint">·</span><LiveTimer startedAt={it.run_started_at} /></>
            : agoLabel(it.last_run?.ended_at) && (
              <>
                <span className="text-faint">·</span>
                <span className="text-faint" title="When this item's last run finished">{agoLabel(it.last_run?.ended_at)}</span>
              </>
            )}
        </div>
      )}
      {/* 3 · model · ctx */}
      {!tight && (model || ctx != null) && (
        <div className="flex items-center gap-1.5 text-[10.5px] text-muted">
          {model && <span className="truncate">{fmtModel(model)}</span>}
          {model && ctx != null && <span className="text-faint">·</span>}
          {ctx != null && <span className="tabular-nums">ctx {ctx}%</span>}
        </div>
      )}
      {/* 4 · tokens · time — always on once this phase's run has started */}
      {!tight && showMeter && (
        <div className="flex items-center gap-1.5 text-[10.5px] text-faint">
          {running && <Loader2 size={10} className="animate-spin text-accent-text" />}
          <span className="tabular-nums" title="Tokens used (3-type basis) — this run while live, else the item total">
            {hasTokens ? `${fmtTokens(tokens ?? 0)} tok` : '—'}
          </span>
          <span>·</span>
          {running
            ? <LiveTimer startedAt={it.run_started_at} />
            : <span className="tabular-nums" title="Duration of the last run">{settledTime ?? '—'}</span>}
          {/* How long since it moved, only when nothing runs: two clocks on one row invite
              comparing them. */}
          {!running && agoLabel(it.last_run?.ended_at) && (
            <>
              <span>·</span>
              <span title="When this item's last run finished">{agoLabel(it.last_run?.ended_at)}</span>
            </>
          )}
        </div>
      )}
      {/* The ONE action a card carries: a STOPPED item's next act is unambiguous and should not
          cost a click. */}
      {stopped && onResume && (
        <button
          onClick={(e) => { e.stopPropagation(); onResume(it) }}
          disabled={!!authReason}
          title={authReason ?? (it.error_reason ? `Re-fire the run that stopped — ${it.error_reason}` : 'Re-fire the run that stopped')}
          className="mt-0.5 self-start rounded border border-danger/50 px-1.5 py-0.5 text-[10.5px] font-medium text-danger transition hover:bg-danger/10 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
        >
          Resume
        </button>
      )}
    </div>
  )
}

// A ticking elapsed timer from an epoch-seconds start (the live "time taken" while running).
function LiveTimer({ startedAt }: { startedAt?: number | null }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  if (!startedAt) return null
  return <span className="tabular-nums">{fmtDuration(now - startedAt * 1000)}</span>
}

// --- inbox ----------------------------------------------------------------------
