import { useState, useEffect, type ReactNode } from 'react'
import { Plus, Trash2, CornerDownRight, GitBranch, ArrowRight, X, Bot, User, Loader2, MessageSquareText, MessagesSquare } from 'lucide-react'
import Dropdown from '@/ui/Dropdown'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import SectionHeader from '@/ui/SectionHeader'
import TabBar from '@/ui/TabBar'
import Toggle from '@/ui/Toggle'
import { useEditGate, EditActions } from '@/ui/EditGate'
import { addInbox, updateInbox, deleteInbox, pushInbox, getRepos, getSystem, getInboxBrief, saveInboxBrief,
         type WorkItem, type InboxEntry, type InboxKind, type InboxBrief } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { fmtLocal, fmtTokens, fmtDuration, fmtModel, toModelKey, MODELS as MODEL_CATALOG, DEFAULT_MODEL, EFFORTS as EFFORT_CATALOG, DEFAULT_EFFORT } from '@/lib/format'
import { PHASE_LABEL, PHASE_VERB, STATUS_COLOR, STATUS_LABEL, STATUS_STRIPE, primaryStatus,
         agoLabel, researchKindLabel, KIND_TEXT, workKindLabel } from './common'
import { useContainerWidth } from '@/lib/layout'

// Phase accent to a literal dot class — Tailwind needs the full string present in source.

// The catalog's concrete ids; the daemon normalizes any value to the latest at consumption.
export const RUN_MODELS = MODEL_CATALOG.map((m) => ({ value: m.key, label: m.label }))
export const DEFAULT_RUN_MODEL = DEFAULT_MODEL
// Reasoning-effort levels selectable per run, alongside the model. Default "medium".
export const RUN_EFFORTS = EFFORT_CATALOG.map((e) => ({ value: e.key, label: e.label }))
export const DEFAULT_RUN_EFFORT = DEFAULT_EFFORT
// WHO runs, as data: a row's config is a model and an effort PER ROLE, because vet and the deputy
// deliberately do not run on what the work runs on. The Setting tab is a loop over this list.
export const RUN_ROLES = [
  { key: '', label: 'Work', hint: 'Every phase run of this item' },
  { key: 'vet', label: 'Vet', hint: 'Checks what build produced' },
  { key: 'deputy', label: 'Deputy', hint: 'Judges the gates' },
] as const
export type RunRole = (typeof RUN_ROLES)[number]['key']
/** A role's field name on the row: the work role owns the bare keys, the rest are prefixed. */
export const roleField = (role: RunRole, f: 'model' | 'effort') =>
  (role ? `${role}_${f}` : f) as 'model' | 'effort' | 'vet_model' | 'vet_effort' | 'deputy_model' | 'deputy_effort'
/** What each role runs when this row says nothing — the chain's answer, resolved by the caller. */
export type RoleDefaults = Partial<Record<RunRole, { model: string; effort: string }>>
// The PROPOSED kind. "Undecided" is a real answer — triage judges alone — so it leads the list.
export const WORK_KIND_OPTS = [
  { value: '', label: 'Undecided' },
  { value: 'implementation', label: 'Implementation' },
  { value: 'research', label: 'Research' },
]

// Content and setting travel together, because the card has one Save. `model` and `effort` are
// always concrete.
type InboxConfigPatch = {
  title: string | null
  text: string
  kind: InboxKind
  model: string
  effort: string
  autopilot: boolean
  work_kind: string
  // The two roles that do NOT run on this item's model, which is why these carry an empty option.
  vet_model: string
  vet_effort: string
  deputy_model: string
  deputy_effort: string
}

// Shared dev-knowledge store views — the bodies for the board and the capture queue.
//
// `phase` is the per-kind pipeline; `status` the runnable axis, with `done` derived.

