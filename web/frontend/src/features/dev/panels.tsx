import { useState, useEffect, type ReactNode } from 'react'
import { Plus, Trash2, CornerDownRight, GitBranch, ArrowRight, X, Bot, User, Loader2, MessageSquareText, MessagesSquare } from 'lucide-react'
import Dropdown from '@/ui/Dropdown'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import SectionHeader from '@/ui/SectionHeader'
import TabBar from '@/ui/TabBar'
import Toggle from '@/ui/Toggle'
import { useEditGate, EditActions } from '@/ui/EditGate'
import { addInbox, updateInbox, deleteInbox, pushInbox, getRepos, getInboxBrief, saveInboxBrief,
         type WorkItem, type InboxEntry, type InboxKind, type InboxBrief } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { fmtLocal, fmtTokens, fmtDuration, fmtModel, toModelKey, MODELS as MODEL_CATALOG, DEFAULT_MODEL, EFFORTS as EFFORT_CATALOG, DEFAULT_EFFORT } from '@/lib/format'
import { PHASE_LABEL, PHASE_VERB, STATUS_COLOR, STATUS_LABEL, STATUS_STRIPE, primaryStatus,
         agoLabel, researchKindLabel, KIND_TEXT, workKindLabel } from './common'
import { useContainerWidth } from '@/lib/layout'

// Phase accent → literal dot class (Tailwind needs the full string present in source).
// (the per-lane dot colour map lived here until 2026-07-31 — see KANBAN_GROUPS for why it went)

// Models selectable per run on a work-item card. Values are the canonical catalog's concrete ids; the
// daemon normalizes any value to the latest concrete at consumption. Labels come from the catalog.
export const RUN_MODELS = MODEL_CATALOG.map((m) => ({ value: m.key, label: m.label }))
export const DEFAULT_RUN_MODEL = DEFAULT_MODEL
// Reasoning-effort levels selectable per run, alongside the model. Default "medium".
export const RUN_EFFORTS = EFFORT_CATALOG.map((e) => ({ value: e.key, label: e.label }))
export const DEFAULT_RUN_EFFORT = DEFAULT_EFFORT
// The PROPOSED work-item kind. "Undecided" is a first-class option, not a placeholder — leaving it
// there is a real answer (triage judges alone), so it leads the list rather than sitting under one.
export const WORK_KIND_OPTS = [
  { value: '', label: 'Undecided' },
  { value: 'implementation', label: 'Implementation' },
  { value: 'research', label: 'Research' },
]

// What Save on an inbox card writes back. Content and setting travel together because the card has
// one Save. `model`/`effort` are always concrete — the picker offers the three catalog options and
// opens on the repo's default, so saving states a pick rather than an inheritance.
type InboxConfigPatch = {
  title: string | null
  text: string
  kind: InboxKind
  model: string
  effort: string
  autopilot: boolean
  work_kind: string
}

// Shared dev-knowledge store views — the bodies for Workspace (work-items) and Inbox. Rendered
// both by the main Development map (in-panel zooms) and reusable elsewhere. Workspace-workflow
// model: phase = the per-kind pipeline (triage→plan→…→close); status = the runnable axis
// (active/awaiting_*/done), where `done` is the derived terminal display state.

// --- status / branch-off chrome -------------------------------------------------

// The badge answers "what is this item doing?" — so a live run wins over the status word: an item
// with an agent on it reads "triaging…", not "in progress". `running` covers both a background run
// fired from the board and a bound session's turn. `bucket` is the item's attention tier, which is
// where the "parked at a gate" verdict comes from (D2 — one rule, computed by the daemon).
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
  // R4: re-fire the run of a STOPPED item. Offered on the card because that item's next act is
  // unambiguous; every other control still lives in the popup.
  onResume?: (it: WorkItem) => void
  running?: string[]
  boundItemId?: string | null
}

// --- workspace: kanban by phase group -------------------------------------------

