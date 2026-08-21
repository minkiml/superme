// The router — the path is the single source of "where am I".
//
// Hand-written rather than a library: the address space is a closed set of shapes, and every
// navigation is a button rather than an anchor.

import { useCallback, useSyncExternalStore } from 'react'

// `workspace` is the Pipeline tab's OTHER pane, a peer of the capture queue rather than something
// nested under it.
export const DEV_TABS = ['pipeline', 'workspace', 'project', 'activity'] as const
export type DevTab = (typeof DEV_TABS)[number]

/**
 * Addressed as a QUERY, not a path: these are overlays, and a path would displace the surface
 * behind them.
 */
export const STATS_TILES = ['tokens', 'ops', 'learning'] as const
export type StatsTile = (typeof STATS_TILES)[number]

export const SURFACES = ['activity', 'internals'] as const
export type Surface = (typeof SURFACES)[number]

/**
 * A query, like the stats tiles: the popup opens OVER whatever you were looking at.
 */
export const CONFIG_SECTIONS = [
  'general', 'learning',
  'identity', 'constitution', 'skills', 'agents',
  'psettings', 'plearning', 'partifacts', 'pxray',
] as const
export type ConfigSection = (typeof CONFIG_SECTIONS)[number]

/**
 * Rewritten on arrival, so an old link lands on the same content instead of silently on the Nexus.
 */
const LEGACY_SECTION: Record<string, ConfigSection> = {
  '/config': 'general',
  '/foundations': 'identity',
}

/**
 * The repo stays in the path and the section joins the query, so the address still names both.
 */
const LEGACY_DEV_TAB: Record<string, ConfigSection> = {
  learning: 'plearning',
  artifacts: 'partifacts',
  promptxray: 'pxray',
}

export const PHASES = ['triage', 'plan', 'build', 'vet', 'investigate', 'review', 'close'] as const
export type Phase = (typeof PHASES)[number]

// Both are CLOSED vocabularies, which lets `pr` share the first slot without ambiguity.
export const ITEM_TABS = ['quick', 'reports', 'trace', 'git'] as const
export type ItemTab = (typeof ITEM_TABS)[number]

// Which subs a tab offers is the DRILLDOWN's grammar; the router only knows the vocabulary.
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
   * `tab: null` is the default tab, so a bare item link stays valid as the tab set evolves.
   */
  | { name: 'item'; repoId: string; itemId: string; tab: ItemTab | null; sub: ItemSub | null }
  /** The PR page — a path, but still its own document: `main.tsx` forks on it above `App`. */
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
      // An unknown tab falls back to Pipeline rather than 404ing, so a rename degrades instead of
      // bouncing home.
      return { name: 'dev', repoId, tab: tab && DEV_TABS.includes(tab) ? tab : 'pipeline' }
    }
    if (seg[2] === 'item' && seg[3]) {
      const itemId = decodeURIComponent(seg[3])
      if (seg[4] === 'pr') return { name: 'pr', repoId, itemId }
      // Unknown tokens are DROPPED, and canonicalisation rewrites the URL to what is actually
      // shown.
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
      // A sub cannot be addressed without its tab, so naming one forces the tab segment.
      if (!r.tab) return itemBase(r.repoId, r.itemId)
      return `${itemBase(r.repoId, r.itemId)}/${r.tab}${r.sub ? `/${r.sub}` : ''}`
    }
  }
}

function itemBase(repoId: string, itemId: string): string {
  return `/repo/${encodeURIComponent(repoId)}/item/${encodeURIComponent(itemId)}`
}

// ── the store ──
//
// One subscription over `popstate`, so every reader sees the same route object.

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
  // A path that does not round-trip is rewritten in place, so junk cannot render under a lying
  // address.
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
 * The query is CARRIED unless `search` says otherwise: the chat binding lives there and is
 * orthogonal to the path.
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
