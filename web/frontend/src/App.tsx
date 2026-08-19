import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Radar, Activity, SlidersHorizontal, Boxes } from 'lucide-react'
import TopBar from '@/features/shell/TopBar'
import NavColumn, { type NavRow } from '@/features/shell/NavColumn'
import StatusBar from '@/features/shell/StatusBar'
import Nexus from '@/features/shell/Nexus'
import RepoInspector from '@/features/shell/RepoInspector'
import TokenDrilldown from '@/features/shell/TokenDrilldown'
import AgentsDrilldown from '@/features/shell/AgentsDrilldown'
import LearningDrilldown from '@/features/shell/LearningDrilldown'
import { useCommandStats, type OrbitRepo } from '@/features/shell/useCommandStats'
import DevWorkspace from '@/features/dev/DevWorkspace'
import CoreDashboard from '@/features/core/CoreDashboard'
import GlobalActivity from '@/features/activity/GlobalActivity'
import SystemConfig from '@/features/config/SystemConfig'
import Internals from '@/features/internals/Internals'
import ChatPanel, { type DevBinding, type SeedTurn } from '@/features/chat/ChatPanel'
import ConnectModal from '@/features/shell/ConnectModal'
import { GLOBAL, type ContextRef } from '@/lib/contexts'
import { useFrame, CHAT_MIN, CHAT_MAX } from '@/lib/layout'
import { listContexts, type ChatMode, type Run, type SystemHold } from '@/lib/api'
import { invalidate } from '@/lib/live'
import { startPush } from '@/lib/live/push'
import { topicSystem } from '@/lib/live/keys'
import { navigate, setParam, useParam, useRoute, type Surface } from '@/lib/router'

// System & Dev local nav. Nexus (orbit) is the main entry; Me + projects are reached from orbit
// nodes, so there's no separate Functional tier in the nav.
const NAV: NavRow[] = [
  { id: 'nexus', label: 'Nexus', icon: Radar },
  { id: 'activity', label: 'Activity', icon: Activity },
  // Not a surface: this row OPENS the System config popup (`?config=`), which lives over whatever
  // you are looking at rather than replacing it.
  { id: 'config', label: 'System config', icon: SlidersHorizontal },
  { id: 'internals', label: 'Internals', icon: Boxes }, // TEMPORARY internals inventory — deletable
]

// (The chat-rail bounds and the whole three-band responsive rule live in `lib/layout` — the frame
// is the same on every page, so its arithmetic belongs in one place rather than in this file.)

const NAV_COLLAPSE_KEY = 'superme.nav.collapsed'

