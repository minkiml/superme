// The shared endpoint cache — one fetch per endpoint per interval, fanned out to every component
// that wants it (routing audit §3.2).
//
// The problem it replaces: fifteen independent `setInterval`s, each owned by whichever component
// happened to want the data, with no knowledge of each other. One screen fetched `/dev/attention`
// three times per cycle and `/dev/work-items/{id}/detail` three-to-four times, because
// `DevWorkspace`, `DevDashboard` and `WorkItemModal` each ran their own clock over the same URL.
//
// Three properties matter, and they are the reason this exists rather than a helper hook:
//
//   1. ONE request per key. Subscribers share an entry; concurrent asks share the in-flight promise.
//   2. Polling is SUBSCRIPTION-DRIVEN. A key nobody watches has no timer. That is what makes a
//      router safe: unmounting a view drops its load, mounting one costs one request, not fifteen.
//   3. Data OUTLIVES its subscribers (briefly). Returning to a board you left two seconds ago
//      renders from cache and revalidates, instead of blanking. A naive router breaks exactly here.
//
// Deliberately not a query library: the whole surface is subscribe/read/invalidate, and being ours
// is what lets `invalidate()` be driven by a server push later (§3.3) without touching a component.

// ── connection ───────────────────────────────────────────────────────────────────────────────────
// A daemon that ANSWERS is connected, whatever it answers: a 404 or a 409 is a working backend
// stating a fact, and `client.ts` raises those as `ApiError`. Only a transport failure — fetch
// itself rejecting — means the daemon is unreachable. Conflating the two would flash "disconnected"
// every time a route legitimately 404s.
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

// A gateway telling us it could not reach the daemon is NOT the daemon answering. The BFF sits
// between the browser and the daemon and replies `502 {"detail":"daemon unreachable"}` when the
// daemon is down — an `ApiError` with a status, which would otherwise read as a healthy backend.
// Verified live: killing the daemon with the BFF still up left the bar reading "Live" while nothing
// on screen was current. 5xx gateway statuses mean exactly "upstream is unreachable", so they count
// as transport failures.
const GATEWAY_DOWN = new Set([502, 503, 504])

// `ApiError` carries a status: something replied. Anything else (TypeError from fetch, an abort)
// is the transport being down — as is a gateway status, per above.
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

// What a subscriber reads. Immutable and replaced wholesale on change, so `useSyncExternalStore`
// can compare by reference (returning a fresh object each read would loop forever).
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

// How long an unsubscribed entry's data survives. Long enough that navigating away and back is
// instant; short enough that a parked tab isn't holding a snapshot of a world that moved on.
const GC_MS = 120_000

// Counters, for proving the guarantee rather than asserting it (see `stats()`).
let requestCount = 0
let startedAt = Date.now()

// ── the push backstop ────────────────────────────────────────────────────────────────────────────
// While `/ws/dashboard` is delivering invalidations, polling stops being the mechanism and becomes
// insurance against a dropped frame — so idle feeds slow right down. Losing the socket releases the
// backstop on the spot, which is what makes the degradation honest: a dead channel falls back to
// exactly the pre-push cadence rather than to a screen that has quietly stopped updating.
const BACKSTOP_MS = 30_000
// …but only for feeds that were on the ORDINARY cadence. A subscriber asking for sub-5s is saying
// "this is changing continuously and I am watching it" — a live run's token counter, which ticks
// without writing a dev event and so has nothing to push. Slowing that to 30s would trade a real
// regression for a number.
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
  // Dedup: a second caller during an in-flight request joins it rather than issuing its own. This
  // is what collapses the three-way `/dev/attention` fetch even when the three components mount in
  // the same tick.
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
      // KEEP the last good data. The caller decides what to show; `fetchedAt` is how it knows the
      // data is old, and the connection banner is how the owner knows why. Silently rendering
      // stale numbers as current is the defect this whole layer exists to remove — but throwing the
      // data away on one blip is not the fix either.
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
  // The key encodes the endpoint AND its params, so every subscriber's fetcher is the same request;
  // taking the latest keeps closures from going stale without changing what is fetched.
  e.fetcher = fetcher
  if (e.gc) { clearTimeout(e.gc); e.gc = null }

  const id = Symbol('sub')
  e.subs.set(id, { intervalMs, notify })
  e.intervalMs = effectiveInterval(e)

  // Revalidate on subscribe only when the cached copy is older than the cadence asked for. Mounting
  // a view that another view already keeps warm costs zero requests.
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
 * Refetch every WATCHED key whose name starts with one of `prefixes` — the topic channel.
 *
 * This is the seam the server push lands on (§3.3): a socket frame saying "work-items in
 * test-playground changed" becomes `invalidate(['dev:test-playground'])`, and every subscribed view
 * of that repo refetches at once. Push carries a TOPIC, never data, so push and poll can never
 * disagree about a value — the only failure mode a push channel would otherwise add.
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
  // The slowest cadence anything on screen is actually running at. "Nothing has arrived recently"
  // is only alarming relative to how often anything was DUE — a fixed threshold cannot know that,
  // and got it wrong the moment the push backstop stretched idle feeds to 30s.
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
