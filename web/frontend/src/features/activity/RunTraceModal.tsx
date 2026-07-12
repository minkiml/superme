import { useEffect, useState } from 'react'
import {
  Loader2, X, Terminal, MessageSquareText, BookOpen, FolderSearch, Search, SquareTerminal,
  FilePen, Bot, Globe, Sparkles, Wrench,
} from 'lucide-react'
import Modal from '@/ui/Modal'
import Markdown from '@/ui/Markdown'
import { RepoIcon } from '@/lib/repoIcons'
import { featureColor, featureLabel } from '@/lib/palette'
import { fmtLocal, fmtTokens, fmtModel, fmtDuration } from '@/lib/format'
import { getRunTrace, type Run, type RunEvent } from '@/lib/api'

// Run trace — the popup for one Activity row. Two tabs (mirroring the work-item detail's
// Review/Execution): CONVERSATION (the prompt + assistant reply, readable — the default) and EXECUTION
// (the call-trail of tools / sub-agents / skills, same layout as the work-item Execution tab). Everything
// is scoped to THIS run (not the whole session), so each row has its own thread — works for headless runs.

// Icon + color per call, resolved by tool NAME first then by kind — mirrors WorkItemModal's map so the
// Activity trace and the work-item Execution tab read identically.
const CALL_STYLE: Record<string, { icon: typeof Terminal; color: string }> = {
  read: { icon: BookOpen, color: '#60a5fa' },
  glob: { icon: FolderSearch, color: '#a78bfa' },
  grep: { icon: Search, color: '#fbbf24' },
  bash: { icon: SquareTerminal, color: '#34d399' },
  edit: { icon: FilePen, color: '#fb7185' },
  write: { icon: FilePen, color: '#fb7185' },
  agent: { icon: Bot, color: '#818cf8' },
  task: { icon: Bot, color: '#818cf8' },
  webfetch: { icon: Globe, color: '#22d3ee' },
  websearch: { icon: Globe, color: '#22d3ee' },
}
const KIND_STYLE: Record<string, { icon: typeof Terminal; color: string }> = {
  agent: { icon: Bot, color: '#818cf8' },
  subagent: { icon: Bot, color: '#818cf8' }, // a spawned sub-agent (Task/Agent) — named by its type
  skill: { icon: Sparkles, color: '#e0a35a' },
  tool: { icon: Terminal, color: '#8b93a1' },
}
function callVisual(e: RunEvent) {
  return CALL_STYLE[(e.name ?? '').toLowerCase()] ?? KIND_STYLE[e.kind] ?? KIND_STYLE.tool
}

const CALL_KINDS = new Set(['tool', 'skill', 'agent', 'subagent'])
type Tab = 'trace' | 'conversation'

export default function RunTraceModal({
  run,
  meta,
  onClose,
}: {
  run: Run
  meta: { label: string; color: string; icon: string | null }
  onClose: () => void
}) {
  const [events, setEvents] = useState<RunEvent[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('conversation')

  useEffect(() => {
    let alive = true
    getRunTrace(run.id)
      .then((d) => { if (alive) setEvents(d.events) })
      .catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [run.id])

  const took = run.ended_at ? fmtDuration(Date.parse(run.ended_at) - Date.parse(run.started_at)) : '—'
  const isHub = run.repo_id === 'global'
  const calls = (events ?? []).filter((e) => CALL_KINDS.has(e.kind))
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
          <button onClick={onClose} className="ml-auto rounded p-1 text-muted hover:bg-hover hover:text-fg">
            <X size={16} />
          </button>
        </div>
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
          <TraceTab run={run} calls={calls} />
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

// The call-trail — same layout as the work-item Execution tab: Run # · N calls, then seq · icon ·
// name - description · timestamp, in call order.
function TraceTab({ run, calls }: { run: Run; calls: RunEvent[] }) {
  if (calls.length === 0) {
    return <div className="text-sm text-faint">No tool / skill / sub-agent calls in this run.</div>
  }
  return (
    <div>
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-faint">
        Run #{run.id} · {calls.length} call{calls.length === 1 ? '' : 's'}
      </div>
      <ol className="space-y-0.5">
        {calls.map((e) => {
          const { icon: Icon, color } = callVisual(e)
          return (
            <li key={e.id} className="flex items-baseline gap-2 text-xs">
              <span className="w-5 shrink-0 text-right font-mono text-[10px] text-faint">{e.seq}</span>
              <Icon size={12} className="shrink-0 translate-y-0.5" style={{ color }} />
              <span className="min-w-0 flex-1 truncate" title={`${e.name}${e.description ? ' - ' + e.description : ''}`}>
                <span className="text-fg">{e.name}</span>
                {e.description && <span className="text-faint"> - {e.description}</span>}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-faint">{fmtLocal(e.created_at)}</span>
            </li>
          )
        })}
      </ol>
    </div>
  )
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
