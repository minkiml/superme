import { useState } from 'react'
import { Plus, Trash2, ArrowRight, X, Bot, User, MessagesSquare } from 'lucide-react'
import Dropdown from '@/ui/Dropdown'
import { addInbox, updateInbox, deleteInbox, pushInbox, getRepos, getSystem, type InboxEntry, type InboxKind } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { fmtLocal, toModelKey } from '@/lib/format'
import { KIND_TEXT, workKindLabel } from '../common'
import { InboxItemModal } from './InboxItemModal'
import { DEFAULT_RUN_EFFORT, DEFAULT_RUN_MODEL, InboxConfigPatch, RoleDefaults } from './runConfig'
import { useAuthGate } from '@/lib/authGate'

// The inbox: what has been filed but not yet pushed into work.

// Two kinds, differing in what the row can DO: an `item` becomes a work-item, a `note` never is.
export const KIND_OPTS = [
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
  const { reason: authReason } = useAuthGate()
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
                  // Pushing triages the item, which is a run — greyed rather than hidden, so the
                  // reason is readable where the click would have been.
                  title={authReason ?? 'Push to workspace — creates a queued work-item'}
                  onClick={onPush}
                  disabled={!!authReason}
                  className="inline-flex items-center gap-1 rounded-md bg-accent-soft px-2 py-1 text-[11px] font-medium text-accent-text transition hover:bg-accent hover:text-on-accent disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-accent-soft disabled:hover:text-accent-text"
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