// The 8-stage union pipeline collapsed to FOUR board columns so the whole board fits one view
// (no horizontal scroll). Adjacent phases that read as one stage of work merge: intake (triage +
// plan) and the doing (build/vet for implementation, investigate/report for research). Review and
// close stay their own columns — they're the human gates. An item keeps its real phase; it's just
// placed in the column whose group owns that phase.
//
// That middle column is labelled `Work`, not `Build & Vet` (owner, 2026-08-18). A column label
// names the whole lane, and this lane holds two different machineries: a research item in it is
// investigating or reporting, never building or vetting, so half the board's items sat under a
// heading that described the other kind. Naming both pairs would need four words and still leave
// a reader matching card to clause. The card already prints the item's real phase, so the column
// only has to say WHICH STAGE of the pipeline this is — the one where the work is actually done.
//
// The lane dots CARRY NO COLOUR (2026-07-31). They used to walk the status palette — dev-blue,
// warn-amber, success-green — and that palette already means something everywhere else on this
// screen: green is completed work (`21 done`, `21 shipped`), amber is needs-you, red is stopped.
// So `success` meant "review lane" here and "finished" ten pixels away, and review + close were
// literally the same green. Two fixes were tried before this one (recolour close, then grey it);
// both just moved the collision. The lane's identity is its LABEL and its position in the row —
// left-to-right already IS the progression — so the dot is a bullet, not a signal, and the palette
// goes back to meaning one thing per colour.
const KANBAN_GROUPS: { key: string; label: string; phases: string[] }[] = [
  { key: 'intake', label: 'Triage & Plan', phases: ['triage', 'plan'] },
  { key: 'work', label: 'Work', phases: ['build', 'investigate', 'vet', 'report'] },
  { key: 'review', label: 'Review', phases: ['review'] },
  { key: 'close', label: 'Close', phases: ['close'] },
]

