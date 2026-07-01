import { useEffect, useState } from 'react'
import { Hammer, RefreshCw, ArrowLeft, ArrowRight, Boxes, Inbox, Circle, Clock, Check, Bot, X, Archive } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'
import Dropdown from '@/ui/Dropdown'
import { getDev, planWorkItem, deleteWorkItem, type DevData, type DevGlance, type InboxEntry, type WorkItem } from '@/lib/api'
import { fmtLocalDate } from '@/lib/format'
import { WorkspaceKanban, PlanList, InboxView, isActive } from './panels'
import WorkItemModal from './WorkItemModal'

// The Development dashboard — a live environment for one context's dev-knowledge. Today it shows
// the active pipeline: Inbox → Workspace (the work in flight). Clicking a store zooms its working
// surface into the panel in place (back button). Decisions/Learnings were removed from the surface
// pending a clearer model; the abstract schema lives on "Model". Built context-parameterized
// (D-011) — only global is wired today.

type Zoom = 'workspace' | 'inbox' | null
type WsView = 'kanban' | 'plan'
const WS_VIEWS = [
  { value: 'kanban', label: 'Worktree · kanban' },
  { value: 'plan', label: 'Plan · list' },
]

export default function DevDashboard({
  contextId = 'global',
  onBindItem,
  onUnbindItem,
  boundItemId,
}: {
  contextId?: string
  onBindItem?: (it: WorkItem, contextId: string) => void
  onUnbindItem?: () => void
  boundItemId?: string | null
}) {
  const [data, setData] = useState<DevData | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [zoom, setZoom] = useState<Zoom>(null)
  const [wsView, setWsView] = useState<WsView>('kanban')
  const [reviewId, setReviewId] = useState<string | null>(null) // work-item open in the review popup
  const [showShipped, setShowShipped] = useState(false) // the completed-items list overlay

  async function load() {
    setLoading(true)
    setErr(null)
    try {
      setData(await getDev(contextId))
    } catch (e) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }

  // Headless "Plan it": kick off a background /plan turn, then refresh so the card flips
  // to its "planning…" state (the running-poll below keeps reloading until it settles).
  async function handlePlan(it: WorkItem, model?: string) {
    try {
      await planWorkItem(it.id, contextId, model)
    } catch {
      /* surfaced on next load */
    }
    load()
  }

  // Hard-delete a plan/design item and erase its trace (folder + session + inbox row). Confirm
  // first — it's irreversible. If it's the bound item, drop the chat binding too.
  async function handleDelete(it: WorkItem) {
    if (!window.confirm(`Delete "${it.title || it.id}"? This removes the work-item, its session, and its inbox row. This can't be undone.`)) return
    try {
      await deleteWorkItem(it.id, contextId)
      if (boundItemId === it.id) onUnbindItem?.()
    } catch (e) {
      alert(`Couldn't delete — ${e}`)
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
    const t = setTimeout(load, 2500)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.running])

  // The item open in the review popup — looked up live so it reflects the latest poll/reload
  // (and auto-closes if the item disappears, e.g. after a delete).
  const reviewItem = reviewId ? (data?.work_items.find((w) => w.id === reviewId) ?? null) : null

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
      <PageHeader
        icon={Hammer}
        title="Development"
        subtitle="Live environment for this SuperMe's dev-knowledge — operate every store from one map"
        badge="prototype"
        right={
          <button
            onClick={load}
            disabled={loading}
            title="Refresh"
            aria-label="Refresh"
            className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        }
      />

      <div className="min-h-0 flex-1 overflow-auto">
        {loading && !data ? (
          <div className="p-6 text-sm text-muted">Loading…</div>
        ) : err ? (
          <div className="p-6 text-sm text-danger">Couldn’t load dev knowledge — {err}</div>
        ) : !data ? null : (
          <div className="mx-auto max-w-5xl p-6">
            {zoom === null ? (
              <EnvMap data={data} onZoom={setZoom} />
            ) : zoom === 'workspace' ? (
              <ZoomPanel
                title="Workspace"
                icon={Boxes}
                meta={`${data.work_items.filter(isActive).length} active`}
                onBack={() => setZoom(null)}
                control={<Dropdown value={wsView} options={WS_VIEWS} onChange={(v) => setWsView(v as WsView)} />}
              >
                <WorkspaceStats g={data.glance} onShowShipped={() => setShowShipped(true)} />
                {wsView === 'kanban' ? (
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
                  />
                ) : (
                  <PlanList
                    items={data.work_items}
                    onBind={onBindItem ? (it) => onBindItem(it, contextId) : undefined}
                    onPlan={handlePlan}
                    onDelete={handleDelete}
                    running={data.running}
                    boundItemId={boundItemId}
                  />
                )}
              </ZoomPanel>
            ) : (
              <ZoomPanel title="Inbox" icon={Inbox} tag="capture queue" meta={`${data.glance.inbox_open ?? 0} open`} onBack={() => setZoom(null)}>
                <InboxView entries={data.inbox} contextId={contextId} onChanged={load} />
              </ZoomPanel>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// --- the environment (active pipeline) ------------------------------------------

function EnvMap({ data, onZoom }: { data: DevData; onZoom: (z: Zoom) => void }) {
  const g = data.glance
  const openInbox = data.inbox.filter((e) => e.status === 'open')
  const activeCount = data.work_items.filter(isActive).length
  return (
    <section>
      <ZoneLabel>Active pipeline</ZoneLabel>
      <div className="flex flex-wrap items-stretch gap-2">
        <StoreCard className="w-60" icon={Inbox} label="Inbox" subtitle="capture queue" hint="open →" onClick={() => onZoom('inbox')}>
          <Metric n={g.inbox_open ?? 0} unit="open" tone={g.inbox_open ? 'text-accent-text' : 'text-muted'} />
          <KindBreakdown entries={openInbox} />
        </StoreCard>

        <Connector label="push" />

        <StoreCard className="w-72" hub icon={Boxes} label="Workspace" subtitle="the living plan · worktree" hint="zoom in →" onClick={() => onZoom('workspace')}>
          <StatusDots item={g.by_status} total={activeCount} />
          <AgentChip n={0} />
        </StoreCard>
      </div>
    </section>
  )
}

function ZoneLabel({ children }: { children: React.ReactNode }) {
  return <div className="mb-3 text-[11px] font-medium uppercase tracking-wider text-faint">{children}</div>
}

// A clickable store card — flows in the layout (not absolutely positioned).
function StoreCard({
  icon: Icon, label, subtitle, hint, hub, onClick, children, className = '',
}: {
  icon: typeof Boxes
  label: string
  subtitle: string
  hint: string
  hub?: boolean
  onClick: () => void
  children: React.ReactNode
  className?: string
}) {
  return (
    <button
      onClick={onClick}
      className={`group flex flex-col rounded-xl border bg-surface px-3.5 py-3 text-left shadow-sm transition hover:border-accent hover:shadow-md ${
        hub ? 'border-accent/40 ring-1 ring-accent/20' : 'border-line'
      } ${className}`}
    >
      <div className="flex items-center gap-2">
        <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg ${hub ? 'bg-accent text-on-accent' : 'bg-hover text-muted group-hover:text-fg'}`}>
          <Icon size={15} />
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-fg">{label}</span>
          <span className="block truncate text-[10.5px] text-faint">{subtitle}</span>
        </span>
      </div>
      <div className="mt-2.5">{children}</div>
      <span className="mt-2 text-[10.5px] font-medium text-faint opacity-0 transition group-hover:text-accent-text group-hover:opacity-100">
        {hint}
      </span>
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
        {dot(Circle, item.in_progress ?? 0, 'in progress', 'fill-current text-accent-text')}
        {dot(Clock, item.waiting ?? 0, 'waiting', item.waiting ? 'text-danger' : 'text-faint')}
        {dot(Check, item.done ?? 0, 'done', 'text-success')}
      </div>
    </div>
  )
}

// Active-agents indicator on the Workspace — a placeholder until the orchestration layer lands.
function AgentChip({ n }: { n: number }) {
  return (
    <span className="mt-2 inline-flex items-center gap-1 rounded-full border border-dashed border-line px-2 py-0.5 text-[10px] text-faint">
      <Bot size={11} /> {n} agents · soon
    </span>
  )
}

// The work-item stat row — moved out of the page top to sit under the Workspace panel header.
function WorkspaceStats({ g, onShowShipped }: { g: DevGlance; onShowShipped: () => void }) {
  const cell = (label: string, n: number, tone = 'text-fg') => (
    <div className="flex items-baseline gap-1.5">
      <span className={`text-base font-semibold tabular-nums ${tone}`}>{n}</span>
      <span className="text-[11px] uppercase tracking-wide text-muted">{label}</span>
    </div>
  )
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-line bg-sunken/50 px-3.5 py-2">
      {cell('in progress', g.by_status.in_progress ?? 0, 'text-accent-text')}
      {cell('waiting', g.by_status.waiting ?? 0, 'text-warn')}
      {cell('queued', g.by_status.queued ?? 0)}
      {cell('blocked', g.blocked.length, g.blocked.length ? 'text-danger' : 'text-fg')}
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
    <div
      className="absolute inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/50 p-4 sm:p-8"
      onClick={onClose}
    >
      <div
        className="my-auto w-full max-w-lg rounded-xl border border-line bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <Archive size={15} className="text-success" />
          <h2 className="text-[15px] font-semibold text-fg">Shipped work</h2>
          <span className="rounded-full bg-hover px-2 py-0.5 text-[10px] text-muted">{sorted.length}</span>
          <button onClick={onClose} aria-label="Close" className="ml-auto rounded p-1 text-muted hover:bg-hover hover:text-fg">
            <X size={16} />
          </button>
        </div>
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
      </div>
    </div>
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
  onBack: () => void
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-line bg-surface">
      <div className="flex items-center gap-2.5 border-b border-line px-3 py-2.5">
        <button
          onClick={onBack}
          title="Back to map"
          aria-label="Back to map"
          className="rounded-md border border-line bg-surface p-1.5 text-muted hover:bg-hover hover:text-fg"
        >
          <ArrowLeft size={15} />
        </button>
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