// The renovated cockpit: full-width top bar + global stats strip, then a row of
// [navigate · orbit · chater rail] under the strip, over a slim status bar.
export default function App() {
  // WHERE AM I is the URL, and only the URL (routing-audit §6.3). `active` and `dest` used to be
  // component state; they are gone rather than mirrored, because two writers for one fact is the
  // defect this exists to remove. Everything below reads `route`.
  const route = useRoute()
  const [contexts, setContexts] = useState<ContextRef[]>([GLOBAL])
  const [chatContext, setChatContext] = useState(GLOBAL.id)
  const [chatOpen, setChatOpen] = useState(true)
  const [chatMode, setChatMode] = useState<ChatMode>('dev') // dev-chater | core-chater
  const [connecting, setConnecting] = useState(false)
  // Chat ⇄ work-item binding: clicking a work-item takes the chat rail over as that item's dev
  // thread (opens its session, tags sends). Lifted here so DevWorkspace and ChatPanel can share it.
  const [binding, setBinding] = useState<DevBinding | null>(null)
  // A one-shot turn to birth a fresh session in the chat rail (onboarding OR diagnosis launch) — set
  // here, sent once by ChatPanel when its socket is ready, then cleared via onSeedConsumed.
  const [seed, setSeed] = useState<SeedTurn | null>(null)
  // (The PR path never reaches App — main.tsx mounts the PR page instead of the cockpit for it.)
  // Optimistic tag overrides — a saved tag shows instantly, before the /repos poll confirms it.
  const [tagOverrides, setTagOverrides] = useState<Record<string, { color: string; icon: string | null }>>({})
  // Chat rail width — owner-draggable (the default 480px is cramped for reading long agent turns).
  // Persisted so it survives reloads; clamped so the rail can never eat or vanish from the layout.
  const [chatWidth, setChatWidth] = useState(() => {
    const saved = Number(localStorage.getItem('superme.chatWidth'))
    return saved >= CHAT_MIN && saved <= CHAT_MAX ? saved : 480
  })
  // The nav rail's collapse is the OWNER's choice, held here rather than inside the rail, because
  // the frame also collapses it on its own when the window can no longer afford the labels — two
  // writers for one fact only work if the fact lives above both of them.
  const [navPref, setNavPref] = useState(() => {
    try { return localStorage.getItem(NAV_COLLAPSE_KEY) === '1' } catch { return false }
  })
  const toggleNav = useCallback(() => {
    setNavPref((c) => {
      const next = !c
      try { localStorage.setItem(NAV_COLLAPSE_KEY, next ? '1' : '0') } catch { /* private mode */ }
      return next
    })
  }, [])
  const frame = useFrame(chatWidth, navPref)
  // Below the stacking width the two bands cannot share the row, so arriving there closes the rail
  // rather than letting it cover the surface the owner just navigated to. Opening it deliberately
  // from the icon strip still works — that is the owner asking for the chat INSTEAD of the board.
  useEffect(() => { if (frame.stacked) setChatOpen(false) }, [frame.stacked])

  const dragging = useRef(false)
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return
      // The rail is right-anchored, so its width is the distance from the cursor to the viewport edge.
      setChatWidth(Math.min(CHAT_MAX, Math.max(CHAT_MIN, window.innerWidth - e.clientX)))
    }
    const onUp = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      setChatWidth((w) => { localStorage.setItem('superme.chatWidth', String(w)); return w })
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
  }, [])
  const startDrag = useCallback(() => {
    dragging.current = true
    // Hold the resize cursor + kill text selection for the whole drag, not just over the handle.
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  const rawStats = useCommandStats()
  const stats = useMemo(() => {
    if (!Object.keys(tagOverrides).length) return rawStats
    const patch = (r: OrbitRepo | null) => (r && tagOverrides[r.id] ? { ...r, ...tagOverrides[r.id] } : r)
    return { ...rawStats, hub: patch(rawStats.hub), nodes: rawStats.nodes.map((r) => patch(r) as OrbitRepo) }
  }, [rawStats, tagOverrides])

  useEffect(() => {
    listContexts()
      .then((cs) => cs.length && setContexts(cs))
      .catch(() => {
        /* daemon may be down; the global seed still works */
      })
  }, [])

  // The dashboard's live-push channel, opened once for the tab and kept open across every
  // navigation (it is not tied to any view, which is why it lives here and not in a feature).
  // It carries invalidation TOPICS only — the cache still reads every value over HTTP — and while
  // it is up the polls drop to a slow backstop. See lib/live/push.ts.
  useEffect(() => startPush(), [])

  // The repo a repo-scoped route names. `null` while the roster is still loading — which is the
  // whole reason this is derived rather than stored: deep-linking to /repo/x/dev arrives before
  // /repos has answered, and the old `dest` object could not exist until something clicked it.
  const roster = [stats.hub, ...stats.nodes].filter((r): r is OrbitRepo => !!r)
  const repoOf = (id: string) => roster.find((r) => r.id === id) ?? null
  const routeRepoId =
    route.name === 'dev' || route.name === 'core' || route.name === 'repo' || route.name === 'item'
      ? route.repoId
      : null
  const routeRepo = routeRepoId ? repoOf(routeRepoId) : null
  // A repo id in the path that the roster doesn't have (disconnected, renamed, mistyped) must not
  // hang on a spinner forever — once the roster HAS loaded and still doesn't know it, go home.
  useEffect(() => {
    if (routeRepoId && !stats.loading && !routeRepo) navigate({ name: 'nexus' }, { replace: true })
  }, [routeRepoId, routeRepo, stats.loading])

  // Opening a repo surface points the chat rail at that repo, exactly as the click handlers did —
  // now keyed off ARRIVING at the address, so a deep link and a click behave identically instead of
  // each carrying its own copy of the rule.
  useEffect(() => {
    if (route.name !== 'dev' && route.name !== 'core' && route.name !== 'item') return
    const repoId = route.repoId
    // Keep a binding that already belongs to this repo. The attention centre binds the item's own
    // session and THEN navigates (deliberately — letting the board bind after mount raced the
    // established-gate and lost); clearing unconditionally here would undo that every time.
    setBinding((b) => (b && b.contextId === repoId ? b : null))
    setChatContext(repoId)
    setChatMode(route.name === 'core' ? 'core' : 'dev')
    // Tab changes and phase/sub changes must NOT re-run this: `route.name` and the repo id are the
    // only things that mean "you arrived somewhere new".
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.name, routeRepoId])

  // Launch a diagnosis session from an Activity row: point the rail at the run's repo dev thread and
  // seed a read-only diagnosis turn (kind=diagnosis + subject_run_id). The daemon injects the run's
  // trace so the session starts oriented. (session-kinds-diagnose)
  function launchDiagnosis(run: Run, query: string) {
    setBinding(null)
    setChatContext(run.repo_id)
    setChatMode('dev')
    setChatOpen(true)
    setSeed({ prompt: query, kind: 'diagnosis', subjectRunId: run.id })
  }

  // Discuss a NOTE — the owner's own jotting, which never becomes work and so has no item session
  // of its own to open. It gets a fresh GENERAL session instead, born with a turn naming the note by
  // id: the agent reads it through `read_inbox` and picks the conversation up from there. Same seed
  // machinery as a diagnosis, minus the kind — this is an ordinary chat that happens to start
  // pointed at something.
  function discussNote(repoId: string, inboxId: number, title: string) {
    setBinding(null)
    setChatContext(repoId)
    setChatMode('dev')
    setChatOpen(true)
    setSeed({ prompt: `Let's talk about my note inbox:${inboxId} — "${title}". Read it, then tell me what you make of it.` })
  }

  // Attention center → jump to an item. One `navigate`, where this used to be six ordered `set*`
  // calls with a comment explaining which order avoided a race (§6.3): the path IS the destination,
  // and the drilldown now has an address of its own rather than a `focusItem` request handed across
  // a mount boundary and consumed once.
  //
  // The binding is still set HERE rather than left to the board's arrival effect: the hold carries
  // `session_id`, so the rail can open the item's own thread immediately instead of waiting for the
  // board's data to land (Fix C — the late bind raced the established-gate and lost).
  function gotoItem(repoId: string, hold: SystemHold) {
    setBinding({ workItemId: hold.id, sessionId: hold.session_id ?? null, title: hold.title, contextId: repoId })
    setChatOpen(true)
    navigate({ name: 'item', repoId, itemId: hold.id, tab: null, sub: null })
  }

  // Which global-strip tile is drilled in — read from `?stats=`, so the surface underneath is
  // untouched and closing is just dropping the param (no history entry: a modal's exit is its
  // close button, not the back gesture).
  const drill = useParam('stats')
  const closeDrill = () => setParam('stats', null)
  // Which System config section is open, if any — same query-overlay treatment as `?stats=`.
  const configSection = useParam('config')

  // The inspector is the `repo` route, not a selection: `/repo/:id` is an address you can link to.
  const selectedRepo = route.name === 'repo' ? routeRepo : null
  // The tag of the repo the chat rail is currently talking to (for the chater header).
  const chatRepo = repoOf(chatContext)
  const chatTag = chatRepo ? { color: chatRepo.color, icon: chatRepo.icon, isHub: chatRepo.id === 'global' } : undefined

  // `/` and `/repo/:id` both render the Nexus — the second with the inspector open over it.
  const nexus = (
    <Nexus
      stats={stats}
      selectedId={route.name === 'repo' ? route.repoId : null}
      onSelectRepo={(id) => navigate(id ? { name: 'repo', repoId: id } : { name: 'nexus' })}
      onConnect={() => setConnecting(true)}
    />
  )

  function Main() {
    if (route.name === 'dev' || route.name === 'core' || route.name === 'item') {
      // The roster hasn't answered yet — a deep link arrives here before /repos does. Hold the
      // frame rather than flashing the Nexus; the redirect effect above handles a repo that turns
      // out not to exist.
      if (!routeRepo) return <div className="p-6 text-sm text-muted">Loading {route.repoId}…</div>
      if (route.name === 'core') return <CoreDashboard repo={routeRepo} onExit={() => navigate({ name: 'nexus' })} />
      return (
        <DevWorkspace
          repo={routeRepo}
          // An item drilldown is an overlay ON the pipeline board, so its address implies that tab.
          tab={route.name === 'item' ? 'pipeline' : route.tab}
          onTabChange={(tab) => navigate({ name: 'dev', repoId: routeRepo.id, tab })}
          onExit={() => navigate({ name: 'nexus' })}
          repos={roster}
          onSwitch={(r) => navigate({ name: 'dev', repoId: r.id, tab: 'pipeline' })}
          onDiscussNote={(id, title) => discussNote(routeRepo.id, id, title)}
          boundItemId={binding?.workItemId ?? null}
          onBindItem={(it, ctx) => {
            // Take the chat over as this item's dev thread and reveal the rail.
            setBinding({ workItemId: it.id, sessionId: it.session_id ?? null, title: it.title || it.id, contextId: ctx })
            setChatContext(ctx)
            setChatMode('dev')
            setChatOpen(true)
          }}
          onUnbindItem={() => setBinding(null)}
        />
      )
    }
    if (route.name === 'surface') {
      if (route.surface === 'activity') return <GlobalActivity stats={stats} onDiagnose={launchDiagnosis} />
      return <Internals />
    }
    return nexus
  }

  // Which nav row reads as current. A repo surface belongs to the Nexus branch (that is where you
  // came from and where Back goes), so it keeps the Nexus row lit rather than lighting nothing.
  // The config row lights while its popup is open — it is the only row that is not a destination.
  const navActive = configSection ? 'config' : route.name === 'surface' ? route.surface : 'nexus'

  return (
    <div className="flex h-full flex-col bg-app font-sans text-fg">
      {/* A stat's breakdown is in the URL (`?stats=tokens`), not a component flag — linkable, and
          it survives a refresh. In the QUERY rather than a path segment because it is an overlay:
          it belongs over whatever surface you are on, and a path would have replaced that surface.
          `projects` has no overlay of its own — Nexus IS the projects view, so it navigates. */}
      <TopBar
        stats={stats}
        onGoto={gotoItem}
        onDetails={(id) => (id === 'projects' ? navigate({ name: 'nexus' })
                                             : setParam('stats', id))}
      />

      <div className="flex min-h-0 flex-1">
        <NavColumn
          items={NAV}
          active={navActive}
          collapsed={frame.navIcons}
          onToggle={toggleNav}
          onSelect={(id) =>
            id === 'config'
              ? setParam('config', 'general')
              : navigate(id === 'nexus' ? { name: 'nexus' } : { name: 'surface', surface: id as Surface })
          }
        />
        {/* Stacked + open ⇒ the chat IS the surface; main is unmounted from the row rather than
            squeezed behind it. `hidden` and not a conditional render, so the surface keeps its
            state (scroll position, open drilldown) while the chat is being read. */}
        <main className={frame.stacked && chatOpen ? 'hidden' : 'min-w-0 flex-1 overflow-hidden'}>{Main()}</main>

        {/* persistent chater rail — kept mounted; full panel when open, a slim quick-switch rail
            (recent sessions + dev/core toggle) when collapsed. */}
        {/* drag handle — only while the rail is open (the collapsed rail has a fixed width) */}
        {chatOpen && !frame.stacked && (
          <div
            onMouseDown={startDrag}
            title="Drag to resize the chat"
            // 8px grab target — a hairline is accurate to render but miserable to actually hit.
            className="w-2 shrink-0 cursor-col-resize bg-transparent transition hover:bg-accent/40"
          />
        )}
        {/* Three states, one element: the icon strip (closed), a resizable band beside the surface,
            or — when the row is too narrow to split — the whole row. The width is `frame.railWidth`,
            never the raw preference: see `lib/layout`. */}
        <div
          style={chatOpen && !frame.stacked ? { width: frame.railWidth } : undefined}
          className={`shrink-0 border-l border-line ${
            !chatOpen ? 'w-14' : frame.stacked ? 'min-w-0 flex-1' : ''
          }`}
        >
          <ChatPanel
            key={`${chatContext}:${chatMode}`}
            contextId={chatContext}
            contexts={contexts}
            tag={chatTag}
            onContextChange={setChatContext}
            onCollapse={() => setChatOpen(false)}
            mode={chatMode}
            onModeChange={setChatMode}
            binding={binding && binding.contextId === chatContext && chatMode === 'dev' ? binding : null}
            onUnbind={() => setBinding(null)}
            onBindingSession={(sid) => setBinding((b) => (b ? { ...b, sessionId: sid } : b))}
            seed={seed}
            onSeedConsumed={() => setSeed(null)}
            collapsed={!chatOpen}
            onExpand={() => setChatOpen(true)}
          />
        </div>
      </div>

      <RepoInspector
        repo={selectedRepo}
        onClose={() => navigate({ name: 'nexus' })}
        // The chat rail follows you to the repo you open — now handled once, by the arrival effect
        // above, instead of repeated in each opener.
        onOpenDev={(r) => navigate({ name: 'dev', repoId: r.id, tab: 'pipeline' })}
        onOpenCore={(r) => navigate({ name: 'core', repoId: r.id })}
        onTagSaved={(id, patch) => setTagOverrides((o) => ({ ...o, [id]: patch }))}
        onDisconnected={(id) => {
          // The repo is gone — leave any address pointing at it, and drop the rail's hold on it.
          if (binding?.contextId === id) setBinding(null)
          if (chatContext === id) setChatContext('global')
          navigate({ name: 'nexus' }, { replace: true })
        }}
      />
      {configSection && (
        <SystemConfig
          repos={roster}
          // Opened over a repo surface, the picker starts on that repo — the project you are looking
          // at is the project you came here to configure.
          initialRepoId={routeRepoId}
          onClose={() => setParam('config', null)}
        />
      )}
      {drill === 'tokens' && <TokenDrilldown stats={stats} onClose={closeDrill} />}
      {drill === 'ops' && <AgentsDrilldown stats={stats} onClose={closeDrill} />}
      {drill === 'learning' && <LearningDrilldown onClose={closeDrill} />}
      {connecting && (
        <ConnectModal
          onClose={() => setConnecting(false)}
          onConnected={() => {
            setConnecting(false)
            // Refresh the chat context list so the new repo is selectable, and the roster with it so
            // the orbit node appears at once rather than on its next tick.
            listContexts().then((cs) => cs.length && setContexts(cs)).catch(() => {})
            invalidate(topicSystem)
          }}
        />
      )}

      {/* The honest answer to "is this data real?" — one connection status for the whole app, plus
          the live feed/request count. Deliberately at the bottom edge, always visible: stale data
          must never render as current, and until this existed a dead daemon looked like a quiet one. */}
      <StatusBar />
    </div>
  )
}

// (There is no onboarding kickoff prompt. Onboarding is a repo STATE, not a launched action: while
// a dev repo's memory is unestablished, ws.py already treats every general session in it as an
// onboarding session and `onboarding_preamble(mode)` already carries the skill directive in the
// system prompt — invisible to the owner, absent from the transcript and the activity trail. The
// owner's first message is their project description, full stop. See OnboardingLanding.)

