import { ShieldCheck, User, Sparkles, type LucideIcon } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import ApprovalCard from './ApprovalCard'
import { useStickyScroll } from './useStickyScroll'
import { fmtElapsed } from '@/lib/format'
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
          : <Markdown text={m.text} tone={tone} />}
      </div>
    </div>
  )
}

// NOTE (renovation §3.2): a run's closing report is no longer prose to be parsed out of the reply.
// It arrives as the `report_completion` tool's `user` payload and is persisted as the `run.report`
// event; the card that renders it lives in TimelineView, which is what a work-item thread uses.
// This list renders general chats, which have no phase runs and so no report — plain Markdown.

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
  // N3 — stick to the bottom only while the owner is reading it; "See more" prepends without a jump.
  const { scrollRef, onScroll, preserve } = useStickyScroll([messages, live, statusLabel, approval])

  function seeMore() {
    preserve()
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
          <span className="tabular-nums text-muted">{fmtElapsed(elapsed)}</span>
        </div>
      )}

      {approval && <ApprovalCard approval={approval} onAnswer={onAnswer} />}
    </div>
  )
}
