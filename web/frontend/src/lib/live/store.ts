// The shared endpoint cache — one fetch per endpoint per interval, fanned out to everyone who wants
// it.
//
// ONE request per key, polling that is SUBSCRIPTION-DRIVEN, and data that briefly outlives its
// subscribers.

// ── connection ──
//
// A daemon that ANSWERS is connected, whatever it answers: a 404 is a working backend stating a
// fact. Only a transport failure means it is unreachable.
export type Connection = {
  online: boolean
  lastOkAt: number // epoch ms of the last successful response (0 = never)
  since: number // epoch ms the current online/offline state began
}

let connection: Connection = { online: true, lastOkAt: 0, since: Date.now() }
const connWatchers = new Set<() => void>()

function setConnection(next: Connection) {
  connection = next
  connWatchers.forEach((w) => w())
}

export function readConnection(): Connection {
  return connection
}

export function watchConnection(notify: () => void): () => void {
  connWatchers.add(notify)
  return () => connWatchers.delete(notify)
}

// A gateway saying it could not reach the daemon is NOT the daemon answering.
//
// The BFF replies 502 when the daemon is down, which would otherwise read as a healthy backend, so
// gateway statuses count as transport failures.
const GATEWAY_DOWN = new Set([502, 503, 504])

// `ApiError` carries a status, so something replied. Anything else is the transport being down.
function isTransportFailure(e: unknown): boolean {
  const status = e && typeof e === 'object' ? (e as { status?: number }).status : undefined
  if (typeof status !== 'number') return true
  return GATEWAY_DOWN.has(status)
}

function markOk() {
  const now = Date.now()
  setConnection({ online: true, lastOkAt: now, since: connection.online ? connection.since : now })
}

function markDown() {
  if (!connection.online) return // already down — keep `since` at the first failure, not the latest
  setConnection({ ...connection, online: false, since: Date.now() })
}

// ── the cache ────────────────────────────────────────────────────────────────────────────────────

// Immutable and replaced wholesale, so `useSyncExternalStore` can compare by reference.
export type Snapshot<T> = {
  data: T | undefined
  error: Error | undefined
  fetchedAt: number // last SUCCESSFUL fetch (0 = never landed)
  loading: boolean // a request is in flight AND we have nothing yet
}

const EMPTY: Snapshot<unknown> = { data: undefined, error: undefined, fetchedAt: 0, loading: true }

type Sub = { intervalMs: number; notify: () => void }

type Entry = {
  key: string
  fetcher: () => Promise<unknown>
  snap: Snapshot<unknown>
  inflight: Promise<void> | null
  timer: ReturnType<typeof setInterval> | null
  intervalMs: number // effective cadence = the FASTEST any subscriber asked for
  subs: Map<symbol, Sub>
  gc: ReturnType<typeof setTimeout> | null
}

const entries = new Map<string, Entry>()

// Long enough that navigating away and back is instant, short enough that a parked tab is not
// holding a stale world.
const GC_MS = 120_000

// Counters, for proving the guarantee rather than asserting it (see `stats()`).
let requestCount = 0
let startedAt = Date.now()

// ── the push backstop ──
//
// While the socket delivers invalidations, polling is insurance against a dropped frame, so idle
// feeds slow right down. Losing the socket releases the backstop on the spot.
const BACKSTOP_MS = 30_000
// Only for feeds on the ORDINARY cadence: a sub-5s subscriber is watching something that ticks
// without writing an event.
const BACKSTOP_FLOOR_MS = 5000

let pushOnline = false

/** Called by the push client when the channel comes up (true) or drops (false). */
export function setPushOnline(on: boolean): void {
  if (pushOnline === on) return
  pushOnline = on
  entries.forEach((e) => { if (e.subs.size) schedule(e) }) // re-time every live feed at once
}

export function isPushOnline(): boolean {
  return pushOnline
}

function effectiveInterval(e: Entry): number {
  let ms = Infinity
  e.subs.forEach((s) => { ms = Math.min(ms, s.intervalMs) })
  if (!Number.isFinite(ms)) return 0
  return pushOnline && ms >= BACKSTOP_FLOOR_MS ? BACKSTOP_MS : ms
}

function publish(e: Entry, snap: Snapshot<unknown>) {
  e.snap = snap
  e.subs.forEach((s) => s.notify())
}

