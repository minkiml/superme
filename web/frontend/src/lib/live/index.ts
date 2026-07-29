// React bindings over the shared endpoint cache (`store.ts`). This is the only import site a
// component needs: `useLive` replaces the `useState + useEffect + setInterval + let alive` block
// that every polling component grew its own copy of.

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import {
  invalidate as storeInvalidate,
  read,
  readConnection,
  refresh as storeRefresh,
  stats as storeStats,
  subscribe,
  watchConnection,
  type Connection,
  type Snapshot,
} from './store'

export { resetStats } from './store'
export type { Connection, Snapshot } from './store'

// Default cadence. The old timers ranged 1s–10s with no principle behind the numbers; 5s is what
// most of them already used and what the audit measured against. Callers that genuinely need
// faster (a live run's detail) pass it explicitly, and once push invalidation lands (§3.3) these
// become a safety net rather than the mechanism.
const DEFAULT_INTERVAL = 5000

const DISABLED: Snapshot<never> = { data: undefined, error: undefined, fetchedAt: 0, loading: false }
const noop = () => () => {}

export type LiveResult<T> = Snapshot<T> & {
  /** True when the newest data is older than twice its cadence — i.e. a poll has been missed. */
  stale: boolean
  /** Refetch now (after a mutation this view made). */
  refresh: () => void
}

/**
 * Subscribe to one endpoint. Every component asking for the same `key` shares one request and one
 * timer, so three views of `/dev/attention` cost one fetch per cycle instead of three.
 *
 * `key` must encode the endpoint AND its params — it is the cache identity. `null` disables the
 * subscription entirely (nothing fetched, no timer), which is how a component with no repo selected
 * or a collapsed panel drops its load.
 *
 * `intervalMs: 0` fetches once and never re-polls (config, rosters — things that change only when
 * the owner changes them, and can be `refresh()`ed at that moment).
 */
export function useLive<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  intervalMs: number = DEFAULT_INTERVAL,
): LiveResult<T> {
  // The fetcher is a fresh closure every render; the store must not re-subscribe over that. Keep it
  // in a ref and hand the store a stable wrapper.
  const fn = useRef(fetcher)
  fn.current = fetcher
  const stable = useCallback(() => fn.current(), [])

  const sub = useCallback(
    (notify: () => void) => (key ? subscribe(key, stable, intervalMs, notify) : noop()),
    [key, intervalMs, stable],
  )
  const snap = useSyncExternalStore(sub, () => (key ? read<T>(key) : (DISABLED as Snapshot<T>)))

  const refresh = useCallback(() => { if (key) storeRefresh(key) }, [key])
  const stale = !!snap.fetchedAt && !!intervalMs && Date.now() - snap.fetchedAt > intervalMs * 2

  return useMemo(() => ({ ...snap, stale, refresh }), [snap, stale, refresh])
}

/** The one connection status, app-wide. Drives StatusBar and anything that must not lie. */
export function useConnection(): Connection {
  return useSyncExternalStore(
    useCallback((notify: () => void) => watchConnection(notify), []),
    readConnection,
  )
}

/**
 * Refetch every subscribed key under `prefixes` — call after a mutation so the change appears at
 * once instead of on the next tick. `invalidate('dev:my-repo')` covers every view of that repo.
 */
export function invalidate(...prefixes: string[]): void {
  storeInvalidate(prefixes)
}

/**
 * Live request counters, for measuring the dashboard rather than asserting it. The counters are
 * plain module state with no change notification, so this re-renders on a local clock that touches
 * no network — it exists only on the diagnostics surface.
 */
export function useLiveStats(intervalMs = 1000): ReturnType<typeof storeStats> {
  const [, tick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return storeStats()
}
