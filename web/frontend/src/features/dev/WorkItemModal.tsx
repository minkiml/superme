import { useEffect, useState } from 'react'
import { X, ArrowRight, Sparkles, Trash2, Check, Loader2, FileText, ListChecks, ScrollText, History, Terminal, Bot, Wrench, Archive, BookOpen, FolderSearch, Search, SquareTerminal, FilePen, Globe } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Dropdown from '@/ui/Dropdown'
import Modal from '@/ui/Modal'
import SectionHeader from '@/ui/SectionHeader'
import {
  getWorkItemDetail, getWorkItemArtifacts, advanceWorkItem, completeWorkItem, setWorkItemModel, setWorkItemEffort, getDevLog,
  type WorkItem, type WorkItemDetail, type DevEvent, type RunArtifact,
} from '@/lib/api'
import { fmtModel, fmtTokens, fmtLocal } from '@/lib/format'
import { StatusBadge, isPlannable, RUN_MODELS, DEFAULT_RUN_MODEL, RUN_EFFORTS, DEFAULT_RUN_EFFORT } from './panels'
import { PHASE_LABEL } from './common'

// The work-item review popup — the decision surface. Clicking a card opens this: a structured
// render of the plan/design (plan.md as Markdown, tasks.md as a checklist, prd.md if present)
// plus run telemetry, with the forward gate (Approve → advance phase), Discuss (hand off to
// the bound chat), and Drop. Raw artifacts are digested into scannable sections, not dumped.
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
  const [tab, setTab] = useState<'review' | 'execution'>('review')
  const [err, setErr] = useState<string | null>(null)
  const [advancing, setAdvancing] = useState(false)
  // Model is configured here (the card no longer carries it) — used by "Plan it". Defaults to
  // the last run's model, else Sonnet.
  const [model, setModel] = useState(it.model ?? DEFAULT_RUN_MODEL)
  const [effort, setEffort] = useState(it.effort ?? DEFAULT_RUN_EFFORT)

  useEffect(() => {
    let alive = true
    getWorkItemDetail(it.id, contextId)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setErr(String(e)))
    // This item's own event timeline (PRD §4.9) — the containment view at item scope.
    getDevLog(contextId, { itemId: it.id, limit: 50 })
      .then((d) => alive && setEvents(d.events))
      .catch(() => {})
    // The call-trail: tools / sub-agents / skills this item's runs invoked.
    getWorkItemArtifacts(it.id, contextId)
      .then((d) => alive && setArtifacts(d.artifacts))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [it.id, contextId])

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const phase = it.phase ?? 'plan_design'
  const inPlan = phase === 'plan_design'
  const inBuild = phase === 'build_eval'
  const inDone = phase === 'done'
  const completed = !!it.done_at
  const running = !!it.running
  const queued = isPlannable(it) // a never-planned plan/design item — the "Plan it" gate

  function plan() {
    onPlan(it, model, effort)
    onClose()
  }

  // Reconfigure the item's model anytime — persists to item.md; future runs (plan + bound
  // chat) use it. Optimistic local update, then refresh the board so the card reflects it.
  async function changeModel(m: string) {
    setModel(m)
    try {
      await setWorkItemModel(it.id, m, contextId)
      onChanged()
    } catch (e) {
      setErr(`Couldn't set model — ${e}`)
    }
  }

  // Reconfigure the item's reasoning effort — persists to item.md; future runs use it.
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

  // The tick-out: archive the execution trace to a file, mark done, free session + run rows.
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

  return (
    // Contained (not viewport-fixed) so the overlay covers only the dashboard column — the chat
    // rail stays interactive for side-by-side read + discuss. `column` pins the header/tabs/actions
    // and scrolls only the body.
    <Modal onClose={onClose} contain column maxW="max-w-3xl" z="z-40">
        {/* Header */}
        <div className="flex shrink-0 items-start gap-2 border-b border-line px-4 py-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-[10px] text-faint">{it.id}</span>
              <StatusBadge it={it} />
              <span className="rounded-full bg-hover px-2 py-0.5 text-[10px] uppercase tracking-wide text-faint">
                {PHASE_LABEL[phase] ?? phase}
              </span>
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

        {/* Tabs — Review (the plan/tasks/history digest) · Execution (the call-trail) */}
        <div className="flex shrink-0 gap-1 border-b border-line px-4">
          {([['review', 'Review', FileText], ['execution', 'Execution', Terminal]] as const).map(([id, label, Icon]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition ${
                tab === id ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg'
              }`}
            >
              <Icon size={14} /> {label}
              {id === 'execution' && artifacts.length > 0 && (
                <span className="rounded-full bg-hover px-1.5 text-[10px] text-muted">{artifacts.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* Body — flexes to fill the capped modal height and scrolls on its own. */}
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
          {tab === 'execution' ? (
            <ArtifactsTab artifacts={artifacts} archived={detail?.execution ?? null} />
          ) : err && !detail ? (
            <div className="text-sm text-danger">Couldn’t load — {err}</div>
          ) : !detail ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted">
              <Loader2 size={14} className="animate-spin" /> Loading review…
            </div>
          ) : (
            <>
              {err && <div className="rounded-md border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-xs text-danger">{err}</div>}
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
                        {/* Task text is Markdown too — render inline so `code` picks up the dev tint and
                            **bold** the shared emphasis color, matching the Plan/PRD previews. */}
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

        {/* Actions — the item's control surface. Queued → configure model + Plan it; planned →
            Approve to advance; Drop (plan/design). Discussion happens in the auto-bound chat. */}
        <div className="flex shrink-0 items-center gap-2 border-t border-line px-4 py-3">
          {running ? (
            <span className="inline-flex items-center gap-1.5 text-xs text-accent-text">
              <Loader2 size={14} className="animate-spin" /> Planning…
            </span>
          ) : (
            <>
              {/* Model + effort config — always shown + reconfigurable; drive plan + bound-chat runs. */}
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
              ) : inPlan ? (
                <button
                  onClick={advance}
                  disabled={advancing}
                  title="Approve the plan and advance to Build / Eval"
                  className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
                >
                  {advancing ? <Loader2 size={14} className="animate-spin" /> : <ArrowRight size={14} />}
                  Approve → Build
                </button>
              ) : inBuild ? (
                <button
                  onClick={advance}
                  disabled={advancing}
                  title="Approve the build / eval and advance to Done"
                  className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
                >
                  {advancing ? <Loader2 size={14} className="animate-spin" /> : <ArrowRight size={14} />}
                  Approve → Done
                </button>
              ) : inDone && !completed ? (
                <button
                  onClick={complete}
                  disabled={advancing}
                  title="Complete — archive the execution trace to a file, then free the session + run rows"
                  className="inline-flex items-center gap-1.5 rounded-md bg-success px-3 py-1.5 text-xs font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
                >
                  {advancing ? <Loader2 size={14} className="animate-spin" /> : <Archive size={14} />}
                  Complete &amp; archive
                </button>
              ) : completed ? (
                <span className="inline-flex items-center gap-1.5 text-xs text-success">
                  <Check size={14} /> Completed · trace archived
                </span>
              ) : null}
            </>
          )}
          {inPlan && !running && (
            <button
              onClick={() => {
                onDelete(it)
                onClose()
              }}
              title="Delete — hard-removes this item, its session, and its inbox row"
              className="ml-auto inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-faint transition hover:text-danger"
            >
              <Trash2 size={14} /> Drop
            </button>
          )}
        </div>
    </Modal>
  )
}

// The run telemetry line — full detail (model · context fill · total tokens), the data that
// came off the card face.
function RunMeta({ it }: { it: WorkItem }) {
  const bits = [
    it.model && fmtModel(it.model),
    it.context_pct != null && `ctx ${it.context_pct}%`,
    (it.total_tokens ?? 0) > 0 && `Σ ${fmtTokens(it.total_tokens ?? 0)} tok`,
  ].filter(Boolean)
  if (!bits.length) return null
  return <div className="mt-1 text-[11px] text-faint">{bits.join(' · ')}</div>
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

// The Artifacts tab — the call-trail of tools / sub-agents / skills the item's runs invoked,
// grouped by run (newest run first), in call order within each run.
// Each call gets a distinct icon + color so the trail is scannable. Resolved by tool NAME first
// (Read/Glob/Grep/Bash/Agent/…), then by kind (skill/subagent/mcp) for anything else.
const CALL_STYLE: Record<string, { icon: typeof Terminal; color: string }> = {
  read: { icon: BookOpen, color: '#60a5fa' },        // blue
  glob: { icon: FolderSearch, color: '#a78bfa' },    // purple
  grep: { icon: Search, color: '#fbbf24' },          // amber
  bash: { icon: SquareTerminal, color: '#34d399' },  // emerald
  edit: { icon: FilePen, color: '#fb7185' },         // rose
  write: { icon: FilePen, color: '#fb7185' },
  agent: { icon: Bot, color: '#818cf8' },            // indigo — the agent (Task) robot head
  task: { icon: Bot, color: '#818cf8' },
  webfetch: { icon: Globe, color: '#22d3ee' },       // cyan
  websearch: { icon: Globe, color: '#22d3ee' },
}
const KIND_STYLE: Record<string, { icon: typeof Terminal; color: string }> = {
  subagent: { icon: Bot, color: '#818cf8' },
  skill: { icon: Sparkles, color: '#e0a35a' },       // warn amber
  mcp: { icon: Wrench, color: '#5fe3b3' },            // success
  tool: { icon: Terminal, color: '#8b93a1' },        // muted
}
function callVisual(a: RunArtifact) {
  return CALL_STYLE[(a.name ?? '').toLowerCase()] ?? KIND_STYLE[a.kind] ?? KIND_STYLE.tool
}

function ArtifactsTab({ artifacts, archived }: { artifacts: RunArtifact[]; archived: string | null }) {
  if (artifacts.length === 0) {
    // Completed items have their rows freed → fall back to the archived execution.md snapshot.
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
    return <Empty>No calls recorded yet — they’re captured while an agent works this item (plan or chat).</Empty>
  }
  // Already ordered (newest run first, calls in order within a run); group consecutively by run_id.
  const groups: { run: number | null; items: RunArtifact[] }[] = []
  for (const a of artifacts) {
    const g = groups[groups.length - 1]
    if (g && g.run === (a.run_id ?? null)) g.items.push(a)
    else groups.push({ run: a.run_id ?? null, items: [a] })
  }
  return (
    <div className="space-y-4">
      {groups.map((g, gi) => (
        <div key={gi}>
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-faint">
            {g.run != null ? `Run #${g.run}` : 'Unattached'} · {g.items.length} call{g.items.length === 1 ? '' : 's'}
          </div>
          <ol className="space-y-0.5">
            {g.items.map((a) => {
              const { icon: Icon, color } = callVisual(a)
              return (
                <li key={a.id} className="flex items-baseline gap-2 text-xs">
                  <span className="w-5 shrink-0 text-right font-mono text-[10px] text-faint">{a.seq}</span>
                  <Icon size={12} className="shrink-0 translate-y-0.5" style={{ color }} />
                  <span className="min-w-0 flex-1 truncate" title={`${a.name}${a.description ? ' - ' + a.description : ''}`}>
                    <span className="text-fg">{a.name}</span>
                    {a.description && <span className="text-faint"> - {a.description}</span>}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-faint">{fmtLocal(a.created_at)}</span>
                </li>
              )
            })}
          </ol>
        </div>
      ))}
    </div>
  )
}
