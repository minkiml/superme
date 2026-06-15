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
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [messages, live, statusLabel, approval])

  return (
    <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
      {messages.length === 0 && !live && (
        <div className="text-sm text-muted">
          Talk to {ctxLabel} SuperMe. It reads and writes this context's knowledge.
        </div>
      )}
      {olderHidden > 0 && (
        <div className="text-center text-xs text-faint">
          ⋯ {olderHidden} earlier message{olderHidden === 1 ? '' : 's'} hidden — SuperMe still has the full context
        </div>
      )}
      {messages.map((m, i) => (
        <div key={i} className={m.role === 'you' ? 'text-right' : 'text-left'}>
          <div
            className={`inline-block max-w-[90%] overflow-hidden break-words rounded-lg px-3 py-2 text-sm [overflow-wrap:anywhere] ${
              m.role === 'you' ? 'whitespace-pre-wrap bg-accent text-on-accent' : 'bg-hover text-fg'
            }`}
          >
            {m.role === 'you' ? m.text : <Markdown text={m.text} />}
          </div>
        </div>
      ))}
      {live && (
        <div className="text-left">
          <div className="inline-block max-w-[90%] overflow-hidden break-words rounded-lg bg-hover px-3 py-2 text-sm text-fg [overflow-wrap:anywhere]">
            <Markdown text={live} />
          </div>
        </div>
      )}
      {busy && (
        <div className="flex items-center gap-2 text-xs text-muted">
          <span className="flex gap-0.5">
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent [animation-delay:-0.3s]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent [animation-delay:-0.15s]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent" />
          </span>
          <span>{statusLabel ? `${statusLabel}…` : live ? 'responding…' : 'thinking…'}</span>
          <span className="tabular-nums text-muted">{elapsed.toFixed(1)}s</span>
        </div>
      )}

      {approval && <ApprovalCard approval={approval} onAnswer={onAnswer} />}
    </div>
  )
}
