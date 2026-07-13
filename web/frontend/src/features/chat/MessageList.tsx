import { useEffect, useRef } from 'react'
import Markdown from '@/ui/Markdown'
import ApprovalCard from './ApprovalCard'
import type { Approval, Msg } from './types'

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
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (preserveRef.current != null) {
      el.scrollTop = el.scrollHeight - preserveRef.current
      preserveRef.current = null
    } else {
      el.scrollTo(0, el.scrollHeight)
    }
  }, [messages, live, statusLabel, approval])

  function seeMore() {
    preserveRef.current = scrollRef.current?.scrollHeight ?? null
    onLoadMore?.()
  }

  return (
    <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
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
      {messages.map((m, i) => (
        <div key={i} className={m.role === 'you' ? 'text-right' : 'text-left'}>
          <div
            className={`inline-block max-w-[90%] overflow-hidden break-words rounded-lg px-3 py-2 text-sm [overflow-wrap:anywhere] ${
              m.role === 'you'
                ? 'whitespace-pre-wrap border border-line border-l-2 border-l-[var(--chat-accent)] bg-surface text-fg'
                : 'bg-hover text-fg'
            }`}
          >
            {m.role === 'you' ? m.text : <Markdown text={m.text} tone={tone} />}
          </div>
        </div>
      ))}
      {live && (
        <div className="text-left">
          <div className="inline-block max-w-[90%] overflow-hidden break-words rounded-lg bg-hover px-3 py-2 text-sm text-fg [overflow-wrap:anywhere]">
            <Markdown text={live} tone={tone} />
          </div>
        </div>
      )}
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
