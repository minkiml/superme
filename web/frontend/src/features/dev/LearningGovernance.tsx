import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Sparkles, Check, X, Trash2, Loader2, RefreshCw, Brain,
  Bot, Pencil, Save, FileText, Layers, Send, FileCode, Gauge, Terminal,
} from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import Toggle from '@/ui/Toggle'
import ArtifactTabs from '@/ui/ArtifactTabs'
import SectionHeader from '@/ui/SectionHeader'
import SourceEditor from '@/ui/SourceEditor'
import {
  getProposals, approveProposal, updateStagedArtifact, publishProposal, rejectProposal, dropProposal,
  getMemoryStats, runDistill, getProposalExecution,
  getPublished, togglePublished, deletePublished, getPublishedFile, savePublishedFile,
  type MemoryProposal, type ProposalCandidate, type MemoryStats,
  type EvalReport, type PublishedItem, type PublishedForm, type ProposalStep,
} from '@/lib/api'
import { fmtLocalDate, fmtLocal } from '@/lib/format'
import { Empty } from './common'

// Learning governance surfaces (PRD §4.10.4) — the tier-C pipeline hosted in the Dev workspace's
// Learning tab: `MemoryGovernance` (candidate gauges + the two-gate distill→forge→publish review
// queue, each proposal carrying its execution trace) and `PublishedInventory` (the live inventory
// of learned artifacts). `PublishedFileModal` is exported for Foundations' skills/agents surface.
// Was split out of the old ManageHarness.
// --- Published inventory (#6 runtime management) --------------------------------------------
// The LEARNED artifacts the owner published into the live harness, keyed by their proposal (so
// SuperMe's shipped skills never appear). Disable suspends one without losing it (constitution =
// frontmatter flag; skill/agent = moved into a `.disabled/` shadow); delete removes it for good.
// Effective on the next dev turn.
const PUB_FORM_META: Record<PublishedForm, { label: string; icon: typeof Bot; blurb: string }> = {
  constitution: { label: 'Constitution', icon: FileText, blurb: 'always-on rules in the system prompt' },
  skill: { label: 'Skills', icon: Sparkles, blurb: 'loaded via the dev plugin' },
  agent: { label: 'Agents', icon: Bot, blurb: 'delegate workers' },
}
const pubScopeLabel = (s: string) =>
  s === 'universal_dev' ? 'universal' : s === 'repo_dev' ? 'repo' : s

