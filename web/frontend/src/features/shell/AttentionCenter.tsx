import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Bell, GitMerge, ArrowUpRight, AlertTriangle, Hand, Inbox } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { getSystemAttention, advanceWorkItem, type SystemHold, type SystemHoldKind, type SystemRepoAttention } from '@/lib/api'

// The top-of-SuperMe attention center (Pass 2 · Q2) — one bell beside the brand that surfaces every
// `awaiting_human` hold across EVERY connected repo. Owner principle: nothing auto-stops forever; a
// hold notifies and offers a next move (Open · Approve & merge), it never disposes silently. Clicking
// the bell opens a popover that STAYS OPEN until the owner clicks elsewhere (their explicit spec).

// Per-kind chrome: icon + dot color + a one-word label. `kind` is the daemon's classification of WHY
// the item is parked; it drives which quick actions the row offers.
const KIND: Record<SystemHoldKind, { icon: LucideIcon; dot: string; label: string }> = {
  escalation: { icon: AlertTriangle, dot: 'bg-danger', label: 'Escalated' },
  breaker: { icon: Hand, dot: 'bg-warn', label: 'Paused' },
  paged: { icon: Inbox, dot: 'bg-warn', label: 'Upstream' },
  review: { icon: GitMerge, dot: 'bg-accent', label: 'Review' },
  gate: { icon: Bell, dot: 'bg-muted', label: 'Gate' },
}

export default function AttentionCenter({ onGoto }: { onGoto: (repoId: string, hold: SystemHold) => void }) {
  const [feed, setFeed] = useState<SystemRepoAttention[]>([])
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<string | null>(null) // itemId of a quick action in flight
  const ref = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ top: 0, right: 0 })

  async function pull() {
    setFeed(await getSystemAttention())
  }
  useEffect(() => {
    let alive = true
    const tick = () => getSystemAttention().then((f) => alive && setFeed(f)).catch(() => {})
    tick()
    const t = setInterval(tick, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [])

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
    // Outside-click closes (their spec: "stay until user release it by clicking other places").
    // A click inside the trigger or the portalled popover must NOT close it.
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

  // Approve & merge from the popover — the review gate's one-click action. advance_item on a review
  // item runs the same merge body the modal's button does (Pass 1 B2); on a merge conflict it 409s,
  // surfaced inline, and the item stays at review (never force-merged).
  async function approveMerge(repoId: string, h: SystemHold) {
    setBusy(h.id)
    try {
      await advanceWorkItem(h.id, repoId)
      await pull()
    } catch (e) {
      // Keep the popover open and show the daemon's own words (ApiError.toString = detail).
      window.alert(String(e))
    } finally {
      setBusy(null)
    }
  }

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
                        busy={busy === h.id}
                        onOpen={() => { setOpen(false); onGoto(r.repo_id, h) }}
                        onApprove={h.kind === 'review' ? () => approveMerge(r.repo_id, h) : undefined}
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
  h, busy, onOpen, onApprove,
}: {
  h: SystemHold
  busy: boolean
  onOpen: () => void
  onApprove?: () => void
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
          <div className="mt-1.5 flex items-center gap-2">
            <button
              type="button"
              onClick={onOpen}
              className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-fg hover:bg-hover"
            >
              <ArrowUpRight size={12} /> Open
            </button>
            {onApprove && (
              <button
                type="button"
                onClick={onApprove}
                disabled={busy}
                className="flex items-center gap-1 rounded-md bg-accent px-2 py-1 text-xs font-medium text-on-accent hover:opacity-90 disabled:opacity-50"
              >
                <GitMerge size={12} /> {busy ? 'Merging…' : 'Approve & merge'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
