import { useEffect, useState } from 'react'
import { useConnection, useLiveStats } from '@/lib/live'
import { isPushOnline } from '@/lib/live/store'

// The slim full-width status bar — the app's answer to "is what I'm looking at real?"
//
// It was a placeholder reading "live run & activity signal appears here" while `useCommandStats`
// swallowed a dead daemon in a bare `catch {}`, so a backend that had stopped rendered as a quiet
// system: zeros and stale numbers, asserted confidently, with nothing anywhere saying "not
// connected". That is worse than a slow refresh — the same class of defect as a surface claiming a
// state that isn't happening — and this bar is the fix.
//
// The distinction it draws is deliberate: the daemon ANSWERING is "connected", whatever it answers.
// A 404 or a 409 is a working backend stating a fact. Only a transport failure — the fetch itself
// rejecting — counts as down (see `lib/live/store.ts`).

function ago(ms: number): string {
  const s = Math.round(ms / 1000)
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  return `${Math.round(m / 60)}h ago`
}

export default function StatusBar() {
  const conn = useConnection()
  const stats = useLiveStats(2000)
  // Re-render on a clock so "12s ago" keeps counting up while nothing else changes. Local only —
  // this never touches the network.
  const [, tick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [])

  const staleFor = conn.lastOkAt ? Date.now() - conn.lastOkAt : 0
  // Green is not "we once connected" — it is "data arrived recently". A connection that has gone
  // quiet for longer than any poll cadence is reported as such rather than left reading healthy.
  //
  // The threshold has to come FROM the cadence, not from a constant. A fixed 20s was right while
  // everything polled at 5s, then push raised idle feeds to the 30s backstop and the bar started
  // flashing "No update for 21s" for a third of every cycle on a perfectly healthy system — a
  // false alarm, which costs an honesty indicator more than a missed one. `slowestMs` is the
  // interval the slowest live feed is actually running at; a full cycle-and-a-half past it means a
  // poll was genuinely missed. The floor keeps a screen of one fast feed from being twitchy.
  const quietAfter = Math.max(20_000, stats.slowestMs * 1.5 + 3000)
  const quiet = conn.online && conn.lastOkAt > 0 && staleFor > quietAfter

  const dot = !conn.online ? 'bg-danger' : quiet ? 'bg-warn' : 'bg-success'
  const text = !conn.online
    ? `Not connected — data is from ${conn.lastOkAt ? ago(staleFor) : 'before this session'}`
    : !conn.lastOkAt
      ? 'Connecting…'
      : quiet
        ? `No update for ${ago(staleFor)}`
        // `push` vs plain `live` is the honest distinction between "the daemon tells us"
        // and "we ask every few seconds" — and it is the one signal that says whether the slow
        // backstop is in force or the ordinary cadence is carrying the screen.
        : `${isPushOnline() ? 'Push' : 'Live'} · updated ${ago(staleFor)}`

  return (
    <div className="flex h-8 shrink-0 items-center gap-3 border-t border-line bg-sidebar px-4 text-[13px] text-faint">
      <span className="font-semibold uppercase tracking-wider">Status</span>
      <span className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${dot} ${conn.online && !quiet ? 'animate-pulse' : ''}`} />
        <span className={conn.online ? '' : 'text-danger'}>{text}</span>
      </span>
      {!conn.online && <span className="text-danger/70">nothing on screen is current</span>}
      {/* The load this screen actually costs — the guarantee, measured rather than asserted.
          `feeds` is how many endpoints are subscribed right now; `req/min` is the real request rate
          since load. Both drop when a view unmounts, which is the property that makes routing safe. */}
      <span className="ml-auto tabular-nums" title="Live data feeds subscribed · request rate since load">
        {stats.watched} feeds · {stats.perMinute.toFixed(0)} req/min
      </span>
    </div>
  )
}
