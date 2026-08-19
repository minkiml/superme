// The router — routing-audit §3.1, slice 3.
//
// "Where am I" was nine pieces of `App` state and nothing in the URL: F5 landed on the Nexus, there
// was no back button because nothing had navigated, and nothing was linkable. This makes the path
// the single source of that answer.
//
// **Hand-written rather than react-router**, deliberately. The whole address space is a closed set
// of about ten shapes; there are no nested data loaders, no route-level code splitting, and every
// navigation in the app is a button rather than an `<a>` (so there is no link interception to do).
// What is actually needed is `pushState` + `popstate` + a small matcher, which is what this is.
// Everything goes through `useRoute`/`navigate`, so swapping in a library later is a contained
// change rather than a rewrite.
//
// Scope note (slice 3): nav surfaces + repo workspaces. Item drilldowns, the stats tiles and the PR
// page are slice 4 and stay component state until then — see §6 for the full inventory, which is
// the contract this grows into.

import { useCallback, useSyncExternalStore } from 'react'

// `workspace` is not a tab in the rail — it is the Pipeline tab's OTHER pane (the board, versus the
// capture queue). §6.1 folds it into this slot rather than nesting it under `pipeline`, because the
// two panes are peers: you are looking at one or the other, never at a workspace inside an inbox.
export const DEV_TABS = ['pipeline', 'workspace', 'project', 'activity'] as const
export type DevTab = (typeof DEV_TABS)[number]

/**
 * Global-strip tiles that open a drill-in. Addressed as `?stats=<tile>` rather than `/stats/<tile>`
 * — §6.1 tabled a path, and the reason it gave (the owner links to a token breakdown) is satisfied
 * either way, but a PATH displaces whatever was on screen. These are overlays: opened from Activity
 * with a path, the page behind the scrim silently became the Nexus, and closing had to guess where
 * to return. The query keeps the surface you were on and is equally linkable — the same treatment
 * §3.1 already gives the chat rail, for the same reason: it is orthogonal to where you are.
 */
export const STATS_TILES = ['tokens', 'ops', 'learning'] as const
export type StatsTile = (typeof STATS_TILES)[number]

export const SURFACES = ['activity', 'internals'] as const
export type Surface = (typeof SURFACES)[number]

/**
 * Sections of the System config popup, addressed as `?config=<section>`. A QUERY, like `?stats=`
 * and for the same reason: the popup opens OVER whatever you were looking at, so a path segment
 * would displace that surface and closing would have to guess where to return.
 */
export const CONFIG_SECTIONS = [
  'general', 'learning',
  'identity', 'constitution', 'skills', 'agents',
  'psettings', 'plearning', 'partifacts', 'pxray',
] as const
export type ConfigSection = (typeof CONFIG_SECTIONS)[number]

/**
 * Paths that used to be pages and are now sections of that popup. Rewritten on arrival, so an old
 * link lands on the same content instead of silently on the Nexus.
 */
const LEGACY_SECTION: Record<string, ConfigSection> = {
  '/config': 'general',
  '/foundations': 'identity',
}

/**
 * Dev-workspace tabs that became project sections of the popup. The repo stays in the path and the
 * section joins the query, so the address still names the same repo AND the same content.
 */
const LEGACY_DEV_TAB: Record<string, ConfigSection> = {
  learning: 'plearning',
  artifacts: 'partifacts',
  promptxray: 'pxray',
}

export const PHASES = ['triage', 'plan', 'build', 'vet', 'investigate', 'review', 'close'] as const
export type Phase = (typeof PHASES)[number]

// The item drilldown's two extra segments. Both are CLOSED vocabularies, which is what lets `pr`
// share the first slot without ambiguity: `pr` is not a tab, so `/item/:id/pr` can only ever mean
// the PR page.
//
// These used to be `phase/sub` — the drilldown's stepper made every phase an address. §4 replaced
// the stepper with a PROGRESS BAR that is deliberately not clickable (reading a past stage is what
// the Reports tab is for), so the address now names the TAB you are on. A phase still appears here,
// as the Reports tab's sub.
export const ITEM_TABS = ['quick', 'reports', 'trace', 'git'] as const
export type ItemTab = (typeof ITEM_TABS)[number]

