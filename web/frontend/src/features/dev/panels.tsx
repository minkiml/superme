import { useState, useEffect } from 'react'
import { ChevronRight, Plus, Trash2, Clock, CornerDownRight, GitBranch, ArrowRight, X, Bot, Sparkles, Loader2, MessageSquareText, ListChecks } from 'lucide-react'
import Dropdown from '@/ui/Dropdown'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import { addInbox, updateInbox, deleteInbox, pushInbox, artifactPath, type WorkItem, type InboxEntry, type InboxKind } from '@/lib/api'
import { fmtLocal, fmtTokens, fmtDuration, fmtModel, MODELS as MODEL_CATALOG, EFFORTS as EFFORT_CATALOG, DEFAULT_EFFORT } from '@/lib/format'
import { PHASES, PHASE_LABEL, PHASE_ACCENT, STATUS_COLOR, STATUS_LABEL, STATUS_STRIPE, primaryStatus, Empty } from './common'

// Phase accent → literal dot class (Tailwind needs the full string present in source).
const PHASE_DOT: Record<string, string> = { dev: 'bg-dev', warn: 'bg-warn', success: 'bg-success' }

// Models selectable per run on a work-item card. Default is Sonnet; the values are CLI aliases the
// daemon resolves to the latest concrete model. Labels come from the canonical model catalog.
export const RUN_MODELS = MODEL_CATALOG.map((m) => ({ value: m.key, label: m.label }))
// The concrete Sonnet id (the `sonnet` alias lags — see lib/format.ts / core/models.py).
export const DEFAULT_RUN_MODEL = 'claude-sonnet-5'
// Reasoning-effort levels selectable per run, alongside the model. Default "medium".
export const RUN_EFFORTS = EFFORT_CATALOG.map((e) => ({ value: e.key, label: e.label }))
export const DEFAULT_RUN_EFFORT = DEFAULT_EFFORT

// Shared dev-knowledge store views — the bodies for Workspace (work-items) and Inbox. Rendered
// both by the main Development map (in-panel zooms) and reusable elsewhere. v2 work-item model
// (D-018): phase = Plan/Design → Build/Eval → Done; status = queued/in_progress/waiting/dropped;
// `done` (completion) and `blocked` are derived display states.

// --- status / branch-off chrome -------------------------------------------------

export function StatusBadge({ it }: { it: WorkItem }) {
  const s = primaryStatus(it)
  return (
    <span className={`shrink-0 whitespace-nowrap rounded bg-hover px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${STATUS_COLOR[s] ?? 'text-faint'}`}>
      {STATUS_LABEL[s] ?? '—'}
    </span>
  )
}

