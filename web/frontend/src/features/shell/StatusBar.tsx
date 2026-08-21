import { useEffect, useState } from 'react'
import { useConnection, useLiveStats } from '@/lib/live'
import { isPushOnline } from '@/lib/live/store'

// The slim status bar — the app's answer to "is what I am looking at real?"
//
// The daemon ANSWERING is connected, whatever it answers: a 404 is a working backend stating a
// fact. Only a transport failure counts as down.

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
  // Re-render on a clock so "12s ago" keeps counting. Local only: this never touches the network.
  const [, tick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [])

  const staleFor = conn.lastOkAt ? Date.now() - conn.lastOkAt : 0
  // Green is not "we once connected" but "data arrived recently": a channel quiet past every
  // cadence is reported as such.
  const quietAfter = Math.max(20_000, stats.slowestMs * 1.5 + 3000)
  const quiet = conn.online && conn.lastOkAt > 0 && staleFor > quietAfter

  const dot = !conn.online ? 'bg-danger' : quiet ? 'bg-warn' : 'bg-success'
  const text = !conn.online
    ? `Not connected — data is from ${conn.lastOkAt ? ago(staleFor) : 'before this session'}`
    : !conn.lastOkAt
      ? 'Connecting…'
      : quiet
        ? `No update for ${ago(staleFor)}`
        // The honest distinction between the daemon telling us and us asking, and the one signal
        // for the slow backstop.
        : `${isPushOnline() ? 'Push' : 'Live'} · updated ${ago(staleFor)}`

  return (
    <div className="flex h-8 shrink-0 items-center gap-3 border-t border-line bg-sidebar px-4 text-[13px] text-faint">
      <span className="font-semibold uppercase tracking-wider">Status</span>
      <span className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${dot} ${conn.online && !quiet ? 'animate-pulse' : ''}`} />
        <span className={conn.online ? '' : 'text-danger'}>{text}</span>
      </span>
      {!conn.online && <span className="text-danger/70">nothing on screen is current</span>}
      {/* The load this screen actually costs, measured rather than asserted. */}
      <span className="ml-auto tabular-nums" title="Live data feeds subscribed · request rate since load">
        {stats.watched} feeds · {stats.perMinute.toFixed(0)} req/min
      </span>
    </div>
  )
}