// Which subs a given tab offers is the DRILLDOWN's grammar, not the router's — the router only knows
// the vocabulary. A sub that is real but wrong for its tab parses fine here and the modal corrects
// the address on arrival; keeping that mapping in one place beats duplicating it where it drifts.
export const ITEM_SUBS = ['now', 'deputy', 'proof', 'auth', 'runs', 'timeline', ...PHASES] as const
export type ItemSub = (typeof ITEM_SUBS)[number]

export type Route =
  | { name: 'nexus' }
  | { name: 'surface'; surface: Surface }
  /** The orbit node's inspector, open over the Nexus — a repo IS an address. */
  | { name: 'repo'; repoId: string }
  | { name: 'dev'; repoId: string; tab: DevTab }
  | { name: 'core'; repoId: string }
  /**
   * A work-item drilldown, open over its repo's pipeline board.
   * `tab: null` means the default tab (Quick View) — a bare item link stays valid as the tab set
   * evolves, and the modal canonicalises the address to whatever it is actually showing.
   */
  | { name: 'item'; repoId: string; itemId: string; tab: ItemTab | null; sub: ItemSub | null }
  /** The PR page — a path, but still its own document: `main.tsx` forks on it above `App` (§3.1). */
  | { name: 'pr'; repoId: string; itemId: string }

const NEXUS: Route = { name: 'nexus' }

// ── path ⇄ route ─────────────────────────────────────────────────────────────────────────────────

export function parse(pathname: string): Route {
  const seg = pathname.split('/').filter(Boolean)
  if (seg.length === 0) return NEXUS
  if (seg.length === 1 && (SURFACES as readonly string[]).includes(seg[0])) {
    return { name: 'surface', surface: seg[0] as Surface }
  }
  if (seg[0] === 'repo' && seg[1]) {
    const repoId = decodeURIComponent(seg[1])
    if (seg[2] === 'core') return { name: 'core', repoId }
    if (seg[2] === 'dev') {
      const tab = seg[3] as DevTab | undefined
      // A bare `/dev` is the Pipeline tab; an unknown tab falls back to it rather than 404ing, so a
      // renamed tab degrades to the workspace instead of throwing the owner back to the Nexus.
      return { name: 'dev', repoId, tab: tab && DEV_TABS.includes(tab) ? tab : 'pipeline' }
    }
    if (seg[2] === 'item' && seg[3]) {
      const itemId = decodeURIComponent(seg[3])
      if (seg[4] === 'pr') return { name: 'pr', repoId, itemId }
      // Unknown tokens are DROPPED rather than 404'd, and canonicalisation then rewrites the URL to
      // what is actually being shown — a renamed phase degrades to the item's current one instead of
      // bouncing the owner to the Nexus mid-review.
      const tab = (ITEM_TABS as readonly string[]).includes(seg[4]) ? (seg[4] as ItemTab) : null
      const sub = tab && (ITEM_SUBS as readonly string[]).includes(seg[5]) ? (seg[5] as ItemSub) : null
      return { name: 'item', repoId, itemId, tab, sub }
    }
    if (seg.length === 2) return { name: 'repo', repoId }
  }
  return NEXUS
}

export function build(r: Route): string {
  switch (r.name) {
    case 'nexus': return '/'
    case 'surface': return `/${r.surface}`
    case 'repo': return `/repo/${encodeURIComponent(r.repoId)}`
    case 'core': return `/repo/${encodeURIComponent(r.repoId)}/core`
    // `pipeline` is the canonical bare address, so the common case has the shorter URL.
    case 'dev': return `/repo/${encodeURIComponent(r.repoId)}/dev${r.tab === 'pipeline' ? '' : `/${r.tab}`}`
    case 'pr': return `${itemBase(r.repoId, r.itemId)}/pr`
    case 'item': {
      // A sub cannot be addressed without its tab (`/item/x//proof` is not a path), so naming a sub
      // forces the tab segment even when it is the default one.
      if (!r.tab) return itemBase(r.repoId, r.itemId)
      return `${itemBase(r.repoId, r.itemId)}/${r.tab}${r.sub ? `/${r.sub}` : ''}`
    }
  }
}

function itemBase(repoId: string, itemId: string): string {
  return `/repo/${encodeURIComponent(repoId)}/item/${encodeURIComponent(itemId)}`
}