// Blocked is a derived overlay (hidden unless set) — shown as a small danger chip, not the status.
function BlockedChip({ it }: { it: WorkItem }) {
  if (!it.blocked) return null
  return (
    <span className="inline-flex items-center gap-1 text-[10px] text-danger" title={`blocked by ${(it.blocked_by ?? []).join(', ')}`}>
      <Clock size={10} /> blocked by {(it.blocked_by ?? []).join(', ')}
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

// An item is on the active board until it terminates: completed (ticked out of Done — `done_at`)
// or dropped. Terminal items leave the interface (D-018 — a DONE-phase/ticked-out item is gone).
export const isActive = (it: WorkItem) => !it.done_at && it.status !== 'dropped'

// A work-item is "plannable" — eligible for the one-shot "Plan it" gate — only while it's a
// QUEUED plan/design item, i.e. it has never been planned. The first plan moves it
// queued → in_progress → waiting and it never returns to queued, so the button is offered
// exactly once. After that it's forward-only (review / discuss in chat / advance phase);
// re-planning, if ever needed, is a natural chat request, not a card affordance. The caller
// also gates on `!running` (can't launch while an agent is already on it).
export const isPlannable = (it: WorkItem) =>
  (it.phase ?? 'plan_design') === 'plan_design' && (it.status ?? 'queued') === 'queued'

// Actions a board surface can offer per card. `bind` opens the item in the chat (dev);
// `plan` fires a headless /plan turn; `delete` hard-deletes a plan/design item; `running`
// is the set of ids currently planning.
export type WorkActions = {
  onOpen?: (it: WorkItem) => void // open the review popup (card click)
  onBind?: (it: WorkItem) => void // bind the chat to the item (review popup's "Discuss")
  onPlan?: (it: WorkItem, model?: string) => void
  onDelete?: (it: WorkItem) => void
  running?: string[]
  boundItemId?: string | null
}

// Delete is only offered while an item is in plan/design — past that gate code may be touched.
const isDeletable = (it: WorkItem) => (it.phase ?? 'plan_design') === 'plan_design'

// --- workspace: kanban by phase -------------------------------------------------

export function WorkspaceKanban({ items, onOpen, running, boundItemId }: { items: WorkItem[] } & WorkActions) {
  const visible = items.filter(isActive)
  if (visible.length === 0) return <Empty>No active work-items.</Empty>
  const byPhase = (key: string) => visible.filter((it) => (it.phase ?? 'plan_design') === key)
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      {PHASES.map((ph) => {
        const col = byPhase(ph.key)
        const dot = PHASE_DOT[PHASE_ACCENT[ph.key] ?? 'dev'] ?? 'bg-line'
        return (
          <div key={ph.key} className="flex min-h-[7rem] flex-col rounded-xl bg-sunken p-2">
            <div className="mb-2 flex items-center gap-2 px-1">
              <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
              <span className="text-[11px] font-semibold uppercase tracking-wide text-fg">{ph.label}</span>
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
                  />
                ))
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function WorkCard({
  it, onOpen, planning, bound,
}: {
  it: WorkItem
  onOpen?: (it: WorkItem) => void
  planning?: boolean
  bound?: boolean
}) {
  const clickable = !!onOpen
  const running = !!planning || !!it.running
  const hasTotal = (it.total_tokens ?? 0) > 0
  const telemetryParts = [
    it.model ? fmtModel(it.model) : null,
    it.context_pct != null ? `ctx ${it.context_pct}%` : null,
    hasTotal ? `Σ ${fmtTokens(it.total_tokens ?? 0)} tok` : null,
  ] as const
  const hasTelemetry = telemetryParts.some(Boolean)
  // The card is a pure glance + entry point: clicking opens the review popup AND binds the
  // chat. All actions (model config, Plan it, Approve, Drop) live in the popup now.
  const showFooter = running || hasTelemetry || !!it.tasks
  const stripe = STATUS_STRIPE[primaryStatus(it)] ?? 'border-l-line'
  return (
    <div
      onClick={clickable ? () => onOpen!(it) : undefined}
      title={clickable ? 'Open review + chat for this work-item' : undefined}
      className={`rounded-md border border-line border-l-2 bg-surface px-2.5 py-2 shadow-sm ${
        bound ? 'border-l-accent ring-2 ring-accent' : it.blocked ? 'border-l-danger' : stripe
      } ${clickable ? 'cursor-pointer transition hover:border-accent hover:shadow-md' : ''}`}
    >
      <div className="flex items-start gap-1.5">
        <div className="min-w-0 flex-1 text-[12.5px] leading-snug text-fg">{it.title}</div>
        <BranchInfo it={it} />
        <StatusBadge it={it} />
      </div>
      {it.blocked && (
        <div className="mt-1.5">
          <BlockedChip it={it} />
        </div>
      )}
      {showFooter && (
        <div className="mt-2 text-[11px]">
          {running ? (
            <RunMeter it={it} />
          ) : (
            (it.tasks || hasTelemetry) && (
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-faint">
                {it.tasks && <TaskProgress tasks={it.tasks} />}
                {hasTelemetry && (
                  <span className="flex items-center gap-0">
                    {telemetryParts[0] && <span>{telemetryParts[0]}</span>}
                    {telemetryParts[1] && (
                      <><span className="mx-1">·</span><span>{telemetryParts[1]}</span></>
                    )}
                    {telemetryParts[2] && (
                      <><span className="mx-1">·</span><span title="Critical tokens used, summed across all runs">{telemetryParts[2]}</span></>
                    )}
                  </span>
                )}
              </div>
            )
          )}
        </div>
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

// Live run telemetry while an agent works the item: pulse + model + elapsed time + this-run tokens.
function RunMeter({ it }: { it: WorkItem }) {
  return (
    <span className="inline-flex items-center gap-1 text-accent-text" title="Agent running — model · live time · this run's tokens · context fill">
      <Loader2 size={11} className="animate-spin" />
      {it.run_model && <span className="text-muted">{fmtModel(it.run_model)}</span>}
      <LiveTimer startedAt={it.run_started_at} />
      {it.run_tokens != null && <span className="text-muted">· {fmtTokens(it.run_tokens)} tok</span>}
      {it.run_context_pct != null && <span className="text-muted">· {it.run_context_pct}% ctx</span>}
    </span>
  )
}

// Build progress from the item's tasks.md checklist (done / total), with a thin bar. Hidden
// when the item has no tasks.md (e.g. not yet planned).
function TaskProgress({ tasks }: { tasks: { done: number; total: number } }) {
  const { done, total } = tasks
  const pct = total ? Math.round((done / total) * 100) : 0
  const complete = done >= total
  return (
    <span
      className="inline-flex items-center gap-1 text-faint"
      title={`Tasks: ${done} of ${total} done`}
    >
      <ListChecks size={11} className={complete ? 'text-success' : undefined} />
      <span className="tabular-nums">{done}/{total}</span>
      <span className="h-1 w-8 overflow-hidden rounded-full bg-hover">
        <span
          className={`block h-full rounded-full ${complete ? 'bg-success' : 'bg-accent'}`}
          style={{ width: `${pct}%` }}
        />
      </span>
    </span>
  )
}

// --- workspace: plan list (grouped by phase, expandable) ------------------------

export function PlanList({ items, onBind, onPlan, onDelete, running, boundItemId }: { items: WorkItem[] } & WorkActions) {
  const visible = items.filter(isActive)
  if (visible.length === 0) return <Empty>No active work-items.</Empty>
  return (
    <div className="space-y-6">
      {PHASES.map((ph) => {
        const rows = visible.filter((it) => (it.phase ?? 'plan_design') === ph.key)
        if (!rows.length) return null
        return (
          <div key={ph.key}>
            <div className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">{ph.label}</div>
            <div className="space-y-1.5">
              {rows.map((it) => (
                <div key={it.id} className="flex items-stretch gap-1.5">
                  <div className="min-w-0 flex-1">
                    <ExpandRow
                      header={
                        <>
                          <StatusBadge it={it} />
                          <span className="min-w-0 flex-1 truncate text-sm text-fg">{it.title}</span>
                          {boundItemId === it.id && (
                            <span className="inline-flex items-center gap-0.5 text-[10px] text-accent-text" title="Bound to the chat">
                              <MessageSquareText size={11} /> chat
                            </span>
                          )}
                          <BranchInfo it={it} />
                        </>
                      }
                    >
                      {it.blocked && <div className="mb-2"><BlockedChip it={it} /></div>}
                      <Refs it={it} />
                      {it.description && <Markdown text={it.description} />}
                    </ExpandRow>
                  </div>
                  <RowActions it={it} onBind={onBind} onPlan={onPlan} onDelete={onDelete} planning={running?.includes(it.id)} />
                </div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// Per-row actions for the plan list — kept OUTSIDE the ExpandRow's <button> (no nested
// buttons): open-in-chat (bind) and the headless Plan-it for plannable items.
function RowActions({
  it, onBind, onPlan, onDelete, planning,
}: {
  it: WorkItem
  onBind?: (it: WorkItem) => void
  onPlan?: (it: WorkItem) => void
  onDelete?: (it: WorkItem) => void
  planning?: boolean
}) {
  const running = !!planning || !!it.running
  // Plan-it only on a queued item (see isPlannable): forward-only once planned.
  const showPlan = !!onPlan && isPlannable(it) && !running
  const showDelete = !!onDelete && isDeletable(it) && !running
  if (!onBind && !showPlan && !showDelete && !running) return null
  return (
    <div className="flex shrink-0 items-center gap-1.5">
      {running ? (
        <span className="px-1 text-[11px]"><RunMeter it={it} /></span>
      ) : (
        showPlan && (
          <button
            onClick={() => onPlan!(it)}
            title="Plan it — a headless agent drafts the plan and moves it to in_progress"
            aria-label="Plan it"
            className="grid place-items-center rounded-md border border-line bg-surface p-1.5 text-muted hover:border-accent hover:text-accent-text"
          >
            <Sparkles size={14} />
          </button>
        )
      )}
      {onBind && (
        <button
          onClick={() => onBind(it)}
          title="Open this work-item in the chat (dev)"
          aria-label="Open in chat"
          className="grid place-items-center rounded-md border border-line bg-surface p-1.5 text-muted hover:border-accent hover:text-accent-text"
        >
          <MessageSquareText size={14} />
        </button>
      )}
      {showDelete && (
        <button
          onClick={() => onDelete!(it)}
          title="Delete — hard-removes this item, its session, and its inbox row"
          aria-label="Delete work-item"
          className="grid place-items-center rounded-md border border-line bg-surface p-1.5 text-faint hover:border-danger hover:text-danger"
        >
          <Trash2 size={14} />
        </button>
      )}
    </div>
  )
}

function Refs({ it }: { it: WorkItem }) {
  // Artifacts may be path strings or {type, path} entries — normalize to display strings so
  // a structured entry never renders as an object (React throws on object children).
  const rows: [string, string[] | undefined][] = [
    ['blocked by', it.blocked_by],
    ['branch of', it.parent_id ? [it.parent_id] : undefined],
    ['branch-offs', it.children.length ? it.children : undefined],
    ['session', it.session_id ? [it.session_id] : undefined],
    ['artifacts', it.artifacts?.map(artifactPath)],
  ]
  const shown = rows.filter(([, v]) => v && v.length)
  if (!shown.length) return null
  return (
    <div className="mb-2 flex flex-col gap-1 text-xs text-muted">
      {shown.map(([label, vals]) => (
        <div key={label} className="flex gap-2">
          <span className="w-20 shrink-0 text-faint">{label}</span>
          <span className="flex flex-wrap gap-1.5">
            {vals!.map((v) => (
              <code key={v} className="rounded bg-sunken px-1.5 py-0.5 text-[11px] text-fg">{v}</code>
            ))}
          </span>
        </div>
      ))}
    </div>
  )
}

// --- inbox ----------------------------------------------------------------------

const KIND_OPTS = [
  { value: 'todo', label: 'todo' },
  { value: 'idea', label: 'idea' },
  { value: 'note', label: 'note' },
  { value: 'question', label: 'question' },
]
// Each capture kind gets a distinct token-driven marker (warn / universal / dev / danger) so the
// palette re-themes from the design tokens, not per-file hex.
const KIND_COLUMNS: { kind: InboxKind; label: string; dot: string }[] = [
  { kind: 'todo', label: 'Todo', dot: 'bg-warn' },
  { kind: 'idea', label: 'Idea', dot: 'bg-universal' },
  { kind: 'note', label: 'Note', dot: 'bg-dev' },
  { kind: 'question', label: 'Question', dot: 'bg-danger' },
]
// Left-edge stripe per kind (mirrors the work-card status stripe) — literal classes for Tailwind.
const KIND_STRIPE: Record<string, string> = {
  todo: 'border-l-warn',
  idea: 'border-l-universal',
  note: 'border-l-dev',
  question: 'border-l-danger',
}

// The inbox is the active capture queue, laid out as columns by kind. Resolving an item clears
// it from view (kept in the DB). Quick-capture drops into whichever kind is selected.
export function InboxView({
  entries,
  contextId,
  onChanged,
}: {
  entries: InboxEntry[]
  contextId: string
  onChanged: () => void
}) {
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState<InboxKind>('todo')
  const [busy, setBusy] = useState(false)

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
        <button
          onClick={submit}
          disabled={busy || !text.trim()}
          className="flex shrink-0 items-center gap-1 rounded-md bg-accent px-2.5 py-1.5 text-xs text-on-accent disabled:opacity-40"
        >
          <Plus size={14} /> Add
        </button>
      </div>

      {/* columns by kind — 2×2 */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {KIND_COLUMNS.map((col) => {
          const its = open.filter((e) => e.kind === col.kind)
          return (
            <div key={col.kind} className="flex min-h-[5rem] flex-col rounded-xl border border-line bg-surface">
              <div className="flex items-center justify-between border-b border-line px-3 py-2">
                <span className="flex items-center gap-2 text-sm font-semibold text-fg">
                  <span className={`h-2.5 w-2.5 rounded-[3px] ${col.dot}`} />
                  {col.label}
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
                      onPush={() => pushInbox(e.id, contextId).then(onChanged)}
                      onSave={(patch) => updateInbox(e.id, patch).then(onChanged)}
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
  onPush,
  onSave,
  onDelete,
}: {
  e: InboxEntry
  onPush: () => void
  onSave: (patch: { title: string | null; text: string; kind: InboxKind }) => void
  onDelete: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)

  // The whole card is the click target (like a work-item card) → opens the edit view. Action
  // controls stop propagation so they don't also trip the edit-open.
  return (
    <div
      onClick={() => setEditing(true)}
      title="Edit this item"
      className={`group cursor-pointer rounded-md border border-line border-l-2 ${KIND_STRIPE[e.kind] ?? 'border-l-line'} bg-surface px-2.5 py-2 shadow-sm transition hover:border-accent hover:bg-hover`}
    >
      {editing && (
        <InboxEditModal
          e={e}
          onCancel={() => setEditing(false)}
          onSave={(patch) => { onSave(patch); setEditing(false) }}
        />
      )}
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          {e.title ? (
            <div className="text-[14px] font-medium leading-snug text-fg">{e.title}</div>
          ) : (
            <div className="text-[14px] font-medium italic leading-snug text-faint">Untitled</div>
          )}
          <div className="mt-1 line-clamp-2 text-[12.5px] leading-snug text-muted">{e.text}</div>
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
              {/* Push — the primary action, made prominent (tinted pill, always visible). */}
              <button
                title="Push to workspace — creates a queued work-item"
                onClick={onPush}
                className="inline-flex items-center gap-1 rounded-md bg-accent-soft px-2 py-1 text-[11px] font-medium text-accent-text transition hover:bg-accent hover:text-on-accent"
              >
                Push <ArrowRight size={12} />
              </button>
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
      <div className="mt-1.5 flex items-center gap-2 text-[11px] text-faint">
        {e.origin === 'agent' && (
          <span className="inline-flex items-center gap-0.5 text-accent" title="Proposed by an agent">
            <Bot size={11} /> agent
          </span>
        )}
        {!e.title && <span className="italic">untitled</span>}
        <span>{fmtLocal(e.created_at)}</span>
      </div>
    </div>
  )
}

// Popup editor for one inbox item — title, kind, and text. Backdrop click cancels.
function InboxEditModal({
  e,
  onSave,
  onCancel,
}: {
  e: InboxEntry
  onSave: (patch: { title: string | null; text: string; kind: InboxKind }) => void
  onCancel: () => void
}) {
  const [title, setTitle] = useState(e.title ?? '')
  const [text, setText] = useState(e.text)
  const [kind, setKind] = useState<InboxKind>(e.kind)

  return (
    // Contained (not viewport-fixed) so it overlays the dashboard column and leaves the chat rail
    // interactive — same containment as the work-item review popup.
    <Modal onClose={onCancel} title="Edit inbox item" maxW="max-w-lg" z="z-40" contain>
      <div className="p-4">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Dropdown value={kind} options={KIND_OPTS} onChange={(v) => setKind(v as InboxKind)} />
            <input
              className="min-w-0 flex-1 rounded border border-line bg-sunken px-2 py-1.5 text-sm font-medium text-fg outline-none focus:border-accent placeholder:text-faint"
              placeholder="Title (optional)"
              value={title}
              onChange={(ev) => setTitle(ev.target.value)}
              autoFocus
            />
          </div>
          <textarea
            className="min-h-[16rem] w-full flex-1 resize-none rounded border border-line bg-sunken px-2 py-1.5 text-sm leading-relaxed text-fg outline-none focus:border-accent"
            value={text}
            onChange={(ev) => setText(ev.target.value)}
          />
        </div>
        <div className="mt-3 flex justify-end gap-2">
          <button className="rounded-md bg-hover px-3 py-1.5 text-xs text-fg hover:text-fg" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="rounded-md bg-accent px-3 py-1.5 text-xs text-on-accent hover:opacity-90 disabled:opacity-40"
            disabled={!text.trim()}
            onClick={() => onSave({ title: title.trim() || null, text: text.trim() || e.text, kind })}
          >
            Save
          </button>
        </div>
      </div>
    </Modal>
  )
}

// --- shared ---------------------------------------------------------------------

export function ExpandRow({ header, children }: { header: React.ReactNode; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const hasBody = Boolean(children)
  return (
    <div className="rounded-lg border border-line bg-surface">
      <button
        onClick={() => hasBody && setOpen((o) => !o)}
        className={`flex w-full items-center gap-2 px-3 py-2 text-left ${hasBody ? 'hover:bg-hover' : 'cursor-default'}`}
      >
        {header}
        {hasBody && (
          <ChevronRight
            size={14}
            className="shrink-0 text-faint transition-transform"
            style={{ transform: open ? 'rotate(90deg)' : 'none' }}
          />
        )}
      </button>
      {open && hasBody && <div className="border-t border-line px-3 py-3 text-sm text-fg">{children}</div>}
    </div>
  )
}

// Note: `PHASE_LABEL` is re-exported for consumers that label a single phase.
export { PHASE_LABEL }
