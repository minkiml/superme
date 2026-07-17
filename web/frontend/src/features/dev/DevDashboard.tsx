import { useEffect, useState } from 'react'
import { Hammer, RefreshCw, ArrowLeft, ArrowRight, Boxes, Inbox, Circle, Clock, Check, Bot, Archive } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'
import Modal from '@/ui/Modal'
import ConfirmDialog from '@/ui/ConfirmDialog'
import { getDev, getAttention, planWorkItem, deleteWorkItem, type AttentionData, type DevData, type DevGlance, type InboxEntry, type WorkItem } from '@/lib/api'
import { fmtLocalDate } from '@/lib/format'
import { WorkspaceKanban, InboxView, isActive } from './panels'
import WorkItemModal from './WorkItemModal'

// The Development dashboard — a live environment for one context's dev-knowledge. Today it shows
// the active pipeline: Inbox → Workspace (the work in flight). Clicking a store zooms its working
// surface into the panel in place (back button). Decisions/Learnings were removed from the surface
// pending a clearer model; the abstract schema lives on "Model". Built context-parameterized
// (D-011) — only global is wired today.

type Zoom = 'workspace' | 'inbox'

export default function DevDashboard({
  contextId = 'global',
  onBindItem,
  onUnbindItem,
  boundItemId,
  embedded = false,
}: {
  contextId?: string
  onBindItem?: (it: WorkItem, contextId: string) => void
  onUnbindItem?: () => void
  boundItemId?: string | null
  embedded?: boolean // hosted inside the Dev workspace shell — the shell owns the header
}) {
  const [data, setData] = useState<DevData | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [zoom, setZoom] = useState<Zoom>('inbox') // repo dev dashboard opens on the Inbox (capture queue)
  const [reviewId, setReviewId] = useState<string | null>(null) // work-item open in the review popup
  const [showShipped, setShowShipped] = useState(false) // the completed-items list overlay
  const [confirmDel, setConfirmDel] = useState<WorkItem | null>(null) // item pending delete confirm
  const [attn, setAttn] = useState<AttentionData | null>(null) // attention buckets (S7)

  // `silent` background reloads (the running-poll) skip the `loading` flag so the manual Refresh
  // button doesn't re-trigger its spin every 2.5s — it should spin only on a user-initiated refresh.
  async function load(opts?: { silent?: boolean }) {
    if (!opts?.silent) setLoading(true)
    setErr(null)
    try {
      setData(await getDev(contextId))
      getAttention(contextId).then(setAttn).catch(() => {}) // best-effort; never blocks the board
    } catch (e) {
      setErr(String(e))
    } finally {
      if (!opts?.silent) setLoading(false)
    }
  }

  // Headless "Plan it": kick off a background /plan turn, then refresh so the card flips
  // to its "planning…" state (the running-poll below keeps reloading until it settles).
  async function handlePlan(it: WorkItem, model?: string, effort?: string) {
    try {
      await planWorkItem(it.id, contextId, model, effort)
    } catch {
      /* surfaced on next load */
    }
    load()
  }

  // Hard-delete a plan/design item and erase its trace (folder + session + inbox row). Irreversible,
  // so it goes through the themed ConfirmDialog (handleDelete just opens it); doDelete runs on confirm.
  function handleDelete(it: WorkItem) {
    setConfirmDel(it)
  }
  async function doDelete() {
    const it = confirmDel
    if (!it) return
    setConfirmDel(null)
    try {
      await deleteWorkItem(it.id, contextId)
      if (boundItemId === it.id) onUnbindItem?.() // it's the bound item — drop the chat binding too
    } catch (e) {
      setErr(`Couldn't delete — ${e}`)
    }
    load()
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextId])

  // Poll while any work-item has a headless plan in flight — re-arms after each load (the
  // `running` array identity changes per fetch) and stops once nothing is planning.
  useEffect(() => {
    if (!data?.running?.length) return
    const t = setTimeout(() => load({ silent: true }), 2500)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.running])

  // The item open in the review popup — looked up live so it reflects the latest poll/reload
  // (and auto-closes if the item disappears, e.g. after a delete).
  const reviewItem = reviewId ? (data?.work_items.find((w) => w.id === reviewId) ?? null) : null

  // id → attention tier, for the kanban card tint (S7).
  const bucketOf: Record<string, string> = {}
  for (const tier of ['needs_you', 'running', 'unread'] as const) {
    for (const r of attn?.buckets?.[tier] ?? []) bucketOf[r.id] = tier
  }
  function openItem(id: string) {
    const it = data?.work_items.find((w) => w.id === id)
    if (!it) return
    setReviewId(id)
    onBindItem?.(it, contextId)
  }

  return (
    <div className="relative flex h-full flex-col">
      {reviewItem && (
        <WorkItemModal
          it={reviewItem}
          contextId={contextId}
          onClose={() => setReviewId(null)}
          onPlan={handlePlan}
          onDelete={handleDelete}
          onChanged={load}
        />
      )}
      {confirmDel && (
        <ConfirmDialog
          title="Delete this work-item?"
          body={<>“{confirmDel.title || confirmDel.id}” will be permanently removed — its work-item, session, and inbox row are all erased. This can’t be undone.</>}
          confirmLabel="Delete"
          z="z-50" // above the work-item drilldown (z-40), which stays open behind the confirm
          onCancel={() => setConfirmDel(null)}
          onConfirm={doDelete}
        />
      )}
      {showShipped && data && (
        <ShippedList
          items={data.work_items.filter((w) => w.done_at)}
          onOpen={(id) => {
            setShowShipped(false)
            setReviewId(id)
          }}
          onClose={() => setShowShipped(false)}
        />
      )}
      {embedded ? (
        <div className="flex items-center justify-end px-6 pt-4">
          <button
            onClick={() => load()}
            disabled={loading}
            title="Refresh"
            aria-label="Refresh"
            className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>
      ) : (
        <PageHeader
          icon={Hammer}
          title="Development"
          subtitle="Live environment for this SuperMe's dev-knowledge — operate every store from one map"
          badge="prototype"
          right={
            <button
              onClick={() => load()}
              disabled={loading}
              title="Refresh"
              aria-label="Refresh"
              className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
            </button>
          }
        />
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        {loading && !data ? (
          <div className="p-6 text-sm text-muted">Loading…</div>
        ) : err ? (
          <div className="p-6 text-sm text-danger">Couldn’t load dev knowledge — {err}</div>
        ) : !data ? null : (
          <div className="mx-auto max-w-5xl space-y-5 p-6">
            {/* Pipeline summary — Inbox → Workspace, always visible; each card selects the store
                whose working surface shows below. */}
            {attn && <AttentionStrip attn={attn} onOpen={openItem} />}
            <EnvMap
              data={data}
              selected={zoom}
              onSelect={(z) => {
                setZoom(z)
                // Leaving the workspace for the Inbox means you're no longer on a work-item —
                // drop the chat binding so the rail falls back to the general dev session.
                if (z === 'inbox') onUnbindItem?.()
              }}
            />

            {zoom === 'workspace' ? (
              <ZoomPanel
                title="Workspace"
                icon={Bot}
                meta={`${data.work_items.filter(isActive).length} active`}
              >
                <WorkspaceStats g={data.glance} onShowShipped={() => setShowShipped(true)} />
                <WorkspaceKanban
                  items={data.work_items}
                  // Clicking a card opens its review popup AND binds the chat to its session —
                  // read + discuss side by side (the popup overlays only the dashboard column).
                  onOpen={(it) => {
                    setReviewId(it.id)
                    onBindItem?.(it, contextId)
                  }}
                  running={data.running}
                  boundItemId={boundItemId}
                  buckets={bucketOf}
                />
              </ZoomPanel>
            ) : (
              <ZoomPanel title="Inbox" icon={Inbox} meta={`${data.glance.inbox_open ?? 0} open`}>
                <InboxView entries={data.inbox} contextId={contextId} onChanged={load} />
              </ZoomPanel>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// --- attention strip (S7/D10) -----------------------------------------------------
// Strict-priority rows derived from durable state: what needs YOU (orange) > what's running
// (green) > unread closeouts (blue). Terminal items live only here (they're off the board) —
// clicking a row opens its drilldown, which stamps it seen and clears the blue.

const TIER_STYLE: Record<string, { dot: string; label: string }> = {
  needs_you: { dot: 'bg-warn', label: 'Needs you' },
  running: { dot: 'bg-success', label: 'Running' },
  unread: { dot: 'bg-accent', label: 'Unread' },
}

function AttentionStrip({ attn, onOpen }: { attn: AttentionData; onOpen: (id: string) => void }) {
  const tiers = (['needs_you', 'running', 'unread'] as const)
    .map((t) => ({ tier: t, rows: attn.buckets?.[t] ?? [] }))
    .filter((x) => x.rows.length > 0)
  if (tiers.length === 0) return null
  return (
    <div className="space-y-1.5 rounded-xl border border-line bg-surface px-3 py-2.5">
      {tiers.map(({ tier, rows }) => (
        <div key={tier} className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="flex w-20 shrink-0 items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
            <span className={`h-2 w-2 rounded-full ${TIER_STYLE[tier].dot}`} /> {TIER_STYLE[tier].label}
          </span>
          {rows.map((r) => (
            <button
              key={r.id}
              onClick={() => onOpen(r.id)}
              title={r.reason}
              className="max-w-[16rem] truncate rounded-md border border-line bg-sunken px-2 py-0.5 text-left text-[12px] text-fg hover:bg-hover"
            >
              {r.title}
              <span className="ml-1.5 text-[10px] text-faint">{r.reason}</span>
            </button>
          ))}
        </div>
      ))}
    </div>
  )
}

// --- the environment (active pipeline) ------------------------------------------

function EnvMap({ data, selected, onSelect }: { data: DevData; selected: Zoom; onSelect: (z: Zoom) => void }) {
  const g = data.glance
  const openInbox = data.inbox.filter((e) => e.status === 'open')
  const activeCount = data.work_items.filter(isActive).length
  return (
    <div className="flex items-stretch gap-2">
      <StoreCard
        className="flex-1" icon={Inbox} label="Inbox" subtitle="capture queue"
        selected={selected === 'inbox'} onClick={() => onSelect('inbox')}
      >
        <Metric n={g.inbox_open ?? 0} unit="open" tone="text-fg" />
        <KindBreakdown entries={openInbox} />
      </StoreCard>

      <Connector label="push" />

      <StoreCard
        className="flex-1" icon={Bot} label="Workspace" subtitle="the living plan · worktree"
        selected={selected === 'workspace'} onClick={() => onSelect('workspace')}
      >
        <StatusDots item={g.by_status} total={activeCount} />
      </StoreCard>
    </div>
  )
}

// A clickable store card — flows in the layout (not absolutely positioned).
function StoreCard({
  icon: Icon, label, subtitle, selected, onClick, children, className = '',
}: {
  icon: typeof Boxes
  label: string
  subtitle: string
  selected?: boolean // this store's working surface is the one shown below
  onClick: () => void
  children: React.ReactNode
  className?: string
}) {
  return (
    <button
      onClick={onClick}
      className={`group flex flex-col rounded-xl border bg-surface px-3.5 py-3 text-left shadow-sm transition hover:border-accent ${
        selected ? 'border-accent ring-1 ring-accent' : 'border-line'
      } ${className}`}
    >
      <div className="flex items-center gap-2">
        <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg ${selected ? 'bg-accent text-on-accent' : 'bg-hover text-muted group-hover:text-fg'}`}>
          <Icon size={15} />
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-fg">{label}</span>
          <span className="block truncate text-[10.5px] text-faint">{subtitle}</span>
        </span>
      </div>
      <div className="mt-2.5">{children}</div>
    </button>
  )
}

// The labelled arrow between two pipeline stages.
function Connector({ label }: { label: string }) {
  return (
    <div className="flex shrink-0 flex-col items-center justify-center gap-1 self-center px-1">
      <span className="text-[10.5px] text-muted">{label}</span>
      <span className="flex items-center text-faint">
        <span className="h-px w-9 bg-line" />
        <ArrowRight size={14} className="-ml-1.5" />
      </span>
    </div>
  )
}

function Metric({ n, unit, tone }: { n: number; unit: string; tone: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className={`text-xl font-semibold tabular-nums ${tone}`}>{n}</span>
      <span className="text-[11px] text-muted">{unit}</span>
    </div>
  )
}

// Open-inbox breakdown by kind (todo · note · idea · question), nonzero only.
function KindBreakdown({ entries }: { entries: InboxEntry[] }) {
  const kinds: InboxEntry['kind'][] = ['todo', 'idea', 'note', 'question']
  const counts = kinds
    .map((k) => [k, entries.filter((e) => e.kind === k).length] as const)
    .filter(([, n]) => n > 0)
  if (!counts.length) return null
  return (
    <div className="mt-1.5 flex flex-wrap gap-x-2.5 gap-y-0.5 text-[10.5px] text-muted">
      {counts.map(([k, n]) => (
        <span key={k}>
          {k} <span className="tabular-nums text-fg">{n}</span>
        </span>
      ))}
    </div>
  )
}

// Compact live status breakdown for the Workspace node.
function StatusDots({ item, total }: { item: Record<string, number>; total: number }) {
  const dot = (Icon: typeof Circle, n: number, label: string, cls: string) => (
    <span className="inline-flex items-center gap-1" title={`${n} ${label}`}>
      <Icon size={11} className={cls} />
      <span className="tabular-nums text-fg">{n}</span>
    </span>
  )
  return (
    <div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-xl font-semibold tabular-nums text-fg">{total}</span>
        <span className="text-[11px] text-muted">work-items</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
        {dot(Circle, item.active ?? 0, 'active', 'fill-current text-accent-text')}
        {dot(Clock, item.awaiting_human ?? 0, 'needs you', item.awaiting_human ? 'text-danger' : 'text-faint')}
        {dot(Check, item.done ?? 0, 'done', 'text-success')}
      </div>
    </div>
  )
}

// The work-item stat row — moved out of the page top to sit under the Workspace panel header.
function WorkspaceStats({ g, onShowShipped }: { g: DevGlance; onShowShipped: () => void }) {
  const cell = (label: string, n: number, tone = 'text-fg') => (
    <div className="flex flex-col">
      <span className={`text-xl font-semibold leading-none tabular-nums ${tone}`}>{n}</span>
      <span className="mt-1 text-[10px] uppercase tracking-wide text-muted">{label}</span>
    </div>
  )
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-7 gap-y-3 rounded-xl bg-sunken px-4 py-3">
      {cell('active', g.by_status.active ?? 0, 'text-accent-text')}
      {cell('needs you', g.by_status.awaiting_human ?? 0, 'text-warn')}
      {cell('awaiting child', g.by_status.awaiting_child ?? 0)}
      {(g.by_status.done ?? 0) > 0 ? (
        <button
          onClick={onShowShipped}
          title="View completed work + their archived execution traces"
          className="ml-auto inline-flex items-center gap-1 text-[11px] text-faint underline-offset-2 hover:text-fg hover:underline"
        >
          {g.by_status.done} shipped
        </button>
      ) : (
        <span className="ml-auto text-[11px] text-faint">0 shipped</span>
      )}
    </div>
  )
}

// --- shipped (completed) work list ----------------------------------------------

// Completed items leave the active board; this overlay lists them (newest-completed first) so
// the owner can reopen one and read its Review + the archived Execution trace.
function ShippedList({
  items, onOpen, onClose,
}: {
  items: WorkItem[]
  onOpen: (id: string) => void
  onClose: () => void
}) {
  const sorted = [...items].sort((a, b) => (b.done_at ?? '').localeCompare(a.done_at ?? ''))
  return (
    <Modal
      onClose={onClose}
      maxW="max-w-lg"
      z="z-40"
      contain
      title={
        <span className="flex items-center gap-2">
          <Archive size={15} className="text-success" /> Shipped work
          <span className="rounded-full bg-hover px-2 py-0.5 text-[10px] font-medium tabular-nums text-muted">{sorted.length}</span>
        </span>
      }
    >
      <div className="max-h-[60vh] overflow-y-auto p-2">
        {sorted.length === 0 ? (
          <div className="p-4 text-sm text-faint">No completed work yet.</div>
        ) : (
          sorted.map((it) => (
            <button
              key={it.id}
              onClick={() => onOpen(it.id)}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left hover:bg-hover"
            >
              <Check size={13} className="shrink-0 text-success" />
              <span className="min-w-0 flex-1 truncate text-sm text-fg">{it.title || it.id}</span>
              <span className="shrink-0 font-mono text-[10px] text-faint">{fmtLocalDate(it.done_at)}</span>
            </button>
          ))
        )}
      </div>
    </Modal>
  )
}

// --- in-panel zoom frame --------------------------------------------------------

// Shared chrome for a zoomed-in store: a header with an icon back-button + title + (tag or a
// custom control) + meta, then the store's working surface.
function ZoomPanel({
  title, icon: Icon, tag, control, meta, onBack, children,
}: {
  title: string
  icon: typeof Boxes
  tag?: string
  control?: React.ReactNode
  meta: string
  onBack?: () => void
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl bg-surface shadow-sm">
      <div className="flex items-center gap-2.5 border-b border-line px-3 py-2.5">
        {onBack && (
          <button
            onClick={onBack}
            title="Back to map"
            aria-label="Back to map"
            className="rounded-md border border-line bg-surface p-1.5 text-muted hover:bg-hover hover:text-fg"
          >
            <ArrowLeft size={15} />
          </button>
        )}
        <Icon size={16} className="text-accent-text" />
        <span className="text-sm font-semibold text-fg">{title}</span>
        {control ? (
          <span className="ml-1">{control}</span>
        ) : tag ? (
          <span className="ml-1 rounded-full bg-hover px-2 py-0.5 text-[10px] uppercase tracking-wide text-faint">{tag}</span>
        ) : null}
        <span className="ml-auto text-[11px] text-faint">{meta}</span>
      </div>
      <div className="p-4">{children}</div>
    </div>
  )
}