export function WorkspaceKanban({ items, onOpen, onResume, running, boundItemId, buckets }: { items: WorkItem[]; buckets?: Record<string, string> } & WorkActions) {
  const visible = items.filter(isActive)
  const [boardRef, boardW] = useContainerWidth<HTMLDivElement>()
  // NO whole-board empty state (owner, 2026-08-09). An empty board used to collapse to one line of
  // prose, so the four columns — the shape of the pipeline itself — vanished exactly when the owner
  // had the most room to learn them, and the board appeared to be a different component each time
  // work drained. Every column already renders its own `—`, and the header above already says
  // `0 active`, so the lanes ARE the empty state and the layout never moves.
  const inGroup = (phases: string[]) => visible.filter((it) => phases.includes(it.phase ?? 'triage'))
  // The four lanes REFLOW; the board never scrolls sideways (owner, 2026-08-18). A lane past the
  // right edge is a stage of the pipeline the owner cannot see they have, and showing the whole
  // pipeline at once is the board's entire job.
  //
  // 4 → 2 → 1, and never 3: `auto-fit` would have packed three lanes and left `Close` alone on a
  // second row, which reads as a wrap accident rather than a layout. Halving keeps the rows even
  // and keeps the pairs meaningful — intake beside work, then review beside close.
  const lanes = boardW === 0 || boardW >= 716 ? 4 : boardW >= 392 ? 2 : 1
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
              <span className="text-[11px] font-semibold uppercase tracking-wide text-fg">{g.label}</span>
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
  it, onOpen, planning, bound, bucket, onResume,
}: {
  it: WorkItem
  onOpen?: (it: WorkItem) => void
  planning?: boolean
  bound?: boolean
  bucket?: string // attention tier (S7): error | needs_you | deputy_working | running | unread — tints the card ring
  onResume?: (it: WorkItem) => void // R4: only ever called for a STOPPED card (see below)
}) {
  const clickable = !!onOpen
  const running = !!planning || !!it.running
  // Fixed 4-row card (owner-specced): status · name · model|ctx · tokens|time. Live values win
  // while a run is in flight; otherwise the settled figures. Tokens = the item's grand-total spend
  // (same figure the drilldown leads with); time = the last run's duration (shows once this phase's
  // run has started and persists through the phase — a fresh phase has no run yet, so it's hidden
  // until one lands). The card is a pure glance + entry point — every action lives in the popup.
  const model = (running ? it.run_model : null) ?? it.model
  const ctx = running ? it.run_ctx_pct : it.ctx_pct
  const tokens = running ? it.run_tokens : it.total_tokens
  const hasTokens = (tokens ?? 0) > 0
  const settledTime = it.last_run?.duration_ms != null ? fmtDuration(it.last_run.duration_ms) : null
  const showMeter = running || hasTokens || !!settledTime
  // A LIVE RUN WINS OVER THE STATUS WORD — the same rule StatusBadge and attnRing already follow.
  // Without this the stripe was the one element that didn't know an agent was on the card: the badge
  // read green ("triaging…"), the attention ring read green, and the left edge stayed `active`-blue.
  // Three colours for one fact, and the odd one out was the fastest scan cue on the board.
  const liveNow = running && primaryStatus(it, bucket) !== 'done'
  const stripe = liveNow ? 'border-l-success'
    : STATUS_STRIPE[primaryStatus(it, bucket)] ?? 'border-l-line'
  const stopped = primaryStatus(it, bucket) === 'error'
  // Attention tint (S7): the card carries its bucket color as a soft ring — orange pages, purple =
  // the deputy is covering it, green = a phase agent is on it. (Unread applies to terminal items,
  // which live off-board in the strip.)
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
        {/* RESEARCH ONLY. A kanban card is a few centimetres wide and the status badge already
            fills most of the row — adding "IMPLEMENTATION" to the majority of cards truncated the
            status it sits beside and said nothing (implementation is the default). The FAMILY is
            the half that carries information, so that is the half the card spends its width on;
            the drilldown header states both in full. */}
        {researchKindLabel(it.research_kind) && (
          <span className="min-w-0 truncate rounded bg-kind-research/10 px-1 py-px text-[9.5px] font-medium uppercase tracking-wide text-kind-research">
            {researchKindLabel(it.research_kind)}
          </span>
        )}
        <span className="ml-auto shrink-0 flex items-center gap-1.5"><BranchInfo it={it} /></span>
      </div>
      {/* 2 · name — one line, ellipsis when long */}
      <div className="truncate text-[12.5px] leading-snug text-fg" title={it.title}>{it.title}</div>
      {/* 3 · model · ctx */}
      {(model || ctx != null) && (
        <div className="flex items-center gap-1.5 text-[10.5px] text-muted">
          {model && <span className="truncate">{fmtModel(model)}</span>}
          {model && ctx != null && <span className="text-faint">·</span>}
          {ctx != null && <span className="tabular-nums">ctx {ctx}%</span>}
        </div>
      )}
      {/* 4 · tokens · time — always on once this phase's run has started */}
      {showMeter && (
        <div className="flex items-center gap-1.5 text-[10.5px] text-faint">
          {running && <Loader2 size={10} className="animate-spin text-accent-text" />}
          <span className="tabular-nums" title="Tokens used (3-type basis) — this run while live, else the item total">
            {hasTokens ? `${fmtTokens(tokens ?? 0)} tok` : '—'}
          </span>
          <span>·</span>
          {running
            ? <LiveTimer startedAt={it.run_started_at} />
            : <span className="tabular-nums" title="Duration of the last run">{settledTime ?? '—'}</span>}
          {/* HOW LONG SINCE it last moved — only when nothing is running, because a live timer
              already answers "is this warm?" and two clocks on one row invite comparing them. */}
          {!running && agoLabel(it.last_run?.ended_at) && (
            <>
              <span>·</span>
              <span title="When this item's last run finished">{agoLabel(it.last_run?.ended_at)}</span>
            </>
          )}
        </div>
      )}
      {/* 5 · the ONE action a card carries (R4). The card is otherwise a pure glance — every other
          control lives in the popup — but a STOPPED item is the one case where the owner's next act
          is unambiguous and shouldn't cost a click to reach. Rendered only when stopped, so the
          exception can't spread. `stopPropagation` keeps it from also opening the drilldown. */}
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

// TWO kinds, and they differ in what the row can DO. `item` becomes a work-item when pushed;
// `note` is the owner's own thought, never pushed, there to be picked up in conversation. The
// predecessors — todo/idea/note/question — were a flavour nothing read: no code anywhere branched
// on the value, so four columns bought a decision at capture time and paid nothing back.
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
  // The repo's Quick-config defaults, so a card's Config tab can NAME what an unset row inherits
  // ("Repo default — Sonnet 5") instead of showing a blank that looks like nothing is configured.
  const repos = useLive(K.repos, getRepos).data
  const repo = repos?.find((r) => r.id === contextId)

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
        {/* Model/effort are NOT chosen here (owner, 2026-08-02). Capture is a one-line act; a row
            is born inheriting the repo's Quick-config defaults, and the per-item config lives in
            the card's Config tab — where it sits next to autopilot and the row's metadata. */}
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
                      repoModel={repo?.model_override}
                      repoEffort={repo?.effort_override}
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
  repoModel,
  repoEffort,
  onPush,
  onDiscuss,
  onSave,
  onDelete,
}: {
  e: InboxEntry
  repoModel?: string | null
  repoEffort?: string | null
  onPush: () => void
  onDiscuss?: () => void
  onSave: (patch: InboxConfigPatch) => Promise<void>
  onDelete: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)

  // The whole card is the click target (like a work-item card) → opens the edit view. Action
  // controls stop propagation so they don't also trip the edit-open. The edit modal is a SIBLING
  // of the card, never a descendant: if it lived inside the card's onClick, every click within the
  // modal (Cancel / Save / the X) would bubble up and re-fire setEditing(true) — so the modal could
  // never close. (The card isn't positioned, so it was never the `contain` modal's containing block;
  // moving the modal out of it doesn't change where the modal renders.)
  return (
    <>
    <div
      onClick={() => setEditing(true)}
      title="Edit this item"
      className={`group cursor-pointer rounded-md border border-line border-l-2 ${KIND_STRIPE[e.kind] ?? 'border-l-line'} bg-surface px-2.5 py-2 shadow-sm transition hover:border-accent hover:bg-hover`}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          {/* The id, on the CARD and not only inside the edit view. It is how the owner names a
              row out loud — in chat, in a message, to me — and one buried a click deep cannot do
              that job. Monospace + muted so it labels without competing with the title. */}
          {/* NO body preview under the title (owner, 2026-08-18). A row's title is written to be
              the whole row — two clamped lines of the body under it repeated the same sentence in
              grey, and a card whose second line is always a truncated version of its first reads as
              unfinished. A title-less row falls back to the body IN the title slot, so the fallback
              is the identity line rather than a second one. */}
          <div className="flex items-baseline gap-1.5">
            <span className="shrink-0 font-mono text-[11px] text-faint">#{e.id}</span>
            {/* `overflow-wrap:anywhere` because these strings are written by an agent or by the
                owner: a title can be one unbroken token (a path, an identifier) longer than the
                column, and a word that cannot break pushes the whole card wider than its lane. */}
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
              {/* Push — the primary action, made prominent (tinted pill, always visible), and
                  absent on a NOTE. A note has no work to become, so the button would be a promise
                  the route refuses anyway; the honest surface is not to offer it. */}
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
      {/* The footer reads left-to-right as WHO · WHAT · WHEN, and the time is pushed to the far
          edge by `ml-auto`: it is the one field every row carries and the only one worth scanning
          down a column, which a ragged position defeats. */}
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
        {/* The proposed work kind, shown ONLY when one was filed: an absent label means undecided,
            which is a real state and not worth a word of its own on every row. It carries the same
            hue the work-item board gives that kind, so the row and the card it becomes agree. */}
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
        repoModel={repoModel}
        repoEffort={repoEffort}
        onClose={() => setEditing(false)}
        onSave={onSave}
      />
    )}
    </>
  )
}

