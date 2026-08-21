import { useEffect, useMemo, useState } from 'react'
import {
  X, ArrowRight, Sparkles, Check, Loader2, FileText, ScrollText, History,
  Terminal, GitBranch, FlaskConical, Ban, GitMerge, Undo2, ShieldCheck,
  AlertTriangle, MessageSquare, CornerUpLeft, Plane, ExternalLink, Gauge, ListChecks,
  ChevronRight, CircleDot, RotateCcw, Play, Plus, Trash2, PenLine,
  Network, Inbox, ClipboardCheck, ClipboardList, KeyRound, Gavel,
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
import { useContainerWidth, railTight } from '@/lib/layout'

// The work-item drilldown: what is needed from me, and what has this produced.
//
// TYPE SCALE — four steps only; anything wanting a size between two wants a different weight.
//
// If you compute `disabled` here, the rule belongs in `services/drilldown.py`.

// The SUBJECT of each check, beside the mark carrying its verdict — mono slugs look alike in a
// list.
//
// An unmapped criterion falls back rather than blanking: the kernel emits an unknown slug when it
// has no evaluator.
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
  owner_rulings: Gavel,
}

// The progress bar's stops, HAND-COPIED from the backend profiles. Nothing enforces the mirror.
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
// THREE slots only: a gate decision outranks a recovery, which outranks a launch.
//
// The other two are not rendered at all, which is different from greying the one that is.
const PRIMARY_ORDER = ['approve', 'resume', 'run'] as const