function run(e: Entry): Promise<void> {
  // A second caller during an in-flight request joins it rather than issuing its own.
  if (e.inflight) return e.inflight
  requestCount += 1
  const p = e.fetcher()
    .then((data) => {
      markOk()
      // Never blank on refetch: a successful result replaces data, and clears any prior error.
      publish(e, { data, error: undefined, fetchedAt: Date.now(), loading: false })
    })
    .catch((err: unknown) => {
      if (isTransportFailure(err)) markDown()
      else markOk() // the daemon answered — it is up, this key just failed
      // KEEP the last good data: `fetchedAt` is how the caller knows it is old.
      publish(e, { ...e.snap, error: err as Error, loading: false })
    })
    .finally(() => { e.inflight = null })
  e.inflight = p
  return p
}

function schedule(e: Entry) {
  if (e.timer) { clearInterval(e.timer); e.timer = null }
  const ms = effectiveInterval(e)
  if (!ms || !e.subs.size) return // nobody watching, or a subscriber that never wants a re-poll
  e.timer = setInterval(() => run(e), ms)
}

/** Subscribe to `key`. Returns the unsubscribe. `intervalMs: 0` means fetch once, never re-poll. */
export function subscribe(
  key: string,
  fetcher: () => Promise<unknown>,
  intervalMs: number,
  notify: () => void,
): () => void {
  let e = entries.get(key)
  if (!e) {
    e = { key, fetcher, snap: EMPTY, inflight: null, timer: null, intervalMs, subs: new Map(), gc: null }
    entries.set(key, e)
  }
  // The key encodes the endpoint AND its params, so taking the latest fetcher cannot change what is
  // fetched.
  e.fetcher = fetcher
  if (e.gc) { clearTimeout(e.gc); e.gc = null }

  const id = Symbol('sub')
  e.subs.set(id, { intervalMs, notify })
  e.intervalMs = effectiveInterval(e)

  // Revalidate on subscribe only when the cached copy is older than the cadence asked for.
  const age = e.snap.fetchedAt ? Date.now() - e.snap.fetchedAt : Infinity
  if (age >= (intervalMs || Infinity) || !e.snap.fetchedAt) run(e)
  schedule(e)

  const entry = e
  return () => {
    entry.subs.delete(id)
    entry.intervalMs = effectiveInterval(entry)
    if (entry.subs.size) { schedule(entry); return }
    // Last watcher left: stop the clock now, keep the data for a while.
    if (entry.timer) { clearInterval(entry.timer); entry.timer = null }
    entry.gc = setTimeout(() => entries.delete(entry.key), GC_MS)
  }
}

export function read<T>(key: string): Snapshot<T> {
  return (entries.get(key)?.snap ?? EMPTY) as Snapshot<T>
}

/** Refetch one key now, if anything is watching it. Used by mutations that know what they changed. */
export function refresh(key: string): void {
  const e = entries.get(key)
  if (e && e.subs.size) run(e)
}

/**
 * Refetch every WATCHED key under one of `prefixes` — the seam the server push lands on.
 *
 * Push carries a TOPIC, never data, so push and poll can never disagree about a value.
 */
export function invalidate(prefixes: string[]): void {
  entries.forEach((e) => {
    if (e.subs.size && prefixes.some((p) => e.key.startsWith(p))) run(e)
  })
}

/** Live counters — what makes "the dashboard is efficient" checkable instead of claimed. */
export function stats() {
  let watched = 0
  let timers = 0
  // "Nothing has arrived recently" is only alarming relative to how often anything was DUE.
  let slowestMs = 0
  entries.forEach((e) => {
    if (!e.subs.size) return
    watched += 1
    slowestMs = Math.max(slowestMs, effectiveInterval(e))
    if (e.timer) timers += 1
  })
  const secs = Math.max(1, (Date.now() - startedAt) / 1000)
  return {
    requests: requestCount,
    perMinute: (requestCount / secs) * 60,
    keys: entries.size,
    watched,
    timers,
    slowestMs,
    sinceMs: Date.now() - startedAt,
  }
}

/** Reset the counters (so a measurement can be scoped to one screen rather than the whole session). */
export function resetStats(): void {
  requestCount = 0
  startedAt = Date.now()
}
