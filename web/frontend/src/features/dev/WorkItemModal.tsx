import { useEffect, useMemo, useState } from 'react'
import {
  X, ArrowRight, Sparkles, Trash2, Check, Loader2, FileText, ListChecks, ScrollText, History,
  Terminal, Archive, Scale, GitBranch, Milestone, FlaskConical, BookOpenText, Ban, RefreshCw,
  GitMerge, Undo2,
} from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Dropdown from '@/ui/Dropdown'
import Modal from '@/ui/Modal'
import SectionHeader from '@/ui/SectionHeader'
import { TraceRows } from './ExecutionTrace'
import { pairTrace } from '@/lib/trace'
import {
  getWorkItemDetail, getWorkItemArtifacts, advanceWorkItem, completeWorkItem, setWorkItemModel,
  setWorkItemEffort, getDevLog, getWorkItemGateBrief, getWorkItemGit, syncWorkItemGit,
  mergeWorkItemGit, revertWorkItemGit, abandonWorkItem, markWorkItemSeen,
  type WorkItem, type WorkItemDetail, type DevEvent, type RunArtifact, type GateBrief,
  type GitHealth,
} from '@/lib/api'
import { fmtModel, fmtTokens, fmtLocal, toModelKey } from '@/lib/format'
import { StatusBadge, isPlannable, RUN_MODELS, DEFAULT_RUN_MODEL, RUN_EFFORTS, DEFAULT_RUN_EFFORT } from './panels'
import { PHASE_LABEL, GATED_PHASES } from './common'

// The work-item drilldown (S7 v1) — "tell me about this one". Header (overview + meta + progress)
// → clickable PHASE STEPPER (current stage pulsing) → P0-curated per-phase sub-tabs, leading with
// the newest GATE BRIEF (the kernel-assembled decision surface; raw trace one click deeper) →
// the action footer (gates are decided here). Contained to the dashboard column so the bound chat
// stays interactive beside it.

// Per-kind pipeline (mirrors the backend KIND_PROFILES).
const PIPELINES: Record<string, string[]> = {
  implementation: ['triage', 'plan', 'build', 'validate', 'deliver', 'close'],
  research: ['triage', 'plan', 'investigate', 'report', 'close'],
}

// P0-curated sub-tabs per phase — the most useful reads for that stage, nothing else. `trace`
// (the raw call-trail) is appended to every phase: one click deeper, never leading.
type SubTab = 'gate' | 'item' | 'plan' | 'validation' | 'findings' | 'closeout' | 'checkpoints' | 'git' | 'trace'
const PHASE_TABS: Record<string, SubTab[]> = {
  triage: ['gate', 'item'],
  plan: ['gate', 'plan'],
  build: ['plan', 'checkpoints', 'git'],
  validate: ['validation', 'checkpoints', 'git'],
  deliver: ['gate', 'git', 'checkpoints'],
  investigate: ['plan', 'checkpoints'],
  report: ['findings'],
  close: ['gate', 'closeout'],
}
const SUB_META: Record<SubTab, { label: string; icon: typeof FileText }> = {
  gate: { label: 'Gate brief', icon: Scale },
  item: { label: 'Item', icon: ScrollText },
  plan: { label: 'Plan', icon: FileText },
  validation: { label: 'Validation', icon: FlaskConical },
  findings: { label: 'Findings', icon: BookOpenText },
  closeout: { label: 'Closeout', icon: Archive },
  checkpoints: { label: 'Checkpoints', icon: Milestone },
  git: { label: 'Git', icon: GitBranch },
  trace: { label: 'Trace', icon: Terminal },
}