const QUICK_SUBS: { id: ItemSub; label: string }[] = [
  { id: 'now', label: 'Now' },
  { id: 'deputy', label: 'Deputy' },
  // The id is an ADDRESS, so renaming it would dead-link every saved drilldown URL.
  { id: 'proof', label: 'Task' },
  // Appended only when the item has one, so it costs nothing until it exists.
  { id: 'auth', label: 'Authorization' },
]
// Runs is what the AGENTS did; Timeline is what HAPPENED TO the item. Stacked, the long one buried
// the short.
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
  // Passed down so the badge reads the SAME verdict as the card behind it, not the stored word.
  bucket?: string
}) {
  // Brisk while a run is in flight, slow at rest. The payload is ONE call covering every tab.
  const rate = it.running ? 2500 : 10000
  const dQ = useLive<Drilldown>(K.itemDrilldown(contextId, it.id),
                                () => getWorkItemDrilldown(it.id, contextId), rate)
  // The whole history, not a window: a cap drops the triage and plan decisions. The pane says so if
  // reached.
  const logQ = useLive(K.devLog(contextId, it.id, EVENT_CAP),
                       () => getDevLog(contextId, { itemId: it.id, limit: EVENT_CAP }), rate)
  const d = dQ.data ?? null
  const events: DevEvent[] = logQ.data?.events ?? []

  const [mutErr, setMutErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [abandoning, setAbandoning] = useState(false)
  const [abandonReason, setAbandonReason] = useState('')
  const [rerunning, setRerunning] = useState(false)   // the re-run confirm bar
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
  // Pending requests only, and only at the review gate, so a non-empty list IS "something is
  // waiting on you".
  const auths = d?.authorizations ?? []
  // The tab rail measures itself: the drilldown is as wide as the surface it sits in.
  const [railRef, railW] = useContainerWidth<HTMLDivElement>()
  const tightRail = railTight(railW, TABS.length, auths.length > 0 ? 130 : 0)
  // A tab that opens empty on every other item teaches the owner to ignore the row it sits in.
  const subs: ItemSub[] = tab === 'quick'
                          ? QUICK_SUBS.filter((s) => s.id !== 'auth' || auths.length > 0)
                                      .map((s) => s.id)
                        : tab === 'reports' ? (reportPhases as ItemSub[])
                        : tab === 'trace' ? TRACE_SUBS.map((s) => s.id)
                        : []
  // An address naming a sub wrong for its tab falls back to the tab's first, not to blank.
  const sub: ItemSub | null = routeSub && subs.includes(routeSub) ? routeSub : (subs[0] ?? null)
  const go = (t: ItemTab, s: ItemSub | null) =>
    navigate({ name: 'item', repoId: contextId, itemId: it.id, tab: t, sub: s })

  // Read receipt: opening a terminal item's drilldown stamps it seen.
  useEffect(() => {
    if (completed && !it.seen_at) markWorkItemSeen(it.id, contextId).then(onChanged).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [it.id, completed])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // ONE dispatcher. The server said which are live; this only says what they DO.
  async function act(id: string) {
    if (id === 'drop') { setAbandoning(true); return }
    // Re-run DELETES this item's work, and nothing else undoes it, so it asks first — same shape as
    // Drop's confirm.
    if (id === 'rerun') { setRerunning(true); return }
    if (id === 'chat') { onOpenChat?.(); return }
    // Every control here routes through one call; landing the work is the review gate's own act.
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
      // A gate decision closes this view; a launch keeps it open so the owner watches the run.
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

  // The drilldown stays OPEN afterwards, unlike Drop: a fresh run is starting in the same item.
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

  // A grant routes the item back into build to perform it; a deny waives the check, gap on record.
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
  // First MATCH by state, not first ACTIVE: a greyed Resume is still the button the owner needs.
  const byId = Object.fromEntries(barActions.map((a) => [a.id, a]))
  const primaryId = d?.at_gate ? 'approve' : String(it.status) === 'error' ? 'resume' : 'run'
  const primary = byId[primaryId] ?? byId[PRIMARY_ORDER.find((k) => byId[k]?.active) ?? 'approve']

  return (
    <Modal onClose={onClose} contain column fill maxW="max-w-3xl" z="z-40">
      {/* Header band — id · badges · title · model/ctx/token chips. */}
      <div className="shrink-0 border-b border-line px-4 py-3">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-[10px] text-faint">{it.id}</span>
              <StatusBadge it={it} running={running} bucket={bucket} />
              {/* ONE chip: kind and family are one fact read left to right, and splitting them
                  invites reading the family alone */}
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

      {/* The authorization FLAG rides here so a pending one is visible from any tab. A pointer,
          not a second renderer. */}
      {/* Narrow, labels off and the current tab keeps its word — no rail ever scrolls sideways. */}
      <div ref={railRef} className="flex shrink-0 flex-wrap items-center gap-1 border-b border-line px-4">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => go(id, null)} title={label} aria-label={label}
                  className={`flex shrink-0 items-center gap-1.5 border-b-2 py-2 text-[13px] transition ${
                    tightRail ? 'px-2' : 'px-3'
                  } ${tab === id ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg'}`}>
            <Icon size={14} /> {(!tightRail || tab === id) && label}
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
          // The re-run confirm NAMES what goes rather than asking "are you sure": an owner cannot
          // weigh a definition.
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
            {/* Not repeated here: an action bar should carry acts, not read-only facts. */}
            {running && (
              <span className="inline-flex items-center gap-1.5 text-[13px] text-accent-text">
                <Loader2 size={14} className="animate-spin" /> Agent working…
              </span>
            )}
            {/* THREE slots. A TERMINAL item gets none: a greyed button informs only while an act
                is possible. */}
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
            {/* Disposal is ONE button. A mis-capture leaves an `abandoned` record, which is cheap:
                `done_at` clears terminal items off the board. */}
          </>
        )}
      </div>
    </Modal>
  )
}

// ── the frame ───────────────────────────────────────────────────────────────────────────────────