// --- status / branch-off chrome -------------------------------------------------

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
  const inGroup = (phases: string[]) => visible.filter((it) => phases.includes(it.phase ?? 'triage'))
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
        {/* Research only: a card is centimetres wide, and the FAMILY is the half that carries
            information. */}
        {!tight && researchKindLabel(it.research_kind) && (
          <span className="min-w-0 truncate rounded bg-kind-research/10 px-1 py-px text-[9.5px] font-medium uppercase tracking-wide text-kind-research">
            {researchKindLabel(it.research_kind)}
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
          title={it.error_reason ? `Re-fire the run that stopped — ${it.error_reason}` : 'Re-fire the run that stopped'}
          className="mt-0.5 self-start rounded border border-danger/50 px-1.5 py-0.5 text-[10.5px] font-medium text-danger transition hover:bg-danger/10"
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

// Two kinds, differing in what the row can DO: an `item` becomes a work-item, a `note` never is.
const KIND_OPTS = [
  { value: 'item', label: 'item' },
  { value: 'note', label: 'note' },
]
const KIND_COLUMNS: { kind: InboxKind; label: string; dot: string; blurb: string }[] = [
  { kind: 'item', label: 'Items', dot: 'bg-warn',
    blurb: 'Becomes a work-item when you push it' },
  { kind: 'note', label: 'Notes', dot: 'bg-dev',
    blurb: 'Yours — never pushed. Ask about one in a general session' },
]
// Left-edge stripe per kind (mirrors the work-card status stripe) — literal classes for Tailwind.
const KIND_STRIPE: Record<string, string> = {
  item: 'border-l-warn',
  note: 'border-l-dev',
}

// The inbox is the active capture queue, laid out as columns by kind. Resolving an item clears
// it from view (kept in the DB). Quick-capture drops into whichever kind is selected.
export function InboxView({
  entries,
  contextId,
  onChanged,
  onDiscussNote,
}: {
  entries: InboxEntry[]
  contextId: string
  onDiscussNote?: (inboxId: number, title: string) => void
  onChanged: () => void
}) {
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState<InboxKind>('item')
  const [busy, setBusy] = useState(false)
  // So the Config tab can NAME what an unset row inherits, instead of a blank that looks
  // unconfigured.
  const repos = useLive(K.repos, getRepos).data
  const repo = repos?.find((r) => r.id === contextId)
  const sys = useLive(K.systemOverview, getSystem, 0).data
  // What each role ALREADY runs, so an untouched picker states the answer instead of deferring it
  // one level.
  const roleDefaults: RoleDefaults = {
    '': { model: toModelKey(repo?.model_override) || DEFAULT_RUN_MODEL,
          effort: repo?.effort_override || DEFAULT_RUN_EFFORT },
    vet: { model: toModelKey(repo?.vet_model) || DEFAULT_RUN_MODEL,
           effort: repo?.vet_effort || DEFAULT_RUN_EFFORT },
    deputy: { model: toModelKey(sys?.deputy_effective_model) || DEFAULT_RUN_MODEL,
              effort: sys?.deputy_effective_effort || DEFAULT_RUN_EFFORT },
  }

  const open = entries.filter((e) => e.status === 'open')

  async function submit() {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true)
    try {
      await addInbox({ text: t, title: title.trim() || null, kind, origin: 'user' }, contextId)
      setText('')
      setTitle('')
      onChanged()
    } catch {
      /* surfaced on next load */
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* quick-capture — manual title (optional) + text, drops into the selected column */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-sunken p-2 focus-within:border-accent">
        <Dropdown value={kind} options={KIND_OPTS} onChange={(v) => setKind(v as InboxKind)} />
        <input
          className="w-40 shrink-0 rounded bg-transparent px-1 text-sm font-medium text-fg outline-none placeholder:text-faint"
          placeholder="Title (optional)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), submit())}
        />
        <input
          className="min-w-0 flex-1 bg-transparent px-1 text-sm text-fg outline-none placeholder:text-faint"
          placeholder="Quick-capture into the selected column…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), submit())}
        />
        {/* Not chosen here: capture is a one-line act, and the per-item config lives in the card's
            own tab. */}
        <button
          onClick={submit}
          disabled={busy || !text.trim()}
          className="flex shrink-0 items-center gap-1 rounded-md bg-accent px-2.5 py-1.5 text-xs text-on-accent disabled:opacity-40"
        >
          <Plus size={14} /> Add
        </button>
      </div>

      {/* columns by kind — 2×2 */}
      <div className="grid cols-mid gap-3">
        {KIND_COLUMNS.map((col) => {
          const its = open.filter((e) => e.kind === col.kind)
          return (
            <div key={col.kind} className="flex min-h-[5rem] flex-col rounded-xl border border-line bg-surface">
              <div className="flex items-center justify-between border-b border-line px-3 py-2">
                <span className="flex min-w-0 items-baseline gap-2 text-sm font-semibold text-fg">
                  <span className={`h-2.5 w-2.5 shrink-0 self-center rounded-[3px] ${col.dot}`} />
                  {col.label}
                  <span className="truncate text-[11px] font-normal text-faint">{col.blurb}</span>
                </span>
                <span className="rounded-full bg-hover px-2 py-0.5 text-xs font-medium tabular-nums text-muted">{its.length}</span>
              </div>
              <div className="max-h-[70vh] flex-1 space-y-1.5 overflow-y-auto p-1.5">
                {its.length === 0 ? (
                  <div className="px-1.5 py-2 text-[12px] text-faint">—</div>
                ) : (
                  its.map((e) => (
                    <InboxCard
                      key={e.id}
                      e={e}
                      roleDefaults={roleDefaults}
                      onPush={() => pushInbox(e.id, contextId).then(onChanged)}
                      onDiscuss={onDiscussNote && (() => onDiscussNote(e.id, e.title || e.text.slice(0, 60)))}
                      onSave={async (patch) => { await updateInbox(e.id, patch); onChanged() }}
                      onDelete={() => deleteInbox(e.id).then(onChanged)}
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

function InboxCard({
  e,
  roleDefaults,
  onPush,
  onDiscuss,
  onSave,
  onDelete,
}: {
  e: InboxEntry
  roleDefaults: RoleDefaults
  onPush: () => void
  onDiscuss?: () => void
  onSave: (patch: InboxConfigPatch) => Promise<void>
  onDelete: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)

  // The modal is a SIBLING of the card, never a descendant: inside it, every click would re-open
  // it.
  return (
    <>
    <div
      onClick={() => setEditing(true)}
      title="Edit this item"
      className={`group cursor-pointer rounded-md border border-line border-l-2 ${KIND_STRIPE[e.kind] ?? 'border-l-line'} bg-surface px-2.5 py-2 shadow-sm transition hover:border-accent hover:bg-hover`}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          {/* On the CARD, because the id is how the owner names a row out loud. */}
          {/* No body preview: a grey echo of the title reads as unfinished. */}
          <div className="flex items-baseline gap-1.5">
            <span className="shrink-0 font-mono text-[11px] text-faint">#{e.id}</span>
            {/* One unbreakable token would push the card wider than its lane. */}
            {e.title ? (
              <span className="min-w-0 text-[14px] font-medium leading-snug text-fg [overflow-wrap:anywhere]">{e.title}</span>
            ) : (
              <span className="min-w-0 line-clamp-2 text-[13px] leading-snug text-muted [overflow-wrap:anywhere]">{e.text}</span>
            )}
          </div>
        </div>
        <div className="mt-0.5 flex shrink-0 items-center gap-1" onClick={(ev) => ev.stopPropagation()}>
          {confirmDel ? (
            <>
              <button
                title="Confirm delete — removes this item permanently"
                onClick={() => onDelete()}
                className="rounded-md bg-danger/15 px-2 py-1 text-[11px] font-medium text-danger hover:bg-danger hover:text-on-accent"
              >
                Delete
              </button>
              <button title="Cancel" onClick={() => setConfirmDel(false)} className="rounded p-1 text-faint hover:text-fg">
                <X size={13} />
              </button>
            </>
          ) : (
            <>
              {/* Absent on a NOTE: a note has no work to become, so the button would promise what
                  the route refuses. */}
              {e.kind === 'note' && onDiscuss && (
                <button
                  title="Discuss — opens a new general chat about this note"
                  onClick={onDiscuss}
                  className="inline-flex items-center gap-1 rounded-md bg-accent-soft px-2 py-1 text-[11px] font-medium text-accent-text transition hover:bg-accent hover:text-on-accent"
                >
                  <MessagesSquare size={12} /> Discuss
                </button>
              )}
              {e.kind !== 'note' && (
                <button
                  title="Push to workspace — creates a queued work-item"
                  onClick={onPush}
                  className="inline-flex items-center gap-1 rounded-md bg-accent-soft px-2 py-1 text-[11px] font-medium text-accent-text transition hover:bg-accent hover:text-on-accent"
                >
                  Push <ArrowRight size={12} />
                </button>
              )}
              <button
                title="Drop — delete this item"
                onClick={() => setConfirmDel(true)}
                className="rounded p-1 text-faint opacity-0 transition hover:text-danger group-hover:opacity-100"
              >
                <Trash2 size={14} />
              </button>
            </>
          )}
        </div>
      </div>
      {/* The time is pushed to the far edge: it is the one field worth scanning down a column. */}
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-faint">
        {e.origin?.includes('user') && (
          <span className="inline-flex items-center gap-0.5 text-success" title="Created / contributed by you">
            <User size={11} /> User
          </span>
        )}
        {e.origin?.includes('agent') && (
          <span className="inline-flex items-center gap-0.5 text-accent" title="An agent contributed to this item">
            <Bot size={11} /> Agent
          </span>
        )}
        {/* Only when one was filed: absent means undecided, a real state. It carries the board's
            hue for that kind. */}
        {e.work_kind && (
          <span className={KIND_TEXT[e.work_kind] ?? 'text-muted'} title="Proposed work kind — triage confirms it">
            {workKindLabel(e.work_kind)}
          </span>
        )}
        <span className="ml-auto shrink-0">{fmtLocal(e.created_at)}</span>
      </div>
    </div>
    {editing && (
      <InboxItemModal
        e={e}
        roleDefaults={roleDefaults}
        onClose={() => setEditing(false)}
        onSave={onSave}
      />
    )}
    </>
  )
}

// The inspector for one inbox row, in THREE tabs: what it says, the context it hands on, and how it
// is worked.
//
// Two artifacts, two edit gates; the action row shows the gate for whichever tab is open.
function InboxItemModal({
  e,
  roleDefaults,
  onSave,
  onClose,
}: {
  e: InboxEntry
  roleDefaults: RoleDefaults   // per role, what an unset row already runs — its picker's start
  onSave: (patch: InboxConfigPatch) => Promise<void>
  onClose: () => void
}) {
  const [tab, setTab] = useState<'content' | 'brief' | 'setting'>('content')

  // Every pick is CONCRETE. `work_kind` is the exception: an empty value means triage decides
  // alone.
  const roleSaved = Object.fromEntries(RUN_ROLES.flatMap((r) => {
    const d = roleDefaults[r.key] ?? { model: DEFAULT_RUN_MODEL, effort: DEFAULT_RUN_EFFORT }
    return [
      [roleField(r.key, 'model'), toModelKey(e[roleField(r.key, 'model')]) || d.model],
      [roleField(r.key, 'effort'), e[roleField(r.key, 'effort')] || d.effort],
    ]
  })) as Pick<InboxConfigPatch, 'model' | 'effort' | 'vet_model' | 'vet_effort' | 'deputy_model' | 'deputy_effort'>
  const saved: InboxConfigPatch = {
    title: e.title ?? '',
    text: e.text,
    kind: e.kind,
    autopilot: !!e.autopilot,
    work_kind: e.work_kind ?? '',
    ...roleSaved,
  }
  const row = useEditGate<InboxConfigPatch>({
    saved,
    valid: (d) => !!d.text.trim(),
    commit: (d) => onSave({ ...d, title: (d.title ?? '').trim() || null, text: d.text.trim() }),
  })
  const d = row.draft
  const set = (patch: Partial<InboxConfigPatch>) => row.setDraft({ ...d, ...patch })
  // Outside edit mode the tabs read the ROW: an abandoned draft must not decide which tabs exist.
  const kind = row.editing ? d.kind : e.kind

  // Loaded when its tab first opens: most opens never look at it, and most rows have none.
  const [brief, setBrief] = useState<InboxBrief | null>(null)
  const [briefErr, setBriefErr] = useState<string | null>(null)
  useEffect(() => {
    if (tab !== 'brief' || brief) return
    let alive = true
    getInboxBrief(e.id)
      .then((b) => { if (alive) setBrief(b) })
      .catch((err) => alive && setBriefErr(String(err)))
    return () => { alive = false }
  }, [tab, brief, e.id])
  const briefGate = useEditGate({
    saved: brief?.content ?? '',
    valid: (t) => !!t.trim(),
    commit: async (t) => { await saveInboxBrief(e.id, t); setBrief({ ...brief!, content: t }) },
  })

  // A note is never pushed, so both tabs are withheld rather than shown empty.
  const tabs = kind === 'note'
    ? ([['content', 'Content'], ['setting', 'Info']] as const)
    : ([['content', 'Content'], ['brief', 'Brief'], ['setting', 'Setting']] as const)
  const gate = tab === 'brief' ? briefGate : row
  const err = tab === 'brief' ? (briefGate.err ?? briefErr) : row.err

  return (
    // Contained, so it overlays the dashboard column and leaves the chat rail interactive.
    <Modal onClose={onClose} title="Inbox item" maxW="max-w-lg" z="z-40" contain dismissable={false}>
      <div className="p-4">
        <TabBar
          tabs={tabs}
          value={tab === 'brief' && kind === 'note' ? 'content' : tab}
          onChange={setTab}
          size="sm"
          className="mb-3"
        />

        {/* One fixed body height, so switching tabs cannot resize the dialog under the cursor. */}
        <div className="h-[21rem] overflow-y-auto">
        {tab === 'content' ? (
          row.editing ? (
            <div className="flex h-full flex-col gap-2">
              <div className="flex items-center gap-2">
                <Dropdown value={d.kind} options={KIND_OPTS} onChange={(v) => set({ kind: v as InboxKind })} />
                <input
                  className="min-w-0 flex-1 rounded border border-line bg-sunken px-2 py-1.5 text-[13px] font-medium text-fg outline-none focus:border-accent placeholder:text-faint"
                  placeholder="Title (optional)"
                  value={d.title ?? ''}
                  onChange={(ev) => set({ title: ev.target.value })}
                  autoFocus
                />
              </div>
              <textarea
                className="w-full flex-1 resize-none rounded border border-line bg-sunken px-2 py-1.5 text-[13px] leading-relaxed text-fg outline-none focus:border-accent"
                value={d.text}
                onChange={(ev) => set({ text: ev.target.value })}
              />
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-baseline gap-2">
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${e.kind === 'note' ? 'bg-hover text-faint' : 'bg-accent-soft text-accent-text'}`}>{e.kind}</span>
                {e.title && <span className="min-w-0 text-[14px] font-medium leading-snug text-fg [overflow-wrap:anywhere]">{e.title}</span>}
              </div>
              <Markdown text={e.text} variant="doc" tone="dev" />
            </div>
          )
        ) : tab === 'brief' ? (
          brief === null ? (
            briefErr ? null : <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>
          ) : briefGate.editing ? (
            <textarea
              className="h-full w-full resize-none rounded border border-line bg-sunken px-2 py-1.5 font-mono text-[12px] leading-relaxed text-fg outline-none focus:border-accent"
              value={briefGate.draft}
              onChange={(ev) => briefGate.setDraft(ev.target.value)}
              spellCheck={false}
              autoFocus
            />
          ) : brief.content ? (
            <Markdown text={stripFrontmatter(brief.content)} variant="doc" tone="dev" />
          ) : (
            <div className="text-[12px] leading-relaxed text-faint">
              No brief was filed, so this row&rsquo;s whole cold-start context is its own text. Write
              one here and the work-item it becomes reads it first.
            </div>
          )
        ) : (
          <div className="space-y-4">
            {/* ── how this item will be worked ── All four describe a RUN, and what its runs
                spend. */}
            {kind !== 'note' && (
            <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
              <SectionHeader>Setting</SectionHeader>
              <div className="mt-1 text-[11px] leading-snug text-faint">
                Set here while the row is open. Push freezes them onto the work-item.
              </div>
              {row.editing ? (
                <div className="mt-2.5 space-y-2.5">
                  <ConfigRow label="Autopilot" hint="Drives its own gates; the deputy judges each one for you.">
                    <Toggle on={d.autopilot} onChange={(v) => set({ autopilot: v })} onColor="bg-accent" />
                  </ConfigRow>
                  <ConfigRow label="Work kind" hint="Implementation changes code; research answers a question. Triage confirms it.">
                    <Dropdown value={d.work_kind} options={WORK_KIND_OPTS} onChange={(v) => set({ work_kind: v })} width="w-36" align="right" />
                  </ConfigRow>
                  <RoleGrid draft={d} onSet={set} />
                </div>
              ) : (
                <>
                  <dl className="mt-2 space-y-1.5">
                    <MetaRow label="Autopilot">{saved.autopilot ? 'On' : 'Off'}</MetaRow>
                    <MetaRow label="Work kind">{optLabel(WORK_KIND_OPTS, saved.work_kind)}</MetaRow>
                  </dl>
                  <RoleGrid draft={saved} />
                </>
              )}
            </section>
            )}

            {/* ── what this row is ─────────────────────────────────────────────────────────── */}
            <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
              <SectionHeader>Meta info</SectionHeader>
              <dl className="mt-2 space-y-1.5">
                <MetaRow label="Id">#{e.id}</MetaRow>
                <MetaRow label="Status">{e.status}</MetaRow>
                <MetaRow label="Created by">{(e.origin ?? []).join(' · ') || '—'}</MetaRow>
                <MetaRow label="Captured">{fmtLocal(e.created_at)}</MetaRow>
                <MetaRow label="Updated">{fmtLocal(e.updated_at)}</MetaRow>
                {e.spawned_from && (
                  <MetaRow label="Branched from">
                    <span className="font-mono">{e.spawned_from.item}</span>
                    <span className="ml-1.5 text-muted">({e.spawned_from.relation})</span>
                  </MetaRow>
                )}
                {e.routed_to && (
                  <MetaRow label="Work-item"><span className="font-mono">{e.routed_to}</span></MetaRow>
                )}
              </dl>
            </section>
          </div>
        )}
        </div>

        {err && <div className="mt-2 text-[12px] text-danger">{err}</div>}
        <div className="mt-3 flex items-center justify-end gap-2">
          <EditActions
            gate={gate}
            readOnly={tab === 'brief' && (brief === null || !brief.editable)}
            readOnlyNote={tab === 'brief' && brief && !brief.editable
              ? 'Pushed — the brief is the work-item’s provenance now'
              : undefined}
          />
        </div>
      </div>
    </Modal>
  )
}

// A picker's own word for a stored value — so the read view says "Sonnet 5", not `sonnet-5`.
function optLabel(opts: { value: string; label: string }[], value: string): string {
  return opts.find((o) => o.value === value)?.label ?? value ?? '—'
}

// A TABLE, so the question is asked once per column and answered once per row, and a new role is
// one more row.
function RoleGrid({ draft, onSet }: {
  draft: InboxConfigPatch
  onSet?: (patch: Partial<InboxConfigPatch>) => void
}) {
  const cell = 'grid grid-cols-[1fr_auto_auto] items-center gap-x-2'
  return (
    <div className="mt-3 border-t border-line pt-2.5">
      <div className={`${cell} pb-1 text-[10px] font-semibold uppercase tracking-wide text-faint`}>
        <span>Runs on</span>
        <span className="w-28 pl-2">Model</span>
        <span className="w-[6.5rem] pl-2">Effort</span>
      </div>
      {RUN_ROLES.map((r) => {
        const mKey = roleField(r.key, 'model')
        const eKey = roleField(r.key, 'effort')
        return (
          <div key={r.key || 'work'} className={`${cell} py-1`}>
            <div className="min-w-0">
              <div className="text-[12.5px] leading-tight text-fg">{r.label}</div>
              <div className="text-[10.5px] leading-tight text-faint">{r.hint}</div>
            </div>
            {onSet ? (
              <>
                <Dropdown value={draft[mKey]} options={RUN_MODELS} onChange={(v) => onSet({ [mKey]: v })} width="w-28" align="right" />
                <Dropdown value={draft[eKey]} options={RUN_EFFORTS} onChange={(v) => onSet({ [eKey]: v })} width="w-[6.5rem]" align="right" />
              </>
            ) : (
              <>
                <span className="w-28 pl-2 text-[12px] text-muted">{optLabel(RUN_MODELS, draft[mKey])}</span>
                <span className="w-[6.5rem] pl-2 text-[12px] text-muted">{optLabel(RUN_EFFORTS, draft[eKey])}</span>
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}

function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

// One labelled control in the Config section: name + one-line why on the left, the control right.
function ConfigRow({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[13px] text-fg">{label}</div>
        <div className="text-[11px] leading-snug text-faint">{hint}</div>
      </div>
      <div className="shrink-0 pt-0.5">{children}</div>
    </div>
  )
}

// One fact in the Meta section — the label names it, the value IS it (colour rule: muted label,
// fg fact).
function MetaRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2 text-[13px] leading-snug">
      <dt className="w-[6.5rem] shrink-0 text-[11px] leading-[1.45] text-muted">{label}</dt>
      <dd className="min-w-0 break-words text-fg">{children}</dd>
    </div>
  )
}

// Note: `PHASE_LABEL` is re-exported for consumers that label a single phase.
export { PHASE_LABEL }