export default function WorkItemModal({
  it, contextId, onClose, onPlan, onDelete, onChanged,
}: {
  it: WorkItem
  contextId: string
  onClose: () => void
  onPlan: (it: WorkItem, model?: string, effort?: string) => void // fire a headless plan run (queued items)
  onDelete: (it: WorkItem) => void // hard-delete (caller confirms)
  onChanged: () => void // reload the board after an advance
}) {
  const [detail, setDetail] = useState<WorkItemDetail | null>(null)
  const [events, setEvents] = useState<DevEvent[]>([])
  const [artifacts, setArtifacts] = useState<RunArtifact[]>([])
  const [brief, setBrief] = useState<GateBrief | null>(null)
  const [briefErr, setBriefErr] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [advancing, setAdvancing] = useState(false)
  const [abandoning, setAbandoning] = useState(false) // inline abandon confirm row
  const [abandonReason, setAbandonReason] = useState('')
  const [model, setModel] = useState(toModelKey(it.model) || DEFAULT_RUN_MODEL)
  const [effort, setEffort] = useState(it.effort ?? DEFAULT_RUN_EFFORT)

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

  // Stepper selection — defaults to the item's live phase; clicking a step views that stage.
  const [phaseView, setPhaseView] = useState<string>(phase)
  useEffect(() => setPhaseView(phase), [phase])
  const viewingLive = phaseView === phase
  const subTabs = PHASE_TABS[phaseView] ?? ['gate']
  const [sub, setSub] = useState<SubTab>(subTabs[0])
  useEffect(() => setSub((PHASE_TABS[phaseView] ?? ['gate'])[0]), [phaseView])

  // Pull detail + timeline + call-trail; live-poll while a run is in flight.
  useEffect(() => {
    let alive = true
    const pull = () => {
      getWorkItemDetail(it.id, contextId)
        .then((d) => alive && setDetail(d))
        .catch((e) => alive && setErr(String(e)))
      getDevLog(contextId, { itemId: it.id, limit: 50 })
        .then((d) => alive && setEvents(d.events))
        .catch(() => {})
      getWorkItemArtifacts(it.id, contextId)
        .then((d) => alive && setArtifacts(d.artifacts))
        .catch(() => {})
      getWorkItemGateBrief(it.id, contextId)
        .then((b) => alive && setBrief(b))
        .catch((e) => alive && setBriefErr(String(e)))
    }
    pull()
    if (!it.running) return () => { alive = false }
    const t = setInterval(pull, 2500)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [it.id, contextId, it.running])

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
  async function changeModel(m: string) {
    setModel(m)
    try {
      await setWorkItemModel(it.id, m, contextId)
      onChanged()
    } catch (e) {
      setErr(`Couldn't set model — ${e}`)
    }
  }
  async function changeEffort(e: string) {
    setEffort(e)
    try {
      await setWorkItemEffort(it.id, e, contextId)
      onChanged()
    } catch (err) {
      setErr(`Couldn't set effort — ${err}`)
    }
  }
  async function advance() {
    setAdvancing(true)
    try {
      await advanceWorkItem(it.id, contextId)
      onChanged()
      onClose()
    } catch (e) {
      setErr(`Couldn't advance — ${e}`)
      setAdvancing(false)
    }
  }
  async function complete() {
    setAdvancing(true)
    try {
      await completeWorkItem(it.id, contextId)
      onChanged()
      onClose()
    } catch (e) {
      setErr(`Couldn't complete — ${e}`)
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
      setErr(`Couldn't abandon — ${e}`)
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
              <StatusBadge it={it} running={running} />
              <span className="rounded-full bg-hover px-2 py-0.5 text-[10px] uppercase tracking-wide text-faint">
                {it.kind ?? 'implementation'}
              </span>
              {it.deliverable && (
                <span className="rounded-full bg-hover px-2 py-0.5 font-mono text-[10px] text-faint">{it.deliverable}</span>
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
                onClick={() => setPhaseView(p)}
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

      {/* Sub-tabs — P0-curated for the selected stage; the gate brief leads where one exists. */}
      <div className="flex shrink-0 gap-1 border-b border-line px-4">
        {[...subTabs, 'trace' as SubTab].map((id) => {
          const { label, icon: Icon } = SUB_META[id]
          return (
            <button
              key={id}
              onClick={() => setSub(id)}
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
        {(sub === 'validation' || sub === 'findings' || sub === 'closeout') && (!detail ? <Loading /> : (
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
        {sub === 'trace' && (
          <>
            <TracePane artifacts={artifacts} archived={detail?.execution ?? null} />
            {events.length > 0 && (
              <Section icon={History} title="History">
                <ol className="space-y-1">
                  {events.map((e) => (
                    <li key={e.id} className="flex items-baseline gap-2 text-xs">
                      <span className="font-mono text-[10px] text-faint">{fmtLocal(e.created_at)}</span>
                      <span className="rounded bg-hover px-1.5 py-0.5 font-mono text-[10px] text-muted">{e.kind}</span>
                      <span className="min-w-0 flex-1 truncate text-muted">{e.summary}</span>
                    </li>
                  ))}
                </ol>
              </Section>
            )}
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
              placeholder="Why abandon? (lands in the closeout)"
              className="min-w-0 flex-1 rounded-md border border-line bg-sunken px-2 py-1.5 text-xs text-fg placeholder:text-faint"
            />
            <button
              onClick={abandon}
              disabled={advancing}
              className="inline-flex items-center gap-1.5 rounded-md bg-danger px-3 py-1.5 text-xs font-medium text-on-accent hover:opacity-90 disabled:opacity-50"
            >
              {advancing ? <Loader2 size={14} className="animate-spin" /> : <Ban size={14} />} Abandon
            </button>
            <button onClick={() => setAbandoning(false)} className="text-xs text-muted hover:text-fg">Cancel</button>
          </div>
        ) : running ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-accent-text">
            <Loader2 size={14} className="animate-spin" /> Agent working…
          </span>
        ) : (
          <>
            <Dropdown value={model} options={RUN_MODELS} onChange={changeModel} title="Model this item's runs use (plan + chat)" />
            <Dropdown value={effort} options={RUN_EFFORTS} onChange={changeEffort} title="Reasoning effort this item's runs use (plan + chat)" />
            {queued ? (
              <button
                onClick={plan}
                title="Plan it — a headless agent drafts the plan with the selected model"
                className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent transition hover:opacity-90"
              >
                <Sparkles size={14} /> Plan it
              </button>
            ) : completed ? (
              <span className="inline-flex items-center gap-1.5 text-xs text-success">
                <Check size={14} /> {it.outcome === 'abandoned' ? 'Abandoned' : it.outcome === 'superseded' ? 'Superseded' : 'Completed · trace archived'}
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
            ) : nextPhase && atGate ? (
              <button
                onClick={advance}
                disabled={advancing}
                title={`Approve — advances to ${PHASE_LABEL[nextPhase] ?? nextPhase}`}
                className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
              >
                {advancing ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                Approve
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
                  title="Abandon — terminal without completing: worktree removed, branch kept, zero knowledge writes"
                  className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-faint transition hover:text-danger"
                >
                  <Ban size={14} /> Abandon
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
          <button onClick={() => setPhaseView(phase)} className="ml-auto text-accent-text hover:underline">
            Back to current
          </button>
        </div>
      )}
    </Modal>
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
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
          brief.at_gate ? 'bg-warn/15 text-warn' : 'bg-hover text-faint'
        }`}>
          {brief.gate}{brief.at_gate ? ' — decision pending' : ' — preview'}
        </span>
        <span className="flex items-center gap-1 text-[11px] text-faint">
          {brief.checks.map((c) => (
            <span key={c.criterion} title={`${c.criterion}: ${c.detail}`} className={c.ok ? 'text-success' : 'text-danger'}>
              {c.ok ? '●' : '○'}
            </span>
          ))}
        </span>
      </div>
      <Markdown text={brief.brief} variant="doc" tone="dev" />
    </div>
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

// Live git state + the owner's git actions (S4 routes): freshness sync anytime, the deliver
// merge, and the always-offered revert while the backup ref stands.
function GitPane({ it, contextId, onChanged }: { it: WorkItem; contextId: string; onChanged: () => void }) {
  const [health, setHealth] = useState<GitHealth | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const load = () => {
    getWorkItemGit(it.id, contextId).then(setHealth).catch((e) => setMsg(String(e)))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [it.id, contextId])
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
    ['vs trunk', `ahead ${health.ahead ?? 0} · behind ${health.behind ?? 0}${health.behind ? ' — sync first' : ''}`],
    ['merged', health.merged ? `yes${it.git_merge_commit ? ` (${String(it.git_merge_commit).slice(0, 10)})` : ''}` : 'not yet'],
    ['dirty', health.dirty?.length ? health.dirty.join(', ') : 'clean'],
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
        <GitBtn icon={RefreshCw} label="Sync from main" busy={busy === 'sync'}
                onClick={() => act('sync', () => syncWorkItemGit(it.id, contextId))}
                title="Merge the trunk INTO the item branch (freshness — makes the real merge trivial)" />
        {!health.merged && (
          <GitBtn icon={GitMerge} label="Merge" busy={busy === 'merge'} accent
                  onClick={() => act('merge', () => mergeWorkItemGit(it.id, contextId))}
                  title="The deliver merge — lands on main + applies the staged knowledge delta; a backup ref precedes it" />
        )}
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

function GitBtn({ icon: Icon, label, onClick, busy, title, accent }: {
  icon: typeof RefreshCw; label: string; onClick: () => void; busy?: boolean; title?: string; accent?: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={!!busy}
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition disabled:opacity-50 ${
        accent ? 'bg-accent font-medium text-on-accent hover:opacity-90'
               : 'border border-line bg-surface text-muted hover:bg-hover hover:text-fg'
      }`}
    >
      {busy ? <Loader2 size={13} className="animate-spin" /> : <Icon size={13} />} {label}
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
