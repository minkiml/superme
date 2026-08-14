import { useEffect, useMemo, useState } from 'react'
import {
  X, ArrowRight, Sparkles, Check, Loader2, FileText, ScrollText, History,
  Terminal, GitBranch, FlaskConical, Ban, GitMerge, Undo2, ShieldCheck,
  AlertTriangle, MessageSquare, CornerUpLeft, Plane, ExternalLink, Gauge, ListChecks,
  ChevronRight, CircleDot, RotateCcw, Play, Plus, Trash2, PenLine,
  Network, Inbox, ClipboardCheck, ClipboardList, KeyRound,
} from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import SectionHeader from '@/ui/SectionHeader'
import { TraceRows } from './ExecutionTrace'
import { pairTrace } from '@/lib/trace'
import {
  getWorkItemDetail, getWorkItemArtifacts, advanceWorkItem,
  getDevLog, getWorkItemDrilldown, getWorkItemReport, getWorkItemGit,
  resolveWorkItemGit, revertWorkItemGit, abandonWorkItem, markWorkItemSeen,
  resumeWorkItem, rerunWorkItem, runWorkItem, authorizeWorkItem,
  getWorkItemOwnerInput, saveWorkItemOwnerInput,
  type WorkItem, type WorkItemDetail, type DevEvent, type RunArtifact, type RunHeader, type GitHealth,
  type Drilldown, type DrilldownAction, type ProofRow,
  type OwnerInput, type OwnerReference, type OwnerNote,
} from '@/lib/api'
import { invalidate, useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { build, navigate, useRoute, PHASES, type ItemTab, type ItemSub, type Phase } from '@/lib/router'
import { fmtModel, fmtTokens, fmtLocal, toModelKey } from '@/lib/format'
import { StatusBadge, DEFAULT_RUN_MODEL, DEFAULT_RUN_EFFORT } from './panels'
import { PHASE_LABEL, STATUS_LABEL, kindChipClass, researchKindLabel } from './common'

// The work-item drilldown (renovation v2 §4) — "what is needed from me, and what has this produced".
//
// Header band → PROGRESS BAR (not clickable: reading a past stage is what Reports is for) → four
// tabs (Quick View · Reports · Trace · Git) → the action bar.
//
// ★ TYPE SCALE — four steps, and only four (owner, 2026-08-01). This file had reached NINE sizes
// (9.5 / 10 / 10.5 / 11 / 12 / 12.5 / 13 / 14 / 15), most of them a half-pixel apart and picked
// per-component, which is why sibling rows kept looking mis-set rather than deliberately ranked.
//     15px  the item title — one use, the modal's only headline
//     13px  BODY: every normal sentence, value, list row, button, input, empty state
//     11px  META: labels, mono ids, timestamps, annotations hanging off a body line
//     10px  MICRO: badges, pills, uppercase stamps
// Anything new picks one of these. If a thing seems to need a size between two of them, it wants a
// different WEIGHT or COLOUR — see the colour rule (fg = fact, muted = label, faint = provenance).
//
// ★ This component derives almost nothing. Which controls are live, why they are greyed, why the item
// is parked, what the owner should do about it, and how the proof rows join — all of that arrives
// computed on `GET /dev/work-items/:id/drilldown`. The surface it replaced pulled a markdown gate
// brief and re-decided activation locally, next to `approve_blocked_by` that it never read: two
// writers for one question, one of them in TypeScript. **If you find yourself computing `disabled`
// here, the rule belongs in `services/drilldown.py`.**

// One icon per gate criterion — the SUBJECT of the check (evidence, the plan, the children),
// beside the ✓/✗/– that carries its verdict. Mono slugs look alike in a list; a shield and a
// clipboard tell two of them apart before you read either. Every criterion `gate_briefs` can emit
// is listed; an unmapped one falls back rather than blanking, because the kernel emits an
// unknown-slug check on purpose when it has no evaluator.
const CHECK_ICON: Record<string, typeof FileText> = {
  required_artifacts: FileText,
  children_terminal: Network,
  findings_delivered: ScrollText,
  spawns_exist: Inbox,
  triage_ran: ClipboardCheck,
  plan_complete: ClipboardList,
  vet_plan_sharp: FlaskConical,
  revisions_recorded: History,
  evidence_fresh: ShieldCheck,
  no_pending_authorizations: KeyRound,
}

// Per-kind pipeline (mirrors the backend KIND_PROFILES) — the progress bar's stops.
// ⚠ HAND-COPIED, and it has drifted once: research kept a `plan` stop for weeks after the phase was
// removed from the backend profile (research-sweep-model §3), so every sweep showed a step that
// cannot happen. Nothing enforces the mirror — when a pipeline changes, change it here too.
const PIPELINES: Record<string, string[]> = {
  implementation: ['triage', 'plan', 'build', 'vet', 'review', 'close'],
  research: ['triage', 'investigate', 'review', 'close'],
}

const TABS: { id: ItemTab; label: string; icon: typeof FileText }[] = [
  { id: 'quick', label: 'Quick View', icon: Gauge },
  { id: 'reports', label: 'Reports', icon: FileText },
  { id: 'trace', label: 'Trace', icon: Terminal },
  { id: 'git', label: 'Git', icon: GitBranch },
]
// The bar has THREE slots and only three (owner, 2026-07-31): the PRIMARY, then Drop, then Re-run.
// The primary resolves in this order — a gate decision outranks a recovery, which outranks a launch:
//   approve  the item is AT a gate and the decision is yours
//   resume   its run stopped (R2/R4)
//   run      nothing has fired for this phase and nothing is on autopilot
// Exactly one of the three is rendered as the primary; the other two are NOT rendered anywhere,
// because a control that cannot apply in this state is not information, it is a fourth thing to
// read. That is different from GREYING: the resolved primary is always rendered, greyed with its
// server-written reason when it isn't workable, so the slot never goes empty.
const PRIMARY_ORDER = ['approve', 'resume', 'run'] as const

const QUICK_SUBS: { id: ItemSub; label: string }[] = [
  { id: 'now', label: 'Now' },
  { id: 'deputy', label: 'Deputy' },
  // Labelled "Task", id still `proof` — the id is an ADDRESS (`?sub=proof`), and renaming it would
  // dead-link every drilldown URL anyone saved. The pane is a per-task list, which is what the tab
  // should say; the payload keeps the name the backend gives it.
  { id: 'proof', label: 'Task' },
  // Authorization is CONDITIONAL — appended only when the item has one (see `subs`). It was a
  // permanent banner above the tabs, which cost a third of the modal's height on every tab of every
  // item, most of which never have one. As a sub tab it costs nothing until it exists, and the
  // header's blinking flag is what makes sure a pending one is still impossible to miss.
  { id: 'auth', label: 'Authorization' },
]
// Trace's two panes. They answer different questions and are different lengths — Runs is what the
// AGENTS did (every tool call, per run), Timeline is what HAPPENED TO the item (phase advances,
// merges, gate decisions). Stacked, the long one buried the short one; as peers each shows whole.
const TRACE_SUBS: { id: ItemSub; label: string }[] = [
  { id: 'runs', label: 'Runs' },
  { id: 'timeline', label: 'Timeline' },
]
// Runaway guard on the item's event feed, not a display window — see the fetch site.
const EVENT_CAP = 1000

export default function WorkItemModal({
  it, contextId, onClose, onChanged, onOpenChat, bucket,
}: {
  it: WorkItem
  contextId: string
  onClose: () => void
  onChanged: () => void // reload the board after a mutation
  // The ask-card's one-click "answer in chat". Optional: without it the card still says WHERE to go.
  onOpenChat?: () => void
  // The item's attention tier, passed down so the drilldown's status badge reads the SAME verdict as
  // the card behind it — otherwise the header falls back to the stored word and re-opens the D2
  // split, a card saying NEEDS YOU over a popup saying IN PROGRESS.
  bucket?: string
}) {
  // Cadence: 2.5s while a run is in flight, 10s at rest. The drilldown payload is ONE call covering
  // every tab, so the old four-feed fan-out is down to two (payload + the trace/log the other tabs
  // read lazily).
  const rate = it.running ? 2500 : 10000
  const dQ = useLive<Drilldown>(K.itemDrilldown(contextId, it.id),
                                () => getWorkItemDrilldown(it.id, contextId), rate)
  // The item's WHOLE event history, not a window. The old 50 silently dropped the early rows — the
  // triage/plan decisions, which are exactly what you go looking for months later. Scoped to one
  // item, so this is bounded by how much work the item did (the busiest to date: ~135 rows); the cap
  // is a runaway guard, and TimelinePane says so out loud if it is ever reached.
  const logQ = useLive(K.devLog(contextId, it.id, EVENT_CAP),
                       () => getDevLog(contextId, { itemId: it.id, limit: EVENT_CAP }), rate)
  const d = dQ.data ?? null
  const events: DevEvent[] = logQ.data?.events ?? []

  const [mutErr, setMutErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [abandoning, setAbandoning] = useState(false)
  const [abandonReason, setAbandonReason] = useState('')
  const [rerunning, setRerunning] = useState(false)   // the re-run confirm bar (R5)
  const [authBusy, setAuthBusy] = useState<string | null>(null)
  const [model] = useState(toModelKey(it.model) || DEFAULT_RUN_MODEL)
  const [effort] = useState(it.effort ?? DEFAULT_RUN_EFFORT)
  const err = mutErr ?? (d ? null : dQ.error ? String(dQ.error) : null)

  const pipeline = PIPELINES[it.kind ?? 'implementation'] ?? PIPELINES.implementation
  const phase = d?.phase ?? it.phase ?? 'triage'
  const running = !!it.running
  const completed = !!d?.terminal || !!it.done_at || it.status === 'done'

  // Tab + sub are ADDRESSES, so a drilldown is linkable and survives F5.
  const route = useRoute()
  const tab: ItemTab = (route.name === 'item' && route.tab) || 'quick'
  const routeSub = route.name === 'item' ? route.sub : null
  const reportPhases = d?.reports ?? []
  // The feed carries PENDING requests only, and only at the review gate (`gate_briefs.gate_state`) —
  // so a non-empty list IS "something is waiting on you", with no second rule deciding that here.
  const auths = d?.authorizations ?? []
  // The Authorization sub exists only on an item that HAS one — a tab that opens empty on every
  // other item teaches the owner to ignore the row it sits in.
  const subs: ItemSub[] = tab === 'quick'
                          ? QUICK_SUBS.filter((s) => s.id !== 'auth' || auths.length > 0)
                                      .map((s) => s.id)
                        : tab === 'reports' ? (reportPhases as ItemSub[])
                        : tab === 'trace' ? TRACE_SUBS.map((s) => s.id)
                        : []
  // An address naming a sub that is real but wrong for its tab (a hand-edited URL, a link from before
  // the tab set changed) falls back to the tab's first sub rather than rendering blank.
  const sub: ItemSub | null = routeSub && subs.includes(routeSub) ? routeSub : (subs[0] ?? null)
  const go = (t: ItemTab, s: ItemSub | null) =>
    navigate({ name: 'item', repoId: contextId, itemId: it.id, tab: t, sub: s })

  // Read receipt (S7 attention): opening a terminal item's drilldown stamps it seen.
  useEffect(() => {
    if (completed && !it.seen_at) markWorkItemSeen(it.id, contextId).then(onChanged).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [it.id, completed])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // ONE dispatcher for every control. The server said which are live; this only says what they DO,
  // so adding a control means adding it in `services/drilldown.py` and one line here.
  async function act(id: string) {
    if (id === 'drop') { setAbandoning(true); return }
    // Re-run DELETES this item's work. It is the one control here whose damage cannot be undone by
    // clicking something else, so it asks first — in the same bar, the same shape as Drop's confirm.
    if (id === 'rerun') { setRerunning(true); return }
    if (id === 'chat') { onOpenChat?.(); return }
    // `merge` was a second key onto this same `advanceWorkItem` call — the identical request, with
    // a weaker activation rule behind it. Deleted with its action (owner, 2026-08-03).
    const run: Record<string, () => Promise<unknown>> = {
      approve: () => advanceWorkItem(it.id, contextId),
      run: () => runWorkItem(it.id, contextId),
      resume: () => resumeWorkItem(it.id, contextId),
    }
    if (!run[id]) return
    setBusy(id)
    setMutErr(null)
    try {
      await run[id]()
      onChanged()
      dQ.refresh()
      // A gate decision moves the item out of this view; a launch keeps it open so the owner can
      // watch the run they just started.
      if (id === 'approve') onClose()
    } catch (e) {
      setMutErr(`${id} failed — ${e}`)
    } finally {
      setBusy(null)
    }
  }

  async function abandon() {
    setBusy('drop')
    try {
      await abandonWorkItem(it.id, abandonReason, contextId)
      onChanged()
      onClose()
    } catch (e) {
      setMutErr(`Couldn't drop — ${e}`)
      setBusy(null)
      setAbandoning(false)
    }
  }

  // Re-run, confirmed. The drilldown stays OPEN afterwards (unlike Drop, which closes it): the item
  // still exists and a fresh run is starting in it, so the owner watches it come back to life.
  async function rerun() {
    setBusy('rerun')
    try {
      await rerunWorkItem(it.id, contextId)
      onChanged()
      dQ.refresh()
      setRerunning(false)
    } catch (e) {
      setMutErr(`Couldn't re-run — ${e}`)
    } finally {
      setBusy(null)
    }
  }

  // BV-A2: the owner's grant/deny on a deferred contract change. A grant routes the item back into
  // build to perform it; a deny waives the deferred check so it can close with the gap on record.
  async function decideAuth(authId: string, decision: 'granted' | 'denied') {
    setAuthBusy(authId)
    try {
      await authorizeWorkItem(it.id, authId, decision, contextId)
      onChanged()
      onClose()
    } catch (e) {
      setMutErr(`Couldn't ${decision === 'granted' ? 'grant' : 'deny'} — ${e}`)
      setAuthBusy(null)
    }
  }

  const barActions = (d?.actions ?? []).filter((a) => a.home === 'actions')
  // The primary slot. First MATCH by state, not first ACTIVE: a greyed Resume on a stopped item
  // whose phase can't be re-fired still has to be the button the owner sees (with the reason why),
  // which is exactly the case the owner called out. So `approve` claims the slot whenever the item
  // is at a gate, `resume` whenever it has stopped, and `run` otherwise.
  const byId = Object.fromEntries(barActions.map((a) => [a.id, a]))
  const primaryId = d?.at_gate ? 'approve' : String(it.status) === 'error' ? 'resume' : 'run'
  const primary = byId[primaryId] ?? byId[PRIMARY_ORDER.find((k) => byId[k]?.active) ?? 'approve']

  return (
    <Modal onClose={onClose} contain column fill maxW="max-w-3xl" z="z-40">
      {/* Header band — id · badges · title · model/ctx/token chips (kept from S7). */}
      <div className="shrink-0 border-b border-line px-4 py-3">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-[10px] text-faint">{it.id}</span>
              <StatusBadge it={it} running={running} bucket={bucket} />
              {/* ONE chip, not two: kind and family are a single fact read left to right
                  ("RESEARCH: AUDIT"), and splitting them invited reading the family as a second,
                  unrelated badge. */}
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
                kindChipClass(it.kind)}`}
                    title={researchKindLabel(it.research_kind)
                      ? 'The research family this item follows — it picks the guide investigate reads and the bar review judges against.'
                      : undefined}>
                {it.kind ?? 'implementation'}
                {researchKindLabel(it.research_kind) && `: ${researchKindLabel(it.research_kind)}`}
              </span>
              {it.deliverable && (
                <span className="rounded-full bg-hover px-2 py-0.5 font-mono text-[10px] text-faint">{it.deliverable}</span>
              )}
              {it.autopilot && (
                <span className="flex items-center gap-1 rounded-full bg-accent/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-accent-text"
                      title="This item drives its own gates without a click — the deputy judges each gate on your behalf.">
                  <Plane size={10} /> Autopilot
                </span>
              )}
            </div>
            <h2 className="mt-1 text-[15px] font-semibold leading-snug text-fg">{it.title || it.id}</h2>
            <RunMeta it={it} />
          </div>
          <button onClick={onClose} title="Close" aria-label="Close"
                  className="shrink-0 rounded p-1 text-muted hover:bg-hover hover:text-fg">
            <X size={16} />
          </button>
        </div>
        <ProgressBar pipeline={pipeline} phase={phase} running={running} done={completed} />
      </div>

      {/* Tabs. The authorization FLAG rides at the end of this row (owner, 2026-08-08): the request
          itself lives in its own Quick View sub, and this is what makes a pending one impossible to
          miss from any tab without spending a third of the modal on a card most items never have.
          It is a pointer, not a second renderer — it carries no detail and takes no decision. */}
      <div className="flex shrink-0 items-center gap-1 border-b border-line px-4">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => go(id, null)}
                  className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] transition ${
                    tab === id ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg'
                  }`}>
            <Icon size={14} /> {label}
          </button>
        ))}
        {auths.length > 0 && (
          <button onClick={() => go('quick', 'auth')}
                  title="A contract change is waiting on your grant or deny — it holds the merge"
                  className="ml-auto flex animate-pulse items-center gap-1.5 rounded-full border
                             border-warn/50 bg-warn/10 px-2.5 py-1 text-[11px] font-semibold
                             text-warn transition hover:bg-warn/20">
            <ShieldCheck size={12} />
            Authorization
          </button>
        )}
      </div>

      {/* Sub-tabs — Quick View's three panes, or Reports' per-phase list. */}
      {subs.length > 0 && (
        <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-line bg-sunken px-4 py-1.5">
          {subs.map((s) => (
            <button key={s} onClick={() => go(tab, s)}
                    className={`rounded px-2 py-0.5 text-[11px] font-medium transition ${
                      sub === s ? 'bg-accent/15 text-accent-text' : 'text-faint hover:text-fg'
                    }`}>
              {tab === 'quick' ? QUICK_SUBS.find((q) => q.id === s)?.label
               : tab === 'trace' ? TRACE_SUBS.find((t) => t.id === s)?.label
               : PHASE_LABEL[s] ?? s}
            </button>
          ))}
          {/* Reports with nothing written say so, rather than offering tabs that open empty. */}
          {tab === 'reports' && subs.length === 0 && (
            <span className="text-[11px] text-faint">No reports written yet.</span>
          )}
        </div>
      )}

      {/* Body */}
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {err && <div className="rounded-md border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-[13px] text-danger">{err}</div>}
        {!d ? <Loading /> : tab === 'quick' ? (
          sub === 'deputy' ? <DeputyLogPane events={events} phase={d.now.phase} />
          : sub === 'auth' ? <AuthorizationsPane auths={auths} busy={authBusy} onDecide={decideAuth} />
          : sub === 'proof' ? <ProofPane rows={d.proof} auths={auths} lenses={d.lenses} />
          : <NowPane d={d} it={it} contextId={contextId} busy={busy} onAct={act} />
        ) : tab === 'reports' ? (
          reportPhases.length === 0
            ? <Empty>No phase has written a report yet — each phase writes one as its closing act.</Empty>
            : <ReportPane itemId={it.id} contextId={contextId} phase={String(sub ?? reportPhases[0])}
                          itemPhase={d.now.phase} />
        ) : tab === 'git' ? (
          <GitPane it={it} contextId={contextId} actions={d.actions} busy={busy} onAct={act}
                   onChanged={onChanged} />
        ) : (
          <TraceTab it={it} contextId={contextId} rate={rate} events={events}
                    pane={sub === 'timeline' ? 'timeline' : 'runs'} />
        )}
      </div>

      {/* Action bar — every control always rendered, activation + reason from the server. */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-t border-line px-4 py-3">
        {abandoning ? (
          <div className="flex flex-1 items-center gap-2">
            <input autoFocus value={abandonReason} onChange={(e) => setAbandonReason(e.target.value)}
                   placeholder="Why drop it? (lands in the close report)"
                   className="min-w-0 flex-1 rounded-md border border-line bg-sunken px-2 py-1.5 text-[13px] text-fg placeholder:text-faint" />
            <button onClick={abandon} disabled={busy === 'drop'}
                    className="inline-flex items-center gap-1.5 rounded-md bg-danger px-3 py-1.5 text-[13px] font-medium text-on-accent hover:opacity-90 disabled:opacity-50">
              {busy === 'drop' ? <Loader2 size={14} className="animate-spin" /> : <Ban size={14} />} Drop
            </button>
            <button onClick={() => setAbandoning(false)} className="text-[13px] text-muted hover:text-fg">Cancel</button>
          </div>
        ) : rerunning ? (
          // The re-run confirm — one line, one click, no typing (same shape as Drop's). It NAMES
          // what goes rather than asking "are you sure?": an owner cannot weigh an act they have to
          // remember the definition of.
          <div className="flex flex-1 flex-wrap items-center gap-2">
            <span className="min-w-0 flex-1 text-[13px] text-muted">
              Re-run this item? Its artifacts, reports, checkpoints and sessions are{' '}
              <span className="font-medium text-danger">cleared</span>, its run trace leaves this
              item&apos;s view and the branch is re-cut — it starts from the beginning. Nothing is
              destroyed: the trace still counts toward the project totals.
            </span>
            <button onClick={rerun} disabled={busy === 'rerun'}
                    className="inline-flex items-center gap-1.5 rounded-md bg-danger px-3 py-1.5 text-[13px] font-medium text-on-accent hover:opacity-90 disabled:opacity-50">
              {busy === 'rerun' ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />} Re-run
            </button>
            <button onClick={() => setRerunning(false)} className="text-[13px] text-muted hover:text-fg">Cancel</button>
          </div>
        ) : (
          <>
            {/* The run config (model · effort) is NOT repeated here. It is fixed at capture and
                already stated in the header — an action bar should carry acts, not read-only facts. */}
            {running && (
              <span className="inline-flex items-center gap-1.5 text-[13px] text-accent-text">
                <Loader2 size={14} className="animate-spin" /> Agent working…
              </span>
            )}
            {/* THREE slots: primary · Drop · Re-run. The primary is the first of approve/resume/run
                that applies to this state, greyed with its own reason when it isn't workable.
                A TERMINAL item gets NONE of them — every one is inactive by definition (finished ·
                already terminal · finished), so the row was three dead controls saying the same
                thing three ways. A greyed button is information while an action is still POSSIBLE
                and merely blocked; once the item is over, it is just clutter on top of a record. */}
            {!d?.terminal && primary && (
              <ActionButton a={primary} busy={busy === primary.id} primary
                            onClick={() => act(primary.id)} />
            )}
            {d?.terminal && (
              <span className="text-[13px] text-faint">
                This item is closed. Its record stays readable.
              </span>
            )}
            <span className="ml-auto flex items-center gap-1">
              {!d?.terminal && barActions.filter((a) => a.id === 'drop' || a.id === 'rerun').map((a) => (
                <ActionButton key={a.id} a={a} busy={busy === a.id} quiet
                              onClick={() => act(a.id)} />
              ))}
            </span>
            {/* Disposal is ONE button — Drop (owner, 2026-08-02). A second "Delete" lived here that
                hard-erased the folder, the session transcripts and the originating inbox row. It
                went for three reasons: it only worked pre-build, so it could never be THE disposal
                act; every wedge it could cause (parent stuck at awaiting_child, downstream parked on
                a vanished id) had to be hand-simulated because no terminal event ever fires for a
                deleted item; and erasing the inbox row contradicts inbox_flow's own contract that
                the pushed row survives as trace. A mis-capture now leaves an `abandoned` record —
                cheap, since `done_at` already clears terminal items off the board. */}
          </>
        )}
      </div>
    </Modal>
  )
}

