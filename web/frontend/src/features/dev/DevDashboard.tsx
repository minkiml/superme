import { useEffect, useState } from 'react'
import { Hammer, ArrowLeft, ArrowRight, Boxes, Inbox, Circle, Clock, Check, Bot, Archive, Shield, ChevronRight } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'
import Modal from '@/ui/Modal'
import ConfirmDialog from '@/ui/ConfirmDialog'
import { getDev, getAttention, resumeWorkItem, markWorkItemSeen, type AttentionData, type DevData, type DevGlance, type InboxEntry, type WorkItem } from '@/lib/api'
import { invalidate, useLive } from '@/lib/live'
import { K, topicRepo } from '@/lib/live/keys'
import { navigate, useRoute } from '@/lib/router'
import { fmtLocalDate } from '@/lib/format'
import { WorkspaceKanban, InboxView, isActive } from './panels'
import WorkGraphView from './WorkGraphView'
import { STATUS_LABEL, primaryStatus } from './common'
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
  // Which item is drilled into is an ADDRESS (`/repo/:id/item/:itemId`), read straight off the
  // router rather than threaded down as a prop. That is what retired `focusItem`: the attention
  // centre used to hand an id across a mount boundary for this view to consume once, which is
  // exactly the job a path does natively.
  const route = useRoute()
  const openId = route.name === 'item' && route.repoId === contextId ? route.itemId : null
  // Which store is zoomed in is an ADDRESS too: `/repo/:id/dev` is the capture queue (the landing
  // pane, unchanged) and `/repo/:id/dev/workspace` is the board. They are PEERS in the tab slot
  // rather than nested under `pipeline` — you are looking at one or the other, never at a workspace
  // inside an inbox (§6.1).
  //
  // An open item drilldown implies the board: an item lives on it, so closing the drilldown should
  // land on the card you were reading, not back on the queue.
  const zoom: Zoom =
    route.name === 'item' ? 'workspace'
      : route.name === 'dev' && route.tab === 'workspace' ? 'workspace'
        : 'inbox'
  const setZoom = (z: Zoom) =>
    navigate({ name: 'dev', repoId: contextId, tab: z === 'workspace' ? 'workspace' : 'pipeline' })
  const [board, setBoard] = useState<'kanban' | 'graph'>('kanban') // Workspace projection toggle
  const [showShipped, setShowShipped] = useState(false) // the completed-items list overlay
  const [mutErr, setMutErr] = useState<string | null>(null) // a write this view attempted and failed

  // The board keeps a faster cadence while ANY item has a run in flight, and the ordinary one when
  // the repo is idle — the same rule as before, now expressed as the subscription's interval rather
  // than a self-re-arming `setTimeout` chain. Because the cache takes the FASTEST interval any
  // subscriber asks for, a live run speeds the shared key up for every view of it at once.
  const boardQ = useLive<DevData>(K.dev(contextId), () => getDev(contextId), 5000)
  const data = boardQ.data ?? null
  const running = !!data?.running?.length
  const fast = useLive<DevData>(running ? K.dev(contextId) : null, () => getDev(contextId), 2500)
  void fast // subscription only — it speeds the shared key up; `data` above is the single reader

  // Attention buckets (S7). The SAME key DevWorkspace's badge subscribes to: one request feeds both.
  const attn = useLive<AttentionData>(K.devAttention(contextId), () => getAttention(contextId)).data ?? null

  const loading = boardQ.loading
  const err = mutErr ?? (boardQ.data ? null : boardQ.error ? String(boardQ.error) : null)

  // A write this view made — refresh the whole repo topic so the board, the attention feed and any
  // open drilldown all reflect it at once, instead of each waiting out its own tick.
  const load = () => invalidate(topicRepo(contextId))

  // Resume a STOPPED item straight from its card (R4) — the one card-level action, because a
  // stopped item's next act is unambiguous. Failures surface in the same error slot every other
  // write on this view uses; the board refresh then shows whether the run actually took.
  async function resumeItem(it: WorkItem) {
    try {
      await resumeWorkItem(it.id, contextId)
    } catch (e) {
      setMutErr(`Couldn't resume — ${e}`)
    }
    load()
  }

  // The item open in the review popup — looked up live so it reflects the latest poll/reload
  // (and auto-closes if the item disappears, e.g. after a delete).
  const reviewItem = openId ? (data?.work_items.find((w) => w.id === openId) ?? null) : null

  // id → attention tier. The card tint (S7), and — since D2 — the one source every surface on this
  // screen reads its "needs you" verdict from: the cards, the stat row, the deputy strip and the
  // drilldown badge. When three of them derived it independently they disagreed by two.
  const bucketOf: Record<string, string> = {}
  for (const tier of ['error', 'needs_you', 'deputy_working', 'running', 'unread'] as const) {
    for (const r of attn?.buckets?.[tier] ?? []) bucketOf[r.id] = tier
  }
  // Opening an item is a navigation, nothing more. The chat binding is NOT done here — the arrival
  // effect below owns it, so a click and a deep link produce the same result instead of each
  // carrying its own copy of the rule.
  const openItem = (id: string) =>
    navigate({ name: 'item', repoId: contextId, itemId: id, tab: null, sub: null })
  // Back to the board the card came from, not to the capture queue.
  const closeItem = () => navigate({ name: 'dev', repoId: contextId, tab: 'workspace' })

  // Arriving at an item address — by click, by the attention centre's Open, or by a pasted link.
  // Two jobs, both of which used to live in the click handler and so didn't happen for deep links:
  // bind the chat rail to the item's thread, and refuse to sit on an address for an item that
  // isn't there (deleted, or a mistyped id) rather than showing the board under a URL that lies.
  useEffect(() => {
    if (!openId || !data) return
    const it = data.work_items.find((w) => w.id === openId)
    // `replace`, not push: this is a correction, and `back` must not walk into the dead address.
    if (!it) { navigate({ name: 'dev', repoId: contextId, tab: 'workspace' }, { replace: true }); return }
    // `gotoItem` binds BEFORE navigating (it has the hold's session_id and the board's data may not
    // have landed yet — Fix C), so this must not clobber a binding that is already correct.
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
          // The ask-card's one-click "answer in chat". Binding is already done on arrival (the effect
          // above); re-firing it is what REVEALS the rail, which is the part the owner needs.
          onOpenChat={() => onBindItem?.(reviewItem, contextId)}
          bucket={bucketOf[reviewItem.id]}
        />
      )}
      {showShipped && data && (
        <ShippedList
          items={data.work_items.filter((w) => w.done_at)}
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
                                shipped={data.glance.by_status.done ?? 0}
                                onShowShipped={() => setShowShipped(true)} />
                {board === 'graph' ? (
                  <WorkGraphView contextId={contextId} onBindItem={onBindItem} embedded />
                ) : (
                <WorkspaceKanban
                  items={data.work_items}
                  // Clicking a card navigates to the item's address; the arrival effect binds the
                  // chat to its session — read + discuss side by side (the popup overlays only the
                  // dashboard column).
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
                <InboxView entries={data.inbox} contextId={contextId} onChanged={load} />
              </ZoomPanel>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// The deputy-activity strip (autopilot slice 4b) — fills the space above the stat cards ONLY when
// the deputy is shepherding autopilot items, so it's silent on a hand-driven board. Two counts a
// glance answers: how many the deputy is carrying, and how many it has handed back to you. Derived
// from the already-loaded items (no extra fetch); `awaiting_human` on an autopilot item is a deputy
// escalation (the deputy is the one that paged you).
//
// D2: this read the RAW `w.status` while the board beside it applied the derived gate rule, so it
// under-counted by the number of stalled items — 5 sitting next to a 7 next to a badge of 6. It now
// reads the same bucket map as everything else; only the DENOMINATOR is legitimately different
// (autopilot items only), never the rule.
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

// --- attention strip (S7/D10) -----------------------------------------------------
// Strict-priority rows derived from durable state: what needs YOU (orange) > what's running
// (green) > unread closeouts (blue). Terminal items live only here (they're off the board) —
// clicking a row opens its drilldown, which stamps it seen and clears the blue.

const TIER_STYLE: Record<string, { dot: string; label: string }> = {
  error: { dot: 'bg-danger', label: 'Error' },
  needs_you: { dot: 'bg-warn', label: 'Needs you' },
  deputy_working: { dot: 'bg-deputy', label: 'Deputy reviewing' },
  running: { dot: 'bg-success', label: 'Running' },
  unread: { dot: 'bg-accent', label: 'Unread' },
}

function AttentionStrip({ attn, onOpen }: { attn: AttentionData; onOpen: (id: string) => void }) {
  const tiers = (['error', 'needs_you', 'deputy_working', 'running', 'unread'] as const)
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
        {/* Done = officially closed (approved through close) — kept beside the live count, off the
            live-status dots below (a shipped item has left the board). */}
        <span className="text-[11px] text-faint">· <span className="tabular-nums text-success">{item.done ?? 0}</span> done</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
        {dot(Circle, item.active ?? 0, STATUS_LABEL.active, 'fill-current text-accent-text')}
        {dot(Clock, item.awaiting_human ?? 0, STATUS_LABEL.awaiting_human, item.awaiting_human ? 'text-danger' : 'text-faint')}
      </div>
    </div>
  )
}

// The work-item stat row — moved out of the page top to sit under the Workspace panel header.
// Counted from the SAME verdict the cards render by (the item's attention bucket), not from the
// backend's `by_status` roll-up over stored status: those two disagreed on any item whose hold was
// derived rather than stored, so the row could read "1 in progress" above three NEEDS YOU cards.
// One rule, one screen. `shipped` still comes from the roll-up — terminal items leave the board.
function WorkspaceStats({ items, buckets, shipped, onShowShipped }: {
  items: WorkItem[]; buckets: Record<string, string>; shipped: number; onShowShipped: () => void
}) {
  const n = (want: string) =>
    items.filter((it) => !it.done_at && primaryStatus(it, buckets[it.id]) === want).length
  const cell = (label: string, n: number, tone = 'text-fg') => (
    <div className="flex flex-col">
      <span className={`text-xl font-semibold leading-none tabular-nums ${tone}`}>{n}</span>
      <span className="mt-1 text-[10px] uppercase tracking-wide text-muted">{label}</span>
    </div>
  )
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-7 gap-y-3 rounded-xl bg-sunken px-4 py-3">
      {/* Stopped work leads the row and appears ONLY when there is some (R2): a permanent "0
          stopped" tile trains the eye to skip the one place it must not. */}
      {n('error') > 0 && cell('stopped', n('error'), 'text-danger')}
      {cell(STATUS_LABEL.active, n('active'), 'text-accent-text')}
      {cell(STATUS_LABEL.awaiting_human, n('awaiting_human'), 'text-warn')}
      {cell(STATUS_LABEL.awaiting_child, n('awaiting_child'))}
      {/* Shipped is a TILE, not a footnote. It carried the same weight as a caption — 11px, faint,
          no affordance — while being the only way into completed work and its execution traces, so
          it read as decoration and went unclicked. It keeps the row's tile shape (count over label)
          and gains the chevron + hover that say it opens something. */}
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
  // Opening this list IS reading the notice — every completed item is named on screen, so they all
  // get their read receipt here. Before this, `seen_at` was stamped in ONE place: opening an item's
  // own drilldown. So the `unread` attention tier (terminal + no `seen_at`) could only ever grow —
  // 21 shipped items meant 21 chips that no amount of looking at the list would clear, and the only
  // way down was opening 21 modals one at a time. The tier still does its job for the NEXT thing
  // that lands overnight; it just stops accumulating what the owner has already been shown.
  useEffect(() => {
    const unseen = sorted.filter((it) => !it.seen_at)
    if (!unseen.length) return
    Promise.allSettled(unseen.map((it) => markWorkItemSeen(it.id, contextId))).then(onSeen)
    // Mount-only: the list is a snapshot of what was on screen when it opened. Re-running as
    // `sorted` changes underneath would re-stamp on every board poll.
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
              {/* The id under the title, not instead of it. Titles here are the owner's own words
                  and several read alike ("tally count should…", "tally list should…"), so the row
                  the owner clicked and the item they then talk about were only joinable by opening
                  it. The id is what every other surface — branch name, worktree path, artifacts
                  folder, the log — keys on. */}
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
