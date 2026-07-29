import { useEffect, useMemo, useState } from 'react'
import {
  X, ArrowRight, Sparkles, Trash2, Check, Loader2, FileText, ListChecks, ScrollText, History,
  Terminal, Archive, Scale, GitBranch, Milestone, FlaskConical, BookOpenText, Ban, RefreshCw,
  GitMerge, Undo2, ShieldCheck, AlertTriangle, MessageSquare, CornerUpLeft, Lock, Plane,
  ExternalLink,
} from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import SectionHeader from '@/ui/SectionHeader'
import { TraceRows } from './ExecutionTrace'
import { pairTrace } from '@/lib/trace'
import {
  getWorkItemDetail, getWorkItemArtifacts, advanceWorkItem, completeWorkItem,
  getDevLog, getWorkItemGateBrief, getWorkItemGit, syncWorkItemGit, resolveWorkItemGit,
  revertWorkItemGit, abandonWorkItem, markWorkItemSeen, vetWorkItem, continueWorkItem, authorizeWorkItem,
  type WorkItem, type WorkItemDetail, type DevEvent, type RunArtifact, type GateBrief,
  type GitHealth,
} from '@/lib/api'
import { getRepos } from '@/lib/api/system'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { build, navigate, useRoute, type Phase } from '@/lib/router'
import { fmtModel, fmtTokens, fmtLocal, toModelKey } from '@/lib/format'
import { StatusBadge, isPlannable, DEFAULT_RUN_MODEL, DEFAULT_RUN_EFFORT } from './panels'
import { PHASE_LABEL, GATED_PHASES } from './common'

// The work-item drilldown (S7 v1) — "tell me about this one". Header (overview + meta + progress)
// → clickable PHASE STEPPER (current stage pulsing) → P0-curated per-phase sub-tabs, leading with
// the newest GATE BRIEF (the kernel-assembled decision surface; raw trace one click deeper) →
// the action footer (gates are decided here). Contained to the dashboard column so the bound chat
// stays interactive beside it.

// Per-kind pipeline (mirrors the backend KIND_PROFILES).
const PIPELINES: Record<string, string[]> = {
  implementation: ['triage', 'plan', 'build', 'vet', 'review', 'close'],
  research: ['triage', 'plan', 'investigate', 'review', 'close'],
}

// P0-curated sub-tabs per phase — the most useful reads for that stage, nothing else. `trace`
// (the raw call-trail) is appended to every phase: one click deeper, never leading.
type SubTab = 'gate' | 'item' | 'plan' | 'investigation' | 'checkpoints' | 'git' | 'trace' | 'deputy'
const PHASE_TABS: Record<string, SubTab[]> = {
  triage: ['gate', 'item'],
  plan: ['gate', 'plan'],
  build: ['plan', 'checkpoints', 'git'],
  vet: ['plan', 'checkpoints', 'git'],
  review: ['gate', 'git', 'checkpoints'],
  investigate: ['plan', 'investigation', 'checkpoints'],
  close: ['gate'],
}
const SUB_META: Record<SubTab, { label: string; icon: typeof FileText }> = {
  gate: { label: 'Gate brief', icon: Scale },
  item: { label: 'Item', icon: ScrollText },
  plan: { label: 'Plan', icon: FileText },
  investigation: { label: 'Investigation', icon: BookOpenText },
  checkpoints: { label: 'Checkpoints', icon: Milestone },
  git: { label: 'Git', icon: GitBranch },
  trace: { label: 'Trace', icon: Terminal },
  deputy: { label: 'Deputy', icon: ShieldCheck },
}

// Why the item is parked for the owner when it isn't a plain gate wait (a deputy escalation, a
// build⟷vet halt, a blocked run). Lands on the gate-brief payload as `paged`; the modal leads with
// it so "what's going on / what do I decide" is answered on arrival. (Typed here until gen:api.)
type PagedData = { source: string; gate: string | null; headline: string; detail: string; next: string | null }