// ── the frame ───────────────────────────────────────────────────────────────────────────────────

// The progress indicator — deliberately NOT tabs (§4.1). The old clickable stepper made every phase
// an address and a decision-button context; a button under a stage that already happened acts on the
// LIVE phase behind the owner's back. Reading a past stage is what the Reports tab is for.
function ProgressBar({ pipeline, phase, running, done }: {
  pipeline: string[]; phase: string; running: boolean; done: boolean
}) {
  const idx = pipeline.indexOf(phase)
  return (
    <div className="mt-3 flex items-center gap-0" aria-label="pipeline progress">
      {pipeline.map((p, i) => {
        const state = done || i < idx ? 'done' : i === idx ? 'current' : 'future'
        const last = i === pipeline.length - 1
        return (
          // The last stage carries no connector, so giving it an equal `flex-1` slot parked its dot
          // a sixth of the width short of the right edge and the whole track read as left-shifted.
          // It sizes to its label instead; the connectors absorb the width, edge to edge.
          <div key={p} className={`flex items-center ${last ? 'min-w-0 shrink-0' : 'min-w-0 flex-1'}`}>
            <div className="flex flex-col items-center gap-1">
              <span className={`grid h-3 w-3 place-items-center rounded-full ${
                state === 'done' ? 'bg-success'
                : state === 'current' ? (running ? 'animate-pulse bg-accent' : 'bg-accent')
                : 'bg-line'
              }`}>
                {state === 'current' && running && <span className="h-1 w-1 rounded-full bg-on-accent" />}
              </span>
              <span className={`whitespace-nowrap text-[10px] font-medium uppercase tracking-wide ${
                state === 'current' ? 'text-fg' : state === 'done' ? 'text-faint' : 'text-line'
              }`}>{PHASE_LABEL[p] ?? p}</span>
            </div>
            {i < pipeline.length - 1 && (
              <span className={`-mt-4 h-px min-w-2 flex-1 ${i < idx || done ? 'bg-success' : 'bg-line'}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}

function ActionButton({ a, busy, primary, quiet, onClick }: {
  a: DrilldownAction; busy: boolean; primary?: boolean; quiet?: boolean; onClick: () => void
}) {
  // `active` and `reason` both come from the server. Never re-decide either here.
  //
  // An INACTIVE primary drops the accent fill. `disabled:opacity-40` alone left a filled accent
  // button on a dark ground still reading as "do this" — on a shipped item, `Run Review` looked
  // live while being unclickable, which is worse than either state on its own: the owner tries it,
  // nothing happens, and nothing says why (the reason is in a `title`, which needs a hover to
  // find). Greyed has to LOOK greyed for the disabled state to be information.
  const cls = primary && a.active
    ? 'bg-accent font-medium text-on-accent hover:opacity-90 px-3'
    : quiet
      ? 'text-faint hover:text-fg px-2.5'
      : 'border border-line bg-surface text-muted hover:bg-hover hover:text-fg px-3'
  return (
    <button onClick={onClick} disabled={!a.active || busy} title={a.reason}
            className={`inline-flex items-center gap-1.5 rounded-md py-1.5 text-[13px] transition disabled:opacity-40 ${cls}`}>
      {busy ? <Loader2 size={14} className="animate-spin" /> : <ActionIcon id={a.id} />}
      {a.label}
    </button>
  )
}

function ActionIcon({ id }: { id: string }) {
  const map: Record<string, typeof Check> = {
    approve: Check, drop: Ban, run: Play,
    force: ArrowRight, merge: GitMerge, pr: ExternalLink,
    rerun: RotateCcw,
  }
  const Icon = map[id] ?? ChevronRight
  return <Icon size={14} />
}

// ── Quick View › Now ────────────────────────────────────────────────────────────────────────────

function NowPane({ d, it, contextId, busy, onAct }: {
  d: Drilldown; it: WorkItem; contextId: string
  busy: string | null; onAct: (id: string) => void
}) {
  // EVERY block on this pane is a card (owner, 2026-08-01). The pane spent a while the other way —
  // one box for the decision, everything else flat text on the pane background separated by
  // hairlines — on the theory that a frame reads as "a unit you act on". In practice a hairline
  // between two blocks of similar-sized text is not a boundary anyone sees, and the pane read as one
  // undifferentiated column. So: uniform cards carry SEPARATION, and COLOUR alone carries urgency —
  // the attention card is the only one wearing warn. Same `rounded-md border-line bg-sunken`
  // vocabulary the Proof pane already uses, so the two Quick View panes read as one surface.
  return (
    <div className="space-y-3">
      {/* ABOUT FIRST (owner, 2026-08-08). What this item IS precedes what is happening to
          it: a reader opening a strange item needs the subject before the verb, and the
          Problem row is the one line that makes every card under it legible. */}
      {/* ABOUT THIS WORK-ITEM — what it IS, for a reader who has just opened a strange item. It
          replaced `Item at a glance`, whose two rows restated the title in the header two inches up
          and the tasks/checks the Task tab renders in full. Rows are server-composed and rendered
          in order; this reads no label by name, which is what kept a backend relabel from turning
          into three blank rows. */}
      {/* COLLAPSED by default (owner, 2026-08-08). What the item IS is read once, when you first
          open a strange item — after that it is four rows of unchanging text standing between the
          owner and the thing that is actually asking for them. Closed it costs one line and stays
          first, so the subject is still named before the verb; open it is the same card as before. */}
      {d.about.length > 0 && (
        <details className="group rounded-md border border-line bg-sunken px-3 py-2.5">
          <summary className="flex cursor-pointer list-none items-center gap-1.5">
            <ChevronRight size={13} className="shrink-0 text-faint transition group-open:rotate-90" />
            <SectionHeader className="flex items-center gap-1.5">
              <Gauge size={13} /> About this work-item
            </SectionHeader>
          </summary>
          <dl className="mt-1.5 text-[13px]">
            {d.about.map((r) => (
              <div key={r.label} className="flex gap-2 border-t border-line py-1.5 first:border-t-0">
                <dt className="w-20 shrink-0 text-muted">{r.label}</dt>
                <dd className="min-w-0 flex-1 text-fg">{codeSpans(sentence(r.value))}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}

      {/* The CURRENT PHASE card: the live line is its title, and the ask lives inside it. The live
          line has always been the attention card's header — "Review · cycle 1" says which phase is
          asking — and splitting them into two peer cards broke that sentence in half. The phase name
          wears the SectionHeader treatment like every other block's title, so the cards all start on
          the same typographic beat.

          The ask is hidden entirely when nothing needs the owner, rather than rendered as an empty
          shell — so this card is a status line alone whenever the item is just working. */}
      <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <CircleDot size={13} className={d.now.running ? 'animate-pulse text-accent' : 'text-faint'} />
          <SectionHeader>{PHASE_LABEL[d.now.phase] ?? d.now.phase}</SectionHeader>
          {d.now.cycle > 0 && <span className="text-[13px] text-muted">· cycle {d.now.cycle}</span>}
          {/* No telemetry here: model · ctx · Σtok is the HEADER's line (RunMeta), and printing the
              same three numbers twice on one screen invites the reader to look for the difference.
              And no live line either (owner, 2026-08-08): the newest event's own sentence sat here,
              truncated mid-word, restating the attention card one inch below it. The phase name,
              the cycle and the running dot are the whole status read; the event stream is Activity's
              job and the reason it is parked is the card's. */}
        </div>
        {/* WHAT THIS PHASE CONCLUDED, in its own report's words — the reason every user-facing
            report opens with `**Summary:**`. It sits directly under the phase line because it
            finishes that sentence: "Vet · cycle 2" then what vet found. Absent while the phase is
            still working: a phase writes its report as its closing act, so there is nothing to
            show yet and a placeholder would read as a conclusion. */}
        {/* LABELLED `Summary:` (owner, 2026-08-08). The sentence is a quotation from another
            document — the phase's own report — and unlabelled it read as the pane's own narration,
            indistinguishable from the live line above it. The reports name this block the same way
            (`**Summary:**`), so the label is the one already in the source. */}
        {/* HELD OVER while the phase works (owner, 2026-08-08). A phase writes its report as its
            closing act, so a running phase has no summary and this card used to go blank — which in
            autopilot is most of what the owner ever sees. It now holds the last completed phase's
            conclusion, LABELLED with that phase, so nothing is passed off as the running phase's
            own. `summary_phase` is the server's answer to "whose line is this". */}
        {d.now.summary && (
          <p className="mt-1.5 text-[13px] leading-snug text-fg">
            <span className="font-semibold text-warn">
              {d.now.summary_phase && d.now.summary_phase !== d.now.phase
                ? `${PHASE_LABEL[d.now.summary_phase] ?? d.now.summary_phase} summary:`
                : 'Summary:'}
            </span>{' '}
            {codeSpans(sentence(d.now.summary))}
          </p>
        )}
        {d.attention && (
          <div className="mt-2.5">
            <AttentionCardView card={d.attention} busy={busy} onAct={onAct}
                               contextId={contextId} />
          </div>
        )}
      </section>

      {/* THE OWNER'S OWN INPUT, and only while it can still land. Second on the pane because
          during triage it is the one thing on this screen the owner can ADD — everything below is
          something to read. It disappears the moment plan starts, which is the honest shape: the
          section is read once, cold, on plan's way in, and a form still offering to change it
          afterwards would be offering nothing. */}
      {d.now.phase === 'triage' && <FromYouCompose itemId={it.id} contextId={contextId} />}

      {/* WHAT MUST RESOLVE — and ONLY that (owner, 2026-08-07). The standing Mechanical-checks
          block is gone: it rendered on every item at every moment, mostly as a list of green rows
          and off-gate previews, and it was the largest thing on the pane while being, almost always,
          a list of things nobody had to do. What survives is the case that actually costs the owner
          something: Approve is greyed and the button alone can't say why.

          Three conditions, all necessary. `blocked_by` non-empty is the same set that greys the
          button — one rule, one writer. `at_gate`, because off-gate the server grades the gate
          AHEAD, so these are things the loop is still working through, not the owner's. And
          `!running`, because a check measured mid-write is a measurement of an unfinished file —
          that one shipped as a red "plan.md does not exist" beside its own "Agent working…" label.

          Nothing was lost: `d.checks` still ships and the Task tab reads the same gate's evidence
          in full. This pane answers one question — is anything stopping me. */}
      {d.blocked_by.length > 0 && d.at_gate && !d.now.running && (
        <section className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2.5">
          <SectionHeader className="mb-1.5 flex items-center gap-1.5 text-danger">
            <ListChecks size={13} /> Must resolve before {d.gate_label}
          </SectionHeader>
          <ul>
            {/* The named rows, filtered to the blocking failures. Every row here stops the button,
                so none of them carries a severity badge — a badge that says the same thing on every
                row is a column of ink saying nothing. The criterion is the LABEL and the detail is
                the fact, so the sentence reads at full contrast and the slug stays quiet. */}
            {d.checks.filter((c) => !c.ok && c.blocking).map((c) => {
              const Icon = CHECK_ICON[c.criterion] ?? ListChecks
              return (
                <li key={c.criterion} className="border-t border-line py-1.5 text-[13px] first:border-t-0">
                  <div className="flex items-baseline gap-2">
                    <span className="w-3 shrink-0 text-center text-danger">✗</span>
                    <Icon size={12} className="shrink-0 self-center text-faint" />
                    <span className="font-mono text-[11px] font-medium text-muted">{c.criterion}</span>
                  </div>
                  {/* Indented past the mark AND the icon so the sentence hangs under the name. */}
                  <p className="mt-0.5 pl-10 leading-snug text-fg">{c.detail}</p>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {/* The gate's `facts` chip row lived here and is gone (owner, 2026-08-02). Every chip it drew
          was already on screen: KIND and DELIVERABLE are in the header two inches up, and TRIAGED
          restated the date the mechanical check states in a full sentence directly above. The whole
          `facts` computation went with it — the drilldown was its only reader. */}
    </div>
  )
}

// §4.2's attention card: WHY (back story) · WHAT (the act) · REFERENCE (where to look). Every field
// is server-composed — this only lays it out. No leading icon: the card is already the only tinted
// block on the pane, so an alert glyph next to the word "attention" is the third thing saying it.
function AttentionCardView({ card, busy, onAct, contextId }: {
  card: NonNullable<Drilldown['attention']>; busy: string | null
  onAct: (id: string) => void; contextId: string
}) {
  const waiting = card.kind === 'awaiting_child'
  return (
    // WAITING IS NOT NEEDING (owner, 2026-08-09). An `awaiting_child` card asks nothing of the
    // owner — the last child terminating releases it automatically — so it must not wear the warn
    // frame that means "you are the blocker". Same card, quieter clothes and an honest heading.
    <div className={waiting
      ? 'rounded-lg border border-line bg-sunken px-3.5 py-3'
      : 'rounded-lg border border-warn/40 bg-warn/10 px-3.5 py-3'}>
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1 space-y-2">
          {/* The card's TITLE, so it uses the same SectionHeader as "About this work-item" and
              "Mechanical checks" — only the colour differs. It was a bespoke 11px bold span, which
              made the loudest card on the pane carry the quietest heading. */}
          <SectionHeader className={waiting ? 'text-muted' : 'text-warn'}>
            {waiting ? 'Waiting on a sub-item' : 'Need your attention'}
          </SectionHeader>
          {/* Why / What / Reference are PEERS — a numbered list with parallel labels — so they are
              one size and one weight (owner, 2026-08-01). They were 14px/medium, 12.5px/normal and
              12px/normal, which read as a heading followed by two footnotes and made the three rows
              look mis-set rather than enumerated. The `n label` column already carries the ordering;
              the type doesn't need to re-state it. */}
          <Row n="1" label="Why">
            {/* MARKDOWN, not pre-wrapped text (owner, 2026-08-08). BOTH lines: the deputy writes
                its headline as `…at the **review** gate` and its escalation as `**Issue summary:**`
                over bulleted concerns, so raw text printed the asterisks and lost the one structure
                that makes a page card scannable. `report` is the variant, not `chat`: it is the
                panel's own voice (13px, matching the What/Reference rows beside it, where `chat`
                is 14px and made the card two sizes), and it reserves its warn tint for a bold that
                opens a paragraph — the block-label role `**Issue summary:**` and `**Concern:**`
                play. `**review**` takes the same tint (CSS `:first-child` counts elements, so the
                first bold in a paragraph is "first" even mid-sentence), which is right here: the
                card is warn-framed and the deputy's chat bubble tints that word too. */}
            {/* The variant is calibrated for a scrolling report, where a paragraph earns its
                10px of air. Inside a card row the outer margins fight the row's own rhythm, so
                the first and last block give theirs back. */}
            <div className="space-y-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0">
              <Markdown text={card.why} variant="report" tone="dev" />
              {card.detail && <Markdown text={card.detail} variant="report" tone="dev" />}
            </div>
          </Row>
          <Row n="2" label="What">
            <p className="text-[13px] leading-snug text-fg">{card.do}</p>
            {/* ONE button per act, app-wide (owner rule). Every act this card can name — approve,
                drop, continue, resume, rerun, merge, pr — already has exactly one button, in the
                action bar below; repeating it here made the bar's copy look like a different control
                and split "is it live?" across two renderers. The card SAYS what to do and the bar is
                where you do it. `chat` is the one exception: answering a question has no bar button,
                so the card carries the only one there is. */}
            {card.click === 'chat' && (
              <button onClick={() => onAct(card.click)} disabled={busy === card.click}
                      className="mt-1.5 inline-flex items-center gap-1.5 rounded-md bg-warn px-2.5 py-1 text-[13px] font-semibold text-white transition hover:brightness-110 disabled:opacity-50">
                {busy === card.click ? <Loader2 size={13} className="animate-spin" />
                                     : <ActionIcon id={card.click} />}
                Open chat
              </button>
            )}
          </Row>
          {/* THE SUB-ITEMS THEMSELVES (owner, 2026-08-09) — id AND title AND where each one is,
              because "open children: 7f2a1c9b4e02" is a join key, not an answer. Each row navigates
              to that item, so following the chain is a click rather than a search of the board. */}
          {card.children.length > 0 && (
            <Row n="2" label="Blocked on">
              <ul className="space-y-1">
                {card.children.map((c) => (
                  <li key={c.id}>
                    <button
                      onClick={() => navigate({ name: 'item', repoId: contextId, itemId: c.id,
                                                tab: null, sub: null })}
                      className="group flex w-full items-baseline gap-2 rounded px-1 py-0.5 text-left
                                 transition hover:bg-hover">
                      <span className="shrink-0 font-mono text-[10px] text-faint">{c.id.slice(0, 8)}</span>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-fg
                                       group-hover:text-accent-text">{c.title || '(untitled)'}</span>
                      <span className="shrink-0 text-[11px] text-muted">
                        {PHASE_LABEL[c.phase] ?? c.phase}
                        {c.status ? ` · ${STATUS_LABEL[c.status] ?? c.status}` : ''}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </Row>
          )}
          {card.basis.length > 0 && (
            <Row n="3" label="Reference">
              {/* No bullet glyph: the label column already separates these from the row above,
                  and a "·" in front of a one-line list is decoration pretending to be structure. */}
              <ul className="space-y-0.5 text-[13px] leading-snug text-fg">
                {card.basis.map((b, i) => <li key={i}>{b}</li>)}
              </ul>
            </Row>
          )}
          {card.questions.length > 0 && (
            <ol className="space-y-1.5 border-t border-warn/25 pt-2">
              {card.questions.map((q, i) => (
                <li key={i} className="text-[13px] leading-snug">
                  <span className="mr-1 font-mono text-[10px] text-warn">?{i + 1}</span>
                  <span className="text-fg">{q.question}</span>
                  {q.recommend && <div className="mt-0.5 text-[11px] text-muted">recommends: {q.recommend}</div>}
                  {q.why && <div className="text-[11px] text-faint">{q.why}</div>}
                  {q.instead && <div className="text-[11px] text-faint">instead: {q.instead}</div>}
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  )
}

// The label column is a FIXED width, so the content of every row starts at the same x. Ragged
// labels ("Why" vs "Reference") pushed each row's text to its own indent and the card read as three
// unrelated fragments instead of one instruction.
function Row({ n, label, children }: { n: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      {/* 11px, the META step — these are row LABELS. 10px is reserved for badges and stamps, and at
          that size a label sitting beside 13px prose read as a typo rather than a column. Wide
          enough for the longest of them (`3 Reference`) on ONE line: a two-line label beside a
          one-line value is a column that looks broken, not a column that wrapped. */}
      <span className="mt-px w-[5.75rem] shrink-0 whitespace-nowrap font-mono text-[11px] tracking-wide text-warn">
        {n} {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

// ── Quick View › Proof ──────────────────────────────────────────────────────────────────────────

// The connected view (§4.2): one row per BUILT THING, each carrying its own validation →
// verification. A bare grid of check ids tells the owner nothing; "this feature, proven this way"
// does. The join is mechanical (plan task ids) — assembled server-side, never by an LLM.
type Verified = ProofRow['verified'][number]

/* The pane's CHROME. A CARD, in the same `rounded-md border-line bg-sunken` vocabulary every other
   Quick View block uses, titled with the shared `SectionHeader` — because this tab has to read as
   the same surface as Now, one click away (owner, 2026-08-01, and again 2026-08-08: "why do we have
   to have the design of this task subtab different from others?").

   I briefly rebuilt this as flat ruled sections. That is precisely the arrangement the 2026-08-01
   ruling had already tried and discarded: "a hairline between two blocks of similar-sized text is
   not a boundary anyone sees, and the pane read as one undifferentiated column." Cards carry the
   separation; colour is reserved for urgency. The refinements that DID help — a progress rail, one
   aligned glyph column, state as a pill, id chips — live inside the card, where they belong. */

function ProofSection({ title, meta, children }: {
  title: string; meta?: React.ReactNode; children: React.ReactNode
}) {
  return (
    <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <SectionHeader>{title}</SectionHeader>
        {meta}
      </div>
      <div className="mt-2">{children}</div>
    </section>
  )
}

/* One fixed-width status column, so every glyph in the pane lands on the SAME vertical line and
   the eye can run down it to read state without reading a word. */
function Glyph({ tone, children }: { tone: string; children: React.ReactNode }) {
  return <span className={`w-3.5 shrink-0 text-center text-[12px] leading-[1.45] ${tone}`}>{children}</span>
}

/* State as a PILL, not a right-floating word: the words differ in length ("not run yet" vs "✓"),
   so a bare span left a ragged right edge down the column. A pill is fixed-shape and colour-carried,
   which is the read the owner wants at a glance. */
function StatePill({ v }: { v: Verified }) {
  const [tone, label] =
    !v.ran ? ['border-line text-faint', 'not run yet']
    : v.deferred ? ['border-line text-muted', 'deferred']
    : v.passed ? ['border-success/40 bg-success/10 text-success', historyGlyph(v.history, true)]
    : ['border-danger/40 bg-danger/10 text-danger', historyGlyph(v.history, false)]
  return (
    <span className={`shrink-0 rounded-full border px-1.5 py-px text-[10px] font-medium ${tone}`}>
      {label}
    </span>
  )
}

function ProofPane({ rows, auths, lenses }: {
  rows: ProofRow[]; auths: Drilldown['authorizations']; lenses: Drilldown['lenses']
}) {
  const real = rows.filter((r) => r.built.length || r.validated.length || r.verified.length || r.task)
  if (!real.length && !lenses.length) {
    return <Empty>No tasks yet — the plan writes them, with the checks that prove them.</Empty>
  }
  const tasks = rows.filter((r) => r.task)
  const doneCount = tasks.filter((r) => r.done).length
  const itemWide = rows.find((r) => !r.task)
  // THE JOIN RUNS check → tasks, so render it that way (owner: "very uglily layouted", 2026-08-08).
  // `proof_rows` fans each check out under every task its `covers:` names, which is right for the
  // payload and wrong for the eye: one check covering t1/t2/t4 printed its full `proves` sentence
  // three times, and each task's title printed twice more (tracker, then block header). Four tasks
  // and four checks rendered as ~20 blocks of mostly repeated prose. Folding the fan-out back here
  // costs nothing — the covered ids are exactly the keys it was fanned out under.
  const byCheck = new Map<string, { v: Verified; covers: string[] }>()
  for (const r of rows) {
    for (const v of r.verified) {
      const seen = byCheck.get(v.check)
      if (seen) { if (r.task) seen.covers.push(r.task) } else {
        byCheck.set(v.check, { v, covers: r.task ? [r.task] : [] })
      }
    }
  }
  const checks = [...byCheck.values()]
  return (
    <div className="space-y-3">
      {/* ONE CONTRAST RULE across this pane (owner, 2026-08-06): the SENTENCE reads at `text-fg`,
          its NAME stays quiet at `text-faint`. Lens name / check id / section label are addressing —
          you scan them to find your place — while what a check proves and what a lens probed are the
          thing you opened the tab to read.

          READING ORDER (owner, 2026-08-07): what was committed to → whether it holds → what nobody
          had to remember to ask. THREE sections, one per question — not a block per task carrying a
          copy of every check that touches it (owner, 2026-08-08). */}
      {tasks.length > 0 && (
        <ProofSection title="Tasks"
                 meta={<span className="text-[11px] tabular-nums text-muted">{doneCount}/{tasks.length}</span>}>
          {/* A 2px rail instead of a second sentence about progress: "how much of this is done" is
              the question asked on the way in, and a bar answers it before any word is read. */}
          <div className="mb-2 h-[3px] overflow-hidden rounded-full bg-hover">
            <div className="h-full rounded-full bg-success transition-[width]"
                 style={{ width: `${Math.round((doneCount / tasks.length) * 100)}%` }} />
          </div>
          <ul className="space-y-2">
            {tasks.map((r) => (
              <li key={r.task} className="flex gap-2">
                <Glyph tone={r.done ? 'text-success' : 'text-faint'}>{r.done ? '✓' : '·'}</Glyph>
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] leading-snug">
                    {/* The id is ADDRESSING: it is what a check's `covers` chips point back at, so
                        it has to be visible here or those chips resolve to nothing. Set inline and
                        quiet, it labels the sentence without taking a column of its own. */}
                    <span className="mr-1.5 font-mono text-[10px] text-faint">{r.task}</span>
                    {/* A done task goes QUIET, not struck through: the ticks are a progress read,
                        and a column of struck-out text reads as cancelled work. */}
                    <span className={r.done ? 'text-muted' : 'text-fg'}>{codeSpans(sentence(r.text))}</span>
                  </div>
                  {/* The SPECIFICATION, folded — the same `▸` affordance the checks use, because it
                      is the same kind of thing: real detail that is not the answer to the question
                      the tab is open for. Printed inline it was 340 characters and eight code spans
                      per row, and four of them made the pane a wall (owner, 2026-08-08). The plan
                      writes it on the indented lines under the task's name; `parse_tasks` keeps the
                      two apart so the name can be a name. */}
                  {r.detail && (
                    <details className="group mt-0.5">
                      <summary className="cursor-pointer list-none text-[11px] text-faint
                                          transition hover:text-muted">
                        <span className="inline-block transition group-open:rotate-90">▸</span>{' '}
                        what this covers
                      </summary>
                      <p className="mt-1 border-l border-line pl-2 text-[12px] leading-snug text-muted">
                        {codeSpans(sentence(r.detail))}
                      </p>
                    </details>
                  )}
                  {/* Validation is the BUILD's own evidence for this task — it belongs under the
                      task it evidences, hung off a rule so it reads as support rather than as more
                      tasks. The `built` prose stays out (owner, 2026-08-01): it is the build
                      report's §Built bullets verbatim, one tab over under Reports → Build. */}
                  {/* REAL BULLETS (owner, 2026-08-08). This was a rule on the left and nothing
                      else, so four separate observations read as one wrapped paragraph and the
                      owner asked where the bullets were. The rule groups; the disc separates. Same
                      `list-disc` + faint marker the report renderer uses, so a list is one object
                      whether it arrives as markdown or as payload.

                      `space-y-2`, and the same value on every evidence list in this pane (owner,
                      2026-08-08): these entries WRAP, so the gap between two bullets has to beat
                      the gap between two lines of one bullet or the disc is the only thing telling
                      them apart. At `0.5` the two were within a pixel of each other. */}
                  {r.validated.length > 0 && (
                    <ul className="mt-1 list-disc space-y-2 border-l border-line pl-5 text-[12px]
                                   leading-snug text-muted marker:text-faint">
                      {r.validated.map((v, i) => <li key={i}>{codeSpans(sentence(v))}</li>)}
                    </ul>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {/* Work that named no task — a whole-suite run is not per-task and never was. It used to
              get a card of its own titled `Item-wide`, which read as a fifth task. */}
          {/* ONE label for the GROUP, not a tag on every row (owner, 2026-08-08). Each row wore a
              bare `across`, which named nothing on its own — the reader had to infer that the word
              meant "this evidence belongs to no single task". Said once, in full, above the list,
              it is a heading instead of a riddle. */}
          {itemWide && itemWide.validated.length > 0 && (
            <div className="mt-2 border-l border-line pl-2">
              <div className="text-[10px] uppercase tracking-wide text-faint">Across the whole item</div>
              <ul className="mt-0.5 list-disc space-y-2 pl-3.5 text-[12px] leading-snug
                             text-muted marker:text-faint">
                {itemWide.validated.map((v, i) => <li key={i}>{codeSpans(sentence(v))}</li>)}
              </ul>
            </div>
          )}
        </ProofSection>
      )}
      {/* THE EXAM, once. Every check the plan declared, in plan order, each naming the tasks it
          defends rather than being reprinted under each of them. */}
      {/* The ACTIVITY, not a past-tense claim: these rows exist from the plan gate on, and
          "Verified" above a check nobody has run yet is a lie the layout tells. */}
      {checks.length > 0 && (
        <ProofSection title="Verification"
                 meta={<span className="text-[11px] tabular-nums text-muted">
                   {checks.filter((c) => c.v.ran && c.v.passed).length}/{checks.length}
                 </span>}>
          <ul className="space-y-3">
            {checks.map(({ v, covers }) => (
              <li key={v.check}>
                {/* THE ROW LEADS WITH WHAT IT PROVES (owner, 2026-08-07) — the plan's own
                    sentence for what is true of the product when this check passes. It used
                    to lead with the check id and `expect`, which meant the owner read a slug
                    and a shell expectation and had to reconstruct the meaning from them.
                    Older plans have no `proves:` (it became a field in this same phase), so
                    the row falls back to `expect` and then to the id rather than blanking. */}
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 text-[13px] leading-snug text-fg">
                    {codeSpans(sentence(v.proves || v.expect || v.check))}
                  </span>
                  {/* A check the loop hasn't reached is NOT a failure — it reads "not run
                      yet" in the quiet colour. Rendering it as ✗ would tell the owner
                      approving a plan that their exam had already failed. */}
                  <StatePill v={v} />
                </div>
                {/* Which tasks this defends, as ids rather than as repetition. This is the whole
                    reason the pane can show each check once: the connection §4.2 asks for is
                    preserved by naming the tasks, not by reprinting the check under each. */}
                {covers.length > 0 && (
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <span className="text-[10px] text-faint">covers</span>
                    {covers.map((id) => (
                      <span key={id} className="rounded bg-hover px-1 py-px font-mono text-[10px] text-muted">
                        {id}
                      </span>
                    ))}
                  </div>
                )}
                {/* HOW it was checked, one click away. The id, the mode, the expectation and
                    the captured output are all real evidence and none of them is the answer
                    to "did this hold" — folded away, they stop competing with the sentence
                    above for the reader's first pass, and a `<details>` costs one click to
                    get back. A failure's DIAGNOSIS is deliberately NOT in here: that is the
                    actionable finding, and it stays open below. */}
                <details className="mt-0.5 group">
                  <summary className="cursor-pointer list-none text-[11px] text-faint
                                      transition hover:text-muted">
                    <span className="inline-block transition group-open:rotate-90">▸</span>{' '}
                    how this was checked
                  </summary>
                  <div className="mt-1 space-y-1 border-l border-line pl-2">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px] text-faint">
                      <span className="font-mono">{v.check}</span>
                      {v.mode && <span>· {v.mode}</span>}
                      {/* Provenance, only when it is the stronger claim: the kernel ran this
                          one itself, so no model sits between the exit code and the verdict.
                          Agent-attested is the norm and needs no badge — labelling both
                          would spend a row of ink to say nothing. */}
                      {v.by === 'machine' && (
                        <span className="rounded bg-hover px-1 tracking-wide"
                              title="Run by the kernel in the sandbox — no agent between the exit code and this verdict">
                          machine-run
                        </span>
                      )}
                      {v.source && <span>· {v.source}</span>}
                    </div>
                    {v.expect && (
                      <p className="text-[13px] leading-snug text-muted">
                        expects {codeSpans(v.expect)}
                      </p>
                    )}
                    {v.how && (
                      <pre className="max-h-28 overflow-auto whitespace-pre-wrap rounded bg-hover px-1.5 py-1 font-mono text-[11px] leading-relaxed text-muted">{v.how}</pre>
                    )}
                  </div>
                </details>
                {/* A rubric is judged criterion by criterion, so it is shown that way: the
                    plan's list before anything runs, each line marked once it has been
                    judged. A bare "2/3" would hide the only part that is actionable —
                    WHICH one missed. */}
                {(v.criteria.length > 0 || v.rubric.length > 0) && (
                  <ul className="mt-1 space-y-2">
                    {(v.criteria.length ? v.criteria
                                        : v.rubric.map((text) => ({ text, met: null })))
                      .map((c, i) => (
                      <li key={i} className="flex gap-1.5 text-[13px] leading-snug">
                        <span className={`shrink-0 ${
                          c.met === null ? 'text-faint'
                          : c.met ? 'text-success' : 'text-danger'}`}>
                          {c.met === null ? '·' : c.met ? '✓' : '✗'}
                        </span>
                        <span className={c.met === false ? 'text-fg' : 'text-muted'}>
                          {codeSpans(sentence(c.text))}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {/* The located cause, above the raw output: "where it broke" is the line
                    the reader wants, and the output is the supporting evidence under it.
                    Vet never names the fix, so there is none to render. */}
                {!v.passed && !v.deferred && v.why && (
                  <p className="mt-0.5 text-[13px] leading-snug text-muted">
                    <span className="font-medium text-fg">{codeSpans(v.where)}</span>
                    {' — '}{codeSpans(v.why)}
                    {v.unknown && (
                      <span className="text-faint"> · undetermined: {codeSpans(v.unknown)}</span>
                    )}
                  </p>
                )}
                {/* A failing row shows the vet's captured output verbatim — the failure IS the
                    expected-vs-actual, and hiding it behind a tooltip is what made the old
                    check grid say nothing. */}
                {!v.passed && !v.deferred && v.result && (
                  <pre className="mt-0.5 max-h-28 overflow-auto whitespace-pre-wrap rounded bg-hover px-1.5 py-1 font-mono text-[11px] leading-relaxed text-muted">{v.result}</pre>
                )}
              </li>
            ))}
          </ul>
        </ProofSection>
      )}
      {/* THE LENSES, last and under the QUESTION each one asks. They answer what nobody had to
          remember to ask, so they belong after the plan's own exam rather than above it — the
          reading order is: what was committed to, whether it holds, then what else was looked for.
          The slug alone (`intent`, `robustness`) named a category without saying what was asked of
          the work; the question says it in the words the owner would use.

          A lens with nothing to report still shows what it probed — that is the difference between
          "nothing is wrong" and "nobody looked" — and one probe per LINE, because the owner reads
          this list to count what was actually tried. */}
      {lenses.length > 0 && (
        <ProofSection title="Also looked at">
          <ul className="space-y-3">
            {lenses.map((l) => (
              <li key={l.lens}>
                {/* The QUESTION is the heading here, so it takes heading treatment — medium weight
                    at `text-fg`. Set at the same 13px muted as the probes under it, the list read
                    as one undifferentiated paragraph of sentences.

                    NAMED as well as asked (owner, 2026-08-08). The question alone said what was
                    looked for but never which lens looked, so four questions in a row read as a
                    list of unrelated concerns rather than as the four standing lenses — and the
                    name is what the vet plan, the reports and the ledger all call this thing. An
                    unmapped lens shows its name alone rather than printing the slug twice.

                    The name reads at FULL contrast, not `text-faint` (owner, 2026-08-08). The
                    pane's quiet-name rule is for addressing you scan PAST — a check id, a lens
                    slug in a corner. This name is part of the heading itself: greyed, it read as a
                    caption in front of the sentence rather than as the first half of it. */}
                <div className="text-[12.5px] font-medium leading-snug text-fg">
                  <span>{sentence(l.lens)}:</span>{' '}
                  {LENS_QUESTION[l.lens] ?? ''}
                </div>
                <ul className="mt-1 list-disc space-y-2 border-l border-line pl-5 text-[12px]
                               leading-snug text-muted marker:text-faint">
                  {l.probed.map((probe, i) => <li key={i}>{codeSpans(sentence(probe))}</li>)}
                </ul>
                {/* FINDINGS STAND OFF the probe list (owner, 2026-08-08). At `mt-1` the first
                    finding sat 4px under the last probe — closer to it than two probes are to each
                    other — so the one line in the section that reports a PROBLEM read as one more
                    thing that was looked at. A finding is a different kind of statement from a
                    probe, and the gap is what says so.

                    `items-baseline` on the row: the label is 10px against a 12.5px sentence, and
                    stretched from the top the two sat on different lines by a pixel or two — enough
                    to read as misaligned rather than as a label on a sentence. */}
                {l.findings.length > 0 && (
                <div className="mt-3 space-y-2">
                {l.findings.map((f, i) => (
                  <p key={i} className="flex items-baseline gap-1.5 text-[12.5px] leading-snug">
                    {/* A LABEL, not a chip (owner, 2026-08-08). Given `bg-hover` it wore the exact
                        background a code span wears, so `low` sitting beside `tally rename food ""`
                        read as another typable token. `bg-hover` belongs to code alone; severity is
                        carried by colour and case.

                        And it says WHAT it is a measure of: a bare `LOW` beside a sentence named
                        no quantity, and the owner had to ask (2026-08-08). One extra word answers
                        it without a legend anywhere else on the page. */}
                    <span className={`shrink-0 text-[10px] font-semibold uppercase tracking-wide ${
                      f.severity === 'high' ? 'text-danger'
                      : f.severity === 'medium' ? 'text-warn' : 'text-faint'}`}>
                      {f.severity} severity
                    </span>
                    <span className="min-w-0 text-fg">{codeSpans(sentence(f.text))}</span>
                  </p>
                ))}
                </div>
                )}
              </li>
            ))}
          </ul>
        </ProofSection>
      )}
      {/* Only the state that ASKS something of you gets a line. "none pending" is a footer that
          reports the absence of work on every item that never had any — it spends a permanent row
          telling the reader nothing happened. The grant/deny surface is where a real one is acted
          on; this line exists to point at it. */}
      {auths.length > 0 && (
        <p className="text-[11px] text-warn">
          {auths.length} authorization{auths.length === 1 ? '' : 's'} pending your grant/deny
        </p>
      )}
    </div>
  )
}

// What each standing lens actually ASKS of the work, in the owner's words. The slug is the vet
// tool's vocabulary; this is the question a person would ask, and it is what makes a list of probes
// legible to someone who has never read the verification design. An unmapped lens falls back to its
// slug rather than blanking — `performance` is recordable and the set can grow.
const LENS_QUESTION: Record<string, string> = {
  intent: 'Does this actually solve the problem the item was filed for?',
  safety: 'Can this hurt anything — unsafe evaluation, destructive paths, secrets in the open?',
  robustness: 'Which inputs were tried, and which are still unhandled?',
  performance: 'Is it fast enough, against a budget the plan named?',
}

// Sentence-case the first character — for lines that ARE sentences but reach us lowercase: a task
// written "add the --category flag", a validation bullet, an enum rendered as a value. Owner-flagged
// 2026-08-07 across the About card and the Task tab.
//
// Only when the string starts with a LETTER. A line beginning with a backtick opens a code span
// (`` `tally list --category groceries` prints only… ``), and capitalising into it would rewrite a
// command the reader is meant to be able to type.
function sentence(text: string): string {
  return /^[a-z]/.test(text) ? text[0].toUpperCase() + text.slice(1) : text
}

// Plan lines are markdown-flavoured prose, and `code` is the only inline markup our templates use —
// paths, flags, function names. Rendering the raw backticks made every one of them read as a quote.
// Code is 12px absolute here, as in the reports: never `em`, so one token is one size app-wide.
function codeSpans(text: string) {
  return text.split('`').map((part, i) => (i % 2
    // TINTED, like every other code span the owner sees (owner, 2026-08-08). The drilldown rendered
    // `code` as plain `text-fg` while the Reports tab one click away and the standalone .md pages
    // rendered the same token in accent — so the one thing on the screen the reader can actually
    // type changed colour depending on which tab it appeared in. `bg-hover` (not `bg-sunken`) is
    // the background, because these panes ARE `bg-sunken` cards and a sunken chip inside one is
    // invisible; hover separates from every container in both themes.
    ? <code key={i} className="rounded bg-hover px-1 font-mono text-[12px] text-accent-text">{part}</code>
    : <span key={i}>{part}</span>))
}

// `c3 ✗→✓` — a check that failed then passed. Latest-only would hide the loop's whole story.
function historyGlyph(history: { cycle: number | null; passed: boolean }[], passed: boolean): string {
  if (history.length < 2) return passed ? '✓' : '✗'
  const marks = history.map((h) => (h.passed ? '✓' : '✗'))
  const collapsed = marks.filter((m, i) => i === 0 || m !== marks[i - 1])
  return collapsed.length < 2 ? (passed ? '✓' : '✗') : collapsed.join('→')
}

// ── Reports ─────────────────────────────────────────────────────────────────────────────────────

// One phase's report, rendered 1:1, with the link to the full agent-facing contract behind it
// (§4.3). The report is the compact read; the contract is the whole thing, one click away, never
// pasted in — that separation is what keeps the report worth reading.
//
// The leading `# <Phase> — <item title>` is dropped. It's right in the FILE, which is read on its
// own on disk; in this pane the modal header already carries the title and the sub-tab already says
// the phase, so it rendered as the same sentence three times over.
const LEAD_H1 = /^\s*#\s+.*\n+/

// `## From you` is rendered by the editor below, not twice. The section stays in the FILE and in
// `report_text` — the deputy reads the owner's input there — but on this pane the markdown copy and
// the textarea holding the same words sat one inch apart, and the owner had to work out which of
// the two they were supposed to edit.
const FROM_YOU_SECTION = /\n##\s+From you\s*\n[\s\S]*?(?=\n##\s|$)/

function ReportPane({ itemId, contextId, phase, itemPhase }: {
  itemId: string; contextId: string; phase: string; itemPhase: string
}) {
  const q = useLive(K.itemReport(contextId, itemId, phase),
                    () => getWorkItemReport(itemId, phase, contextId), 15000)
  if (!q.data) return q.error ? <Empty>Couldn’t load report-{phase}.md — {String(q.error)}</Empty> : <Loading />
  const r = q.data
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-[10px] text-faint">
        <span className="rounded bg-hover px-1.5 py-0.5 font-mono">reports/{r.name}.md</span>
        <span>updated {fmtLocal(new Date(r.mtime * 1000).toISOString())}</span>
        {/* The contract is a DOC, so it opens as one — its own browser tab, served verbatim by
            `doc.html`. It used to be inert text naming a path the owner had no way to reach. */}
        {r.contract && (
          <a href={`/api/dev/work-items/${encodeURIComponent(itemId)}/doc.html`
                 + `?path=${encodeURIComponent(r.contract)}&context_id=${encodeURIComponent(contextId)}`}
             target="_blank" rel="noreferrer"
             title="Open the agent-facing contract in a new tab"
             className="ml-auto inline-flex items-center gap-1 text-faint transition hover:text-accent-text">
            full contract: <code>{r.contract}</code> <ExternalLink size={11} />
          </a>
        )}
      </div>
      <Markdown text={r.text.replace(LEAD_H1, '').replace(FROM_YOU_SECTION, '')}
                variant="report" tone="dev" />
      {phase === 'triage' && (
        <FromYouSlots itemId={itemId} contextId={contextId} editable={itemPhase === 'triage'} />
      )}
    </div>
  )
}

// ── `## From you` — the owner's own section ─────────────────────────────────────────────────────
//
// The ONE section of any report the owner writes, and the only place their own words reach the plan
// phase as instruction rather than as chat. Two surfaces, split by what they are for (owner,
// 2026-08-08):
//
//   COMPOSE lives on Quick View › Now, and only while the item is in TRIAGE. That is the whole
//   window in which writing here changes anything — plan reads the section once, cold, on its way
//   in — and on autopilot it may never be open at all. Putting the only thing the owner CAN add
//   beside the only phase where adding it lands is what makes the window legible.
//
//   THE SLOTS themselves live under Reports › Triage, with the report they belong to, where each
//   can be removed. Deleting is writing, so it obeys the same triage-only window; afterwards the
//   list stays visible as part of the record.
//
// Both talk to one endpoint and PUT the whole pair of lists — the owner is the section's only
// writer, so there is no concurrent edit for a delta to protect.
const FROM_YOU_HINT = 'Whatever is here when plan starts is authority it follows, not input it weighs.'

function useOwnerInput(itemId: string, contextId: string) {
  const q = useLive(K.itemOwnerInput(contextId, itemId),
                    () => getWorkItemOwnerInput(itemId, contextId), 30000)
  async function save(next: { references: OwnerReference[]; notes: OwnerNote[] }) {
    await saveWorkItemOwnerInput(itemId, next.references, next.notes, contextId)
    invalidate(K.itemOwnerInput(contextId, itemId), K.itemReport(contextId, itemId, 'triage'))
  }
  return { saved: q.data as OwnerInput | undefined, save }
}

const FY_BOX = 'w-full rounded border border-line bg-panel px-2 py-1 text-[13px] text-fg '
             + 'outline-none transition placeholder:text-faint focus:border-accent'

// Add · Clear, the pair every block on this card carries. Add stays inert until every field it
// needs is filled — a half-typed reference is not a reference, and an empty slot on disk would read
// to the plan phase as an instruction with nothing in it.
function AddClear({ ready, busy, onAdd, onClear }: {
  ready: boolean; busy: boolean; onAdd: () => void; onClear: () => void
}) {
  return (
    <div className="flex items-center gap-2">
      <button onClick={onAdd} disabled={!ready || busy}
              className="inline-flex items-center gap-1 rounded bg-accent px-2.5 py-1 text-[11px]
                         font-medium text-on-accent transition hover:opacity-90 disabled:opacity-40">
        {busy ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />} Add
      </button>
      <button onClick={onClear} disabled={busy}
              className="rounded border border-line px-2.5 py-1 text-[11px] text-muted transition
                         hover:text-fg disabled:opacity-40">
        Clear
      </button>
    </div>
  )
}

function FromYouCompose({ itemId, contextId }: { itemId: string; contextId: string }) {
  const { saved, save } = useOwnerInput(itemId, contextId)
  const [ref, setRef] = useState({ source: '', description: '' })
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  if (!saved) return null

  async function add(next: { references: OwnerReference[]; notes: OwnerNote[] }, reset: () => void) {
    setBusy(true); setErr('')
    try { await save(next); reset() } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  const refReady = !!ref.source.trim() && !!ref.description.trim()
  const noteReady = !!note.trim()

  return (
    <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
      <SectionHeader className="mb-0.5 flex items-center gap-1.5">
        <PenLine size={13} /> From you
      </SectionHeader>
      <p className="mb-2 text-[11px] leading-snug text-faint">
        {saved.exists ? FROM_YOU_HINT
                      : 'Triage hasn’t written the brief yet — there is nothing to write into.'}
      </p>
      {saved.exists && (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <div className="text-[11px] font-medium text-muted">Useful imported references</div>
            <input className={FY_BOX} value={ref.source} disabled={busy}
                   placeholder="Source — a doc, URL or path"
                   onChange={(e) => setRef({ ...ref, source: e.target.value })} />
            <input className={FY_BOX} value={ref.description} disabled={busy}
                   placeholder="Description — what it governs"
                   onChange={(e) => setRef({ ...ref, description: e.target.value })} />
            <AddClear ready={refReady} busy={busy}
                      onClear={() => setRef({ source: '', description: '' })}
                      onAdd={() => add({ references: [...saved.references, ref], notes: saved.notes },
                                       () => setRef({ source: '', description: '' }))} />
          </div>
          {/* The hairline is the only separator: two labelled blocks in one card, not two cards —
              they are the same act (handing plan something it can't derive) at two grains. */}
          <div className="space-y-1.5 border-t border-line pt-2.5">
            <div className="text-[11px] font-medium text-muted">Verification notes</div>
            <input className={FY_BOX} value={note} disabled={busy}
                   placeholder="Description — something you want proven; each becomes one check"
                   onChange={(e) => setNote(e.target.value)} />
            <AddClear ready={noteReady} busy={busy} onClear={() => setNote('')}
                      onAdd={() => add({ references: saved.references,
                                         notes: [...saved.notes, { description: note }] },
                                       () => setNote(''))} />
          </div>
          {/* What is already in — a count, not the list. The slots live under Reports › Triage
              with the report they belong to, and duplicating them here would give the owner two
              places to reconcile for one short list. */}
          {(saved.references.length > 0 || saved.notes.length > 0) && (
            <p className="border-t border-line pt-2 text-[11px] text-faint">
              {saved.references.length} reference{saved.references.length === 1 ? '' : 's'} ·{' '}
              {saved.notes.length} note{saved.notes.length === 1 ? '' : 's'} in — see them under
              Reports → Triage, where they can be removed.
            </p>
          )}
          {err && <p className="text-[11px] text-danger-text">{err}</p>}
        </div>
      )}
    </section>
  )
}

// The slots as they stand, under the triage report. Removal is the only act here: composing belongs
// beside the phase that reads it. `editable` is the triage window — afterwards this is the record of
// what the plan phase was handed, and a record you can still edit is not one.
function FromYouSlots({ itemId, contextId, editable }: {
  itemId: string; contextId: string; editable: boolean
}) {
  const { saved, save } = useOwnerInput(itemId, contextId)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  if (!saved?.exists) return null
  const empty = !saved.references.length && !saved.notes.length

  async function drop(key: string, next: { references: OwnerReference[]; notes: OwnerNote[] }) {
    setBusy(key); setErr('')
    try { await save(next) } catch (e) { setErr(String(e)) } finally { setBusy('') }
  }

  const row = 'flex items-start gap-2 border-t border-line py-1.5 text-[13px] first:border-t-0'
  const del = 'shrink-0 rounded p-0.5 text-faint transition hover:bg-hover hover:text-danger '
            + 'disabled:opacity-40'
  return (
    <div className="mt-4 rounded border border-line bg-sunken px-3 py-2.5">
      <SectionHeader className="mb-0.5 flex items-center gap-1.5">
        <PenLine size={13} /> From you
      </SectionHeader>
      <p className="mb-2 text-[11px] leading-snug text-faint">
        {empty
          ? (editable
              ? 'Nothing yet. Add references and verification notes from Quick View → Now.'
              : 'You added nothing here — plan designed from the brief alone.')
          : editable ? FROM_YOU_HINT
                     : 'What the plan phase was handed. Read-only now that triage has passed.'}
      </p>
      {saved.references.length > 0 && (
        <div className="mb-2">
          <div className="text-[11px] font-medium text-muted">Useful imported references</div>
          <ul>
            {saved.references.map((r, i) => (
              <li key={`r${i}`} className={row}>
                <span className="min-w-0 flex-1 leading-snug text-fg">
                  <span className="font-medium">{r.source}</span>
                  {r.source && r.description ? ' — ' : ''}
                  <span className="text-muted">{r.description}</span>
                </span>
                {editable && (
                  <button className={del} title="Remove this reference" disabled={!!busy}
                          onClick={() => drop(`r${i}`, {
                            references: saved.references.filter((_, j) => j !== i),
                            notes: saved.notes })}>
                    {busy === `r${i}` ? <Loader2 size={12} className="animate-spin" />
                                      : <Trash2 size={12} />}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {saved.notes.length > 0 && (
        <div>
          <div className="text-[11px] font-medium text-muted">Verification notes</div>
          <ul>
            {saved.notes.map((n, i) => (
              <li key={`n${i}`} className={row}>
                <span className="min-w-0 flex-1 leading-snug text-fg">{n.description}</span>
                {editable && (
                  <button className={del} title="Remove this note" disabled={!!busy}
                          onClick={() => drop(`n${i}`, {
                            references: saved.references,
                            notes: saved.notes.filter((_, j) => j !== i) })}>
                    {busy === `n${i}` ? <Loader2 size={12} className="animate-spin" />
                                      : <Trash2 size={12} />}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {err && <p className="mt-1 text-[11px] text-danger-text">{err}</p>}
    </div>
  )
}

// ── Trace ───────────────────────────────────────────────────────────────────────────────────────

function TraceTab({ it, contextId, rate, events, pane }: {
  it: WorkItem; contextId: string; rate: number; events: DevEvent[]
  pane: 'runs' | 'timeline'
}) {
  // Lazy TWICE over: the call-trail is the heaviest feed, only this tab reads it, and only the Runs
  // pane renders it — so opening Timeline on a long-lived item costs nothing.
  const artQ = useLive(pane === 'runs' ? K.itemArtifacts(contextId, it.id) : null,
                       () => getWorkItemArtifacts(it.id, contextId), rate)
  const detailQ = useLive<WorkItemDetail>(pane === 'runs' ? K.itemDetail(contextId, it.id) : null,
                                          () => getWorkItemDetail(it.id, contextId), 30000)
  if (pane === 'timeline') return <TimelinePane events={events} />
  return <TracePane artifacts={artQ.data?.artifacts ?? []} runs={artQ.data?.runs ?? []}
                    execution={detailQ.data?.execution ?? null} />
}

// ── panes carried over ──────────────────────────────────────────────────────────────────────────

type AuthRow = Drilldown['authorizations'][number]

// Quick View › Authorization. It was a permanent banner above the tabs; it is a sub now (owner,
// 2026-08-08), reached from the blinking flag in the tab row. Same card, same two buttons — what
// changed is that it costs nothing on the items that have no request, which is nearly all of them.
//
// The feed carries PENDING requests only, so every row here is a decision the owner still owes.
function AuthorizationsPane({ auths, busy, onDecide }: {
  auths: AuthRow[]
  busy: string | null
  onDecide: (id: string, decision: 'granted' | 'denied') => void
}) {
  if (!auths.length) {
    return <Empty>Nothing is waiting on your authorization — this item asked for no contract change.</Empty>
  }
  return (
    <div className="space-y-2">
      <section className="rounded-md border border-warn/40 bg-warn/5 px-3 py-2.5">
        <SectionHeader className="flex items-center gap-1.5 text-warn">
          <ShieldCheck size={13} /> Your call — {auths.length > 1 ? `${auths.length} requests` : '1 request'}
        </SectionHeader>
        <p className="mt-1 text-[13px] leading-snug text-muted">
          The build found that finishing this work would change what the project PROMISES, and
          deferred rather than deciding for you. Nothing is written either way until you approve the
          item: <span className="text-fg">grant</span> and close performs the change when it merges;{' '}
          <span className="text-fg">deny</span> and the code ships with the gap on record.
        </p>
      </section>
      {auths.map((a) => (
        <div key={a.id} className="rounded-md border border-line bg-sunken px-3 py-2.5">
          <p className="text-[13px] font-medium leading-snug text-fg">{sentence(a.what)}</p>
          <p className="mt-1 text-[13px] leading-snug text-muted">{sentence(a.why)}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-faint">
            {a.doc && <span>doc: <code className="text-muted">{a.doc}</code></span>}
            <span>scope: <code className="text-muted">{a.scope}</code></span>
            <span className={a.delegable ? 'text-muted' : 'font-medium text-warn'}>
              {a.delegable ? 'sync-to-reality' : 'owner-reserved — escalated to you'}
            </span>
          </div>
          <div className="mt-2.5 flex items-center gap-2">
            <button onClick={() => onDecide(a.id, 'granted')} disabled={!!busy}
                    className="inline-flex items-center gap-1.5 rounded-md bg-accent px-2.5 py-1 text-[13px] font-semibold text-on-accent transition hover:opacity-90 disabled:opacity-50">
              {busy === a.id ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Grant
            </button>
            <button onClick={() => onDecide(a.id, 'denied')} disabled={!!busy}
                    className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-[13px] text-muted transition hover:text-danger disabled:opacity-50">
              <Ban size={13} /> Deny
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

// The uniform timeline strip: the item's dev-event trail as one glyph-coded feed. Density by
// omission — `.start` rows say nothing their `.end` twin doesn't, so they are dropped.
const MILESTONE_KINDS = new Set([
  'phase.advance', 'git.merge', 'git.pr', 'git.worktree', 'git.revert', 'item.complete', 'item.abandon',
  'close.proposed', 'review.route', 'inbox.push', 'item.await',
])
function TimelinePane({ events }: { events: DevEvent[] }) {
  const rows = events.filter((e) => !e.kind.endsWith('.start'))
  if (!rows.length) {
    return <Empty>Nothing has happened to this item yet — phase moves, gate decisions and git acts land here.</Empty>
  }
  return (
    <Section icon={History} title={`Timeline · ${rows.length} event${rows.length === 1 ? '' : 's'}`}>
      {/* Never let a cap read as "that's all there was" — say it out loud if we ever hit it. */}
      {events.length >= EVENT_CAP && (
        <p className="mb-2 text-[11px] text-warn">
          Showing the newest {EVENT_CAP} events — this item has more history than the feed carries.
        </p>
      )}
      <ol className="space-y-1">
        {rows.map((e) => {
          const milestone = MILESTONE_KINDS.has(e.kind)
          const owner = e.actor === 'owner'
          return (
            <li key={e.id} className="flex items-baseline gap-2 text-[13px]">
              <span className={`w-2 shrink-0 text-center text-[10px] ${
                owner ? 'text-accent' : milestone ? 'text-success' : 'text-line'
              }`}>
                {owner ? '◆' : milestone ? '●' : '·'}
              </span>
              <span className="shrink-0 font-mono text-[10px] tabular-nums text-faint">{fmtLocal(e.created_at)}</span>
              <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] ${
                milestone ? 'bg-hover text-fg' : 'bg-sunken text-faint'
              }`}>{e.kind}</span>
              <span className={`min-w-0 flex-1 truncate ${milestone ? 'text-fg' : 'text-muted'}`}>{e.summary}</span>
            </li>
          )
        })}
      </ol>
    </Section>
  )
}

// The Deputy log — the owner's stand-in's governance trail on this item, in one place and OUT of the
// chat: every gate call it made, with the rationale. The conversation lives in the chat; this is the
// accountability record the owner reads to see WHY the deputy did what it did.
const DEPUTY_ROW: Record<string, { icon: typeof ShieldCheck; label: string; tint: string }> = {
  'deputy.approve': { icon: Check, label: 'Approved', tint: 'text-success' },
  'deputy.escalate': { icon: AlertTriangle, label: 'Escalated to you', tint: 'text-danger' },
  'deputy.query': { icon: MessageSquare, label: 'Sent feedback to the agent', tint: 'text-accent-text' },
  'deputy.send_back': { icon: CornerUpLeft, label: 'Sent back', tint: 'text-warn' },
}
// An escalation is RESOLVED once the item leaves the gate it was raised at — the owner answered by
// advancing (or by sending it back), and nothing about that decision is recorded on the escalation
// event itself. Unmarked, a log of past pages reads as a queue of things still owed (owner,
// 2026-08-08): "why do we keep having this previously escalated to you?" So the row is marked, and
// only the one still standing keeps the alarm colour.
function isSettled(gate: string | undefined, phase: string): boolean {
  const at = PHASES.indexOf(gate as Phase)
  const now = PHASES.indexOf(phase as Phase)
  return at >= 0 && now >= 0 && now > at
}

function DeputyLogPane({ events, phase }: { events: DevEvent[]; phase: string }) {
  const rows = events
    .filter((e) => String(e.kind).startsWith('deputy') && !e.kind.endsWith('.start') && !e.kind.endsWith('.end'))
    // NEWEST FIRST — every log surface in this app reads that way (the event feed arrives DESC and
    // Timeline honours it), and the deputy row the owner needs is always the one it just wrote.
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
  if (!rows.length) return <Empty>The deputy hasn’t acted on this item yet.</Empty>
  return (
    <Section icon={ShieldCheck} title="Deputy log">
      <ol className="space-y-2">
        {rows.map((e) => {
          const m = (e.meta ?? {}) as Record<string, unknown>
          let row = DEPUTY_ROW[e.kind] ?? { icon: ShieldCheck, label: e.kind, tint: 'text-muted' }
          const gate = (m.gate ?? m.phase ?? m.origin_gate) as string | undefined
          // A page the owner has already answered goes QUIET and says so. It stays in the log —
          // it is real history — but it stops competing with the one that still needs them.
          const settled = e.kind === 'deputy.escalate' && isSettled(gate, phase)
          if (settled) row = { ...row, icon: Check, label: 'Escalated to you · resolved',
                               tint: 'text-muted' }
          const Icon = row.icon
          const str = (k: string) => (typeof m[k] === 'string' ? String(m[k]).trim() : '')
          // `because` is the headline the owner read in the chat; the rest expands. Two levels,
          // because the tool caps `because` at 200 chars and puts the detail in `checked`.
          const because = str('because') || str('escalation') || str('speech') || e.summary
          const more = [['checked', str('checked')], ['asked for', str('change')],
                        ['escalation', str('escalation')]].filter(([, v]) => v && v !== because)
          return (
            <li key={e.id} className="rounded-md border border-line bg-sunken px-3 py-2">
              <div className="flex items-center gap-2">
                <Icon size={13} className={`shrink-0 ${row.tint}`} />
                <span className={`text-[13px] font-semibold ${row.tint}`}>{row.label}</span>
                {gate && <span className="rounded bg-hover px-1.5 py-px text-[10px] font-medium text-muted">{gate}</span>}
                <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-faint">{fmtLocal(e.created_at)}</span>
              </div>
              {/* Body copy reads at full contrast. The dim tier is for CHROME — labels, keys,
                  timestamps — and the deputy's reasoning is the thing you came here to read. */}
              {because && <div className="mt-1 text-[13px] leading-snug text-fg"><Markdown text={because} tone="dev" /></div>}
              {more.length > 0 && (
                <details className="mt-1">
                  <summary className="cursor-pointer select-none text-[10px] font-medium tracking-wide text-faint hover:text-fg">
                    Detail
                  </summary>
                  <dl className="mt-1 space-y-1 text-[11px]">
                    {more.map(([k, v]) => (
                      <div key={k} className="flex gap-2">
                        <dt className="w-20 shrink-0 text-faint">{k}</dt>
                        <dd className="min-w-0 flex-1 text-fg">{v}</dd>
                      </div>
                    ))}
                  </dl>
                </details>
              )}
            </li>
          )
        })}
      </ol>
    </Section>
  )
}

// What a git action DID, in a sentence (owner, 2026-08-01). The pane used to print the raw response
// — `{"ok":true,"merged":false,"up_to_date":true}` — which is a debugging artifact, not a result:
// the reader has to know that `merged:false` there means "nothing to merge", not "the merge failed".
//
// An unrecognised shape still shows its JSON rather than a reassuring guess: this line is the only
// feedback these buttons give, and inventing a success sentence for a response we don't model is
// exactly how a silent failure gets reported as a success.
function describeGit(action: string, r: unknown, trunk: string): string {
  const d = (r ?? {}) as Record<string, unknown>
  const conflicts = Array.isArray(d.conflicts) ? (d.conflicts as string[]) : []
  if (action === 'revert') {
    if (d.reverted) return `Reverted. ${trunk} is back at ${String(d.head ?? d.target ?? 'its pre-merge state')}.`
    return 'Nothing to revert — no recorded backup point for this item.'
  }
  if (action === 'resolve') {
    if (d.merged) return 'Conflicts resolved and the sync completed.'
    if (conflicts.length) return `Still conflicting in ${conflicts.join(', ')}.`
  }
  return JSON.stringify(r)
}

// Live git state + the owner's git actions. Activation comes from the drilldown payload, so the
// Merge button and the review gate's Approve can never disagree about the landing rule.
function GitPane({ it, contextId, actions, busy, onAct, onChanged }: {
  it: WorkItem; contextId: string; actions: DrilldownAction[]
  busy: string | null; onAct: (id: string) => void; onChanged: () => void
}) {
  const [localBusy, setLocalBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  // Live, not fetch-once: ahead/behind and `dirty` move underneath an open tab whenever a cycle commits.
  const gitQ = useLive<GitHealth>(K.itemGit(contextId, it.id), () => getWorkItemGit(it.id, contextId), 10000)
  const health = gitQ.data ?? null
  async function local(name: string, fn: () => Promise<unknown>) {
    setLocalBusy(name)
    setMsg(null)
    try {
      const r = await fn()
      setMsg(describeGit(name, r, health?.trunk ?? 'the anchor'))
      onChanged()
      gitQ.refresh()
    } catch (e) {
      setMsg(String(e))
    } finally {
      setLocalBusy(null)
    }
  }
  if (!it.git_branch && !it.git_worktree) {
    return <Empty>No git record yet — the branch + worktree are created when build starts.</Empty>
  }
  if (!health) return <Loading />
  const gitActions = actions.filter((a) => a.home === 'git')
  const pr = gitActions.find((a) => a.id === 'pr')
  const rows: [string, React.ReactNode][] = [
    ['branch', <span className="font-mono">{health.branch ?? it.git_branch}</span>],
    ['worktree', health.dir_exists ? <span className="font-mono">{health.worktree}</span> : <span className="text-faint">removed (terminal)</span>],
    ['anchor', <span className="font-mono">{health.trunk ?? '—'}</span>],
    [`vs ${health.trunk ?? 'anchor'}`, `ahead ${health.ahead ?? 0} · behind ${health.behind ?? 0}${health.behind ? ' — sync first' : ''}`],
    ['merged', health.merged ? `yes${it.git_merge_commit ? ` (${String(it.git_merge_commit).slice(0, 10)})` : ''}` : 'not yet'],
    ['dirty', health.dirty?.length ? health.dirty.join(', ') : 'clean'],
    // The repo's rule, echoed read-only. NAME THE ACTOR: the owner's approve merges in BOTH modes
    // (gates.py keys the PR branch off `actor != "owner"`). Read as a statement about the reader's
    // own click, "strict — approving opens a PR" contradicted the gate button one tab away.
    ['landing', health.review_mode === 'strict'
      ? "strict — the deputy's approval only opens a PR; yours merges"
      : 'fast — either approval merges it'],
  ]
  return (
    <div className="space-y-3">
      <dl className="space-y-1 text-[13px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <dt className="w-20 shrink-0 text-faint">{k}</dt>
            <dd className="min-w-0 flex-1 text-fg">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap items-center gap-2">
        {/* There is NO owner-facing freshness-sync control (owner, 2026-08-01 — button, api fn and route
            all deleted). Sync already happens at all three moments that matter, each better timed
            than a manual press: the build agent syncs itself on long builds
            (`mcp__dev__sync_from_main`); the merge act syncs at Approve and only forces a re-vet
            when the incoming changes OVERLAP files this item touched; and Resolve-with-Agent below
            runs the same sync for the conflict case. A manual press skipped the overlap test and
            paid a vet cycle unconditionally — it had no moment where it was the right call.

            The park path's exit (§2.3): a conflicting sync holds the item at review. The human
            decides WHETHER; the agent resolves in the worktree (D4) — nobody hand-edits markers. */}
        <GitBtn icon={GitMerge} label="Resolve with agent" busy={localBusy === 'resolve'}
                disabled={!!health.merged || !health.behind}
                onClick={() => local('resolve', () => resolveWorkItemGit(it.id, contextId))}
                title={health.merged ? 'Already merged — nothing to resolve'
                  : !health.behind ? 'Offered when the branch is behind, which is when a conflict is possible'
                  : 'Re-runs the sync leaving conflicts in the worktree, then an agent resolves them there. The daemon completes the merge and the item re-enters vet.'} />
        {/* Opens in its OWN browser tab — a diff wants the whole screen, and this window keeps the
            board and the item's chat where they are. A real path, so ⌘-click works too. */}
        {pr && (
          <GitBtn icon={ExternalLink} label={pr.label} busy={false} disabled={!pr.active}
                  href={build({ name: 'pr', repoId: contextId, itemId: it.id })} title={pr.reason} />
        )}
        {/* No Merge button here. It fired the SAME request as the review gate's Approve while
            skipping Approve's two locks (an agent still running; a non-empty must-resolve set) —
            a duplicate control that was also the way around the gate it duplicated. This tab shows
            git STATE and repairs git PROBLEMS; landing the work is the gate's act. */}
        {it.git_backup_ref && (
          <GitBtn icon={Undo2} label="Revert merge" busy={localBusy === 'revert'}
                  onClick={() => local('revert', () => revertWorkItemGit(it.id, contextId))}
                  title="Restore the trunk to its pre-merge state via the recorded backup ref (safe-only)" />
        )}
      </div>
      {msg && <div className="rounded-md bg-sunken px-2.5 py-1.5 text-[13px] leading-snug text-fg">{msg}</div>}
    </div>
  )
}

function GitBtn({ icon: Icon, label, onClick, href, busy, title, accent, disabled }: {
  icon: typeof GitMerge; label: string; onClick?: () => void; busy?: boolean; title?: string
  // A real link, opened in a new tab. `window.open` is a scripted popup — browsers and embedded
  // panes are entitled to refuse it, and one of ours does. An anchor is navigation: it always opens,
  // and it comes with ⌘-click, middle-click and "open in new window" for free.
  href?: string
  accent?: boolean
  disabled?: boolean
}) {
  const cls = `inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] transition disabled:opacity-40 ${
    accent ? 'bg-accent font-medium text-on-accent hover:opacity-90'
           : 'border border-line bg-surface text-muted hover:bg-hover hover:text-fg'
  }`
  const inner = <>{busy ? <Loader2 size={13} className="animate-spin" /> : <Icon size={13} />} {label}</>
  if (href && !disabled && !busy) {
    return <a href={href} target="_blank" rel="noopener" title={title} className={cls}>{inner}</a>
  }
  return (
    <button onClick={onClick} disabled={!!busy || !!disabled} title={title} className={cls}>{inner}</button>
  )
}

// The run telemetry line — model · context fill · per-phase token chips (3-type basis, same as the
// Activity log). Phases with no recorded spend stay hidden.
function RunMeta({ it }: { it: WorkItem }) {
  const phases = useMemo(
    () => Object.entries(it.phase_tokens ?? {}).filter(([, v]) => v > 0),
    [it.phase_tokens],
  )
  const bits = [
    it.model && fmtModel(it.model),
    it.ctx_pct != null && `ctx ${it.ctx_pct}%`,
    (it.total_tokens ?? 0) > 0 && `Σ ${fmtTokens(it.total_tokens ?? 0)} tok`,
  ].filter(Boolean)
  if (!bits.length && !phases.length) return null
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-faint">
      <span>{bits.join(' · ')}</span>
      {phases.map(([p, v]) => (
        <span key={p} title={`Tokens spent while in ${p}`} className="rounded bg-hover px-1.5 py-0.5 font-mono text-[10px]">
          {p} {fmtTokens(v)}
        </span>
      ))}
    </div>
  )
}

function Section({ icon: Icon, title, children }: { icon: typeof FileText; title: string; children: React.ReactNode }) {
  return (
    <section>
      <SectionHeader className="mb-1.5 flex items-center gap-1.5">
        <Icon size={12} /> {title}
      </SectionHeader>
      {children}
    </section>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="text-[13px] text-faint">{children}</div>
}

function Loading() {
  return (
    <div className="flex items-center gap-2 py-6 text-[13px] text-muted">
      <Loader2 size={14} className="animate-spin" /> Loading…
    </div>
  )
}

// What each run WAS, for the group header. A run that opens with a shell command instead of a phase
// skill is not a bug and not a mystery — it is one of these, and saying which is the whole job of
// this map. (`build` appears twice on purpose: cycle 1 invokes the skill, later cycles RESUME that
// same thread, so only the first carries a `skill` row.)
const RUN_KIND: Record<string, string> = {
  chat: 'your chat turn',
  resolve: 'conflict resolver',
  deputy: 'deputy judgment',
  compact: 'compaction',
  triage: 'triage', plan: 'plan', build: 'build cycle', vet: 'vet', review: 'review', close: 'close',
  investigate: 'investigate',
}

// The raw call-trail — tools / sub-agents / skills the item's runs invoked, grouped by run;
// completed items fall back to the execution SNAPSHOT clearance wrote at terminal. That file is
// NOT the retired folder-archive (deleted 2026-07-31) — it is the run trail rendered once and
// kept, because a cleared item's live rows are released while its history must still be readable.
function TracePane({ artifacts, runs, execution }: {
  artifacts: RunArtifact[]; runs: RunHeader[]; execution: string | null
}) {
  const byId = new Map(runs.map((r) => [r.id, r]))
  if (artifacts.length === 0) {
    if (execution) {
      return (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
            <ScrollText size={12} /> Execution snapshot
            <span className="font-mono text-[10px] normal-case text-faint">artifacts/execution.md</span>
          </div>
          <Markdown text={execution} variant="doc" tone="dev" />
        </div>
      )
    }
    return <Empty>No calls recorded yet — they’re captured while an agent works this item.</Empty>
  }
  const groups: { run: number | null; items: RunArtifact[] }[] = []
  for (const a of artifacts) {
    const g = groups[groups.length - 1]
    if (g && g.run === (a.run_id ?? null)) g.items.push(a)
    else groups.push({ run: a.run_id ?? null, items: [a] })
  }
  return (
    <div className="space-y-4">
      {groups.map((g, gi) => {
        const calls = pairTrace(g.items)
        const meta = g.run != null ? byId.get(g.run) : undefined
        const what = meta?.feature ? RUN_KIND[meta.feature] ?? meta.feature : null
        return (
          <div key={gi}>
            <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-faint">
              {g.run != null ? `Run #${g.run}` : 'Unattached'}
              {what && <span className="text-muted"> · {what}</span>}
              {' · '}{calls.length} call{calls.length === 1 ? '' : 's'}
            </div>
            {calls.length === 0
              ? <div className="pl-1 text-[11px] text-faint">No tool calls — this run only exchanged text.</div>
              : <TraceRows rows={calls} time={(a) => fmtLocal(a.created_at)} />}
          </div>
        )
      })}
    </div>
  )
}
