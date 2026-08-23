import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Bell, GitMerge, ArrowUpRight, AlertTriangle, Inbox, MessageCircleQuestion } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { getSystemAttention, type SystemHold, type SystemHoldKind, type SystemRepoAttention } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'

// One bell beside the brand, surfacing every hold across EVERY connected repo.
//
// Nothing auto-stops forever: a hold notifies and offers a next move, it never disposes silently.
// The popover stays open until the owner clicks elsewhere.

// `kind` is the daemon's classification of WHY the item is parked, and it drives which quick
// actions a row offers.
const KIND: Record<SystemHoldKind, { icon: LucideIcon; dot: string; label: string }> = {
  question: { icon: MessageCircleQuestion, dot: 'bg-accent', label: 'Questions' },
  escalation: { icon: AlertTriangle, dot: 'bg-danger', label: 'Escalated' },
  paged: { icon: Inbox, dot: 'bg-warn', label: 'Upstream' },
  review: { icon: GitMerge, dot: 'bg-accent', label: 'Review' },
  gate: { icon: Bell, dot: 'bg-muted', label: 'Gate' },
}

export default function AttentionCenter({ onGoto }: { onGoto: (repoId: string, hold: SystemHold) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ top: 0, right: 0 })

  const { data: feed = [] } = useLive<SystemRepoAttention[]>(K.systemAttention, getSystemAttention)

  const total = feed.reduce((n, r) => n + r.holds.length, 0)

  // Anchor the popover under the bell (fixed, right-aligned to the trigger) so it clears the header.
  function place() {
    if (!ref.current) return
    const r = ref.current.getBoundingClientRect()
    setPos({ top: r.bottom + 8, right: window.innerWidth - r.right })
  }
  useLayoutEffect(() => { if (open) place() }, [open])

  useEffect(() => {
    if (!open) return
    // Outside-click closes; a click inside the trigger or the portalled popover must NOT.
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (ref.current?.contains(t) || menuRef.current?.contains(t)) return
      setOpen(false)
    }
    const onScroll = () => place()
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('resize', onScroll)
    return () => { document.removeEventListener('mousedown', onDoc); window.removeEventListener('resize', onScroll) }
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={total ? `${total} item${total === 1 ? '' : 's'} need you` : 'Nothing needs you'}
        aria-label="Attention center"
        className={`relative flex h-9 w-9 items-center justify-center rounded-lg border text-muted transition hover:bg-hover hover:text-fg ${
          open ? 'border-line bg-hover text-fg' : 'border-transparent'
        }`}
      >
        <Bell size={17} className={total ? 'text-fg' : ''} />
        {total > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-bold leading-none text-on-accent">
            {total > 9 ? '9+' : total}
          </span>
        )}
      </button>

      {open &&
        createPortal(
          <div
            ref={menuRef}
            style={{ position: 'fixed', top: pos.top, right: pos.right, width: 360, maxHeight: '70vh' }}
            className="z-50 flex flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-xl"
          >
            <div className="flex shrink-0 items-center justify-between border-b border-line px-4 py-2.5">
              <span className="text-sm font-semibold text-fg">Needs you</span>
              <span className="text-xs text-faint">{total === 0 ? 'all clear' : `${total} across ${feed.length} project${feed.length === 1 ? '' : 's'}`}</span>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {total === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-faint">
                  Nothing is waiting on you. Autopilot items in flight surface here the moment one holds.
                </div>
              ) : (
                feed.map((r) => (
                  <div key={r.repo_id} className="border-b border-line last:border-b-0">
                    <div className="bg-sunken/60 px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">
                      {r.repo_label}
                    </div>
                    {r.holds.map((h) => (
                      <HoldRow
                        key={h.id}
                        h={h}
                        onOpen={() => { setOpen(false); onGoto(r.repo_id, h) }}
                      />
                    ))}
                  </div>
                ))
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  )
}

function HoldRow({
  h, onOpen,
}: {
  h: SystemHold
  onOpen: () => void
}) {
  const k = KIND[h.kind] ?? KIND.gate
  const Icon = k.icon
  return (
    <div className="px-4 py-2.5 hover:bg-hover/40">
      <div className="flex items-start gap-2.5">
        <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${k.dot}/15`}>
          <Icon size={13} className={k.dot.replace('bg-', 'text-')} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium text-fg">{h.title}</span>
            <span className="shrink-0 rounded bg-hover px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-muted">{k.label}</span>
          </div>
          <p className="mt-0.5 text-xs leading-snug text-muted">{h.reason}</p>
          {h.kind === 'question' && (h.questions?.length ?? 0) > 0 && (
            <ol className="mt-1.5 list-decimal space-y-2 pl-4 text-xs leading-snug">
              {h.questions!.map((q, i) => (
                <li key={i} className="text-fg">
                  <span className="font-medium">{q.question}</span>
                  {/* The recommendation is the point: the owner accepts it or names the
                      alternative. */}
                  {q.recommend && (
                    <span className="mt-1 block text-muted">
                      <span className="text-fg">Recommend</span> — {q.recommend}
                    </span>
                  )}
                  {q.why && <span className="block text-muted">Why — {q.why}</span>}
                  {q.instead && <span className="block text-muted">Instead — {q.instead}</span>}
                </li>
              ))}
            </ol>
          )}
          <div className="mt-1.5 flex items-center gap-2">
            <button
              type="button"
              onClick={onOpen}
              className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-fg hover:bg-hover"
            >
              <ArrowUpRight size={12} /> {h.kind === 'question' ? 'Open chat' : 'Open'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
