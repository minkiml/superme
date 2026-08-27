import { useEffect, useState } from 'react'
import { Hammer, ArrowLeft, ArrowRight, Boxes, Inbox, ArrowDown, Circle, Clock, Check, Bot, Archive, Shield, ChevronRight, OctagonAlert, Lock, PackageCheck, type LucideIcon } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'
import Modal from '@/ui/Modal'
import { getDev, getAttention, resumeWorkItem, markWorkItemSeen, isShipped, type AttentionData, type DevData, type InboxEntry, type WorkItem } from '@/lib/api'
import { invalidate, useLive } from '@/lib/live'
import { K, topicRepo } from '@/lib/live/keys'
import { navigate, useRoute } from '@/lib/router'
import { fmtLocalDate } from '@/lib/format'
import { useContainerWidth, PANE } from '@/lib/layout'
import { WorkspaceKanban, InboxView, isActive } from './panels'
import WorkGraphView from './WorkGraphView'
import { STATUS_LABEL, primaryStatus } from './common'
import WorkItemModal from './WorkItemModal'

// The Development dashboard — one context's dev-knowledge, live: the capture queue and the work in
// flight.
//
// Clicking a store zooms its working surface into the panel in place.

type Zoom = 'workspace' | 'inbox'

export default function DevDashboard({
  contextId = 'global',
  onBindItem,
  onUnbindItem,
  onDiscussNote,
  boundItemId,
  embedded = false,
}: {
  contextId?: string
  onBindItem?: (it: WorkItem, contextId: string) => void
  onUnbindItem?: () => void
  onDiscussNote?: (inboxId: number, title: string) => void
  boundItemId?: string | null
  embedded?: boolean // hosted inside the Dev workspace shell — the shell owns the header
}) {
  // Which item is drilled into is an ADDRESS, read off the router rather than threaded down as a
  // prop.
  const route = useRoute()
  const openId = route.name === 'item' && route.repoId === contextId ? route.itemId : null
  // An ADDRESS too, and the two are PEERS. An open drilldown implies the board, so closing it lands
  // there.
  const zoom: Zoom =
    route.name === 'item' ? 'workspace'
      : route.name === 'dev' && route.tab === 'workspace' ? 'workspace'
        : 'inbox'
  const setZoom = (z: Zoom) =>
    navigate({ name: 'dev', repoId: contextId, tab: z === 'workspace' ? 'workspace' : 'pipeline' })
  const [board, setBoard] = useState<'kanban' | 'graph'>('kanban') // Workspace projection toggle
  const [showShipped, setShowShipped] = useState(false) // the completed-items list overlay
  const [mutErr, setMutErr] = useState<string | null>(null) // a write this view attempted and failed

  // The cache takes the fastest interval asked for, so a live run speeds every view at once.
  const boardQ = useLive<DevData>(K.dev(contextId), () => getDev(contextId), 5000)
  const data = boardQ.data ?? null
  const running = !!data?.running?.length
  const fast = useLive<DevData>(running ? K.dev(contextId) : null, () => getDev(contextId), 2500)
  void fast // subscription only — it speeds the shared key up; `data` above is the single reader

  // Attention buckets. The SAME key DevWorkspace's badge subscribes to: one request feeds both.
  const attn = useLive<AttentionData>(K.devAttention(contextId), () => getAttention(contextId)).data ?? null

  const loading = boardQ.loading
  const err = mutErr ?? (boardQ.data ? null : boardQ.error ? String(boardQ.error) : null)

  // Refresh the whole repo topic, so the board, the feed and any open drilldown reflect a write at
  // once.
  const load = () => invalidate(topicRepo(contextId))

  // The one card-level action, because a stopped item's next act is unambiguous. Failures use the
  // same error slot.
  async function resumeItem(it: WorkItem) {
    try {
      await resumeWorkItem(it.id, contextId)
    } catch (e) {
      setMutErr(`Couldn't resume — ${e}`)
    }
    load()
  }

  // Looked up live, so it reflects the latest poll and auto-closes if the item disappears.
  const reviewItem = openId ? (data?.work_items.find((w) => w.id === openId) ?? null) : null

  // The one source every surface on this screen reads its needs-you verdict from; derived
  // independently they disagreed.
  const bucketOf: Record<string, string> = {}
  for (const tier of ['error', 'needs_you', 'deputy_working', 'running', 'unread'] as const) {
    for (const r of attn?.buckets?.[tier] ?? []) bucketOf[r.id] = tier
  }
  // A navigation, nothing more: the arrival effect owns the binding, so click and link agree.
  const openItem = (id: string) =>
    navigate({ name: 'item', repoId: contextId, itemId: id, tab: null, sub: null })
  // Back to the board the card came from, not to the capture queue.
  const closeItem = () => navigate({ name: 'dev', repoId: contextId, tab: 'workspace' })

  // Two jobs: bind the chat rail, and refuse to sit on an address for an item that is not there.
  useEffect(() => {
    if (!openId || !data) return
    const it = data.work_items.find((w) => w.id === openId)
    // `replace`, not push: this is a correction, and `back` must not walk into the dead address.
    if (!it) { navigate({ name: 'dev', repoId: contextId, tab: 'workspace' }, { replace: true }); return }
    // `gotoItem` binds BEFORE navigating, so this must not clobber a binding that is already
    // correct.
    if (boundItemId !== openId) onBindItem?.(it, contextId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openId, data])

  return (
    <div className="relative flex h-full flex-col">
      {reviewItem && (
        <WorkItemModal
          it={reviewItem}
          contextId={contextId}
          onClose={closeItem}
          onChanged={load}
          // Binding is already done on arrival; re-firing is what REVEALS the rail, which is the
          // point.
          onOpenChat={() => onBindItem?.(reviewItem, contextId)}
          bucket={bucketOf[reviewItem.id]}
        />
      )}
      {showShipped && data && (
        <ShippedList
          items={data.work_items.filter(isShipped)}
          contextId={contextId}
          onSeen={load}
          onOpen={(id) => {
            setShowShipped(false)
            openItem(id)
          }}
          onClose={() => setShowShipped(false)}
        />
      )}
      {/* No manual Refresh — the board auto-polls (steady 5s + a faster 2.5s while a run is live). */}
      {!embedded && (
        <PageHeader
          icon={Hammer}
          title="Development"
          subtitle="Live environment for this SuperMe's dev-knowledge — operate every store from one map"
          badge="prototype"
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
                // Leaving for the Inbox means no work-item, so drop the binding and fall back to
                // the general session.
                if (z === 'inbox') onUnbindItem?.()
              }}
            />

            {zoom === 'workspace' ? (
              <ZoomPanel
                title="Workspace"
                icon={Bot}
                meta={`${data.work_items.filter(isActive).length} active`}
              >
                {/* Kanban ⇄ Graph: two projections of the same population (replaces the Graph tab). */}
                <div className="mb-3 flex justify-end">
                  <div className="inline-flex rounded-md border border-line bg-sunken p-0.5">
                    {(['kanban', 'graph'] as const).map((v) => (
                      <button
                        key={v}
                        onClick={() => setBoard(v)}
                        className={`rounded px-2.5 py-1 text-[11px] font-medium capitalize transition ${
                          board === v ? 'bg-surface text-fg shadow-sm' : 'text-muted hover:text-fg'
                        }`}
                      >
                        {v}
                      </button>
                    ))}
                  </div>
                </div>
                <DeputyStrip items={data.work_items} buckets={bucketOf} />
                <WorkspaceStats items={data.work_items} buckets={bucketOf}
                                shipped={data.glance.shipped ?? 0}
                                onShowShipped={() => setShowShipped(true)} />
                {board === 'graph' ? (
                  <WorkGraphView contextId={contextId} onBindItem={onBindItem} embedded />
                ) : (
                <WorkspaceKanban
                  items={data.work_items}
                  // The arrival effect binds the chat, so reading and discussing sit side by side.
                  onOpen={(it) => openItem(it.id)}
                  onResume={resumeItem}
                  running={data.running}
                  boundItemId={boundItemId}
                  buckets={bucketOf}
                />
                )}
              </ZoomPanel>
            ) : (
              <ZoomPanel title="Inbox" icon={Inbox} meta={`${data.glance.inbox_open ?? 0} open`}>
                <InboxView entries={data.inbox} contextId={contextId} onChanged={load} onDiscussNote={onDiscussNote} />
              </ZoomPanel>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// Shown only when the deputy is shepherding autopilot items, so it is silent on a hand-driven
// board.
//
// It reads the same bucket map as everything else; only the DENOMINATOR is legitimately different,
// never the rule.
function DeputyStrip({ items, buckets }: { items: WorkItem[]; buckets: Record<string, string> }) {
  const auto = items.filter((w) => w.autopilot && w.status !== 'done' && !w.outcome)
  if (auto.length === 0) return null
  const waiting = auto.filter((w) => primaryStatus(w, buckets[w.id]) === 'awaiting_human').length
  return (
    <div className="flex items-center gap-2 rounded-md border border-warn/30 bg-warn/5 px-3 py-1.5 text-[11px]">
      <Shield size={13} className="shrink-0 text-warn" />
      <span className="font-medium text-fg">Deputy</span>
      <span className="text-muted">shepherding {auto.length} on autopilot</span>
      {waiting > 0 && (
        <span className="ml-auto rounded-full bg-warn/15 px-2 py-0.5 font-semibold text-warn">
          {waiting} need{waiting === 1 ? 's' : ''} you
        </span>
      )}
    </div>
  )
}

// --- attention strip ---
//
// Strict-priority rows from durable state. Terminal items live only here, and opening one stamps it
// seen.

const TIER_STYLE: Record<string, { dot: string; label: string }> = {
  error: { dot: 'bg-danger', label: 'Error' },
  needs_you: { dot: 'bg-warn', label: 'Needs you' },
  deputy_working: { dot: 'bg-deputy', label: 'Deputy reviewing' },
  running: { dot: 'bg-success', label: 'Running' },
  unread: { dot: 'bg-accent', label: 'Unread' },
}

// A tier is unbounded: `needs_you` holds every item waiting on the owner, and nothing leaves it
// until they act. Three per tier keeps the strip a strip; the count beside the label is the real
// size, and the rest is one click away.
const COLLAPSED = 3

function AttentionStrip({ attn, onOpen }: { attn: AttentionData; onOpen: (id: string) => void }) {
  const [ref, w] = useContainerWidth<HTMLDivElement>()
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  // Narrow, a row is one thing: WHICH item. The reason costs the most width and says the least.
  const tight = w > 0 && w < PANE.narrow
  const tiers = (['error', 'needs_you', 'deputy_working', 'running', 'unread'] as const)
    .map((t) => ({ tier: t, rows: attn.buckets?.[t] ?? [] }))
    .filter((x) => x.rows.length > 0)
  if (tiers.length === 0) return null
  return (
    <div ref={ref} className="space-y-1 rounded-xl border border-line bg-surface px-2.5 py-2">
      {tiers.map(({ tier, rows }) => {
        const open = expanded[tier] ?? false
        const shown = open ? rows : rows.slice(0, COLLAPSED)
        const hidden = rows.length - shown.length
        return (
          <div key={tier} className={`flex gap-x-2 gap-y-0.5 ${tight ? 'flex-col items-stretch' : 'flex-wrap items-center'}`}>
            <span className={`flex shrink-0 items-center gap-1 text-[9px] font-semibold uppercase tracking-wide text-muted ${tight ? '' : 'w-[4.5rem]'}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${TIER_STYLE[tier].dot}`} />
              {TIER_STYLE[tier].label}
              {/* Always: collapsed, this is the only place the real size shows. */}
              <span className="tabular-nums text-faint">{rows.length}</span>
            </span>
            {shown.map((r) => (
              <button
                key={r.id}
                onClick={() => onOpen(r.id)}
                title={`${r.title} — ${r.reason}`}
                className={`truncate rounded border border-line bg-sunken px-1.5 py-px text-left text-[11px] leading-tight text-fg hover:bg-hover ${
                  tight ? 'w-full' : 'max-w-[13rem]'
                }`}
              >
                {r.title}
                {!tight && <span className="ml-1 text-[9px] text-faint">{r.reason}</span>}
              </button>
            ))}
            {(hidden > 0 || open) && (
              <button
                onClick={() => setExpanded((e) => ({ ...e, [tier]: !open }))}
                className={`shrink-0 rounded px-1 py-px text-[10px] text-muted hover:bg-hover hover:text-fg ${
                  tight ? 'w-full text-left' : ''
                }`}
              >
                {open ? 'less' : `+${hidden}`}
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}

// --- the environment (active pipeline) ------------------------------------------

function EnvMap({ data, selected, onSelect }: { data: DevData; selected: Zoom; onSelect: (z: Zoom) => void }) {
  const g = data.glance
  const openInbox = data.inbox.filter((e) => e.status === 'open')
  const activeCount = data.work_items.filter(isActive).length
  // One sentence: below the width where all three fit, it TURNS and the arrow points down.
  const [ref, w] = useContainerWidth<HTMLDivElement>()
  const down = w > 0 && w < 460
  return (
    <div ref={ref} className={`flex gap-2 ${down ? 'flex-col' : 'flex-wrap items-stretch'}`}>
      <StoreCard
        className={down ? 'w-full' : 'flex-1'} icon={Inbox} label="Inbox" subtitle="capture queue"
        selected={selected === 'inbox'} onClick={() => onSelect('inbox')}
      >
        <Metric n={g.inbox_open ?? 0} unit="open" tone="text-fg" />
        <KindBreakdown entries={openInbox} />
      </StoreCard>

      <Connector label="push" down={down} />

      <StoreCard
        className={down ? 'w-full' : 'flex-1'} icon={Bot} label="Workspace" subtitle="the living plan · worktree"
        selected={selected === 'workspace'} onClick={() => onSelect('workspace')}
      >
        <StatusDots item={g.by_status} total={activeCount} done={g.shipped ?? 0} />
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
function Connector({ label, down }: { label: string; down?: boolean }) {
  if (down) {
    return (
      <div className="flex items-center justify-center gap-1.5 py-0.5">
        <span className="flex flex-col items-center text-faint">
          <span className="h-4 w-px bg-line" />
          <ArrowDown size={14} className="-mt-1.5" />
        </span>
        <span className="text-[10.5px] text-muted">{label}</span>
      </div>
    )
  }
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

// Open-inbox breakdown by kind (item · note), nonzero only.
function KindBreakdown({ entries }: { entries: InboxEntry[] }) {
  const kinds: InboxEntry['kind'][] = ['item', 'note']
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
function StatusDots({ item, total, done }: {
  item: Record<string, number>; total: number; done: number
}) {
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
        {/* Done is the same number as shipped: work that was dropped is neither, it just leaves
            the board. */}
        <span className="text-[11px] text-faint">· <span className="tabular-nums text-success">{done}</span> done</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
        {dot(Circle, item.active ?? 0, STATUS_LABEL.active, 'fill-current text-accent-text')}
        {dot(Clock, item.awaiting_human ?? 0, STATUS_LABEL.awaiting_human, item.awaiting_human ? 'text-danger' : 'text-faint')}
      </div>
    </div>
  )
}

// Counted from the SAME verdict the cards render by, not a roll-up over stored status — those
// disagreed.
//
// `shipped` still comes from the roll-up, because terminal items leave the board.
function WorkspaceStats({ items, buckets, shipped, onShowShipped }: {
  items: WorkItem[]; buckets: Record<string, string>; shipped: number; onShowShipped: () => void
}) {
  const [ref, w] = useContainerWidth<HTMLDivElement>()
  const tight = w > 0 && w < PANE.narrow
  const n = (want: string) =>
    items.filter((it) => !it.done_at && primaryStatus(it, buckets[it.id]) === want).length
  const cell = (label: string, n: number, tone = 'text-fg') => (
    <div className="flex flex-col">
      <span className={`text-xl font-semibold leading-none tabular-nums ${tone}`}>{n}</span>
      <span className="mt-1 text-[10px] uppercase tracking-wide text-muted">{label}</span>
    </div>
  )
  // Narrow, each stat keeps its MARK and number: five figures on one line stay comparable.
  const chip = (Icon: LucideIcon, label: string, n: number, tone: string) => (
    <span title={`${n} ${label}`} className={`inline-flex items-center gap-1 ${n > 0 ? tone : 'text-faint'}`}>
      <Icon size={13} />
      <span className="text-[15px] font-semibold tabular-nums">{n}</span>
    </span>
  )
  if (tight) {
    return (
      <div ref={ref} className="mb-4 flex items-center justify-between gap-2 rounded-xl bg-sunken px-3 py-2.5">
        {chip(OctagonAlert, STATUS_LABEL.error, n('error'), 'text-danger')}
        {chip(Circle, STATUS_LABEL.active, n('active'), 'text-accent-text')}
        {chip(Clock, STATUS_LABEL.awaiting_human, n('awaiting_human'), 'text-warn')}
        {chip(Lock, STATUS_LABEL.awaiting_child, n('awaiting_child'), 'text-muted')}
        <button
          onClick={onShowShipped}
          disabled={shipped === 0}
          title={`${shipped} shipped — view completed work`}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-success enabled:hover:bg-hover disabled:text-faint"
        >
          <PackageCheck size={13} />
          <span className="text-[15px] font-semibold tabular-nums">{shipped}</span>
        </button>
      </div>
    )
  }
  return (
    <div ref={ref} className="mb-4 flex flex-wrap items-center gap-x-7 gap-y-3 rounded-xl bg-sunken px-4 py-3">
      {/* Always rendered, greyed at zero: a row that changes width shifts every other number
          leftward. */}
      {cell('stopped', n('error'), n('error') > 0 ? 'text-danger' : 'text-faint')}
      {cell(STATUS_LABEL.active, n('active'), 'text-accent-text')}
      {cell(STATUS_LABEL.awaiting_human, n('awaiting_human'), 'text-warn')}
      {cell(STATUS_LABEL.awaiting_child, n('awaiting_child'))}
      {/* A TILE, not a footnote: as a caption, the only way into completed work read as
          decoration. */}
      {shipped > 0 ? (
        <button
          onClick={onShowShipped}
          title="View completed work + their archived execution traces"
          className="group ml-auto flex flex-col items-start rounded-lg px-2 py-1 -my-1 text-left hover:bg-hover"
        >
          <span className="text-xl font-semibold leading-none tabular-nums text-success">
            {shipped}
          </span>
          <span className="mt-1 inline-flex items-center gap-0.5 text-[10px] uppercase tracking-wide text-muted group-hover:text-fg">
            shipped <ChevronRight size={11} className="-mr-0.5" />
          </span>
        </button>
      ) : (
        <div className="ml-auto flex flex-col">
          <span className="text-xl font-semibold leading-none tabular-nums text-faint">0</span>
          <span className="mt-1 text-[10px] uppercase tracking-wide text-muted">shipped</span>
        </div>
      )}
    </div>
  )
}

// --- shipped (completed) work list ----------------------------------------------

// Completed items leave the active board; this overlay lists them (newest-completed first) so
// the owner can reopen one and read its Review + the archived Execution trace.
function ShippedList({
  items, contextId, onSeen, onOpen, onClose,
}: {
  items: WorkItem[]
  contextId: string
  onSeen: () => void
  onOpen: (id: string) => void
  onClose: () => void
}) {
  const sorted = [...items].sort((a, b) => (b.done_at ?? '').localeCompare(a.done_at ?? ''))
  // Opening this list IS reading the notice, so the unread tier stops accumulating what was shown.
  useEffect(() => {
    const unseen = sorted.filter((it) => !it.seen_at)
    if (!unseen.length) return
    Promise.allSettled(unseen.map((it) => markWorkItemSeen(it.id, contextId))).then(onSeen)
    // Mount-only: re-running as the list changes would re-stamp on every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
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
              {/* The id under the title: several titles read alike, and the id is what every other
                  surface keys on. */}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-fg">{it.title || it.id}</span>
                <span className="block font-mono text-[10px] text-faint">{it.id}</span>
              </span>
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
