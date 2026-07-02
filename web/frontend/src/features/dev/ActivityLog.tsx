import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Loader2, User, Bot, Server, ChevronDown } from 'lucide-react'
import { getDevLog, type DevEvent } from '@/lib/api'
import { fmtLocal } from '@/lib/format'
import { Empty } from './common'

const TWO_DAYS_MS = 2 * 24 * 60 * 60 * 1000

// Activity — the per-repo slice of the append-only event log (PRD §4.9). A selective read (never a
// full dump): the latest window of dev + item + global events for this context, newest first. Scope
// chips narrow the read; the global command-centre log (all repos) lives up in the Activity nav.

const ACTOR_META: Record<string, { icon: typeof User; tint: string }> = {
  owner: { icon: User, tint: 'text-core' },
  agent: { icon: Bot, tint: 'text-dev' },
  daemon: { icon: Server, tint: 'text-universal' },
}
const SCOPES = [
  { id: '', label: 'All' },
  { id: 'dev', label: 'Dev' },
  { id: 'item', label: 'Work-items' },
] as const

export default function ActivityLog({ contextId }: { contextId: string }) {
  const [events, setEvents] = useState<DevEvent[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [scope, setScope] = useState('')
  const [showAll, setShowAll] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setErr(null)
    getDevLog(contextId, { scope: scope || undefined, limit: showAll ? 500 : 200 })
      .then((d) => setEvents(d.events))
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false))
  }, [contextId, scope, showAll])

  useEffect(() => {
    load()
  }, [load])

  const cutoff = Date.now() - TWO_DAYS_MS
  const recent = (events ?? []).filter((e) => Date.parse(e.created_at) >= cutoff)
  const shown = showAll ? events ?? [] : recent
  const hiddenOlder = (events?.length ?? 0) - recent.length

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl p-6">
        <div className="mb-4 flex items-center gap-2">
          <div className="inline-flex rounded-lg border border-line bg-surface p-0.5">
            {SCOPES.map((s) => (
              <button
                key={s.id}
                onClick={() => setScope(s.id)}
                className={`rounded-md px-3 py-1 text-[13px] font-medium transition ${
                  scope === s.id ? 'bg-hover text-fg' : 'text-muted hover:text-fg'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          <span className="text-[12px] text-faint">{shown.length} events{showAll ? '' : ' · last 2 days'}</span>
          <button
            onClick={load}
            title="Refresh"
            aria-label="Refresh"
            className="ml-auto rounded-md border border-line bg-surface p-1.5 text-muted hover:bg-hover hover:text-fg"
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          </button>
        </div>

        {err ? (
          <div className="text-sm text-danger">Couldn’t load activity — {err}</div>
        ) : events === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Loading…
          </div>
        ) : shown.length === 0 ? (
          <Empty>{events && events.length > 0 ? 'No activity in the last 2 days.' : 'No activity recorded for this repo yet.'}</Empty>
        ) : (
          <>
            <ol className="relative space-y-1.5 before:absolute before:bottom-2 before:left-[9px] before:top-2 before:w-px before:bg-line">
              {shown.map((e) => (
                <EventRow key={e.id} e={e} />
              ))}
            </ol>
            {!showAll && hiddenOlder > 0 && (
              <button
                onClick={() => setShowAll(true)}
                className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg border border-line bg-surface px-4 py-2.5 text-[13px] text-muted transition hover:bg-hover hover:text-fg"
              >
                <ChevronDown size={14} /> See {hiddenOlder} older {hiddenOlder === 1 ? 'event' : 'events'}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function EventRow({ e }: { e: DevEvent }) {
  const meta = ACTOR_META[e.actor] ?? ACTOR_META.agent
  const Icon = meta.icon
  return (
    <li className="relative flex gap-3 pl-0">
      <span className="relative z-10 mt-1.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border border-line bg-surface">
        <Icon size={11} className={meta.tint} />
      </span>
      <div className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-faint">{e.kind}</span>
          {e.item_id && (
            <span className="truncate rounded bg-hover px-1.5 py-0.5 font-mono text-[10px] text-muted">{e.item_id}</span>
          )}
          <span className="ml-auto shrink-0 text-[11px] text-faint">{fmtLocal(e.created_at)}</span>
        </div>
        <p className="mt-0.5 text-[13px] leading-relaxed text-muted">{e.summary}</p>
      </div>
    </li>
  )
}