export function PublishedInventory({ contextId }: { contextId: string }) {
  const [items, setItems] = useState<PublishedItem[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)        // proposal_id mid-flight
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [open, setOpen] = useState<PublishedItem | null>(null) // item open in the preview/edit modal
  const [form, setForm] = useState<PublishedForm>('constitution')

  const load = useCallback(() => {
    getPublished(contextId)
      .then((d) => setItems(d.published))
      .catch((e) => setErr(String(e)))
  }, [contextId])
  useEffect(() => { load() }, [load])

  async function toggle(it: PublishedItem) {
    setBusy(it.proposal_id); setErr(null)
    try { await togglePublished(it.proposal_id, !it.enabled, contextId); load() }
    catch (e) { setErr(String(e)) } finally { setBusy(null) }
  }
  async function remove(it: PublishedItem) {
    setBusy(it.proposal_id); setErr(null)
    try { await deletePublished(it.proposal_id, contextId); setConfirmDel(null); load() }
    catch (e) { setErr(String(e)) } finally { setBusy(null) }
  }

  if (err && !items) return <div className="text-sm text-danger">Could not load: {err}</div>
  if (!items) return <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>

  const present = items.filter((i) => i.present)
  const forms: PublishedForm[] = ['constitution', 'skill', 'agent']
  const rows = present.filter((i) => i.form === form)
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-muted">
          Artifacts the learning loop has published into the live harness. Click one to preview or
          edit it; disable to suspend it, or delete to remove it from everywhere — each takes effect
          on the next dev turn.
        </p>
        <button
          onClick={load}
          className="flex shrink-0 items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg"
        >
          <RefreshCw size={13} /> Refresh
        </button>
      </div>
      {err && <div className="text-sm text-danger">{err}</div>}
      {present.length === 0 ? (
        <Empty>Nothing published yet — approve a forged artifact at gate 2 and it lands here.</Empty>
      ) : (
        // Constitution · Skills · Agents as tabs (matches Foundations + the Artifacts tab).
        <>
          <ArtifactTabs
            tint="dev"
            value={form}
            onChange={setForm}
            tabs={forms.map((f) => ({ key: f, label: PUB_FORM_META[f].label, icon: PUB_FORM_META[f].icon, count: present.filter((i) => i.form === f).length }))}
          />
          <p className="text-[12px] text-faint">{PUB_FORM_META[form].blurb}</p>
          {rows.length === 0 ? (
            <p className="text-[12px] text-faint">None published in this form.</p>
          ) : (
            <div className="space-y-2">
              {rows.map((it) => {
                const rowBusy = busy === it.proposal_id
                const confirming = confirmDel === it.proposal_id
                return (
                  <div key={it.proposal_id} className={`rounded-lg border border-line bg-surface ${it.enabled ? '' : 'opacity-60'}`}>
                    <div className="flex items-center gap-2 px-3.5 py-2.5">
                      <button onClick={() => setOpen(it)} className="group flex min-w-0 flex-1 items-center gap-1.5 text-left" title="Preview / edit">
                        <span className="min-w-0 truncate text-[14px] text-fg">{it.title}</span>
                        <span className="shrink-0 rounded bg-hover px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-faint">
                          {pubScopeLabel(it.scope)}
                        </span>
                        <Pencil size={11} className="shrink-0 text-faint opacity-0 transition group-hover:opacity-100" />
                      </button>
                      {rowBusy ? (
                        <Loader2 size={15} className="animate-spin text-muted" />
                      ) : (
                        <Toggle on={it.enabled} onChange={() => toggle(it)} onColor="bg-dev" title={it.enabled ? 'Disable' : 'Enable'} />
                      )}
                      {!confirming && (
                        <button
                          onClick={() => setConfirmDel(it.proposal_id)}
                          disabled={rowBusy}
                          title="Delete from everywhere"
                          className="shrink-0 rounded p-1 text-muted hover:text-danger disabled:opacity-50"
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                    {confirming && (
                      <div className="flex items-center gap-2 border-t border-line px-3.5 py-1.5 text-[11px]">
                        <span className="text-muted">Delete everywhere?</span>
                        <button onClick={() => remove(it)} disabled={rowBusy} className="ml-auto rounded bg-danger/10 px-2 py-0.5 text-danger hover:bg-danger/20 disabled:opacity-50">Delete</button>
                        <button onClick={() => setConfirmDel(null)} className="rounded px-1.5 py-0.5 text-muted hover:bg-hover">Cancel</button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
      {open && (
        <PublishedFileModal
          item={open}
          contextId={contextId}
          onClose={() => setOpen(null)}
          onSaved={() => { setOpen(null); load() }}
          onGovernanceChange={load}
        />
      )}
    </div>
  )
}

// Preview + edit one published artifact's raw markdown (constitution / SKILL.md / agent.md). Loads
// the file, renders it; "Edit" swaps to a textarea; "Save" writes it back (next dev turn).
export function PublishedFileModal({ item, contextId, onClose, onSaved, onGovernanceChange, showScope = true }: {
  item: PublishedItem; contextId: string; onClose: () => void; onSaved: () => void
  onGovernanceChange?: () => void // called after an enable/disable or delete so the opener can refresh
  showScope?: boolean // hide the scope chip where it's redundant (Foundations = always universal)
}) {
  const [content, setContent] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [enabled, setEnabled] = useState(item.enabled)
  const [confirmDel, setConfirmDel] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    getPublishedFile(item.proposal_id, contextId)
      .then((f) => { if (alive) { setContent(f.content); setDraft(f.content) } })
      .catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [item.proposal_id, contextId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && !editing && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, editing])

  const Icon = PUB_FORM_META[item.form].icon
  const body = content ? content.replace(/^---\n[\s\S]*?\n---\n?/, '') : ''

  async function save() {
    setBusy(true); setErr(null)
    try { await savePublishedFile(item.proposal_id, draft, contextId); onSaved() }
    catch (e) { setErr(String(e)); setBusy(false) }
  }
  async function toggleEnabled() {
    setBusy(true); setErr(null)
    try { await togglePublished(item.proposal_id, !enabled, contextId); setEnabled(!enabled); onGovernanceChange?.() }
    catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  async function del() {
    setBusy(true); setErr(null)
    try { await deletePublished(item.proposal_id, contextId); onGovernanceChange?.(); onClose() }
    catch (e) { setErr(String(e)); setBusy(false) }
  }

  return (
    <Modal onClose={onClose} column maxW={editing ? "max-w-4xl" : "max-w-3xl"} dismissable={!editing}>
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-3">
          <Icon size={15} className="text-muted" />
          <span className="text-sm font-semibold text-fg">{item.title}</span>
          <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-warn">learned</span>
          {showScope && (
            <span className="rounded bg-hover px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-faint">{pubScopeLabel(item.scope)}</span>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            {!editing && (
              <>
                <Toggle on={enabled} onChange={() => toggleEnabled()} onColor="bg-dev" disabled={busy} title={enabled ? 'Disable' : 'Enable'} />
                {confirmDel ? (
                  <span className="flex items-center gap-1">
                    <button onClick={del} disabled={busy} className="rounded bg-danger/10 px-2 py-1 text-[11px] text-danger hover:bg-danger/20 disabled:opacity-50">Delete</button>
                    <button onClick={() => setConfirmDel(false)} className="rounded px-1.5 py-1 text-[11px] text-muted hover:bg-hover">Cancel</button>
                  </span>
                ) : (
                  <button onClick={() => setConfirmDel(true)} disabled={busy} title="Delete from everywhere"
                    className="rounded p-1 text-muted hover:bg-hover hover:text-danger disabled:opacity-50">
                    <Trash2 size={13} />
                  </button>
                )}
                <span className="mx-0.5 h-4 w-px bg-line" />
              </>
            )}
            {!editing ? (
              <button onClick={() => { setDraft(content ?? ''); setEditing(true) }} disabled={content === null}
                className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50">
                <Pencil size={12} /> Edit
              </button>
            ) : (
              <>
                <button onClick={() => { setEditing(false); setDraft(content ?? '') }} disabled={busy}
                  className="rounded-md border border-line px-2 py-1 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50">Cancel</button>
                <button onClick={save} disabled={busy || draft === content || !draft.trim()}
                  className="flex items-center gap-1 rounded-md bg-accent px-2 py-1 text-xs text-on-accent hover:opacity-90 disabled:opacity-50">
                  {busy ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} Save
                </button>
              </>
            )}
            <button onClick={onClose} className="rounded p-1 text-muted hover:bg-hover hover:text-fg"><X size={16} /></button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {err && <div className="mb-2 text-sm text-danger">{err}</div>}
          {content === null ? (
            <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>
          ) : editing ? (
            <SourceEditor value={draft} onChange={setDraft} />
          ) : (
            <Markdown text={body} variant="doc" tone="dev" />
          )}
        </div>
    </Modal>
  )
}

// --- Memory governance ----------------------------------------------------------------------

export function MemoryGovernance({ contextId }: { contextId: string }) {
  const [props, setProps] = useState<MemoryProposal[]>([])
  const [stats, setStats] = useState<MemoryStats | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [distilling, setDistilling] = useState(false)
  const [popup, setPopup] = useState<null | 'candidates' | 'knowledge'>(null)
  const poll = useRef<ReturnType<typeof setInterval> | null>(null)

  // load() is referenced by the poll loop, which is itself created before load() is defined;
  // a ref breaks that cycle without recreating the interval every render.
  const loadRef = useRef<() => void>(() => {})

  // Poll the server's distilling truth until it clears, then refetch the queue. Guarded so a
  // remount (which re-syncs from stats) can't spawn a second interval. The spinner is driven
  // ENTIRELY by server state, never a local shadow — so navigating away + back restores it.
  const startPolling = useCallback(() => {
    if (poll.current) return
    const started = Date.now()
    poll.current = setInterval(async () => {
      try {
        const s = await getMemoryStats(contextId)
        setStats(s)
        if (!s.distilling || Date.now() - started > 180_000) {
          if (poll.current) { clearInterval(poll.current); poll.current = null }
          setDistilling(false)
          loadRef.current()
        }
      } catch {
        /* transient — keep polling until the timeout */
      }
    }, 1500)
  }, [contextId])

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([getProposals(contextId), getMemoryStats(contextId)])
      .then(([p, s]) => {
        // Keep every OPEN proposal in the queue — proposed (gate 1), writing (write run in flight),
        // and drafted (gate 2, awaiting publish). Terminal ones drop off.
        setProps(p.proposals.filter((x) => ['proposed', 'writing', 'drafted'].includes(x.status)))
        setStats(s)
        setErr(null)
        // Re-sync to server truth: if a distill is running (e.g. we just remounted mid-run),
        // restore the spinner + resume polling; otherwise make sure we're cleared.
        if (s.distilling) { setDistilling(true); startPolling() }
        else setDistilling(false)
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false))
  }, [contextId, startPolling])

  useEffect(() => { loadRef.current = load }, [load])

  useEffect(() => {
    load()
  }, [load])

  // Stop polling if the component unmounts mid-distill.
  useEffect(() => () => { if (poll.current) { clearInterval(poll.current); poll.current = null } }, [])

  // "Run distill": fire the background pass, then let the (server-truth) poll loop drive the
  // spinner until it clears + refetch. `already_running` still tracks the in-flight pass.
  const onDistill = async () => {
    try {
      const r = await runDistill(contextId)
      if (r.status === 'no_candidates') return
      setDistilling(true)
      startPolling()
    } catch (e) {
      setErr(String(e))
    }
  }

  // Refetch when the owner returns to the tab/window — proposals can land from a background
  // distill turn (or the daemon) while this surface stays mounted, so the mount-only fetch
  // would otherwise show a stale queue until a manual Refresh.
  useEffect(() => {
    const refetch = () => load()
    const onVisible = () => document.visibilityState === 'visible' && load()
    window.addEventListener('focus', refetch)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.removeEventListener('focus', refetch)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load])

  // While any proposal is mid-forge (`writing`), poll the queue so its card narrates the live phase
  // and flips to `drafted` the moment the run finishes.
  const anyWriting = props.some((p) => p.status === 'writing')
  useEffect(() => {
    if (!anyWriting) return
    const t = setInterval(() => loadRef.current(), 2500)
    return () => clearInterval(t)
  }, [anyWriting])

  const candCount = stats?.candidates.total ?? 0
  const knowCount = (stats?.knowledge.facts_total ?? 0) + (stats?.knowledge.artifacts_total ?? 0)

  return (
    <div>
      {/* Stat tiles — pipeline gauges. Click a tile for the drill-down popup. */}
      <div className="mb-4 grid grid-cols-2 gap-3">
        <StatTile
          icon={Sparkles}
          label="Candidates to distill"
          value={candCount}
          sub={`${stats?.candidates.pending_proposals ?? 0} pending proposal(s)`}
          onClick={() => stats && setPopup('candidates')}
        />
        <StatTile
          icon={Brain}
          label="Learned knowledge"
          value={knowCount}
          sub={`${stats?.knowledge.facts_enabled ?? 0} on · ${stats?.knowledge.facts_disabled ?? 0} off · ${stats?.knowledge.artifacts_total ?? 0} artifacts`}
          onClick={() => stats && setPopup('knowledge')}
        />
      </div>

      <div className="mb-4 flex items-center gap-2">
        <span className="text-xs text-faint">{props.length} pending</span>
        <button
          onClick={onDistill}
          disabled={distilling || candCount === 0}
          title={candCount === 0 ? 'No candidates to distill' : 'Run distill over the candidate pool'}
          className="ml-auto inline-flex items-center gap-1 rounded-md border border-accent/40 bg-accent/10 px-2 py-1 text-[12px] text-accent-text transition hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {distilling ? (
            <AgentWorking size={13}>Distilling…</AgentWorking>
          ) : (
            <><Sparkles size={13} /> Run distill</>
          )}
        </button>
        <button
          onClick={load}
          title="Refresh"
          className="rounded p-1.5 text-muted transition hover:bg-hover hover:text-fg"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
        </button>
      </div>

      {popup && stats && (
        <StatPopup kind={popup} stats={stats} onClose={() => setPopup(null)} />
      )}

      {err && (
        <div className="mb-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {err}
        </div>
      )}

      {/* The review queue is the whole Learning surface now — the live published inventory lives in
          the Published tab; the legacy dev/memory "Applied facts" store is retired (WI-8). */}
      {props.length > 0 ? (
        <ReviewQueue proposals={props} contextId={contextId} onChange={load} />
      ) : (
        <Empty>No proposals in review. Run distill over the candidate pool to generate some.</Empty>
      )}
    </div>
  )
}

// --- stat tiles + drill-down popup ----------------------------------------------------------

function StatTile({
  icon: Icon,
  label,
  value,
  sub,
  onClick,
}: {
  icon: typeof Brain
  label: string
  value: number
  sub: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="group flex items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-left shadow-sm transition hover:border-accent hover:bg-hover"
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent-text">
        <Icon size={17} />
      </div>
      <div className="min-w-0">
        <div className="flex items-baseline gap-1.5">
          <span className="text-xl font-semibold text-fg">{value}</span>
          <span className="truncate text-[12px] text-muted">{label}</span>
        </div>
        <div className="truncate text-[11px] text-faint">{sub}</div>
      </div>
    </button>
  )
}

function Chips({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, n]) => n > 0)
  if (!entries.length) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([k, n]) => (
        <span key={k} className="rounded-full bg-hover px-2 py-0.5 text-[11px] text-muted">
          {k} <span className="text-faint">· {n}</span>
        </span>
      ))}
    </div>
  )
}

function StatPopup({
  kind,
  stats,
  onClose,
}: {
  kind: 'candidates' | 'knowledge'
  stats: MemoryStats
  onClose: () => void
}) {
  const isCand = kind === 'candidates'
  return (
    <Modal
      onClose={onClose}
      maxW="max-w-2xl"
      title={
        <span className="flex items-center gap-2">
          {isCand ? <Sparkles size={16} className="text-accent-text" /> : <Brain size={16} className="text-accent-text" />}
          {isCand ? 'Candidates to distill' : 'Learned knowledge'}
        </span>
      }
    >
        <div className="max-h-[70vh] space-y-3 overflow-y-auto px-4 py-3">
          {isCand ? (
            <>
              <div className="flex items-center gap-3 text-[12px] text-muted">
                <span><b className="text-fg">{stats.candidates.total}</b> un-distilled</span>
                <span>·</span>
                <span><b className="text-fg">{stats.candidates.pending_proposals}</b> pending proposal(s)</span>
              </div>
              <Chips counts={stats.candidates.by_form} />
              {stats.candidates.items.length === 0 ? (
                <Empty>The capture pool is empty — nothing to distill.</Empty>
              ) : (
                <div className="space-y-1.5">
                  {stats.candidates.items.map((c) => (
                    <div key={c.id} className="rounded-lg bg-surface px-3 py-2 shadow-sm">
                      <div className="text-[13px] text-fg">{c.signal}</div>
                      <div className="mt-1 flex items-center gap-2 text-[10px] text-faint">
                        {c.form_hint && <span className="rounded bg-hover px-1.5 py-0.5">{c.form_hint}</span>}
                        {c.scope_hint && <span className="rounded bg-hover px-1.5 py-0.5">{c.scope_hint}</span>}
                        {c.source && <span>{c.source}</span>}
                        {c.captured_at && <span>· {fmtLocalDate(c.captured_at)}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3 text-[12px] text-muted">
                <span><b className="text-fg">{stats.knowledge.facts_total}</b> facts</span>
                <span>·</span>
                <span>{stats.knowledge.facts_enabled} on · {stats.knowledge.facts_disabled} off</span>
                <span>·</span>
                <span>{stats.knowledge.artifacts_total} artifacts {stats.knowledge.artifacts_reserved && <span className="text-faint">(reserved)</span>}</span>
              </div>
              <Chips counts={stats.knowledge.facts_by_type} />
              {stats.knowledge.items.length === 0 ? (
                <Empty>No applied facts yet.</Empty>
              ) : (
                <div className="space-y-1.5">
                  {stats.knowledge.items.map((f) => (
                    <div key={f.name} className={`rounded-lg bg-surface px-3 py-2 shadow-sm ${f.enabled ? '' : 'opacity-50'}`}>
                      <div className="flex items-baseline gap-2">
                        <span className="truncate text-[13px] text-fg">{f.description || f.name}</span>
                        <span className="ml-auto shrink-0 rounded bg-hover px-1.5 py-0.5 text-[10px] text-muted">{f.type}</span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 font-mono text-[10px] text-faint">
                        <span>{f.name}</span>
                        {f.source && <span>· {f.source}</span>}
                        {!f.enabled && <span>· disabled</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
    </Modal>
  )
}

// --- the tier-C review queue (moved from DevMemory — the gate lives in Manage Harness) -------

const FORM_TINT: Record<string, string> = {
  fact: 'text-success',
  skill: 'text-accent-text',
  agent: 'text-accent-text',
  contract: 'text-warn',
  core_candidate: 'text-warn',
}
const RECALL_TYPES = ['reference', 'convention', 'decision', 'rule'] as const

function ReviewQueue({
  proposals,
  contextId,
  onChange,
}: {
  proposals: MemoryProposal[]
  contextId: string
  onChange: () => void
}) {
  const [open, setOpen] = useState<MemoryProposal | null>(null)
  // The stats tile counts `pending` = every OPEN proposal (proposed + writing + drafted). This queue
  // shows exactly that set, so we break it down by badge state here (proposed = gate-1, drafted =
  // gate-2) rather than re-using the word "pending" — the sub-counts sum to the tile's pending total.
  const proposed = proposals.filter((p) => p.status === 'proposed').length
  const drafted = proposals.filter((p) => p.status === 'drafted').length
  const writing = proposals.filter((p) => p.status === 'writing').length
  return (
    <section className="mb-6 rounded-xl border border-line p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-accent-text">
        <Sparkles size={12} /> Review queue <span className="text-faint">
          · {proposed} proposed{writing ? ` · ${writing} writing` : ''}{drafted ? ` · ${drafted} drafted` : ''}
        </span>
      </div>
      <p className="mb-3 text-[11px] text-faint">
        The <code className="text-muted">distill</code> agent proposed these from captured candidates.
        Click one to review its details and the candidates behind it, then accept, reject, or drop.
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {proposals.map((p) => (
          <ProposalCard key={p.id} p={p} onOpen={() => setOpen(p)} />
        ))}
      </div>
      {open && (
        <ProposalModal
          p={open}
          contextId={contextId}
          onClose={() => setOpen(null)}
          onDone={() => {
            setOpen(null)
            onChange()
          }}
        />
      )}
    </section>
  )
}

// One proposal as a rectangular card (mirrors the System dashboard repo cards): status badge on top,
// then title, then form/scope/confidence footer. Detail lives in the popup. While the proposal is
// being forged (`writing`), the badge becomes a live phase indicator (forging → evaluating → polishing).
function ProposalCard({ p, onOpen }: { p: MemoryProposal; onOpen: () => void }) {
  const writing = p.status === 'writing'
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!writing) {
      setElapsed(0)
      return
    }
    const t = setInterval(() => setElapsed((e) => e + 1), 1000)
    return () => clearInterval(t)
  }, [writing])
  return (
    <button
      onClick={onOpen}
      className={`flex h-full flex-col gap-2 rounded-xl border bg-surface px-3 py-2.5 text-left shadow-sm transition hover:bg-hover ${
        writing ? 'border-accent' : 'border-line hover:border-accent'
      }`}
    >
      <div className="flex items-center gap-1.5">
        <span className={`font-mono text-[10px] uppercase ${FORM_TINT[p.output_form] ?? 'text-muted'}`}>
          {p.output_form}
        </span>
        <span className="text-[10px] text-faint">· {p.target_scope}</span>
        {writing ? (
          <AgentWorking size={12} className="ml-auto text-[10px] font-medium">{forgePhaseShort(elapsed)}</AgentWorking>
        ) : (
          <StageBadge status={p.status} className="ml-auto" />
        )}
      </div>
      <span className="line-clamp-2 text-sm font-medium leading-snug text-fg">{p.title}</span>
      <div className="mt-auto flex items-center gap-1.5 text-[10px] text-faint">
        {p.confidence && <span>{p.confidence} confidence</span>}
        {p.cluster && (
          <span className="ml-auto rounded bg-hover px-1.5 py-0.5 font-mono text-muted">{p.cluster}</span>
        )}
      </div>
    </button>
  )
}

// The proposal review popup (mirrors WorkItemModal) — the two-gate decision surface. Tabs:
// Proposal (summary · body · spec · clarifications), Candidates (provenance), and — once the write
// The per-proposal execution trace (reused from the events table, keyed on meta.proposal_id) — a
// lifecycle timeline: filed by distill → approved → forge run start/end → artifact edits → publish.
const STEP_META: Record<string, { label: string; tint: string }> = {
  'memory.proposed': { label: 'Filed', tint: 'text-muted' },
  'memory.merged': { label: 'Merged in', tint: 'text-accent-text' },
  'memory.approved': { label: 'Approved', tint: 'text-accent-text' },
  'write.start': { label: 'Forge started', tint: 'text-accent-text' },
  'write.end': { label: 'Forge finished', tint: 'text-fg' },
  'memory.artifact_edited': { label: 'Artifact edited', tint: 'text-warn' },
  'memory.published': { label: 'Published', tint: 'text-success' },
  'memory.rejected': { label: 'Rejected', tint: 'text-danger' },
  'proposal.dropped': { label: 'Dropped', tint: 'text-danger' },
  'memory.dropped': { label: 'Dropped', tint: 'text-danger' }, // legacy rows (pre-split); kept so old trails still render
}

function ExecutionTrace({ steps }: { steps: ProposalStep[] | null }) {
  if (steps === null)
    return <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>
  if (!steps.length) return <Empty>No execution steps recorded yet.</Empty>
  return (
    <PSection title="Execution trace">
      <ol className="space-y-2">
        {steps.map((s, i) => {
          const m = STEP_META[s.kind] ?? { label: s.kind, tint: 'text-muted' }
          return (
            <li key={i} className="rounded-md border border-line bg-app px-3 py-2">
              <div className="flex items-center gap-2">
                <span className={`text-[11px] font-medium uppercase tracking-wide ${m.tint}`}>{m.label}</span>
                <span className="text-[10px] text-faint">· {s.actor}</span>
                {s.created_at && <span className="ml-auto text-[10px] text-faint">{fmtLocal(s.created_at)}</span>}
              </div>
              <p className="mt-0.5 text-[12px] leading-relaxed text-muted">{s.summary}</p>
            </li>
          )
        })}
      </ol>
    </PSection>
  )
}

// phase has run — Artifact (the staged final content). Footer drives the lifecycle:
//   proposed → Approve (gate 1, answer blocking Qs) → writing (poll) → drafted → Publish (gate 2).
function ProposalModal({
  p,
  contextId,
  onClose,
  onDone,
}: {
  p: MemoryProposal
  contextId: string
  onClose: () => void
  onDone: () => void
}) {
  const [cur, setCur] = useState<MemoryProposal>(p)
  const [tab, setTab] = useState<'proposal' | 'candidates' | 'artifact' | 'eval' | 'execution'>('proposal')
  const [steps, setSteps] = useState<ProposalStep[] | null>(null)
  const [busy, setBusy] = useState<null | 'approve' | 'publish' | 'reject' | 'drop' | 'saveArtifact'>(null)
  const [err, setErr] = useState<string | null>(null)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [elapsed, setElapsed] = useState(0) // seconds the write run has been in flight (drives phase text)
  const [editingArtifact, setEditingArtifact] = useState(false)
  const [artifactDraft, setArtifactDraft] = useState('')

  const cands = cur.candidates ?? []
  const fields = cur.fields && typeof cur.fields === 'object' ? cur.fields : null
  const clar = cur.clarifications ?? []
  const status = cur.status
  const writing = status === 'writing'
  const drafted = status === 'drafted'
  const proposed = status === 'proposed'
  const blockingUnanswered = clar.some((q) => q.blocking && !((answers[q.question] ?? '').trim()))

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Tick a seconds counter while the write run is in flight, so the footer can narrate the phase.
  useEffect(() => {
    if (!writing) {
      setElapsed(0)
      return
    }
    const t = setInterval(() => setElapsed((e) => e + 1), 1000)
    return () => clearInterval(t)
  }, [writing])

  // While the write run is in flight, poll the proposal until it leaves `writing` (→ drafted, or
  // back to proposed if the run failed). Then surface the staged artifact.
  useEffect(() => {
    if (!writing) return
    let alive = true
    const t = setInterval(async () => {
      try {
        const { proposals } = await getProposals(contextId)
        const fresh = proposals.find((x) => x.id === cur.id)
        if (alive && fresh) {
          setCur(fresh)
          if (fresh.status === 'drafted') setTab('artifact')
        }
      } catch {
        /* transient */
      }
    }, 2000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [writing, contextId, cur.id])

  const approve = async () => {
    setBusy('approve')
    setErr(null)
    try {
      await approveProposal(cur.id, contextId, Object.keys(answers).length ? answers : undefined)
      setCur({ ...cur, status: 'writing' }) // optimistic → triggers the poll
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(null)
    }
  }
  const publish = async () => {
    setBusy('publish')
    setErr(null)
    try {
      await publishProposal(cur.id, contextId)
      onDone()
    } catch (e) {
      setErr(String(e))
      setBusy(null)
    }
  }
  const saveArtifact = async () => {
    setBusy('saveArtifact')
    setErr(null)
    try {
      const { proposal } = await updateStagedArtifact(cur.id, artifactDraft, contextId)
      setCur(proposal)
      setEditingArtifact(false)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(null)
    }
  }
  const act = async (kind: 'reject' | 'drop') => {
    setBusy(kind)
    setErr(null)
    try {
      if (kind === 'reject') await rejectProposal(cur.id, contextId)
      else await dropProposal(cur.id, contextId)
      onDone()
    } catch (e) {
      setErr(String(e))
      setBusy(null)
    }
  }

  // Execution trace — fetch when the tab opens; refetch as the proposal advances (the timeline
  // grows: approve → write.start/end → edits → publish).
  useEffect(() => {
    if (tab !== 'execution') return
    let alive = true
    setSteps(null)
    getProposalExecution(cur.id, contextId)
      .then((d) => { if (alive) setSteps(d.steps) })
      .catch(() => { if (alive) setSteps([]) })
    return () => { alive = false }
  }, [tab, cur.id, cur.status, contextId])

  const TABS_M = [
    ['proposal', 'Proposal', FileText] as const,
    ['candidates', 'Candidates', Layers] as const,
    ...(cur.staged_artifact ? [['artifact', 'Artifact', FileCode] as const] : []),
    ...(cur.eval_report ? [['eval', 'Eval', Gauge] as const] : []),
    ['execution', 'Execution', Terminal] as const,
  ]

  return (
    <Modal onClose={onClose} contain maxW="max-w-3xl" z="z-40" dismissable={!editingArtifact}>
      <div className="flex h-[80vh] max-h-[680px] w-full flex-col">
        {/* Header */}
        <div className="flex shrink-0 items-start gap-2 border-b border-line px-4 py-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[10px] text-faint">#{cur.id}</span>
              <span className={`font-mono text-[10px] uppercase ${FORM_TINT[cur.output_form] ?? 'text-muted'}`}>
                {cur.output_form}
              </span>
              <span className="text-[10px] text-faint">· {cur.target_scope}</span>
              {cur.confidence && <span className="text-[10px] text-faint">· {cur.confidence} confidence</span>}
              <StageBadge status={status} />
              {cur.cluster && (
                <span className="rounded bg-hover px-1.5 py-0.5 font-mono text-[10px] text-muted">{cur.cluster}</span>
              )}
            </div>
            <h2 className="mt-1 text-[15px] font-semibold leading-snug text-fg">{cur.title}</h2>
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

        {/* Tabs */}
        <div className="flex shrink-0 gap-1 border-b border-line px-4">
          {TABS_M.map(([id, label, Icon]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition ${
                tab === id ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg'
              }`}
            >
              <Icon size={14} /> {label}
              {id === 'candidates' && cands.length > 0 && (
                <span className="rounded-full bg-hover px-1.5 text-[10px] text-muted">{cands.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
          {tab === 'execution' ? (
            <ExecutionTrace steps={steps} />
          ) : tab === 'eval' ? (
            <EvalReportView report={cur.eval_report!} />
          ) : tab === 'artifact' ? (
            <PSection title={`Staged artifact${cur.staged_path ? ` → ${relArtifactPath(cur.staged_path)}` : ''}`}>
              <div className="mb-1.5 flex items-center gap-1.5">
                {drafted && !editingArtifact && (
                  <button
                    onClick={() => { setArtifactDraft(cur.staged_artifact ?? ''); setEditingArtifact(true) }}
                    className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-muted hover:bg-hover hover:text-fg"
                  >
                    <Pencil size={11} /> Edit
                  </button>
                )}
                {editingArtifact && (
                  <>
                    <button
                      onClick={saveArtifact}
                      disabled={busy !== null || artifactDraft.trim() === (cur.staged_artifact ?? '').trim() || !artifactDraft.trim()}
                      className="flex items-center gap-1 rounded-md bg-accent px-2 py-1 text-[11px] font-medium text-on-accent hover:opacity-90 disabled:opacity-50"
                    >
                      {busy === 'saveArtifact' ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />} Save
                    </button>
                    <button
                      onClick={() => setEditingArtifact(false)}
                      disabled={busy === 'saveArtifact'}
                      className="rounded-md border border-line px-2 py-1 text-[11px] text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
                    >
                      Cancel
                    </button>
                    <span className="text-[10px] text-faint">edits save to the draft; publish writes it to disk</span>
                  </>
                )}
              </div>
              {editingArtifact ? (
                <textarea
                  value={artifactDraft}
                  onChange={(e) => setArtifactDraft(e.target.value)}
                  spellCheck={false}
                  className="h-[42vh] w-full resize-none rounded-md border border-line bg-app px-3 py-2.5 font-mono text-[12px] leading-relaxed text-fg outline-none focus:border-accent"
                />
              ) : (
                <StagedArtifactPreview content={cur.staged_artifact ?? ''} />
              )}
            </PSection>
          ) : tab === 'candidates' ? (
            cands.length === 0 ? (
              <Empty>No source candidates linked.</Empty>
            ) : (
              <div className="space-y-2.5">
                {cands.map((c) => (
                  <CandidateBlock key={c.id} c={c} />
                ))}
              </div>
            )
          ) : (
            <>
              {cur.summary && (
                <PSection title="Summary">
                  <p className="whitespace-pre-line text-[13px] leading-relaxed text-fg">{cur.summary}</p>
                </PSection>
              )}
              <PSection title="Body">
                <div className="text-[13px] text-fg">
                  <Markdown text={cur.body} />
                </div>
              </PSection>
              {fields && Object.keys(fields).length > 0 && (
                <PSection title="Spec">
                  <dl className="space-y-1.5 rounded-md border border-line bg-app px-3 py-2.5">
                    {Object.entries(fields).map(([k, v]) => (
                      <div key={k} className="grid grid-cols-[7rem_1fr] gap-2 text-[12px]">
                        <dt className="font-mono text-faint">{k}</dt>
                        <dd className="text-fg">{typeof v === 'string' ? v : JSON.stringify(v)}</dd>
                      </div>
                    ))}
                  </dl>
                </PSection>
              )}
              {clar.length > 0 && (
                <PSection title="Clarifying questions">
                  <ul className="space-y-2">
                    {clar.map((q, i) => (
                      <li key={i} className="rounded-md border border-line bg-app px-2.5 py-2 text-[12px]">
                        <div className="flex items-center gap-1.5">
                          {q.blocking && (
                            <span className="rounded bg-warn/15 px-1 text-[9px] uppercase text-warn">blocking</span>
                          )}
                          <span className="text-fg">{q.question}</span>
                        </div>
                        {q.suggested && <div className="mt-1 text-faint">suggested: {q.suggested}</div>}
                        {proposed ? (
                          <input
                            value={answers[q.question] ?? ''}
                            onChange={(e) => setAnswers((a) => ({ ...a, [q.question]: e.target.value }))}
                            placeholder={q.suggested ? `default: ${q.suggested}` : 'your answer…'}
                            className="mt-1.5 w-full rounded border border-line bg-app px-2 py-1 text-[12px] text-fg outline-none focus:border-accent"
                          />
                        ) : (
                          cur.clarification_answers && (
                            <div className="mt-1 text-[12px] text-accent-text">
                              answered: {answerFor(cur.clarification_answers, q.question) || '—'}
                            </div>
                          )
                        )}
                      </li>
                    ))}
                  </ul>
                </PSection>
              )}
            </>
          )}
        </div>

        {/* Footer — the two-gate lifecycle */}
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-t border-line px-4 py-3">
          {err && <div className="w-full text-[11px] text-danger">{err}</div>}
          {writing ? (
            <AgentWorking size={14} className="text-[12px]">{forgePhase(cur.output_form, elapsed)}</AgentWorking>
          ) : drafted ? (
            <span className="text-[11px] text-faint">Review the staged artifact, then publish it to its live home.</span>
          ) : proposed ? (
            <span className="text-[11px] text-faint">
              Gate 1 — approve the intent to author the {cur.output_form}.
              {blockingUnanswered && ' Answer the blocking question(s) first.'}
            </span>
          ) : (
            <span className="text-[11px] text-faint">{status}</span>
          )}

          <div className="ml-auto flex items-center gap-2">
            {proposed && (
              <button
                onClick={approve}
                disabled={busy !== null || blockingUnanswered}
                title="Gate 1 — approve the intent; the write phase authors the artifact."
                className="inline-flex items-center gap-1 rounded-md bg-accent px-2.5 py-1 text-[12px] font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
              >
                {busy === 'approve' ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                Approve → write
              </button>
            )}
            {drafted && (
              <button
                onClick={publish}
                disabled={busy !== null || editingArtifact}
                title={editingArtifact ? 'Save or cancel your edit first.' : 'Gate 2 — write the staged artifact to its live operational home.'}
                className="inline-flex items-center gap-1 rounded-md bg-accent px-2.5 py-1 text-[12px] font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
              >
                {busy === 'publish' ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
                Publish
              </button>
            )}
            {!writing && (
              <>
                <button
                  onClick={() => act('reject')}
                  disabled={busy !== null}
                  title="The framing is off — re-queues the candidates for a later distill pass."
                  className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-[12px] text-muted transition hover:bg-hover hover:text-fg disabled:opacity-50"
                >
                  {busy === 'reject' ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                  Reject
                </button>
                <button
                  onClick={() => act('drop')}
                  disabled={busy !== null}
                  title="Noise — drop it and its candidates, stop suggesting it."
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12px] text-faint transition hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                >
                  {busy === 'drop' ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                  Drop
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </Modal>
  )
}

function StageBadge({ status, className = '' }: { status: string; className?: string }) {
  const tint =
    status === 'drafted' ? 'bg-accent-soft text-accent-text'
    : status === 'writing' ? 'bg-warn/15 text-warn'
    : 'bg-hover text-muted'
  return <span className={`rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wide ${tint} ${className}`}>{status}</span>
}

// Resolve the owner's stored answer for a clarifying question (dict or list shape).
function answerFor(
  answers: Record<string, string> | { question: string; answer: string }[],
  question: string,
): string {
  if (Array.isArray(answers)) return answers.find((a) => a.question === question)?.answer ?? ''
  return answers[question] ?? ''
}

function CandidateBlock({ c }: { c: ProposalCandidate }) {
  const ev = Array.isArray(c.evidence) ? c.evidence.join('\n') : c.evidence
  return (
    <div className="rounded-lg border border-line bg-app px-3 py-2.5">
      <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[10px] text-faint">
        <span className="font-mono">#{c.id}</span>
        {c.form_hint && <span className="rounded bg-hover px-1.5 py-0.5">{c.form_hint}</span>}
        {c.scope_hint && <span className="rounded bg-hover px-1.5 py-0.5">{c.scope_hint}</span>}
        {c.source && <span>{c.source}</span>}
        {c.captured_at && <span>· {fmtLocalDate(c.captured_at)}</span>}
      </div>
      <div className="text-[13px] text-fg">{c.signal}</div>
      {c.rationale && (
        <div className="mt-1 text-[12px] text-muted">
          <span className="text-faint">why: </span>
          {c.rationale}
        </div>
      )}
      {ev && <div className="mt-1 whitespace-pre-line text-[11px] italic text-faint">evidence: {ev}</div>}
    </div>
  )
}

function PSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <SectionHeader className="mb-1.5">{title}</SectionHeader>
      {children}
    </div>
  )
}

// Artifact homes all live under superme_agent/ — show the repo-relative path, not the long absolute one.
function relArtifactPath(p: string): string {
  const i = p.indexOf('/superme_agent/')
  return i >= 0 ? '.' + p.slice(i) : p
}

// Render a STAGED artifact (pre-publish — not yet a file on disk) the same way PublishedFileModal
// renders a live one: YAML frontmatter as a compact meta block, the markdown body as a real doc.
// So the gate-2 preview reads like the published artifact will, not a raw text dump.
function StagedArtifactPreview({ content }: { content: string }) {
  const fm = content.match(/^---\n([\s\S]*?)\n---\n?/)
  const front = fm ? fm[1] : ''
  const body = fm ? content.slice(fm[0].length) : content
  return (
    <div className="rounded-md border border-line bg-app px-3.5 py-3">
      {front && (
        <pre className="mb-3 overflow-x-auto whitespace-pre-wrap break-words rounded border border-line bg-sunken px-2.5 py-2 font-mono text-[11px] leading-relaxed text-muted">
          {front}
        </pre>
      )}
      <Markdown text={body} variant="doc" tone="dev" />
    </div>
  )
}

// "Agent at work" indicator — a robot head + label that blink together as one unit. Wrap the icon
// AND its text so the whole thing pulses in sync (callers pass the label as children).
function AgentWorking({ size = 14, className = '', children }: { size?: number; className?: string; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 text-accent-text animate-pulse ${className}`} style={{ animationDuration: '1.4s' }}>
      <Bot size={size} className="shrink-0" />
      {children}
    </span>
  )
}

// Narrate the forge run's phase from elapsed time (the run is author → evaluate → brush up). We don't
// have per-step events, so this is time-anchored: forging first, then the long eval, then polish.
function forgePhase(form: string, elapsed: number): string {
  const f = form || 'artifact'
  if (elapsed < 14) return `Forging the ${f}…`
  return Math.floor((elapsed - 14) / 18) % 2 === 0 ? `Evaluating the ${f}…` : `Brushing up the ${f}…`
}

// Compact phase word for the queue card (no form noun — the card already shows the form).
function forgePhaseShort(elapsed: number): string {
  if (elapsed < 14) return 'forging'
  return Math.floor((elapsed - 14) / 18) % 2 === 0 ? 'evaluating' : 'polishing'
}

// The behavioural-eval verdict the forge_kit produced — evidence for the gate-2 decision.
const VERDICT_TINT: Record<string, string> = {
  pass: 'bg-success/15 text-success',
  warn: 'bg-warn/15 text-warn',
  fail: 'bg-danger/15 text-danger',
  skipped: 'bg-hover text-muted',
}
function EvalReportView({ report }: { report: EvalReport }) {
  const verdict = (report.verdict ?? 'unknown').toLowerCase()
  const issues = report.issues ?? []
  const checks = report.checks ?? []
  const m = report.metrics
  const highs = issues.filter((i) => i.severity === 'high').length
  return (
    <div className="space-y-4">
      {/* Scannable header: verdict + per-lens scores + issue tally */}
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-2 py-0.5 font-mono text-[11px] uppercase ${VERDICT_TINT[verdict] ?? 'bg-hover text-muted'}`}>
          {verdict}
        </span>
        {checks.map((c, i) => (
          <span key={i} className="rounded bg-hover px-1.5 py-0.5 font-mono text-[11px] text-muted" title={c.note}>
            {c.name?.replace(/_/g, ' ')} <span className={`${(c.score ?? 0) >= 4 ? 'text-success' : (c.score ?? 0) <= 2 ? 'text-danger' : 'text-warn'}`}>{c.score ?? '–'}/5</span>
          </span>
        ))}
        <span className="font-mono text-[11px] text-faint">
          {issues.length === 0 ? 'no issues' : `${issues.length} issue${issues.length > 1 ? 's' : ''}${highs ? ` · ${highs} high` : ''}`}
        </span>
      </div>
      {/* The artifact's OWN run cost — measured by exercising it once on a synthetic task.
          'run' = skill/agent (tokens/time/cost); 'overhead' = constitution's always-on per-turn cost. */}
      {m && (
        <div className="space-y-1.5 rounded-md border border-line bg-app px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-faint">
            {m.kind === 'overhead' ? 'Always-on cost' : 'Artifact run cost'}
          </div>
          {report.trial_task && m.kind !== 'overhead' && !m.error && (
            <div className="font-mono text-[10px] text-faint">
              <span className="text-muted">Eval on:</span> {report.trial_task}
            </div>
          )}
          <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-muted">
            {m.error ? (
              <span className="text-warn">trial did not complete — {m.error}</span>
            ) : m.kind === 'overhead' ? (
              <span>
                <span className="text-faint">~</span>
                {(m.tokens_per_turn ?? 0).toLocaleString()}
                <span className="text-faint"> tokens / turn</span>
              </span>
            ) : m.context_tokens != null || m.output_tokens != null ? (
              <>
                {m.context_tokens != null && <span><span className="text-faint">context</span> ~{m.context_tokens.toLocaleString()}</span>}
                {m.output_tokens != null && <span><span className="text-faint">output</span> {m.output_tokens.toLocaleString()}</span>}
                {m.duration_s != null && <span><span className="text-faint">time</span> {m.duration_s}s</span>}
              </>
            ) : (
              /* legacy proposals (pre-footprint): cumulative token total only */
              <>
                {m.tokens != null && <span><span className="text-faint">tokens</span> {m.tokens.toLocaleString()}</span>}
                {m.duration_s != null && <span><span className="text-faint">time</span> {m.duration_s}s</span>}
              </>
            )}
          </div>
          {m.capped && !m.error && m.kind === 'run' && (
            <div className="font-mono text-[10px] text-warn">
              floor — trial hit the turn cap ({m.capped}); a full run costs more
            </div>
          )}
        </div>
      )}
      {report.summary && (Array.isArray(report.summary) ? report.summary.length > 0 : true) && (
        <PSection title="Assessment">
          {Array.isArray(report.summary) ? (
            <ul className="space-y-1">
              {report.summary.map((b, i) => (
                <li key={i} className="flex gap-1.5 text-[13px] leading-snug text-fg">
                  <span className="text-faint">•</span>
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] leading-relaxed text-fg">{report.summary}</p>
          )}
        </PSection>
      )}
      {issues.length > 0 && (
        <PSection title="Issues — fix before publishing">
          <ul className="space-y-2">
            {[...issues].sort((a, b) => (a.severity === 'high' ? -1 : 1) - (b.severity === 'high' ? -1 : 1)).map((it, i) => (
              <li key={i} className="rounded-md border border-line bg-app px-2.5 py-2 text-[12px]">
                <div className="flex items-start gap-1.5">
                  <span className={`mt-px rounded px-1 text-[9px] uppercase ${it.severity === 'high' ? 'bg-danger/15 text-danger' : 'bg-hover text-muted'}`}>
                    {it.severity ?? '?'}
                  </span>
                  <span className="text-fg">{it.what}</span>
                </div>
                {it.fix && <div className="mt-1 pl-1 text-faint">→ {it.fix}</div>}
              </li>
            ))}
          </ul>
        </PSection>
      )}
    </div>
  )
}
