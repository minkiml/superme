// React bindings over the shared endpoint cache — the only import site a component needs.

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

// Callers that genuinely need faster pass it explicitly; once push invalidation is up these are a
// safety net.
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
 * Subscribe to one endpoint; every component asking for the same `key` shares one request and one
 * timer.
 *
 * `null` disables the subscription entirely, and `intervalMs: 0` fetches once.
 */
export function useLive<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  intervalMs: number = DEFAULT_INTERVAL,
): LiveResult<T> {
  // The fetcher is a fresh closure every render, so hand the store a stable wrapper.
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
 * Call after a mutation, so the change appears at once instead of on the next tick.
 */
export function invalidate(...prefixes: string[]): void {
  storeInvalidate(prefixes)
}

/**
 * For measuring the dashboard rather than asserting it. Plain module state, so this re-renders on
 * a local clock.
 */
export function useLiveStats(intervalMs = 1000): ReturnType<typeof storeStats> {
  const [, tick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return storeStats()
}
