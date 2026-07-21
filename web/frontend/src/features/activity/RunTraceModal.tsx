import { useEffect, useState } from 'react'
import { Loader2, X, Terminal, MessageSquareText, Stethoscope, ArrowRight } from 'lucide-react'
import Modal from '@/ui/Modal'
import Markdown from '@/ui/Markdown'
import { RepoIcon } from '@/lib/repoIcons'
import { featureColor, featureLabel } from '@/lib/palette'
import { fmtLocal, fmtTokens, fmtModel, fmtDuration } from '@/lib/format'
import { pairTrace } from '@/lib/trace'
import { TraceRows } from '@/features/dev/ExecutionTrace'
import { getRunTrace, type Run, type RunEvent } from '@/lib/api'

// Run trace — the popup for one Activity row. Two tabs (mirroring the work-item detail's
// Review/Execution): CONVERSATION (the prompt + assistant reply, readable — the default) and EXECUTION
// (the call-trail of tools / sub-agents / skills, same layout as the work-item Execution tab). Everything
// is scoped to THIS run (not the whole session), so each row has its own thread — works for background runs.

type Tab = 'trace' | 'conversation'

export default function RunTraceModal({
  run,
  meta,
  onClose,
  onDiagnose,
}: {
  run: Run
  meta: { label: string; color: string; icon: string | null }
  onClose: () => void
  // Launch a read-only diagnosis session pointed at THIS run (session-kinds-diagnose). Absent ⇒ the
  // Diagnose affordance is hidden (e.g. surfaces with no chat rail to receive the session).
  onDiagnose?: (query: string) => void
}) {
  const [events, setEvents] = useState<RunEvent[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('conversation')
  const [diagOpen, setDiagOpen] = useState(false) // the inline "what feels off?" prompt box
  const [query, setQuery] = useState('')

  useEffect(() => {
    let alive = true
    getRunTrace(run.id)
      .then((d) => { if (alive) setEvents(d.events) })
      .catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [run.id])

  const took = run.ended_at ? fmtDuration(Date.parse(run.ended_at) - Date.parse(run.started_at)) : '—'
  const isHub = run.repo_id === 'global'
  const calls = pairTrace(events ?? []) // each tool call paired with its result (result absorbed as an expandable)
  const convo = (events ?? []).filter((e) => e.kind === 'prompt' || e.kind === 'reply')

  return (
    <Modal onClose={onClose} column maxW="max-w-3xl">
      {/* header — run identity + compact telemetry + close */}
      <div className="shrink-0 border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          {meta.icon && !isHub ? (
            <RepoIcon name={meta.icon} size={15} color={meta.color} className="shrink-0" />
          ) : (
            <span className="h-4 w-4 shrink-0 rounded-[4px]" style={isHub ? { backgroundImage: 'var(--grad-iris)' } : { backgroundColor: meta.color }} />
          )}
          <span className="text-sm font-medium text-fg">{meta.label}</span>
          <span className="rounded px-1.5 py-0.5 text-[11px] font-medium" style={{ color: featureColor(run.feature), backgroundColor: 'rgb(var(--c-hover))' }}>
            {featureLabel(run.feature)}
          </span>
          <span className="text-[11px] text-faint">{run.mode}</span>
          <div className="ml-auto flex items-center gap-2">
            {onDiagnose && (
              <button
                onClick={() => setDiagOpen((v) => !v)}
                title="Open a diagnosis session on this run"
                className={`flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition ${
                  diagOpen ? 'border-dev text-dev' : 'border-line text-muted hover:bg-hover hover:text-fg'
                }`}
              >
                <Stethoscope size={13} /> Diagnose
              </button>
            )}
            <button onClick={onClose} className="rounded p-1 text-muted hover:bg-hover hover:text-fg">
              <X size={16} />
            </button>
          </div>
        </div>

        {/* diagnosis launcher — a read-only diagnosis session, pointed at this run, seeded with the
            owner's question. The daemon injects this run's trace so the session starts oriented. */}
        {onDiagnose && diagOpen && (
          <div className="mt-2.5 rounded-lg border border-dev/40 bg-dev/5 p-2.5">
            <textarea
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && query.trim()) onDiagnose(query.trim())
              }}
              rows={2}
              placeholder="What feels off about this run? (e.g. “it claimed to file proposals that don't exist — check what actually happened”)"
              className="w-full resize-none rounded-md border border-line bg-surface px-2.5 py-2 text-[13px] text-fg outline-none placeholder:text-faint focus:border-dev"
            />
            <div className="mt-2 flex items-center justify-between">
              <span className="text-[11px] text-faint">Opens a read-only diagnosis session in the chat rail.</span>
              <button
                onClick={() => query.trim() && onDiagnose(query.trim())}
                disabled={!query.trim()}
                className="flex items-center gap-1 rounded-md bg-dev px-2.5 py-1 text-xs font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
              >
                Start diagnosis <ArrowRight size={13} />
              </button>
            </div>
          </div>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-faint">
          <span className="font-mono">#{run.id}</span>
          <Dot /> <span>{run.model ? fmtModel(run.model) : '—'}</span>
          <Dot /> <span>{fmtTokens(run.tokens)} tok</span>
          <Dot /> <span>{took}</span>
          {run.ctx_pct != null && (<><Dot /> <span>ctx {run.ctx_pct}%</span></>)}
          {run.item_id && (<><Dot /> <span className="font-mono">{run.item_id}</span></>)}
          <Dot /> <span>{fmtLocal(run.started_at)}</span>
        </div>
      </div>

      {/* tabs — Trace (the call-trail) · Conversation (prompt + reply) */}
      <div className="flex shrink-0 gap-1 border-b border-line px-4">
        {([['conversation', 'Conversation', MessageSquareText, convo.length], ['trace', 'Execution', Terminal, calls.length]] as const).map(([id, label, Icon, n]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition ${
              tab === id ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg'
            }`}
          >
            <Icon size={14} /> {label}
            {events && n > 0 && <span className="rounded-full bg-hover px-1.5 text-[10px] text-muted">{n}</span>}
          </button>
        ))}
      </div>

      {/* body */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {err ? (
          <p className="text-[13px] text-danger">Couldn’t load trace — {err}</p>
        ) : events === null ? (
          <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>
        ) : tab === 'trace' ? (
          calls.length === 0 ? (
            <div className="text-sm text-faint">No tool / skill / sub-agent calls in this run.</div>
          ) : (
            <div>
              <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-faint">
                Run #{run.id} · {calls.length} call{calls.length === 1 ? '' : 's'}
              </div>
              <TraceRows rows={calls} time={(e) => fmtLocal(e.created_at)} />
            </div>
          )
        ) : (
          <ConversationTab convo={convo} tone={run.mode === 'dev' ? 'dev' : 'core'} />
        )}
      </div>
    </Modal>
  )
}

function Dot() {
  return <span className="text-faint/50">·</span>
}

// The readable conversation — the prompt that opened the run + the assistant's reply blocks.
function ConversationTab({ convo, tone }: { convo: RunEvent[]; tone: 'dev' | 'core' }) {
  if (convo.length === 0) {
    return <div className="text-sm text-faint">No prompt or reply recorded for this run.</div>
  }
  return (
    <div className="space-y-3">
      {convo.map((e) =>
        e.kind === 'prompt' ? (
          <div key={e.id} className="text-right">
            <div className="inline-block max-w-[90%] whitespace-pre-wrap break-words rounded-lg border border-line border-l-2 border-l-[var(--chat-accent)] bg-surface px-3 py-2 text-left text-sm text-fg [overflow-wrap:anywhere]">
              {e.description || e.name}
            </div>
          </div>
        ) : (
          <div key={e.id} className="inline-block max-w-[90%] overflow-hidden break-words rounded-lg bg-hover px-3 py-2 text-sm text-fg [overflow-wrap:anywhere]">
            <Markdown text={e.description || ''} tone={tone} />
          </div>
        ),
      )}
    </div>
  )
}
