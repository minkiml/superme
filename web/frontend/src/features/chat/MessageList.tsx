import { useEffect, useRef } from 'react'
import { ShieldCheck, User, Sparkles, type LucideIcon } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import ApprovalCard from './ApprovalCard'
import type { Approval, Msg } from './types'

// The three talkers in a work-item thread (N4): you · the work-item agent · the deputy. Each row is
// a small talker icon + a minimal bubble — same clean shape for all, distinguished by icon and a
// restrained tint, so the owner can tell at a glance who spoke without the bubbles shouting. You sits
// on the right (chat convention); the agent and the deputy on the left.
const TALKER: Record<string, { icon: LucideIcon; label: string; right: boolean; avatar: string; bubble: string }> = {
  you: {
    icon: User, label: 'You', right: true, avatar: 'border-[var(--chat-accent)]/50 text-[var(--chat-accent)]',
    bubble: 'border border-line border-l-2 border-l-[var(--chat-accent)] bg-surface text-fg',
  },
  superme: {
    icon: Sparkles, label: 'SuperMe', right: false, avatar: 'border-line text-muted',
    bubble: 'bg-hover text-fg',
  },
  deputy: {
    icon: ShieldCheck, label: 'Deputy', right: false, avatar: 'border-warn/50 text-warn',
    bubble: 'border border-warn/30 border-l-2 border-l-warn bg-warn/5 text-fg',
  },
}

function Row({ m, tone }: { m: Msg; tone?: 'dev' | 'core' }) {
  const t = TALKER[m.role] ?? TALKER.superme
  const Icon = t.icon
  return (
    <div className={`flex items-start gap-2 ${t.right ? 'flex-row-reverse' : ''}`}>
      <div className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border bg-surface ${t.avatar}`}
           title={m.role === 'deputy' ? 'Deputy · on your behalf' : t.label} aria-label={t.label}>
        <Icon size={13} />
      </div>
      <div className={`min-w-0 max-w-[85%] overflow-hidden break-words rounded-lg px-3 py-2 text-sm [overflow-wrap:anywhere] ${t.bubble}`}>
        {m.role === 'deputy' && (
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-warn">Deputy · on your behalf</div>
        )}
        {m.role === 'you'
          ? <span className="whitespace-pre-wrap">{m.text}</span>
          : m.role === 'deputy'
            ? <Markdown text={m.text} tone={tone} />
            : <AssistantContent text={m.text} tone={tone} />}
      </div>
    </div>
  )
}

// A run's completion-report fence (```completion-report\n outcome/summary/next \n```) is the
// end-of-run handshake the daemon parses AND a glance for the owner. Rendered raw it's an ugly code
// block; lift it out and show a compact card (outcome badge + summary + next), keeping the prose
// around it as normal Markdown.
const REPORT_RE = /```completion-report\s*\n([\s\S]*?)```/
function splitReport(text: string): { body: string; report: Record<string, string> | null; tail: string } {
  const m = text.match(REPORT_RE)
  if (!m || m.index == null) return { body: text, report: null, tail: '' }
  const fields: Record<string, string> = {}
  for (const line of m[1].split('\n')) {
    const mm = line.match(/^\s*(outcome|summary|next)\s*:\s*(.+?)\s*$/)
    if (mm) fields[mm[1]] = mm[2]
  }
  if (!fields.outcome) return { body: text, report: null, tail: '' }
  return { body: text.slice(0, m.index).trimEnd(), report: fields, tail: text.slice(m.index + m[0].length).trim() }
}

// Outcome → glance colour + owner-facing label (the schema value is terse; humanize it).
const OUTCOME_TONE: Record<string, { dot: string; text: string; label: string }> = {
  success: { dot: 'bg-success', text: 'text-success', label: 'success' },
  clean_noop: { dot: 'bg-line', text: 'text-muted', label: 'nothing to do' },
  blocked: { dot: 'bg-warn', text: 'text-warn', label: 'blocked' },
  approval_required: { dot: 'bg-warn', text: 'text-warn', label: 'needs you' },
  exhausted: { dot: 'bg-danger', text: 'text-danger', label: 'out of budget' },
  stagnated: { dot: 'bg-danger', text: 'text-danger', label: 'no progress' },
}

function CompletionReport({ r }: { r: Record<string, string> }) {
  const t = OUTCOME_TONE[r.outcome] ?? { dot: 'bg-line', text: 'text-muted', label: r.outcome }
  return (
    <div className="mt-2 rounded-md border border-line bg-surface px-2.5 py-2">
      <div className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${t.dot}`} />
        <span className={`text-[11px] font-semibold uppercase tracking-wide ${t.text}`}>{t.label}</span>
        <span className="ml-auto text-[9px] uppercase tracking-wider text-faint">run report</span>
      </div>
      {r.summary && <div className="mt-1 text-xs text-fg">{r.summary}</div>}
      {r.next && <div className="mt-0.5 text-xs text-muted"><span className="text-faint">next · </span>{r.next}</div>}
    </div>
  )
}