// ── the store ────────────────────────────────────────────────────────────────────────────────────
// `popstate` fires for back/forward but NOT for our own pushState, so navigation notifies watchers
// itself. One subscriber list, read through `useSyncExternalStore` — the same shape the data layer
// uses, and for the same reason: React must not tear between the URL and what is rendered.

const watchers = new Set<() => void>()
let snapshot: Route = parse(window.location.pathname)

function refresh() {
  const legacy = LEGACY_SECTION[window.location.pathname]
  if (legacy) {
    const q = new URLSearchParams(window.location.search)
    q.set('config', legacy)
    window.history.replaceState(null, '', `/?${q.toString()}`)
  }
  const devTab = window.location.pathname.match(/^(\/repo\/[^/]+\/dev)\/([^/]+)$/)
  const movedTo = devTab && LEGACY_DEV_TAB[devTab[2]]
  if (devTab && movedTo) {
    const q = new URLSearchParams(window.location.search)
    q.set('config', movedTo)
    window.history.replaceState(null, '', `${devTab[1]}?${q.toString()}`)
  }
  snapshot = parse(window.location.pathname)
  // Canonicalise: a path that doesn't round-trip through parse/build gets rewritten in place. That
  // covers junk (`/nonsense` → `/`, which would otherwise render the Nexus under an address that
  // lies about where you are) and redundant-but-valid forms (`/repo/x/dev/pipeline` → `/repo/x/dev`,
  // so one view has one address rather than two). `replaceState`, so `back` never lands on the form
  // we just corrected. Terminates because the canonical form round-trips by definition.
  const canonical = build(snapshot)
  if (canonical !== window.location.pathname) {
    window.history.replaceState(null, '', canonical + window.location.search)
  }
  watchers.forEach((w) => w())
}

window.addEventListener('popstate', refresh)

export function current(): Route {
  return snapshot
}

/**
 * Go to `to`. The query string is CARRIED unless `search` says otherwise — the chat binding lives
 * there (§3.1: the rail is orthogonal to the path, you can be anywhere with any thread bound), so
 * a navigation must not silently drop it.
 *
 * `replace: true` for corrections that should not become a back-button stop (a redirect off an
 * unknown path, canonicalising a URL).
 */
export function navigate(to: Route, opts?: { replace?: boolean; search?: string }): void {
  const search = opts?.search ?? window.location.search
  const url = build(to) + (search && search !== '?' ? (search.startsWith('?') ? search : `?${search}`) : '')
  if (url === window.location.pathname + window.location.search) return
  if (opts?.replace) window.history.replaceState(null, '', url)
  else window.history.pushState(null, '', url)
  refresh()
}

/** Read the current route. Re-renders on navigation and on back/forward. */
export function useRoute(): Route {
  return useSyncExternalStore(
    useCallback((notify: () => void) => {
      watchers.add(notify)
      return () => { watchers.delete(notify) }
    }, []),
    current,
  )
}

// ── query params (orthogonal to the path) ───────────────────────────────────────────────────────

/** Read one query param off the live URL. */
export function param(key: string): string | null {
  return new URLSearchParams(window.location.search).get(key)
}

/**
 * Read one query param reactively. Same subscriber list as `useRoute` — an orthogonal overlay
 * (`?stats=tokens`) re-renders on open/close without the PATH having to change, which is the whole
 * point of putting it here rather than in a segment.
 */
export function useParam(key: string): string | null {
  return useSyncExternalStore(
    useCallback((notify: () => void) => {
      watchers.add(notify)
      return () => { watchers.delete(notify) }
    }, []),
    () => param(key),
  )
}

/** Set or clear one query param without touching the path or adding a history entry. */
export function setParam(key: string, value: string | null): void {
  const q = new URLSearchParams(window.location.search)
  if (value === null) q.delete(key)
  else q.set(key, value)
  const s = q.toString()
  window.history.replaceState(null, '', window.location.pathname + (s ? `?${s}` : ''))
  refresh()
}

// The first load needs the same treatment as every later navigation — the module-level `snapshot`
// above is computed before `refresh` has ever run, so a bookmarked junk path would keep its address
// until something else navigated.
refresh()
