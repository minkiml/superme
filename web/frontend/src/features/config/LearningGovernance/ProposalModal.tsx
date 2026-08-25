import { useEffect, useState } from 'react'
import { Check, X, Trash2, Loader2, FileText, Layers, Send, FileCode, Gauge, Terminal } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import SourceEditor from '@/ui/SourceEditor'
import { useEditGate, EditActions } from '@/ui/EditGate'
import { getProposals, approveProposal, updateStagedArtifact, publishProposal, rejectProposal, dropProposal, getProposalExecution, type MemoryProposal, type ProposalCandidate, type ProposalStep } from '@/lib/api'
import { fmtLocalDate, fmtLocal } from '@/lib/format'
import { Empty } from '@/features/dev/common'
import { EvalReportView } from './EvalReportView'
import { AgentWorking, FORM_TINT, PSection, StageBadge, forgePhase } from './bits'

// One proposal in full: what it proposes, how it was produced, and the two gates.

// The two-gate decision surface, in tabs. The action row belongs to whichever gate the proposal is
// at.
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
export function ProposalModal({
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

  const cands = cur.candidates ?? []
  const fields = cur.fields && typeof cur.fields === 'object' ? cur.fields : null
  const clar = cur.clarifications ?? []
  const status = cur.status
  const writing = status === 'writing'
  const drafted = status === 'drafted'
  const proposed = status === 'proposed'
  const blockingUnanswered = clar.some((q) => q.blocking && !((answers[q.question] ?? '').trim()))

  const artGate = useEditGate({
    saved: cur.staged_artifact ?? '',
    valid: (d) => !!d.trim(),
    commit: async (d) => { setCur((await updateStagedArtifact(cur.id, d, contextId)).proposal) },
  })
  const editingArtifact = artGate.editing

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

  // Poll until the proposal leaves `writing`, then surface the staged artifact.
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

  // Fetched when the tab opens and refetched as the proposal advances, because the timeline grows.
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
                <EditActions gate={artGate} readOnly={!drafted} />
                {editingArtifact && (
                  <span className="text-[10px] text-faint">edits save to the draft; publish writes it to disk</span>
                )}
              </div>
              {editingArtifact ? (
                <SourceEditor value={artGate.draft} onChange={artGate.setDraft} surface="bg-app" className="h-[42vh]" />
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
                  <Markdown text={cur.body} tone="dev" />
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
              Gate 1. Approve the intent to author the {cur.output_form}.
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
                title="Approve the intent. The write phase authors the artifact."
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
                title={editingArtifact ? 'Save or cancel your edit first.' : 'Write the staged artifact to its live operational home.'}
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
                  title="The framing is off. Re-queues the candidates for a later distill pass."
                  className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-[12px] text-muted transition hover:bg-hover hover:text-fg disabled:opacity-50"
                >
                  {busy === 'reject' ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                  Reject
                </button>
                <button
                  onClick={() => act('drop')}
                  disabled={busy !== null}
                  title="Noise. Drops it and its candidates, and stops suggesting it."
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

// Artifact homes all live under superme_agent/ — show the repo-relative path, not the long absolute one.
function relArtifactPath(p: string): string {
  const i = p.indexOf('/superme_agent/')
  return i >= 0 ? '.' + p.slice(i) : p
}

// A staged artifact renders like a live one, so the gate-2 reader sees what will land.
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