// Assistant bubble content: prose as Markdown, with any completion-report fence promoted to a card.
function AssistantContent({ text, tone }: { text: string; tone?: 'dev' | 'core' }) {
  const { body, report, tail } = splitReport(text)
  return (
    <>
      {body && <Markdown text={body} tone={tone} />}
      {report && <CompletionReport r={report} />}
      {tail && <Markdown text={tail} tone={tone} />}
    </>
  )
}

// The scrollable transcript: replayed + live bubbles, the typing indicator while a turn
// is in flight, and any pending approval. Owns its own scroll element and keeps it pinned
// to the bottom as content arrives.
export default function MessageList({
  messages,
  live,
  busy,
  statusLabel,
  elapsed,
  olderHidden,
  approval,
  ctxLabel,
  onAnswer,
  onLoadMore,
  tone,
}: {
  messages: Msg[]
  live: string
  busy: boolean
  statusLabel: string | null
  elapsed: number
  olderHidden: number
  approval: Approval | null
  ctxLabel: string
  onAnswer: (approved: boolean) => void
  onLoadMore?: () => void // reveal the next page of older bubbles ("See more")
  tone?: 'dev' | 'core' // colours assistant `code` by scope + **bold** consistently, matching the doc previews
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  // When "See more" prepends older bubbles, keep the viewport anchored (don't yank to the bottom).
  // preserveRef holds the pre-load scrollHeight; the effect restores position by the height delta.
  const preserveRef = useRef<number | null>(null)
  // Auto-scroll only when the owner is ALREADY reading the bottom (N3): if they scrolled up to read
  // history, new content must not yank them back down. `stickRef` tracks "near the bottom", updated
  // on every user scroll; the auto-scroll effect honours it. Starts true (a fresh thread opens pinned).
  const stickRef = useRef(true)
  function onScroll() {
    const el = scrollRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (preserveRef.current != null) {
      el.scrollTop = el.scrollHeight - preserveRef.current
      preserveRef.current = null
    } else if (stickRef.current) {
      el.scrollTo(0, el.scrollHeight)
    }
  }, [messages, live, statusLabel, approval])

  function seeMore() {
    preserveRef.current = scrollRef.current?.scrollHeight ?? null
    onLoadMore?.()
  }

  return (
    <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
      {messages.length === 0 && !live && (
        <div className="text-sm text-muted">
          Talk to {ctxLabel} SuperMe. It reads and writes this context's knowledge.
        </div>
      )}
      {olderHidden > 0 && (
        <div className="text-center">
          <button
            onClick={seeMore}
            className="text-xs text-faint underline-offset-2 transition-colors hover:text-fg hover:underline"
          >
            See {Math.min(10, olderHidden)} more · {olderHidden} earlier hidden
          </button>
        </div>
      )}
      {messages.map((m, i) => <Row key={i} m={m} tone={tone} />)}
      {live && <Row m={{ role: 'superme', text: live }} tone={tone} />}
      {busy && (
        <div className="flex items-center gap-2 text-xs text-muted">
          <span className="flex gap-0.5">
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--chat-accent)] [animation-delay:-0.3s]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--chat-accent)] [animation-delay:-0.15s]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--chat-accent)]" />
          </span>
          <span>{statusLabel ? `${statusLabel}…` : live ? 'responding…' : 'thinking…'}</span>
          <span className="tabular-nums text-muted">{elapsed.toFixed(1)}s</span>
        </div>
      )}

      {approval && <ApprovalCard approval={approval} onAnswer={onAnswer} />}
    </div>
  )
}