// The inspector for one inbox row. THREE tabs, because a row is three separable things: WHAT it
// says (Content), the cold-start context it hands the work-item it becomes (Brief), and HOW it will
// be worked (Setting). Mixing the last into the first put run config in the capture row, where it
// was answered by accident on every keystroke.
//
// Two independent artifacts, so two independent edit gates: the ROW (content + setting travel
// together — one PATCH, one Save) and the BRIEF (its own file, its own route). The action row shows
// the gate belonging to whatever tab is open, because the buttons should act on what you are
// looking at and nothing else.
function InboxItemModal({
  e,
  repoModel,
  repoEffort,
  onSave,
  onClose,
}: {
  e: InboxEntry
  repoModel?: string | null   // the repo's Quick-config default — what an unset row starts at
  repoEffort?: string | null
  onSave: (patch: InboxConfigPatch) => Promise<void>
  onClose: () => void
}) {
  const [tab, setTab] = useState<'content' | 'brief' | 'setting'>('content')

  // Model/effort are always a CONCRETE pick — three options each, no "inherit" row (owner,
  // 2026-08-02). A row that has never been touched shows the repo's Quick-config value as its
  // starting position, so the picker states the answer instead of deferring it one level.
  // `work_kind` is the exception: "" is a real value, not an empty control — nobody has judged,
  // and triage then decides alone, which is what every row did before the field.
  const saved: InboxConfigPatch = {
    title: e.title ?? '',
    text: e.text,
    kind: e.kind,
    model: toModelKey(e.model) || toModelKey(repoModel) || DEFAULT_RUN_MODEL,
    effort: e.effort ?? repoEffort ?? DEFAULT_RUN_EFFORT,
    autopilot: !!e.autopilot,
    work_kind: e.work_kind ?? '',
  }
  const row = useEditGate<InboxConfigPatch>({
    saved,
    valid: (d) => !!d.text.trim(),
    commit: (d) => onSave({ ...d, title: (d.title ?? '').trim() || null, text: d.text.trim() }),
  })
  const d = row.draft
  const set = (patch: Partial<InboxConfigPatch>) => row.setDraft({ ...d, ...patch })
  // Outside edit mode the tabs read the ROW, not the draft — a draft the owner abandoned must not
  // decide which tabs exist.
  const kind = row.editing ? d.kind : e.kind

  // The handoff brief. Loaded when its tab is first opened rather than with the modal: most opens
  // of this dialog never look at it, and a row that has no brief is the common case.
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

  // A NOTE is the owner's own thought and is never pushed, so no work-item is ever born to carry a
  // brief or a run config. Both tabs are withheld rather than shown empty — a card explaining its
  // own absence is still a card — and the third renames to Info, which is all a note's second tab
  // ever holds.
  const tabs = kind === 'note'
    ? ([['content', 'Content'], ['setting', 'Info']] as const)
    : ([['content', 'Content'], ['brief', 'Brief'], ['setting', 'Setting']] as const)
  const gate = tab === 'brief' ? briefGate : row
  const err = tab === 'brief' ? (briefGate.err ?? briefErr) : row.err

  return (
    // Contained (not viewport-fixed) so it overlays the dashboard column and leaves the chat rail
    // interactive — same containment as the work-item review popup.
    <Modal onClose={onClose} title="Inbox item" maxW="max-w-lg" z="z-40" contain dismissable={false}>
      <div className="p-4">
        <TabBar
          tabs={tabs}
          value={tab === 'brief' && kind === 'note' ? 'content' : tab}
          onChange={setTab}
          size="sm"
          className="mb-3"
        />

        {/* One fixed body height for every tab — switching tabs must not resize the dialog under
            the cursor (the action row would jump out from under a click). */}
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
            {/* ── how this item will be worked ───────────────────────────────────────────────
                ALL FOUR describe a RUN — which gates drive themselves, which machinery the item
                becomes, which model and effort its runs spend. */}
            {kind !== 'note' && (
            <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
              <SectionHeader>Setting</SectionHeader>
              <div className="mt-1 text-[11px] leading-snug text-faint">
                Set here while the row is open — push freezes all three onto the work-item.
              </div>
              {row.editing ? (
                <div className="mt-2.5 space-y-2.5">
                  <ConfigRow label="Autopilot" hint="Drives its own gates; the deputy judges each one for you.">
                    <Toggle on={d.autopilot} onChange={(v) => set({ autopilot: v })} onColor="bg-accent" />
                  </ConfigRow>
                  <ConfigRow label="Work kind" hint="Implementation changes code; research answers a question. Triage confirms it.">
                    <Dropdown value={d.work_kind} options={WORK_KIND_OPTS} onChange={(v) => set({ work_kind: v })} width="w-36" align="right" />
                  </ConfigRow>
                  <ConfigRow label="Model" hint="Which model this item's runs use.">
                    <Dropdown value={d.model} options={RUN_MODELS} onChange={(v) => set({ model: v })} width="w-36" align="right" />
                  </ConfigRow>
                  <ConfigRow label="Effort" hint="How much reasoning each run spends.">
                    <Dropdown value={d.effort} options={RUN_EFFORTS} onChange={(v) => set({ effort: v })} width="w-36" align="right" />
                  </ConfigRow>
                </div>
              ) : (
                <dl className="mt-2 space-y-1.5">
                  <MetaRow label="Autopilot">{saved.autopilot ? 'On' : 'Off'}</MetaRow>
                  <MetaRow label="Work kind">{optLabel(WORK_KIND_OPTS, saved.work_kind)}</MetaRow>
                  <MetaRow label="Model">{optLabel(RUN_MODELS, saved.model)}</MetaRow>
                  <MetaRow label="Effort">{optLabel(RUN_EFFORTS, saved.effort)}</MetaRow>
                </dl>
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
          {!gate.editing && (
            <button className="rounded-md bg-hover px-3 py-1.5 text-xs text-fg" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>
    </Modal>
  )
}

// A picker's own word for a stored value — so the read view says "Sonnet 5", not `sonnet-5`.
function optLabel(opts: { value: string; label: string }[], value: string): string {
  return opts.find((o) => o.value === value)?.label ?? value ?? '—'
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