// The progress indicator, deliberately NOT tabs: a button under a stage that already happened acts
// on the LIVE phase.
//
// Reading a past stage is what the Reports tab is for.
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
          // The last stage carries no connector, so it sizes to its label and the connectors absorb
          // the width.
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
  // Both come from the server. Greyed has to LOOK greyed, or it still reads as "do this".
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
  // EVERY block is a card: cards carry separation, and colour alone carries urgency.
  return (
    <div className="space-y-3">
      {/* About first: what this item IS precedes what is happening to it. */}
      {/* Rows are server-composed and rendered in order; this reads no label by name, so a relabel
          cannot blank them. */}
      {/* Collapsed: what the item IS is read once, then it stands between the owner and the ask. */}
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

      {/* Shown where the ruling was GIVEN, so the consequence lands beside the act. */}
      {d.decisions.length > 0 && (
        <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
          <SectionHeader className="flex items-center gap-1.5">
            <Gavel size={13} /> Rules your ruling set
          </SectionHeader>
          <ul className="mt-1.5 space-y-1.5">
            {d.decisions.map((dec) => (
              <li key={dec.id} className="flex gap-2 text-[13px]">
                <span className="shrink-0 font-mono text-[12px] text-muted">{dec.id}</span>
                <span className="min-w-0 flex-1 text-fg">{dec.title}</span>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[11px] text-faint">
            Standing in this project's decision ledger — later runs read it before asking again.
          </p>
        </section>
      )}

      {/* The live line is this card's title and the ask lives inside it; split apart, that
          sentence breaks in half. */}
      <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <CircleDot size={13} className={d.now.running ? 'animate-pulse text-accent' : 'text-faint'} />
          <SectionHeader>{PHASE_LABEL[d.now.phase] ?? d.now.phase}</SectionHeader>
          {d.now.cycle > 0 && <span className="text-[13px] text-muted">· cycle {d.now.cycle}</span>}
          {/* No telemetry here: it is the header's line, and the same numbers twice invite a hunt
              for the difference. */}
        </div>
        {/* What this phase concluded, in its report's words. Absent while it works: a placeholder
            reads as a conclusion. */}
        {/* Labelled, because the sentence is a quotation from another document and unlabelled it
            reads as this pane's own. */}
        {/* A running phase has no summary, so this holds the last completed one, labelled with
            whose it is. */}
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

      {/* Only while it can still land: after plan starts, a form offering to change it offers
          nothing. */}
      {d.now.phase === 'triage' && <FromYouCompose itemId={it.id} contextId={contextId} />}

      {/* What must resolve, and only that: Approve is greyed and the button alone cannot say why. */}
      {d.blocked_by.length > 0 && d.at_gate && !d.now.running && (
        <section className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2.5">
          <SectionHeader className="mb-1.5 flex items-center gap-1.5 text-danger">
            <ListChecks size={13} /> Must resolve before {d.gate_label}
          </SectionHeader>
          <ul>
            {/* Every row here stops the button, so none carries a severity badge — the same badge
                everywhere says nothing. */}
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

      {/* No facts chips: kind and deliverable are in the header, and triaged restated the check
          directly above. */}
    </div>
  )
}

// The attention card: WHY, WHAT and REFERENCE, every field server-composed. No leading icon — the
// tint already says it.
function AttentionCardView({ card, busy, onAct, contextId }: {
  card: NonNullable<Drilldown['attention']>; busy: string | null
  onAct: (id: string) => void; contextId: string
}) {
  const waiting = card.kind === 'awaiting_child'
  return (
    // Waiting is not needing: this card asks nothing, so it must not wear the you-are-the-blocker
    // frame.
    <div className={waiting
      ? 'rounded-lg border border-line bg-sunken px-3.5 py-3'
      : 'rounded-lg border border-warn/40 bg-warn/10 px-3.5 py-3'}>
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1 space-y-2">
          {/* The card's TITLE, so it uses the shared heading and only the colour differs. */}
          <SectionHeader className={waiting ? 'text-muted' : 'text-warn'}>
            {waiting ? 'Waiting on a sub-item' : 'Need your attention'}
          </SectionHeader>
          {/* Peers in a numbered list, so one size and one weight: the `n label` column already
              carries the ordering. */}
          <Row n="1" label="Why">
            {/* Markdown, because raw text prints the deputy's bold labels as asterisks. `report`,
                not `chat`: the panel's own voice. */}
            {/* The variant is calibrated for a scrolling report, so the first and last block give
                their margins back. */}
            <div className="space-y-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0">
              <Markdown text={card.why} variant="report" tone="dev" />
              {card.detail && <Markdown text={card.detail} variant="report" tone="dev" />}
            </div>
          </Row>
          <Row n="2" label="What">
            <p className="text-[13px] leading-snug text-fg">{card.do}</p>
            {/* One button per act: the card says what to do, the bar is where you do it. */}
            {card.click === 'chat' && (
              <button onClick={() => onAct(card.click)} disabled={busy === card.click}
                      className="mt-1.5 inline-flex items-center gap-1.5 rounded-md bg-warn px-2.5 py-1 text-[13px] font-semibold text-white transition hover:brightness-110 disabled:opacity-50">
                {busy === card.click ? <Loader2 size={13} className="animate-spin" />
                                     : <ActionIcon id={card.click} />}
                Open chat
              </button>
            )}
          </Row>
          {/* The sub-items themselves, because an id alone is a join key, not an answer. */}
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
              {/* No bullet glyph: the label column already separates these from the row above. */}
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

// A fixed width, so every row's content starts at the same x; ragged labels read as unrelated
// fragments.
function Row({ n, label, children }: { n: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      {/* The META step, since these are row LABELS, and wide enough for the longest on ONE line. */}
      <span className="mt-px w-[5.75rem] shrink-0 whitespace-nowrap font-mono text-[11px] tracking-wide text-warn">
        {n} {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

// ── Quick View › Proof ──────────────────────────────────────────────────────────────────────────

// One row per BUILT THING, each carrying its own validation and verification.
//
// The join is mechanical, on plan task ids, and assembled server-side.
type Verified = ProofRow['verified'][number]

/* A CARD, in the same vocabulary every other Quick View block uses, because this tab must read as
   the same surface.
   
   Flat ruled sections were tried and discarded: cards carry the separation, and colour is reserved
   for urgency. */

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

/* A PILL, not a floating word: the words differ in length, so a bare span left a ragged right
   edge. */
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
  // The join runs check to tasks, so render it that way and fold the fan-out back.
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
      {/* One contrast rule: the SENTENCE reads at full strength, its NAME stays quiet.
          
          Three sections, in reading order. */}
      {tasks.length > 0 && (
        <ProofSection title="Tasks"
                 meta={<span className="text-[11px] tabular-nums text-muted">{doneCount}/{tasks.length}</span>}>
          {/* A rail instead of a sentence: "how much is done" is answered before any word is read. */}
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
                    {/* The id is what a check's `covers` chips point at, so it has to be visible. */}
                    <span className="mr-1.5 font-mono text-[10px] text-faint">{r.task}</span>
                    {/* Quiet, not struck through: a column of struck-out text reads as cancelled
                        work. */}
                    <span className={r.done ? 'text-muted' : 'text-fg'}>{codeSpans(sentence(r.text))}</span>
                  </div>
                  {/* The specification, folded: real detail, but not the answer the tab is open
                      for. */}
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
                  {/* Build's own evidence, under the task it evidences, so it reads as support
                      rather than more tasks. */}
                  {/* Real bullets. These entries WRAP, so the gap between them must beat the gap
                      inside one. */}
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
          {/* Work that named no task: a whole-suite run is not per-task and never was. */}
          {/* One label for the GROUP: a bare tag on every row named nothing on its own. */}
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
      {/* THE EXAM, once: every check in plan order, each naming the tasks it defends. */}
      {/* The ACTIVITY, not a past-tense claim: "Verified" above an unrun check is a lie the layout
          tells. */}
      {checks.length > 0 && (
        <ProofSection title="Verification"
                 meta={<span className="text-[11px] tabular-nums text-muted">
                   {checks.filter((c) => c.v.ran && c.v.passed).length}/{checks.length}
                 </span>}>
          <ul className="space-y-3">
            {checks.map(({ v, covers }) => (
              <li key={v.check}>
                {/* Leads with what it PROVES, the plan's own sentence. Older plans fall back
                    rather than blanking. */}
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 text-[13px] leading-snug text-fg">
                    {codeSpans(sentence(v.proves || v.expect || v.check))}
                  </span>
                  {/* A check the loop has not reached is NOT a failure, and a cross would say the
                      exam failed. */}
                  <StatePill v={v} />
                </div>
                {/* Ids rather than repetition — naming the tasks is what lets the pane show each
                    check once. */}
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
                {/* Folded away: all evidence, none of it the answer to "did this hold". The
                    DIAGNOSIS stays open. */}
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
                      {/* Provenance only when it is the stronger claim: agent-attested is the norm
                          and needs no badge. */}
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
                {/* A rubric is judged criterion by criterion, so it shows that way: "2/3" would
                    hide which one missed. */}
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
                {/* The located cause first: "where it broke" is the line the reader wants, output
                    is the support. */}
                {!v.passed && !v.deferred && v.why && (
                  <p className="mt-0.5 text-[13px] leading-snug text-muted">
                    <span className="font-medium text-fg">{codeSpans(v.where)}</span>
                    {' — '}{codeSpans(v.why)}
                    {v.unknown && (
                      <span className="text-faint"> · undetermined: {codeSpans(v.unknown)}</span>
                    )}
                  </p>
                )}
                {/* Verbatim, because the failure IS the expected-versus-actual, and a tooltip says
                    nothing. */}
                {!v.passed && !v.deferred && v.result && (
                  <pre className="mt-0.5 max-h-28 overflow-auto whitespace-pre-wrap rounded bg-hover px-1.5 py-1 font-mono text-[11px] leading-relaxed text-muted">{v.result}</pre>
                )}
              </li>
            ))}
          </ul>
        </ProofSection>
      )}
      {/* The lenses, last, under the QUESTION each asks. One with nothing to report still shows
          what it probed. */}
      {lenses.length > 0 && (
        <ProofSection title="Also looked at">
          <ul className="space-y-3">
            {lenses.map((l) => (
              <li key={l.lens}>
                {/* The question is the heading and is NAMED as well as asked, at full contrast. */}
                <div className="text-[12.5px] font-medium leading-snug text-fg">
                  <span>{sentence(l.lens)}:</span>{' '}
                  {LENS_QUESTION[l.lens] ?? ''}
                </div>
                <ul className="mt-1 list-disc space-y-2 border-l border-line pl-5 text-[12px]
                               leading-snug text-muted marker:text-faint">
                  {l.probed.map((probe, i) => <li key={i}>{codeSpans(sentence(probe))}</li>)}
                </ul>
                {/* Findings stand off the probe list: a finding is a different kind of statement. */}
                {l.findings.length > 0 && (
                <div className="mt-3 space-y-2">
                {l.findings.map((f, i) => (
                  <p key={i} className="flex items-baseline gap-1.5 text-[12.5px] leading-snug">
                    {/* A label, not a chip: `bg-hover` belongs to code alone, and severity is
                        carried by colour and case. */}
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
      {/* Only the state that ASKS something gets a line: an absence footer spends a permanent row
          saying nothing happened. */}
      {auths.length > 0 && (
        <p className="text-[11px] text-warn">
          {auths.length} authorization{auths.length === 1 ? '' : 's'} pending your grant/deny
        </p>
      )}
    </div>
  )
}

// What each lens ASKS, in the owner's words — the slug is the tool's vocabulary. An unmapped lens
// falls back to it.
const LENS_QUESTION: Record<string, string> = {
  intent: 'Does this actually solve the problem the item was filed for?',
  safety: 'Can this hurt anything — unsafe evaluation, destructive paths, secrets in the open?',
  robustness: 'Which inputs were tried, and which are still unhandled?',
  performance: 'Is it fast enough, against a budget the plan named?',
}

// Sentence-case lines that ARE sentences but reach us lowercase — only when the string starts with
// a LETTER.
//
// A line opening with a backtick starts a code span, and capitalising into it would rewrite a
// command.
function sentence(text: string): string {
  return /^[a-z]/.test(text) ? text[0].toUpperCase() + text.slice(1) : text
}

// `code` is the only inline markup our templates use, and raw backticks read as quotes. 12px
// absolute, never `em`.
function codeSpans(text: string) {
  return text.split('`').map((part, i) => (i % 2
    // Tinted like every other code span, so the one token the owner can type keeps its colour
    // across tabs.
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

// One phase's report, rendered 1:1, with a link to the full contract behind it.
//
// The leading title heading is dropped: the header and sub-tab already say it.
const LEAD_H1 = /^\s*#\s+.*\n+/

// Rendered by the editor below, not twice: the markdown and the textarea sat an inch apart.
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
        {/* The contract is a DOC, so it opens as one: its own browser tab, served verbatim */}
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

// ── the owner's own section ──
//
// The ONE section of any report the owner writes, and the only place their words reach plan as
// instruction.
//
// COMPOSE lives on Now and only during TRIAGE.
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

// Add stays inert until every field it needs is filled: an empty slot reads to plan as an
// instruction with nothing in it.
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
          {/* One card, not two: they are the same act at two grains. */}
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
          {/* A count, not the list: the slots live with the report they belong to. */}
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

// Removal is the only act here; composing belongs beside the phase that reads it. `editable` is the
// triage window.
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
  // Lazy twice over: the call-trail is the heaviest feed and only one pane renders it.
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

// A sub rather than a banner, so it costs nothing on the items with no request. Every row is a
// decision still owed.
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

// The deputy's governance trail, in one place and OUT of the chat: the conversation lives there,
// this is the record of why.
const DEPUTY_ROW: Record<string, { icon: typeof ShieldCheck; label: string; tint: string }> = {
  'deputy.approve': { icon: Check, label: 'Approved', tint: 'text-success' },
  'deputy.escalate': { icon: AlertTriangle, label: 'Escalated to you', tint: 'text-danger' },
  'deputy.query': { icon: MessageSquare, label: 'Sent feedback to the agent', tint: 'text-accent-text' },
  'deputy.send_back': { icon: CornerUpLeft, label: 'Sent back', tint: 'text-warn' },
}
// An escalation is resolved once the item leaves the gate it was raised at, and nothing records
// that on the event.
//
// Unmarked, a log of past pages reads as a queue of things still owed.
function isSettled(gate: string | undefined, phase: string): boolean {
  const at = PHASES.indexOf(gate as Phase)
  const now = PHASES.indexOf(phase as Phase)
  return at >= 0 && now >= 0 && now > at
}

function DeputyLogPane({ events, phase }: { events: DevEvent[]; phase: string }) {
  const rows = events
    .filter((e) => String(e.kind).startsWith('deputy') && !e.kind.endsWith('.start') && !e.kind.endsWith('.end'))
    // Newest first, like every log surface here: the row the owner needs is the one just written.
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
  if (!rows.length) return <Empty>The deputy hasn’t acted on this item yet.</Empty>
  return (
    <Section icon={ShieldCheck} title="Deputy log">
      <ol className="space-y-2">
        {rows.map((e) => {
          const m = (e.meta ?? {}) as Record<string, unknown>
          let row = DEPUTY_ROW[e.kind] ?? { icon: ShieldCheck, label: e.kind, tint: 'text-muted' }
          const gate = (m.gate ?? m.phase ?? m.origin_gate) as string | undefined
          // An answered page goes quiet and says so: it is real history, but it stops competing.
          const settled = e.kind === 'deputy.escalate' && isSettled(gate, phase)
          if (settled) row = { ...row, icon: Check, label: 'Escalated to you · resolved',
                               tint: 'text-muted' }
          const Icon = row.icon
          const str = (k: string) => (typeof m[k] === 'string' ? String(m[k]).trim() : '')
          // `because` is the headline the owner read; the detail lives in `checked`.
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
              {/* Body copy reads at full contrast: the dim tier is for chrome, and this is what
                  you came to read. */}
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

// What a git action DID, in a sentence: the raw response is a debugging artifact, not a result.
//
// An unrecognised shape still shows its JSON — inventing a success sentence is how a silent failure
// gets reported as one.
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
  // A worktree with no branch is a scratch checkout, so no landing row below applies.
  if (!it.git_branch) {
    return (
      <Empty>
        There is no branch and nothing to land; the tree is removed when the item closes.
        <div className="mt-2 font-mono text-[11px] text-faint">{it.git_worktree}</div>
      </Empty>
    )
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
    // NAME THE ACTOR: the owner's approve merges in BOTH modes, so a mode-only sentence contradicts
    // the gate button.
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
        {/* No manual freshness sync: the build agent syncs itself, and the merge act only re-vets
            on overlap */}
        <GitBtn icon={GitMerge} label="Resolve with agent" busy={localBusy === 'resolve'}
                disabled={!!health.merged || !health.behind}
                onClick={() => local('resolve', () => resolveWorkItemGit(it.id, contextId))}
                title={health.merged ? 'Already merged — nothing to resolve'
                  : !health.behind ? 'Offered when the branch is behind, which is when a conflict is possible'
                  : 'Re-runs the sync leaving conflicts in the worktree, then an agent resolves them there. The daemon completes the merge and the item re-enters vet.'} />
        {/* Its own browser tab, because a diff wants the whole screen. A real path, so cmd-click
        {   works */}
        {pr && (
          <GitBtn icon={ExternalLink} label={pr.label} busy={false} disabled={!pr.active}
                  href={build({ name: 'pr', repoId: contextId, itemId: it.id })} title={pr.reason} />
        )}
        {/* No Merge button: this tab shows git state and repairs git problems; landing the work is
            the gate's act */}
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
  // An anchor, not `window.open`: a scripted popup can be refused, and navigation comes with
  // cmd-click for free.
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

// What each run WAS. `build` appears twice on purpose: cycle 1 invokes the skill, later cycles
// resume that thread.
const RUN_KIND: Record<string, string> = {
  chat: 'chat',
  resolve: 'conflict resolver',
  deputy: 'deputy judgment',
  compact: 'compaction',
  triage: 'triage', plan: 'plan', build: 'build cycle', vet: 'vet', review: 'review', close: 'close',
  investigate: 'investigate',
}

// The raw call-trail, grouped by run; a completed item falls back to the execution snapshot
// clearance wrote.
//
// A cleared item's live rows are released, but its history must still be readable.
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
        // A chat turn is named for the lane it interrupted, so it can be placed against the runs
        // around it.
        const kind = meta?.feature ? RUN_KIND[meta.feature] ?? meta.feature : null
        const what = kind && meta?.feature === 'chat' && meta.phase ? `${meta.phase}:${kind}` : kind
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