export default function WorkItemModal({
  it, contextId, onClose, onPlan, onDelete, onChanged, bucket,
}: {
  it: WorkItem
  contextId: string
  onClose: () => void
  onPlan: (it: WorkItem, model?: string, effort?: string) => void // fire a background plan run (queued items)
  onDelete: (it: WorkItem) => void // hard-delete (caller confirms)
  onChanged: () => void // reload the board after an advance
  // The item's attention tier, passed down so the drilldown's status badge reads the SAME verdict
  // as the card behind it. Without it the header would fall back to the stored word and re-open
  // the D2 split — a card saying NEEDS YOU over a popup saying IN PROGRESS.
  bucket?: string
}) {
  // The drilldown's four feeds. Each is its own cache key, so the ones another surface already
  // keeps warm (the dev log is also read by the chat rail and the timeline) cost nothing here.
  // Cadence: 2.5s while a run is in flight, 10s when the item is at rest — the modal used to poll
  // all four at 2.5s while running and then go completely silent, so an item that changed underneath
  // an open drilldown (a deputy verdict, an autopilot advance) sat visibly wrong until it was closed
  // and reopened.
  const rate = it.running ? 2500 : 10000
  const detailQ = useLive<WorkItemDetail>(K.itemDetail(contextId, it.id), () => getWorkItemDetail(it.id, contextId), rate)
  const logQ = useLive(K.devLog(contextId, it.id, 50), () => getDevLog(contextId, { itemId: it.id, limit: 50 }), rate)
  const artifactsQ = useLive(K.itemArtifacts(contextId, it.id), () => getWorkItemArtifacts(it.id, contextId), rate)
  const briefQ = useLive<GateBrief>(K.itemGateBrief(contextId, it.id), () => getWorkItemGateBrief(it.id, contextId), rate)
  // The repo's landing rule, for the review gate's button (D1). Read off the shared `sys:repos`
  // key the shell already keeps warm rather than the Git pane's `/git` feed — that one is only
  // subscribed while its tab is open, and the gate needs this whichever tab you are on. A repo
  // setting changes about once a quarter, so 60s is generous.
  const reposQ = useLive(K.repos, getRepos, 60_000)
  const reviewMode = reposQ.data?.find((r) => r.id === contextId)?.review_mode ?? null

  const detail = detailQ.data ?? null
  const events: DevEvent[] = logQ.data?.events ?? []
  const artifacts: RunArtifact[] = artifactsQ.data?.artifacts ?? []
  const brief = briefQ.data ?? null
  // Errors only matter while there is nothing to show — last-good data survives a blip, and the
  // connection banner is what tells the owner when the whole daemon is unreachable.
  const briefErr = brief ? null : briefQ.error ? String(briefQ.error) : null
  const [mutErr, setMutErr] = useState<string | null>(null)
  const err = mutErr ?? (detail ? null : detailQ.error ? String(detailQ.error) : null)
  const [advancing, setAdvancing] = useState(false)
  const [abandoning, setAbandoning] = useState(false) // inline abandon confirm row
  const [abandonReason, setAbandonReason] = useState('')
  const [model] = useState(toModelKey(it.model) || DEFAULT_RUN_MODEL)
  const [effort] = useState(it.effort ?? DEFAULT_RUN_EFFORT)

  const pipeline = PIPELINES[it.kind ?? 'implementation'] ?? PIPELINES.implementation
  const phase = it.phase ?? 'triage'
  const idx = pipeline.indexOf(phase)
  const completed = !!it.done_at || it.status === 'done'
  const running = !!it.running
  const queued = isPlannable(it)
  const nextPhase = idx >= 0 && idx + 1 < pipeline.length ? pipeline[idx + 1] : null
  const atClose = idx === pipeline.length - 1
  const preBuild = ['triage', 'plan'].includes(phase)
  // Does this phase end at a briefed human gate? Only then is advancing an "Approve".
  const atGate = GATED_PHASES.has(phase)

  // The review gate's Approve is the OWNER's control, and the owner's approve always MERGES — in
  // both modes. `strict` keys the PR-opening branch off `actor !== 'owner'` (gates.py): it buys a
  // second pair of eyes on the deputy's work, and the owner already is that. This was briefly
  // conditioned on `review_mode` on 2026-07-29, reasoning from the Git tab's `landing` line; a
  // live approve then merged the branch under a button reading "Approve & open PR". The mode
  // changes who ELSE may approve, not what YOUR approval does — so the label must not move.
  const reviewApprove = {
    label: 'Approve & merge',
    title: 'Approve & merge — the review decision IS the merge: lands the branch on the anchor '
      + '(applies the staged knowledge delta, backup ref first), then advances to close. On '
      + 'conflicts it holds here so you can sync + resolve, then approve again.'
      + (reviewMode === 'strict'
        ? ' This repo is `strict`, which means the DEPUTY cannot land it — its approval only opens '
          + 'the PR for you. Yours merges either way.'
        : ''),
  }

  // Stepper selection and sub-tab are ADDRESSES (`/repo/:id/item/:itemId/:phase/:sub`), not local
  // state. An ABSENT phase segment means "whatever phase this item is at now" — so the bare item
  // link follows the work as it advances, exactly as the old `useEffect(() => setPhaseView(phase))`
  // did, while a named phase pins the view for someone reading back through a finished stage.
  const route = useRoute()
  const routePhase = route.name === 'item' ? route.phase : null
  const routeSub = route.name === 'item' ? route.sub : null
  const phaseView: string = routePhase && pipeline.includes(routePhase) ? routePhase : phase
  const viewingLive = phaseView === phase
  // Which sub-tabs a phase offers lives HERE, so the router only has to know the vocabulary. An
  // address naming a sub that is real but wrong for its phase (a hand-edited URL, a link from
  // before a phase's tabs changed) falls back to the phase's first tab rather than rendering blank.
  const goItem = (p: string, s: SubTab | null) =>
    navigate({
      name: 'item', repoId: contextId, itemId: it.id,
      // A sub cannot be addressed without its phase, so choosing one forces the phase segment even
      // when it is the live phase.
      phase: (s || p !== phase ? p : null) as Phase | null,
      sub: s,
    })
  // The brief carries `paged` when the item is parked for a nameable reason (not a plain gate wait).
  const briefPaged = (brief as (GateBrief & { paged?: PagedData | null }) | null)?.paged ?? null
  // BV-A2: deferred contract changes awaiting the owner's grant/deny at the review gate.
  const auths = (brief as (GateBrief & { authorizations?: AuthRow[] }) | null)?.authorizations ?? []
  const [authBusy, setAuthBusy] = useState<string | null>(null)
  // ONE list, used to render the tabs AND to validate the address. They were briefly two — the
  // curated per-phase set for validation, a wider set (plus Deputy and Trace) for rendering — and
  // the gap was immediately visible: `/…/review/deputy` was a URL you could reach by clicking, but
  // `sub` rejected it and snapped the body back to the gate brief while the address kept claiming
  // Deputy. Same defect class as two writers for one row, one level down.
  const phaseTabs = PHASE_TABS[phaseView] ?? ['gate']
  const subTabs: SubTab[] = [
    ...phaseTabs,
    // The Deputy log appears only once the deputy has acted on this item — its governance moves
    // (approve / send-back / escalate) + rationale live HERE, out of the chat (Q1-E).
    ...(events.some((e) => String(e.kind).startsWith('deputy')) ? (['deputy'] as SubTab[]) : []),
    'trace',
  ]
  const sub: SubTab = routeSub && subTabs.includes(routeSub) ? routeSub : subTabs[0]

  // Read receipt (S7 attention): opening a terminal item's drilldown stamps it seen — the blue
  // `unread` row clears on the next attention read.
  useEffect(() => {
    if (completed && !it.seen_at) {
      markWorkItemSeen(it.id, contextId).then(onChanged).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [it.id, completed])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  function plan() {
    onPlan(it, model, effort)
    onClose()
  }
  // F3: model/effort are locked at push — no per-item reconfiguration here. `model`/`effort` state
  // just mirrors the item's locked config for the Plan-it run and the read-only chip.
  async function advance() {
    setAdvancing(true)
    try {
      await advanceWorkItem(it.id, contextId)
      onChanged()
      onClose()
    } catch (e) {
      setMutErr(`Couldn't advance — ${e}`)
      setAdvancing(false)
    }
  }
  // "Run vet" — launch the build⟷vet loop (build-vet-loop §5). One click; from here the daemon
  // driver self-drives (vet → build cycles → re-vet) until the review gate or a breaker pages.
  async function vet() {
    setAdvancing(true)
    try {
      await vetWorkItem(it.id, contextId)
      onChanged()
      onClose()
    } catch (e) {
      setMutErr(`Couldn't start the vet loop — ${e}`)
      setAdvancing(false)
    }
  }
  // "Continue" — the owner's resume of a build parked at a human gate (BV-A1). RE-enters the
  // build⟷vet loop: the build thread finalizes (records any wall as an assumption), then the
  // gap rides to review. This is what makes the paused-build banner's promise true — a chat reply
  // alone runs a bare turn that does not advance the loop.
  async function resumeBuild() {
    setAdvancing(true)
    try {
      await continueWorkItem(it.id, contextId)
      onChanged()
      onClose()
    } catch (e) {
      setMutErr(`Couldn't continue — ${e}`)
      setAdvancing(false)
    }
  }
  // BV-A2: the owner's grant/deny on a deferred contract change. A grant routes the item back into
  // build to perform it (grant-as-send_back → re-vet → review); a deny waives the deferred check so
  // it can close with the gap on record. Either way the item leaves this review rest, so close +
  // refresh. The owner grants unconditionally — the delegated floor binds only the deputy.
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
  async function complete() {
    setAdvancing(true)
    try {
      await completeWorkItem(it.id, contextId)
      onChanged()
      onClose()
    } catch (e) {
      setMutErr(`Couldn't complete — ${e}`)
      setAdvancing(false)
    }
  }
  // Abandon (D8, human-only): terminal-without-completing — worktree removed, branch kept, zero
  // knowledge writes. Two-step inline confirm with an optional reason (it lands in the closeout).
  async function abandon() {
    setAdvancing(true)
    try {
      await abandonWorkItem(it.id, abandonReason, contextId)
      onChanged()
      onClose()
    } catch (e) {
      setMutErr(`Couldn't abandon — ${e}`)
      setAdvancing(false)
      setAbandoning(false)
    }
  }

  const tasksPct = it.tasks?.total ? Math.round((it.tasks.done / it.tasks.total) * 100) : null

  return (
    <Modal onClose={onClose} contain column fill maxW="max-w-3xl" z="z-40">
      {/* Header — overview + meta + task progress */}
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
          <button
            onClick={onClose}
            title="Close"
            aria-label="Close"
            className="shrink-0 rounded p-1 text-muted hover:bg-hover hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>
        {tasksPct != null && (
          <div className="mt-2 flex items-center gap-2" title={`Tasks: ${it.tasks!.done}/${it.tasks!.total} done`}>
            <div className="h-1 flex-1 overflow-hidden rounded-full bg-sunken">
              <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${tasksPct}%` }} />
            </div>
            <span className="text-[10px] tabular-nums text-faint">{it.tasks!.done}/{it.tasks!.total}</span>
          </div>
        )}
        {/* Phase stepper — each stage clickable; the live stage pulses. */}
        <div className="mt-2.5 flex items-center gap-0.5">
          {pipeline.map((p, i) => {
            const state = completed || i < idx ? 'done' : i === idx ? 'current' : 'future'
            const on = phaseView === p
            return (
              <button
                key={p}
                onClick={() => goItem(p, null)}
                title={`${PHASE_LABEL[p] ?? p}${state === 'current' ? ' — current stage' : ''}`}
                className={`flex items-center gap-1 rounded-md px-1.5 py-1 text-[10.5px] font-medium uppercase tracking-wide transition ${
                  on ? 'bg-hover text-fg' : 'text-faint hover:text-fg'
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    state === 'done' ? 'bg-success' : state === 'current' ? 'animate-pulse bg-accent' : 'bg-line'
                  }`}
                />
                {PHASE_LABEL[p] ?? p}
                {i < pipeline.length - 1 && <span className="ml-0.5 text-line">›</span>}
              </button>
            )
          })}
        </div>
      </div>

      {/* Paused banner (fixes "zero idea what to do"): when the item rests awaiting_human for a
          REASON — a deputy escalation, a build⟷vet halt, a blocked run — lead with why + what to
          decide, ABOVE the sub-tabs so it's seen whatever tab you land on. Live phase only. */}
      {viewingLive && briefPaged && (
        <PagedBanner
          p={briefPaged}
          canContinue={phase === 'build'}
          busy={advancing}
          onContinue={resumeBuild}
        />
      )}

      {/* BV-A2: deferred contract changes awaiting the owner's grant/deny at the review gate.
          A grant sends the item back to build to perform the change (then re-vets); a deny waives
          the check so it can close with the gap on record. Leads above the sub-tabs like the pause. */}
      {viewingLive && phase === 'review' && auths.length > 0 && (
        <AuthorizationsBanner auths={auths} busy={authBusy} onDecide={decideAuth} />
      )}

      {/* Sub-tabs — P0-curated for the selected stage; the gate brief leads where one exists. */}
      <div className="flex shrink-0 gap-1 border-b border-line px-4">
        {subTabs.map((id) => {
          const { label, icon: Icon } = SUB_META[id]
          return (
            <button
              key={id}
              onClick={() => goItem(phaseView, id)}
              className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition ${
                sub === id ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg'
              }`}
            >
              <Icon size={14} /> {label}
            </button>
          )
        })}
      </div>

      {/* Body */}
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {err && <div className="rounded-md border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-xs text-danger">{err}</div>}
        {sub === 'gate' && <GateBriefPane brief={brief} err={briefErr} />}
        {sub === 'item' && (
          <Section icon={ScrollText} title="Item brief">
            {it.description ? <Markdown text={it.description} variant="doc" tone="dev" /> : <Empty>No item body yet — triage sharpens it.</Empty>}
          </Section>
        )}
        {sub === 'plan' && (!detail ? <Loading /> : (
          <>
            <Section icon={ListChecks} title="Tasks">
              {detail.tasks?.length ? (
                <ul className="space-y-1">
                  {detail.tasks.map((t, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <span
                        className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded border ${
                          t.done ? 'border-success bg-success/15 text-success' : 'border-faint bg-sunken text-transparent'
                        }`}
                      >
                        <Check size={11} />
                      </span>
                      <span className={`min-w-0 flex-1 ${t.done ? 'text-muted line-through' : 'text-fg'}`}>
                        <Markdown text={t.text} tone="dev" />
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>No task checklist yet.</Empty>
              )}
            </Section>
            <Section icon={FileText} title="Plan">
              {detail.plan ? <Markdown text={detail.plan} variant="doc" tone="dev" /> : <Empty>No plan recorded yet.</Empty>}
            </Section>
            {detail.prd && (
              <Section icon={ScrollText} title="PRD / design">
                <Markdown text={detail.prd} variant="doc" tone="dev" />
              </Section>
            )}
          </>
        ))}
        {sub === 'investigation' && (!detail ? <Loading /> : (
          <Section icon={SUB_META[sub].icon} title={SUB_META[sub].label}>
            {detail.docs?.[sub] ? (
              <Markdown text={detail.docs[sub]!} variant="doc" tone="dev" />
            ) : (
              <Empty>No {sub}.md yet — the {phaseView} phase emits it.</Empty>
            )}
          </Section>
        ))}
        {sub === 'checkpoints' && (!detail ? <Loading /> : <CheckpointsPane stubs={detail.checkpoints ?? []} />)}
        {sub === 'git' && <GitPane it={it} contextId={contextId} onChanged={onChanged} />}
        {sub === 'deputy' && <DeputyLogPane events={events} />}
        {sub === 'trace' && (
          <>
            <TracePane artifacts={artifacts} archived={detail?.execution ?? null} />
            {events.length > 0 && <TimelinePane events={events} />}
          </>
        )}
      </div>

      {/* Actions — the gates are decided HERE, from the brief above. Only ever for the item's LIVE
          phase: the stepper lets the owner read a past stage, and a decision button under a stage
          that already happened acts on the live phase behind their back. Viewing history is
          reading, not deciding. */}
      {viewingLive && (
      <div className="flex shrink-0 items-center gap-2 border-t border-line px-4 py-3">
        {abandoning ? (
          <div className="flex flex-1 items-center gap-2">
            <input
              autoFocus
              value={abandonReason}
              onChange={(e) => setAbandonReason(e.target.value)}
              placeholder="Why drop it? (lands in the closeout)"
              className="min-w-0 flex-1 rounded-md border border-line bg-sunken px-2 py-1.5 text-xs text-fg placeholder:text-faint"
            />
            <button
              onClick={abandon}
              disabled={advancing}
              className="inline-flex items-center gap-1.5 rounded-md bg-danger px-3 py-1.5 text-xs font-medium text-on-accent hover:opacity-90 disabled:opacity-50"
            >
              {advancing ? <Loader2 size={14} className="animate-spin" /> : <Ban size={14} />} Drop
            </button>
            <button onClick={() => setAbandoning(false)} className="text-xs text-muted hover:text-fg">Cancel</button>
          </div>
        ) : running ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-accent-text">
            <Loader2 size={14} className="animate-spin" /> Agent working…
          </span>
        ) : (
          <>
            <span
              title="Run config was locked in at capture (chosen on the inbox item, fixed at push) — F3"
              className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-2 py-1 text-[11px] text-muted"
            >
              <Lock size={11} className="opacity-70" />
              {fmtModel(model)} · {effort}
            </span>
            {queued ? (
              <button
                onClick={plan}
                title="Plan it — a background run drafts the plan with the selected model"
                className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent transition hover:opacity-90"
              >
                <Sparkles size={14} /> Plan it
              </button>
            ) : completed ? (
              <span className="inline-flex items-center gap-1.5 text-xs text-success">
                <Check size={14} /> {it.outcome === 'abandoned' ? 'Dropped' : it.outcome === 'superseded' ? 'Superseded' : 'Completed · trace archived'}
              </span>
            ) : atClose ? (
              <button
                onClick={complete}
                disabled={advancing}
                title="Complete — mechanically refused unless every close criterion is green"
                className="inline-flex items-center gap-1.5 rounded-md bg-success px-3 py-1.5 text-xs font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
              >
                {advancing ? <Loader2 size={14} className="animate-spin" /> : <Archive size={14} />}
                Complete &amp; archive
              </button>
            ) : phase === 'vet' ? (
              <button
                onClick={vet}
                disabled={advancing}
                title="Run vet — launches the build⟷vet loop: a fresh agent runs the plan's vet checks; failures hand back to the build session until green or a breaker stops it"
                className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
              >
                {advancing ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />}
                Run vet
              </button>
            ) : nextPhase && atGate ? (
              <button
                onClick={advance}
                disabled={advancing}
                title={phase === 'review' ? reviewApprove.title
                  : `Approve — advances to ${PHASE_LABEL[nextPhase] ?? nextPhase}`}
                className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
              >
                {advancing ? <Loader2 size={14} className="animate-spin" />
                  : phase === 'review' ? <GitMerge size={14} /> : <Check size={14} />}
                {phase === 'review' ? reviewApprove.label : 'Approve'}
              </button>
            ) : null}
            <span className="ml-auto flex items-center gap-1">
              {/* Non-gate phases carry NO primary action: this phase advances on its own, so a
                  forward button in the decision slot invents a decision the owner doesn't owe.
                  It lives here with the other overrides — and only until the loop drives the
                  build⟷vet edge itself, at which point it's a manual escape, not the mechanism. */}
              {!completed && !queued && !atGate && nextPhase && (
                <button
                  onClick={advance}
                  disabled={advancing}
                  title={`Override — ${PHASE_LABEL[phase] ?? phase} is not a gate; it should advance on its own. This forces the move to ${PHASE_LABEL[nextPhase] ?? nextPhase}.`}
                  className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-faint transition hover:text-fg disabled:opacity-50"
                >
                  {advancing ? <Loader2 size={14} className="animate-spin" /> : <ArrowRight size={14} />}
                  Force {PHASE_LABEL[nextPhase] ?? nextPhase}
                </button>
              )}
              {!completed && (
                <button
                  onClick={() => setAbandoning(true)}
                  title="Drop — terminal without completing: worktree removed, branch kept, zero knowledge writes"
                  className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-faint transition hover:text-danger"
                >
                  <Ban size={14} /> Drop
                </button>
              )}
              {preBuild && (
                <button
                  onClick={() => onDelete(it)}
                  title="Delete — hard-removes this item, its session, and its inbox row"
                  className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-faint transition hover:text-danger"
                >
                  <Trash2 size={14} /> Drop
                </button>
              )}
            </span>
          </>
        )}
      </div>
      )}
      {/* Viewing history — say so, rather than leaving a silently actionless footer. */}
      {!viewingLive && (
        <div className="flex shrink-0 items-center gap-2 border-t border-line px-4 py-3 text-xs text-faint">
          <History size={14} />
          Viewing the {PHASE_LABEL[phaseView] ?? phaseView} stage — this item is at{' '}
          {PHASE_LABEL[phase] ?? phase}.
          <button onClick={() => goItem(phase, null)} className="ml-auto text-accent-text hover:underline">
            Back to current
          </button>
        </div>
      )}
    </Modal>
  )
}

// The paused banner — the FIRST thing the owner sees when they open a parked item that stopped for a
// reason (deputy escalation / build⟷vet halt / blocked run). It answers "why am I here, what do I
// decide" without the owner hunting a sub-tab; the runbook detail + the reporter's own next step lead.
// BV-A2: a deferred contract change awaiting the owner's grant/deny at review.
type AuthRow = { id: string; what: string; why: string; doc: string; scope: string; delegable: boolean }

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
          <span className="text-[11px] font-bold uppercase tracking-wider text-accent-text">
            Authorization {auths.length > 1 ? `requests (${auths.length})` : 'request'} — your call
          </span>
          <p className="mt-1 text-xs leading-snug text-muted">
            The build deferred {auths.length > 1 ? 'these contract changes' : 'this contract change'} rather than self-authorizing.
            Grant to have it performed + re-vetted; deny to close with the gap on record.
          </p>
          <div className="mt-2.5 space-y-2.5">
            {auths.map((a) => {
              const b = busy === a.id
              return (
                <div key={a.id} className="rounded-md border border-line bg-sunken px-2.5 py-2">
                  <p className="text-sm font-medium leading-snug text-fg">{a.what}</p>
                  <p className="mt-0.5 text-xs leading-snug text-muted">{a.why}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-faint">
                    {a.doc && <span>doc: <code className="text-muted">{a.doc}</code></span>}
                    <span>scope: <code className="text-muted">{a.scope}</code></span>
                    <span className={a.delegable ? 'text-muted' : 'font-medium text-warn'}>
                      {a.delegable ? 'sync-to-reality' : 'owner-reserved — escalated to you'}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <button
                      onClick={() => onDecide(a.id, 'granted')}
                      disabled={!!busy}
                      className="inline-flex items-center gap-1.5 rounded-md bg-accent px-2.5 py-1 text-xs font-semibold text-on-accent transition hover:opacity-90 disabled:opacity-50"
                    >
                      {b ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Grant
                    </button>
                    <button
                      onClick={() => onDecide(a.id, 'denied')}
                      disabled={!!busy}
                      className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-xs text-muted transition hover:text-danger disabled:opacity-50"
                    >
                      <Ban size={13} /> Deny
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

function PagedBanner({ p, canContinue, busy, onContinue }: {
  p: PagedData
  canContinue: boolean
  busy: boolean
  onContinue: () => void
}) {
  const deputy = p.source === 'deputy'
  const Icon = deputy ? ShieldCheck : AlertTriangle
  return (
    <div className="mx-4 mt-3 rounded-lg border border-warn/40 bg-warn/10 px-3.5 py-3">
      <div className="flex items-start gap-2.5">
        <Icon size={16} className="mt-0.5 shrink-0 text-warn" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-bold uppercase tracking-wider text-warn">
              {deputy ? `Deputy escalated${p.gate ? ` · ${p.gate}` : ''}` : 'Paused — needs your call'}
            </span>
          </div>
          <p className="mt-1 text-sm font-medium leading-snug text-fg">{p.headline}</p>
          {p.detail && (
            <div className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed text-muted">{p.detail}</div>
          )}
          {p.next && (
            <p className="mt-2 text-xs leading-snug text-fg">
              <span className="font-semibold text-warn">What to decide: </span>{p.next}
            </p>
          )}
          {canContinue ? (
            // BV-A1: a parked build re-enters the loop on Continue — it finalizes, records any wall
            // as an assumption, and the gap rides to review. This makes the resume real (a bare chat
            // reply does not advance the loop).
            <div className="mt-2.5 flex items-center gap-2">
              <button
                onClick={onContinue}
                disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-md bg-warn px-2.5 py-1 text-xs font-semibold text-white transition hover:brightness-110 disabled:opacity-50"
              >
                {busy ? <Loader2 size={13} className="animate-spin" /> : <ArrowRight size={13} />}
                Continue — carry the gap to review
              </button>
              <span className="text-[11px] italic leading-snug text-faint">
                or reply in chat to steer the build first.
              </span>
            </div>
          ) : (
            <p className="mt-2 text-[11px] italic leading-snug text-faint">
              Reply in this item's chat with your decision, or use the buttons below.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// The kernel-assembled gate brief (S6): continuity → delta → narrative → the uniform decision
// block. The gate is answerable from this render alone — that's the contract.
// The brief is fetched by the PARENT and passed in, not fetched here: this pane unmounts whenever
// the owner looks at another tab, so owning the fetch meant re-hitting the route + flashing a
// spinner on every return to a brief that hadn't changed.
function GateBriefPane({ brief, err }: { brief: GateBrief | null; err: string | null }) {
  if (err) return <div className="text-sm text-danger">Couldn’t load the gate brief — {err}</div>
  if (!brief) return <Loading />
  const n = brief.numbers
  const nums: [string, string][] = [
    ['tasks', `${n.tasks_done}/${n.tasks_total}`],
    ['checks', `${n.checks_pass}/${n.checks_total}`],
    ['cycle', String(n.cycle)],
  ]
  return (
    <div className="space-y-3">
      {/* Verdict line: gate state + recommendation + check glyphs — the 3-second read. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
          brief.terminal ? 'bg-success/15 text-success' : brief.at_gate ? 'bg-warn/15 text-warn' : 'bg-hover text-faint'
        }`}>
          {brief.terminal ? 'terminal — nothing to decide' : `${brief.gate}${brief.at_gate ? ' — decision pending' : ' — preview'}`}
        </span>
        {!brief.terminal && <span className="text-sm font-medium text-fg">{brief.decision.recommendation}</span>}
        <span className="flex items-center gap-1 text-[11px] text-faint">
          {brief.checks.map((c) => (
            <span key={c.criterion} title={`${c.criterion}: ${c.detail}`} className={c.ok ? 'text-success' : 'text-danger'}>
              {c.ok ? '●' : '○'}
            </span>
          ))}
        </span>
        <span className="ml-auto flex items-center gap-3 font-mono text-[11px] tabular-nums text-muted">
          {nums.map(([l, v]) => (
            <span key={l}><span className="text-faint">{l}</span> {v}</span>
          ))}
        </span>
      </div>

      {/* Fact rows — present facts only, label:value, tone-tinted. */}
      {(brief.facts.length > 0 || brief.flags.length > 0) && (
        <div className="flex flex-wrap gap-1.5">
          {brief.facts.map((f) => (
            <span key={f.label} className={`rounded-md border px-2 py-0.5 text-[11px] ${
              f.tone === 'warn' ? 'border-warn/40 bg-warn/10 text-warn' : 'border-line bg-sunken text-muted'
            }`}>
              <span className="uppercase tracking-wide text-[9.5px] text-faint">{f.label}</span>{' '}{f.value}
            </span>
          ))}
          {brief.flags.map((f) => (
            <span key={f} className="rounded-md border border-warn/40 bg-warn/10 px-2 py-0.5 text-[11px] text-warn">⚠ {f}</span>
          ))}
        </div>
      )}

      {/* Loop instruments — the checks×cycles convergence matrix (build⟷vet stretch + review). */}
      {brief.loop.cycles.length > 0 && brief.loop.checks.length > 0 && (
        <div className="rounded-md border border-line bg-sunken px-3 py-2">
          <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-faint">
            Convergence — checks × vet cycles
          </div>
          <table className="font-mono text-[11px] tabular-nums">
            <thead>
              <tr>
                <th className="pr-3 text-left font-normal text-faint">check</th>
                {brief.loop.cycles.map((c) => (
                  <th key={c.cycle} className="px-1.5 text-center font-normal text-faint">c{c.cycle}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {brief.loop.checks.map((id) => (
                <tr key={id}>
                  <td className="pr-3 text-muted">{id}</td>
                  {brief.loop.cycles.map((c) => {
                    const v = c.verdicts[id]
                    return (
                      <td key={c.cycle} className={`px-1.5 text-center ${
                        v === undefined ? 'text-line' : v ? 'text-success' : 'text-danger'
                      }`}>
                        {v === undefined ? '−' : v ? '●' : '✕'}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {brief.loop.findings && (
            <div className="mt-2 border-t border-line pt-2">
              <div className="mb-1 text-[10px] uppercase tracking-wide text-danger">latest failure — expected vs actual (verbatim)</div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-canvas px-2 py-1.5 font-mono text-[11px] leading-relaxed text-muted">{brief.loop.findings}</pre>
            </div>
          )}
        </div>
      )}

      {/* Assumptions — the review's real subject (plan gate). */}
      {brief.assumptions.length > 0 && (
        <div className="rounded-md border border-line bg-sunken px-3 py-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-faint">
            Assumptions made without you — confirm or adjust
          </div>
          <ol className="space-y-0.5">
            {brief.assumptions.map((a, i) => (
              <li key={i} className="flex items-baseline gap-2 text-[12.5px] text-fg">
                <span className="font-mono text-[10px] text-accent">{i + 1}</span>
                <span className="min-w-0 flex-1">{a}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Behavior snippet (review) — the newest passing ledger entry, verbatim: captured reality. */}
      {brief.snippet && brief.gate === 'review' && (
        <div className="rounded-md border border-line bg-sunken px-3 py-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-faint">
            Behavior — captured by the vet (verbatim)
          </div>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-canvas px-2 py-1.5 font-mono text-[11px] leading-relaxed text-muted">{brief.snippet}</pre>
        </div>
      )}

      {/* The gate report — agent-rendered from the phase skill's template, embedded verbatim.
          Self-contained html by contract; sandboxed (scripts only — it renders itself, no reach). */}
      {brief.report_html ? (
        <iframe
          title="gate report"
          sandbox="allow-scripts"
          srcDoc={brief.report_html}
          className="h-[560px] w-full rounded-md border border-line bg-[#0d1117]"
        />
      ) : null}

      {/* The narrative brief, demoted to details once the typed card carries the decision. */}
      <details className="group" open={!brief.report_html}>
        <summary className="cursor-pointer select-none text-[11px] font-medium uppercase tracking-wide text-faint hover:text-fg">
          Details — full brief
        </summary>
        <div className="mt-2">
          <Markdown text={brief.brief} variant="doc" tone="dev" />
        </div>
      </details>
    </div>
  )
}

// The uniform timeline strip (renovation build slice): the item's dev-event trail as one glyph-
// coded feed. Density by omission — `.start` rows say nothing their `.end` twin doesn't, so they
// are dropped; milestones (gates, merges, completion) read emphasized, telemetry reads quiet.
const MILESTONE_KINDS = new Set([
  'phase.advance', 'git.merge', 'git.pr', 'git.worktree', 'git.revert', 'item.complete', 'item.abandon',
  'close.proposed', 'review.route', 'inbox.push', 'item.await',
])
function TimelinePane({ events }: { events: DevEvent[] }) {
  const rows = events.filter((e) => !e.kind.endsWith('.start'))
  if (!rows.length) return null
  return (
    <Section icon={History} title="Timeline">
      <ol className="space-y-1">
        {rows.map((e) => {
          const milestone = MILESTONE_KINDS.has(e.kind)
          const owner = e.actor === 'owner'
          return (
            <li key={e.id} className="flex items-baseline gap-2 text-xs">
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

// The Deputy log (Q1-E) — the owner's stand-in's governance trail on this item, in one place and
// OUT of the chat: every gate call it made (approve · escalate), each round of feedback it fired at
// the agent, with the rationale. The conversation itself lives in the chat (3 speakers); this is the
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
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
  if (!rows.length) return <Empty>The deputy hasn’t acted on this item yet.</Empty>
  return (
    <Section icon={ShieldCheck} title="Deputy log">
      <ol className="space-y-2">
        {rows.map((e) => {
          const m = (e.meta ?? {}) as Record<string, unknown>
          const row = DEPUTY_ROW[e.kind] ?? { icon: ShieldCheck, label: e.kind, tint: 'text-muted' }
          const Icon = row.icon
          const gate = (m.gate ?? m.phase ?? m.origin_gate) as string | undefined
          // The reasoning behind the move, in the deputy's own words (what it checked / why / what it asked for).
          const rationale = [m.because, m.escalation, m.change, m.speech, m.checked]
            .map((x) => (typeof x === 'string' ? x.trim() : '')).find(Boolean) || e.summary
          return (
            <li key={e.id} className="rounded-md border border-line bg-sunken px-3 py-2">
              <div className="flex items-center gap-2">
                <Icon size={13} className={`shrink-0 ${row.tint}`} />
                <span className={`text-xs font-semibold ${row.tint}`}>{row.label}</span>
                {gate && <span className="rounded bg-hover px-1.5 py-px text-[10px] font-medium text-muted">{gate}</span>}
                <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-faint">{fmtLocal(e.created_at)}</span>
              </div>
              {rationale && <div className="mt-1 text-xs leading-snug text-muted"><Markdown text={rationale} tone="dev" /></div>}
            </li>
          )
        })}
      </ol>
    </Section>
  )
}

// The continuity feed — newest-first checkpoint stubs (headline + git state); full text stays
// on disk behind the path.
function CheckpointsPane({ stubs }: { stubs: { ts: string; headline: string; git?: string | null; path: string }[] }) {
  if (!stubs.length) return <Empty>No checkpoints banked yet — sessions bank one at wrap-up.</Empty>
  return (
    <ol className="space-y-2">
      {stubs.map((c) => (
        <li key={c.ts} className="rounded-md border border-line bg-sunken px-2.5 py-2">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[10px] text-faint">{c.ts}</span>
            {c.git && <span className="truncate font-mono text-[10px] text-faint">{c.git}</span>}
          </div>
          <div className="mt-0.5 text-[12.5px] text-fg">{c.headline || '(empty checkpoint)'}</div>
        </li>
      ))}
    </ol>
  )
}

// Live git state + the owner's git actions (S4 routes): freshness sync anytime, the review
// merge, and the always-offered revert while the backup ref stands.
function GitPane({ it, contextId, onChanged }: {
  it: WorkItem; contextId: string; onChanged: () => void
}) {
  // The PR is open when the deputy approved and the merge hasn't happened — derived from the two
  // facts that decide it, exactly as the backend does, so the two can never disagree.
  const prOpen = !!it.git_pr_opened_at && !it.git_merge_commit
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  // Live, not fetch-once: ahead/behind and `dirty` move underneath an open tab whenever a build or
  // vet cycle commits, and the previous one-shot read left the counts frozen at whatever they were
  // when the tab was first shown.
  const gitQ = useLive<GitHealth>(K.itemGit(contextId, it.id), () => getWorkItemGit(it.id, contextId), 10000)
  const health = gitQ.data ?? null
  const load = gitQ.refresh
  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name)
    setMsg(null)
    try {
      const r = await fn()
      setMsg(JSON.stringify(r).slice(0, 300))
      onChanged()
      load()
    } catch (e) {
      setMsg(String(e))
    } finally {
      setBusy(null)
    }
  }
  if (!it.git_branch && !it.git_worktree) {
    return <Empty>No git record yet — the branch + worktree are created when build starts.</Empty>
  }
  if (!health) return <Loading />
  const rows: [string, React.ReactNode][] = [
    ['branch', <span className="font-mono">{health.branch ?? it.git_branch}</span>],
    ['worktree', health.dir_exists ? <span className="font-mono">{health.worktree}</span> : <span className="text-faint">removed (terminal)</span>],
    ['anchor', <span className="font-mono">{health.trunk ?? '—'}</span>],
    [`vs ${health.trunk ?? 'anchor'}`, `ahead ${health.ahead ?? 0} · behind ${health.behind ?? 0}${health.behind ? ' — sync first' : ''}`],
    ['merged', health.merged ? `yes${it.git_merge_commit ? ` (${String(it.git_merge_commit).slice(0, 10)})` : ''}` : 'not yet'],
    ['dirty', health.dirty?.length ? health.dirty.join(', ') : 'clean'],
    // The repo's rule, echoed read-only where the merge is — set it in Quick config → Per-repo.
    // NAME THE ACTOR. This used to read "strict — approving opens a PR; you merge from the PR
    // page", which is true of the DEPUTY's approval and false of yours: the owner's approve merges
    // in both modes (gates.py keys the PR branch off `actor != "owner"`). Read as a statement
    // about the reader's own click, it contradicted the gate button one tab away — and on
    // 2026-07-29 it talked me into "fixing" the button to match, until a live approve merged.
    ['landing', health.review_mode === 'strict'
      ? "strict — the deputy's approval only opens a PR; yours merges"
      : 'fast — either approval merges it'],
  ]
  return (
    <div className="space-y-3">
      <dl className="space-y-1 text-[12.5px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <dt className="w-20 shrink-0 text-faint">{k}</dt>
            <dd className="min-w-0 flex-1 text-fg">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap items-center gap-2">
        <GitBtn icon={RefreshCw} label={`Sync from ${health.trunk ?? 'anchor'}`} busy={busy === 'sync'}
                onClick={() => act('sync', () => syncWorkItemGit(it.id, contextId))}
                title="Merge the anchor branch INTO the item branch (freshness — makes the real merge trivial)" />
        {/* The park path's exit (§2.3). A conflicting sync holds the item at review and the refusal
            says "Resolve-with-Agent" — which, until now, named a control that did not exist: the
            route and its API binding were both built, and nothing called them. The human decides
            WHETHER; the agent resolves in the worktree (D4) — nobody hand-edits conflict markers.
            Offered whenever the branch is behind, since that is when a conflict is possible; the
            route itself refuses cleanly ("nothing to resolve") if the sync turns out clean. */}
        {!health.merged && !!health.behind && (
          <GitBtn icon={GitMerge} label="Resolve with agent" busy={busy === 'resolve'}
                  onClick={() => act('resolve', () => resolveWorkItemGit(it.id, contextId))}
                  title="Conflicts only: re-runs the sync leaving the conflicts in the worktree, then an agent resolves them there. The daemon completes the merge and the item re-enters vet." />
        )}
        {/* B2: the merge is no longer a standalone button — it IS the review gate's Approve
            ("Approve & merge" in the Gate tab). One decision, one act; a lone Merge that left the
            item at review unmerged was exactly the stranding this fix removes. */}
        {/* Every action this pane owns is RENDERED ALWAYS and disabled when it isn't workable, with
            the reason in the tooltip (owner rule, 2026-07-29). Mode-conditional rendering read as a
            missing feature: a `fast` repo simply had no PR button anywhere, and nothing on screen
            said why. A greyed control that explains itself teaches the model; an absent one hides
            it. No Reject either way: a change that isn't good enough is said in the item's chat,
            and the agent calls `revise` (§2.1). */}
        {/* Opens in its OWN browser tab — a diff wants the whole screen, and this window keeps the
            board and the item's chat where they are. It is a real path now (`/repo/:id/item/:item/pr`,
            still forked at main.tsx above the cockpit), so the tab is linkable and bookmarkable
            rather than carrying its state in a query string. */}
        <GitBtn icon={ExternalLink} label="Open PR page" busy={false}
                disabled={!health.branch_exists}
                href={build({ name: 'pr', repoId: contextId, itemId: it.id })}
                title={!health.branch_exists
                  ? 'No branch yet — it is created when build starts'
                  : 'Opens a new tab: the review report beside the branch’s diff, grouped by the '
                    + 'plan’s tasks. Readable in any mode, before or after the merge.'} />
        <GitBtn icon={GitMerge} label="Merge" busy={busy === 'merge'} accent
                disabled={health.merged || health.review_mode !== 'strict' || !prOpen}
                onClick={() => act('merge', () => advanceWorkItem(it.id, contextId))}
                title={health.merged
                  ? 'Already merged — see the merge commit above'
                  : health.review_mode !== 'strict'
                    ? 'This repo is `fast`, so nothing is ever parked here for you — merge at the '
                      + 'Gate (“Approve & merge”). Under `strict` the deputy cannot land a branch, '
                      + 'so its approval opens a PR and this button finishes the job.'
                    : prOpen
                      ? 'Squash this branch onto the anchor and advance to close'
                      : 'Active once the deputy has approved and handed you the merge'} />
        {it.git_backup_ref && (
          <GitBtn icon={Undo2} label="Revert merge" busy={busy === 'revert'}
                  onClick={() => act('revert', () => revertWorkItemGit(it.id, contextId))}
                  title="Restore the trunk to its pre-merge state via the recorded backup ref (safe-only)" />
        )}
      </div>
      {msg && <div className="break-all rounded-md bg-sunken px-2.5 py-1.5 font-mono text-[10.5px] text-muted">{msg}</div>}
    </div>
  )
}

function GitBtn({ icon: Icon, label, onClick, href, busy, title, accent, disabled }: {
  icon: typeof RefreshCw; label: string; onClick?: () => void; busy?: boolean; title?: string
  // A real link, opened in a new tab. `window.open` is a scripted popup — browsers and embedded
  // panes are entitled to refuse it, and one of ours does. An anchor is navigation: it always
  // opens, and it comes with ⌘-click, middle-click and "open in new window" for free.
  href?: string
  accent?: boolean
  // The universal activation rule (§4): a git action is live EXACTLY when it is the one required.
  disabled?: boolean
}) {
  const cls = `inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition disabled:opacity-50 ${
    accent ? 'bg-accent font-medium text-on-accent hover:opacity-90'
           : 'border border-line bg-surface text-muted hover:bg-hover hover:text-fg'
  }`
  const inner = <>{busy ? <Loader2 size={13} className="animate-spin" /> : <Icon size={13} />} {label}</>
  if (href && !disabled && !busy) {
    return (
      <a href={href} target="_blank" rel="noopener" title={title} className={cls}>{inner}</a>
    )
  }
  return (
    <button onClick={onClick} disabled={!!busy || !!disabled} title={title} className={cls}>
      {inner}
    </button>
  )
}

// The run telemetry line — model · context fill · per-phase token chips (3-type basis, same as
// the Activity log). Phases with no recorded spend stay hidden.
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
  return <div className="text-sm text-faint">{children}</div>
}

function Loading() {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-muted">
      <Loader2 size={14} className="animate-spin" /> Loading…
    </div>
  )
}

// The raw call-trail (one click deeper than the brief) — tools / sub-agents / skills the item's
// runs invoked, grouped by run; completed items fall back to the archived execution.md snapshot.
function TracePane({ artifacts, archived }: { artifacts: RunArtifact[]; archived: string | null }) {
  if (artifacts.length === 0) {
    if (archived) {
      return (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
            <Archive size={12} /> Archived trace
            <span className="font-mono text-[10px] normal-case text-faint">artifacts/execution.md</span>
          </div>
          <Markdown text={archived} variant="doc" tone="dev" />
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
        return (
          <div key={gi}>
            <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-faint">
              {g.run != null ? `Run #${g.run}` : 'Unattached'} · {calls.length} call{calls.length === 1 ? '' : 's'}
            </div>
            <TraceRows rows={calls} time={(a) => fmtLocal(a.created_at)} />
          </div>
        )
      })}
    </div>
  )
}
