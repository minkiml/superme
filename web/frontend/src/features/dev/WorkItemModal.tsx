import { useEffect, useMemo, useState } from 'react'
import {
  X, ArrowRight, Sparkles, Check, Loader2, FileText, ScrollText, History,
  Terminal, GitBranch, FlaskConical, Ban, GitMerge, Undo2, ShieldCheck,
  AlertTriangle, MessageSquare, CornerUpLeft, Plane, ExternalLink, Gauge, ListChecks,
  ChevronRight, CircleDot, RotateCcw, Play,
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
  type WorkItem, type WorkItemDetail, type DevEvent, type RunArtifact, type RunHeader, type GitHealth,
  type Drilldown, type DrilldownAction, type ProofRow,
} from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { build, navigate, useRoute, type ItemTab, type ItemSub } from '@/lib/router'
import { fmtModel, fmtTokens, fmtLocal, toModelKey } from '@/lib/format'
import { StatusBadge, DEFAULT_RUN_MODEL, DEFAULT_RUN_EFFORT } from './panels'
import { PHASE_LABEL } from './common'

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
const PIPELINES: Record<string, string[]> = {
  implementation: ['triage', 'plan', 'build', 'vet', 'review', 'close'],
  research: ['triage', 'plan', 'investigate', 'review', 'close'],
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
  const subs: ItemSub[] = tab === 'quick' ? QUICK_SUBS.map((s) => s.id)
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
    const run: Record<string, () => Promise<unknown>> = {
      approve: () => advanceWorkItem(it.id, contextId),
      merge: () => advanceWorkItem(it.id, contextId),
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
      if (['approve', 'merge'].includes(id)) onClose()
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
  const auths = d?.authorizations ?? []

  return (
    <Modal onClose={onClose} contain column fill maxW="max-w-3xl" z="z-40">
      {/* Header band — id · badges · title · model/ctx/token chips (kept from S7). */}
      <div className="shrink-0 border-b border-line px-4 py-3">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-[10px] text-faint">{it.id}</span>
              <StatusBadge it={it} running={running} bucket={bucket} />
              <span className="rounded-full bg-hover px-2 py-0.5 text-[10px] uppercase tracking-wide text-faint">
                {it.kind ?? 'implementation'}
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

      {/* Authorization requests — an owner decision that HOLDS the merge, so it leads above the tabs
          rather than waiting to be found under one (§2.1: the must-resolve set greys Approve). */}
      {auths.length > 0 && (
        <AuthorizationsBanner auths={auths} busy={authBusy} onDecide={decideAuth} />
      )}

      {/* Tabs */}
      <div className="flex shrink-0 gap-1 border-b border-line px-4">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => go(id, null)}
                  className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] transition ${
                    tab === id ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg'
                  }`}>
            <Icon size={14} /> {label}
          </button>
        ))}
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
          sub === 'deputy' ? <DeputyLogPane events={events} />
          : sub === 'proof' ? <ProofPane rows={d.proof} auths={auths} />
          : <NowPane d={d} it={it} busy={busy} onAct={act} />
        ) : tab === 'reports' ? (
          reportPhases.length === 0
            ? <Empty>No phase has written a report yet — each phase writes one as its closing act.</Empty>
            : <ReportPane itemId={it.id} contextId={contextId} phase={String(sub ?? reportPhases[0])} />
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
                that applies to this state, greyed with its own reason when it isn't workable. */}
            {primary && (
              <ActionButton a={primary} busy={busy === primary.id} primary
                            onClick={() => act(primary.id)} />
            )}
            <span className="ml-auto flex items-center gap-1">
              {barActions.filter((a) => a.id === 'drop' || a.id === 'rerun').map((a) => (
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
  const cls = primary
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

function NowPane({ d, it, busy, onAct }: {
  d: Drilldown; it: WorkItem; busy: string | null; onAct: (id: string) => void
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
              same three numbers twice on one screen invites the reader to look for the difference. */}
          {d.now.last && (
            <span className="min-w-0 flex-1 truncate text-[13px] text-muted">· {d.now.last}</span>
          )}
        </div>
        {d.attention && (
          <div className="mt-2.5">
            <AttentionCardView card={d.attention} busy={busy} onAct={onAct} />
          </div>
        )}
      </section>

      <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
        <SectionHeader className="mb-1.5 flex items-center gap-1.5">
          <Gauge size={13} /> Item at a glance
        </SectionHeader>
        {/* Rows come from the server as an ordered label→value map — this reads no key by name.
            A hardcoded ['goal','progress','next'] here is what turned a backend relabel into three
            blank rows and a 500 on the response model. */}
        <dl className="text-[13px]">
          {Object.entries(d.glance).map(([label, value]) => (
            <div key={label} className="flex gap-2 border-t border-line py-1.5 first:border-t-0">
              <dt className="w-20 shrink-0 text-muted">{label}</dt>
              <dd className="min-w-0 flex-1 text-fg">{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* Gate checks — NAMED ROWS with the reason inline (owner rule). A failing check used to be a
          coloured dot with its detail in a hover `title`, which made a fatal check and an advisory one
          look identical. `blocking` is the only one that greys Approve; everything else is a fact the
          owner may act over with their eyes open. */}
      {d.checks.length > 0 && (
        <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
          <SectionHeader className="mb-1.5 flex items-center gap-1.5">
            {/* Just "Mechanical checks". The gate is already named twice above (the progress bar and
                the attention card), and a heading that restates it pushes the actual noun out. */}
            <ListChecks size={13} /> Mechanical checks
            {!d.at_gate && (
              <span className="ml-1 text-[10px] font-normal normal-case tracking-normal text-muted">
                preview of {d.gate_label.toLowerCase()}
              </span>
            )}
            {d.at_gate && d.blocked_by.length > 0 && (
              <span className="ml-1 rounded bg-warn/15 px-1.5 py-px text-[10px] font-semibold tracking-wide text-warn">
                {d.blocked_by.length} must resolve
              </span>
            )}
          </SectionHeader>
          <ul>
            {/* Hairline rows, not cards. A FAILING row still has to be findable at a glance, so it
                keeps a coloured left edge and a tint — the severity that used to be carried by a full
                border now rides one edge, and a passing row carries no chrome at all.

                OFF-GATE the severity vocabulary is DROPPED, not just softened. Mid-build/mid-vet the
                server grades the gate AHEAD (gate_briefs walks forward), so a red "Blocks approve"
                row named a button that wasn't on screen — the primary slot holds Run/Resume there —
                and "N must resolve" read as an owner to-do while the loop was already resolving it.
                The rows still show: what review will ask is worth previewing. They just don't shout.

                ADVISORY rows get NO chrome either (owner, 2026-07-31). They were wearing the failure
                costume — a red-family `✗`, a tinted panel, a coloured left edge — with a small chip
                as the only thing saying "you don't have to act". The costume won: the row read as a
                chore blocking the button right below it. An advisory is a PRICE, not a task; it
                cannot stop anything, and there is nothing to do about it. Only a blocking failure —
                the one thing that actually greys Approve — is allowed to look urgent. */}
            {d.checks.map((c) => {
              const stops = !c.ok && c.blocking && d.at_gate
              const Icon = CHECK_ICON[c.criterion] ?? ListChecks
              return (
              <li key={c.criterion}
                  className={`border-t border-line py-1.5 text-[13px] first:border-t-0 ${
                    stops ? 'border-l-2 border-l-danger bg-danger/5 pl-2' : ''
                  }`}>
                <div className="flex items-baseline gap-2">
                  {/* `–` for an advisory, not a dot: the dash is the same glyph the evidence
                      ledger uses for "recorded, no pass/fail verdict", and a mid-line dot read as
                      a bullet — punctuation, not a mark. Fixed width so the names line up. */}
                  <span className={`w-3 shrink-0 text-center ${
                    c.ok ? 'text-success' : stops ? 'text-danger' : 'text-faint'}`}>
                    {c.ok ? '✓' : stops ? '✗' : '–'}
                  </span>
                  <Icon size={12} className="shrink-0 self-center text-faint" />
                  {/* The criterion is the LABEL, the detail is the fact — so the detail is `fg`
                      and the name is muted. It read the other way round: a loud slug over a dim
                      sentence, which is the one line here anyone actually needs. */}
                  <span className="font-mono text-[11px] font-medium text-muted">{c.criterion}</span>
                  {/* Three states, three colours, and the third is deliberately NOT grey: red
                      stops the gate, blue is a fact you may act over, grey is "not evaluated yet".
                      An advisory in the same grey as an unevaluated row said nothing about which
                      one had actually been measured. Blue reads informational without reading
                      urgent — the one thing an advisory must never do. */}
                  {!c.ok && (
                    <span className={`rounded px-1.5 py-px text-[10px] font-semibold tracking-wide ${
                      stops ? 'bg-danger/15 text-danger'
                            : !d.at_gate ? 'bg-fg/8 text-muted'
                            : 'bg-accent/15 text-accent-text'
                    }`}>
                      {stops ? 'Blocks approve' : !d.at_gate ? 'Not yet' : 'For your information'}
                    </span>
                  )}
                </div>
                {/* Indented past the mark AND the icon so the sentence hangs under the name. */}
                <p className="mt-0.5 pl-10 leading-snug text-fg">{c.detail}</p>
              </li>
            )})}
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
function AttentionCardView({ card, busy, onAct }: {
  card: NonNullable<Drilldown['attention']>; busy: string | null; onAct: (id: string) => void
}) {
  return (
    <div className="rounded-lg border border-warn/40 bg-warn/10 px-3.5 py-3">
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1 space-y-2">
          {/* The card's TITLE, so it uses the same SectionHeader as "Item at a glance" and
              "Mechanical checks" — only the colour differs. It was a bespoke 11px bold span, which
              made the loudest card on the pane carry the quietest heading. */}
          <SectionHeader className="text-warn">Need your attention</SectionHeader>
          {/* Why / What / Reference are PEERS — a numbered list with parallel labels — so they are
              one size and one weight (owner, 2026-08-01). They were 14px/medium, 12.5px/normal and
              12px/normal, which read as a heading followed by two footnotes and made the three rows
              look mis-set rather than enumerated. The `n label` column already carries the ordering;
              the type doesn't need to re-state it. */}
          <Row n="1" label="Why">
            <p className="text-[13px] leading-snug text-fg">{card.why}</p>
            {card.detail && (
              <div className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed text-muted">{card.detail}</div>
            )}
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
function ProofPane({ rows, auths }: { rows: ProofRow[]; auths: Drilldown['authorizations'] }) {
  const real = rows.filter((r) => r.built.length || r.validated.length || r.verified.length || r.task)
  if (!real.length) return <Empty>No tasks yet — the plan writes them, with the checks that prove them.</Empty>
  return (
    <div className="space-y-2">
      {/* One BLOCK per built thing, not a three-column table. The drilldown lives in the dashboard
          column beside the chat rail, and at that width three columns of prose collapse to unreadable
          slivers — the connection (this feature → validated this way → verified this way) is what
          §4.2 asks for, and stacking preserves it where a scrolling grid destroys it. */}
      {real.map((r) => (
        <div key={r.task || 'item-wide'} className="rounded-md border border-line bg-sunken px-3 py-2">
          <div className="flex items-baseline gap-2">
            {r.task ? (
              <span className={`shrink-0 rounded px-1.5 py-px font-mono text-[10px] ${
                r.done ? 'bg-success/15 text-success' : 'bg-hover text-faint'
              }`}>{r.task}</span>
            ) : (
              <span className="shrink-0 rounded bg-hover px-1.5 py-px text-[10px] tracking-wide text-faint">
                Item-wide
              </span>
            )}
            {r.task && <span className="min-w-0 text-[13px] font-medium leading-snug text-fg">{codeSpans(r.text)}</span>}
          </div>
          {/* The `built` prose is NOT rendered here (owner, 2026-08-01). It is the build report's
              §Built bullets verbatim — a paragraph per task, already readable one tab over under
              Reports → Build. Proof answers "was it proven", and burying that answer under three
              lines of narration is what made the pane look like a wall. The join still USES built:
              a task with build output but no evidence yet keeps its row. */}
          {/* The label is a HEADING over its list, not a left column (owner, 2026-08-02). At this
              width a label column left ~60% for the content, and a check — id, state, and a full
              sentence of expectation — wrapped into a ragged block with no line the eye can start
              on. Full width, one check per two lines: NAME + STATE, then what it proves under it. */}
          {(r.validated.length > 0 || r.verified.length > 0) && (
            <div className="mt-2 space-y-2 border-t border-line pt-2">
              {r.validated.length > 0 && (
                <div>
                  {/* The ACTIVITY, not a past-tense claim: these rows exist from the plan gate on,
                      and "Verified" above a check nobody has run yet is a lie the layout tells. */}
                  <div className="text-[11px] uppercase tracking-wide text-faint">Validation</div>
                  <ul className="mt-1 space-y-0.5 text-[13px] leading-snug text-muted">
                    {r.validated.map((v, i) => <li key={i}>{codeSpans(v)}</li>)}
                  </ul>
                </div>
              )}
              {r.verified.length > 0 && (
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-faint">Verification</div>
                  <ul className="mt-1 space-y-2">
                    {r.verified.map((v) => (
                      <li key={v.check}>
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="flex min-w-0 items-baseline gap-1.5">
                            <span className="min-w-0 truncate font-mono text-[11px] text-fg">{v.check}</span>
                            {/* Provenance, only when it is the stronger claim: the kernel ran this
                                one itself, so no model sits between the exit code and the verdict.
                                Agent-attested is the norm and needs no badge — labelling both would
                                spend a row of ink to say nothing. */}
                            {v.by === 'machine' && (
                              <span className="shrink-0 rounded bg-hover px-1 text-[10px] tracking-wide text-faint"
                                    title="Run by the kernel in the sandbox — no agent between the exit code and this verdict">
                                machine-run
                              </span>
                            )}
                          </span>
                          {/* A check the loop hasn't reached is NOT a failure — it reads "not run
                              yet" in the quiet colour. Rendering it as ✗ would tell the owner
                              approving a plan that their exam had already failed. */}
                          <span className={`shrink-0 text-[11px] ${
                            !v.ran ? 'text-faint'
                            : v.deferred ? 'text-muted' : v.passed ? 'text-success' : 'text-danger'}`}>
                            {!v.ran ? 'not run yet'
                                    : v.deferred ? '◌ deferred' : historyGlyph(v.history, v.passed)}
                          </span>
                        </div>
                        {/* What this check proves, in the plan's own words — the whole reason the
                            row is worth reading before anything has run. */}
                        {v.expect && (
                          <p className="text-[13px] leading-snug text-muted">{codeSpans(v.expect)}</p>
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
                          <pre className="mt-0.5 max-h-28 overflow-auto whitespace-pre-wrap rounded bg-canvas px-1.5 py-1 font-mono text-[11px] leading-relaxed text-muted">{v.result}</pre>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      ))}
      <p className="text-[11px] text-faint">
        authorizations: {auths.length ? `${auths.length} pending your grant/deny` : 'none pending'}
      </p>
    </div>
  )
}

// Plan lines are markdown-flavoured prose, and `code` is the only inline markup our templates use —
// paths, flags, function names. Rendering the raw backticks made every one of them read as a quote.
// Code is 12px absolute here, as in the reports: never `em`, so one token is one size app-wide.
function codeSpans(text: string) {
  return text.split('`').map((part, i) => (i % 2
    ? <code key={i} className="rounded bg-hover px-1 font-mono text-[12px] text-fg">{part}</code>
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

function ReportPane({ itemId, contextId, phase }: {
  itemId: string; contextId: string; phase: string
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
      <Markdown text={r.text.replace(LEAD_H1, '')} variant="report" tone="dev" />
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

function AuthorizationsBanner({ auths, busy, onDecide }: {
  auths: AuthRow[]
  busy: string | null
  onDecide: (id: string, decision: 'granted' | 'denied') => void
}) {
  return (
    <div className="mx-4 mt-3 rounded-lg border border-accent/40 bg-accent/10 px-3.5 py-3">
      <div className="flex items-start gap-2.5">
        <ShieldCheck size={16} className="mt-0.5 shrink-0 text-accent-text" />
        <div className="min-w-0 flex-1">
          <SectionHeader className="text-accent-text">
            Authorization {auths.length > 1 ? `requests (${auths.length})` : 'request'} — your call
          </SectionHeader>
          <p className="mt-1 text-[11px] leading-snug text-muted">
            The build deferred {auths.length > 1 ? 'these contract changes' : 'this contract change'} rather than self-authorizing.
            Grant to have it performed + re-vetted; deny to close with the gap on record.
          </p>
          <div className="mt-2.5 space-y-2.5">
            {auths.map((a) => (
              <div key={a.id} className="rounded-md border border-line bg-sunken px-2.5 py-2">
                <p className="text-[13px] font-medium leading-snug text-fg">{a.what}</p>
                <p className="mt-0.5 text-[11px] leading-snug text-muted">{a.why}</p>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-faint">
                  {a.doc && <span>doc: <code className="text-muted">{a.doc}</code></span>}
                  <span>scope: <code className="text-muted">{a.scope}</code></span>
                  <span className={a.delegable ? 'text-muted' : 'font-medium text-warn'}>
                    {a.delegable ? 'sync-to-reality' : 'owner-reserved — escalated to you'}
                  </span>
                </div>
                <div className="mt-2 flex items-center gap-2">
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
        </div>
      </div>
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
function DeputyLogPane({ events }: { events: DevEvent[] }) {
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
          const row = DEPUTY_ROW[e.kind] ?? { icon: ShieldCheck, label: e.kind, tint: 'text-muted' }
          const Icon = row.icon
          const gate = (m.gate ?? m.phase ?? m.origin_gate) as string | undefined
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
              {because && <div className="mt-1 text-[13px] leading-snug text-muted"><Markdown text={because} tone="dev" /></div>}
              {more.length > 0 && (
                <details className="mt-1">
                  <summary className="cursor-pointer select-none text-[10px] font-medium tracking-wide text-faint hover:text-fg">
                    Detail
                  </summary>
                  <dl className="mt-1 space-y-1 text-[11px]">
                    {more.map(([k, v]) => (
                      <div key={k} className="flex gap-2">
                        <dt className="w-20 shrink-0 text-faint">{k}</dt>
                        <dd className="min-w-0 flex-1 text-muted">{v}</dd>
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
        {gitActions.filter((a) => a.id === 'merge').map((a) => (
          <GitBtn key={a.id} icon={GitMerge} label={a.label} accent busy={busy === a.id}
                  disabled={!a.active} onClick={() => onAct(a.id)} title={a.reason} />
        ))}
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
