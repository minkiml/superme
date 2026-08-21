import { useEffect, useState } from 'react'
import { X, Loader2, FileText, Terminal, GitBranch, Ban, ShieldCheck, Plane, Gauge, RotateCcw } from 'lucide-react'
import Modal from '@/ui/Modal'
import { advanceWorkItem, getDevLog, getWorkItemDrilldown, abandonWorkItem, markWorkItemSeen, resumeWorkItem, rerunWorkItem, runWorkItem, authorizeWorkItem, type WorkItem, type DevEvent, type Drilldown } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { navigate, useRoute, type ItemTab, type ItemSub } from '@/lib/router'
import { toModelKey } from '@/lib/format'
import { StatusBadge, DEFAULT_RUN_MODEL, DEFAULT_RUN_EFFORT } from '../panels'
import { PHASE_LABEL, kindChipClass, researchKindLabel } from '../common'
import { useContainerWidth, railTight } from '@/lib/layout'
import { AuthorizationsPane } from './AuthorizationsPane'
import { DeputyLogPane } from './DeputyLogPane'
import { GitPane } from './GitPane'
import { NowPane } from './NowPane'
import { ProofPane } from './ProofPane'
import { ReportPane } from './ReportPane'
import { TraceTab } from './TracePane'
import { ActionButton, Empty, Loading, ProgressBar, RunMeta } from './bits'

// The work-item drilldown: what is needed from me, and what has this produced.
//
// TYPE SCALE — four steps only; anything wanting a size between two wants a different weight.
//
// If you compute `disabled` here, the rule belongs in `services/drilldown.py`.

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
export const EVENT_CAP = 1000

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
