import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Radar, Activity, SlidersHorizontal, Boxes } from 'lucide-react'
import TopBar from '@/features/shell/TopBar'
import CredentialBanner from '@/features/shell/CredentialBanner'
import OrphanedRepoBanner from '@/features/shell/OrphanedRepoBanner'
import CredentialSetup from '@/features/shell/CredentialSetup'
import { useAuthGate } from '@/lib/authGate'
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

// Nexus is the main entry; Me and the projects are reached from orbit nodes, so there is no
// separate tier.
const NAV: NavRow[] = [
  { id: 'nexus', label: 'Nexus', icon: Radar },
  { id: 'activity', label: 'Activity', icon: Activity },
  // Not a surface: this row opens the config popup, which lives over whatever you are looking at.
  { id: 'config', label: 'System config', icon: SlidersHorizontal },
  { id: 'internals', label: 'Internals', icon: Boxes }, // TEMPORARY internals inventory — deletable
]

// The chat-rail bounds and the three-band responsive rule live in `lib/layout` — the frame is the
// same on every page.

const NAV_COLLAPSE_KEY = 'superme.nav.collapsed'

// The cockpit: a top bar and stats strip over a row of nav, orbit and chat rail, over a slim status
// bar.
export default function App() {
  // WHERE AM I is the URL, and only the URL: two writers for one fact is the defect.
  const route = useRoute()
  const [contexts, setContexts] = useState<ContextRef[]>([GLOBAL])
  const [chatContext, setChatContext] = useState(GLOBAL.id)
  const [chatOpen, setChatOpen] = useState(true)
  const [chatMode, setChatMode] = useState<ChatMode>('dev') // dev-chater | core-chater
  const [connecting, setConnecting] = useState(false)
  // Clicking a work-item takes the chat rail over as that item's thread. Lifted here so both panes
  // share it.
  const [binding, setBinding] = useState<DevBinding | null>(null)
  // A one-shot turn to birth a fresh session, sent once by the rail when its socket is ready, then
  // cleared.
  const [seed, setSeed] = useState<SeedTurn | null>(null)
  // Optimistic tag overrides — a saved tag shows instantly, before the roster poll confirms it.
  const [tagOverrides, setTagOverrides] = useState<Record<string, { color: string; icon: string | null }>>({})
  // Owner-draggable, persisted so it survives reloads, and clamped so the rail can never eat the
  // layout.
  const [chatWidth, setChatWidth] = useState(() => {
    const saved = Number(localStorage.getItem('superme.chatWidth'))
    return saved >= CHAT_MIN && saved <= CHAT_MAX ? saved : 480
  })
  // The owner's collapse choice lives here, above the rail, because the frame also collapses it on
  // its own.
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
  // Below the stacking width, arriving here closes the rail rather than covering the board.
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

  // Opened once for the tab and kept across every navigation. It carries invalidation TOPICS only;
  // values still come over HTTP.
  useEffect(() => startPush(), [])

  // Derived, not stored: a deep link arrives before the roster has answered.
  const roster = [stats.hub, ...stats.nodes].filter((r): r is OrbitRepo => !!r)
  const repoOf = (id: string) => roster.find((r) => r.id === id) ?? null
  const routeRepoId =
    route.name === 'dev' || route.name === 'core' || route.name === 'repo' || route.name === 'item'
      ? route.repoId
      : null
  const routeRepo = routeRepoId ? repoOf(routeRepoId) : null
  // A repo id the roster does not have must not hang forever: once loaded and still unknown, go
  // home.
  useEffect(() => {
    if (routeRepoId && !stats.loading && !routeRepo) navigate({ name: 'nexus' }, { replace: true })
  }, [routeRepoId, routeRepo, stats.loading])

  // Keyed off ARRIVING at the address, so a deep link and a click behave identically.
  useEffect(() => {
    if (route.name !== 'dev' && route.name !== 'core' && route.name !== 'item') return
    const repoId = route.repoId
    // Keep a binding that already belongs to this repo: the attention centre binds before it
    // navigates.
    setBinding((b) => (b && b.contextId === repoId ? b : null))
    setChatContext(repoId)
    setChatMode(route.name === 'core' ? 'core' : 'dev')
    // `route.name` and the repo id are the only things that mean "you arrived somewhere new".
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.name, routeRepoId])

  // Point the rail at the run's repo thread and seed a read-only diagnosis turn; the daemon injects
  // the trace.
  function launchDiagnosis(run: Run, query: string) {
    setBinding(null)
    setChatContext(run.repo_id)
    setChatMode('dev')
    setChatOpen(true)
    setSeed({ prompt: query, kind: 'diagnosis', subjectRunId: run.id })
  }

  // A note never becomes work, so it gets a fresh general session, born naming the note.
  function discussNote(repoId: string, inboxId: number, title: string) {
    setBinding(null)
    setChatContext(repoId)
    setChatMode('dev')
    setChatOpen(true)
    setSeed({ prompt: `Let's talk about my note inbox:${inboxId} — "${title}". Read it, then tell me what you make of it.` })
  }

  // Jump to an item. The binding is set HERE, because the hold carries a session id.
  function gotoItem(repoId: string, hold: SystemHold) {
    setBinding({ workItemId: hold.id, sessionId: hold.session_id ?? null, title: hold.title, contextId: repoId })
    setChatOpen(true)
    navigate({ name: 'item', repoId, itemId: hold.id, tab: null, sub: null })
  }

  // Read from the query, so the surface underneath is untouched and closing is just dropping the
  // param.
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
      // A deep link arrives before the roster does, so hold the frame rather than flashing the
      // Nexus.
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

  // A repo surface belongs to the Nexus branch — that is where you came from and where Back goes.
  const navActive = configSection ? 'config' : route.name === 'surface' ? route.surface : 'nexus'

  // With no credential nothing runs, so the guide IS the app until there is one. It is a ROUTE,
  // so leaving it is a navigation the Back button can undo — the previous version held this in
  // component state, which made "Look around first" a door with no handle on the other side.
  const auth = useAuthGate()
  const sentToSetup = useRef(false)
  useEffect(() => {
    // Once per load, and only from the address you land on by default: after that the URL is the
    // truth, or looking around would bounce straight back here.
    if (auth.ready || sentToSetup.current || route.name !== 'nexus') return
    sentToSetup.current = true
    navigate({ name: 'setup' }, { replace: true })
  }, [auth.ready, route.name])

  useEffect(() => {
    // The guide is an address for a CONDITION, so it has to leave when the condition does —
    // otherwise "I've done that" succeeds and shows you the instructions again. Replace, because
    // Back to a guide you no longer need is not a place worth returning to.
    if (auth.ready && route.name === 'setup') navigate({ name: 'nexus' }, { replace: true })
  }, [auth.ready, route.name])

  if (route.name === 'setup') {
    return (
      <div className="flex h-full flex-col bg-app font-sans text-fg">
        <CredentialSetup status={auth.status} onSkip={() => navigate({ name: 'nexus' })} />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col bg-app font-sans text-fg">
      {/* In the QUERY rather than a path, because it is an overlay: it belongs over the surface
          you are on. */}
      <TopBar
        stats={stats}
        onGoto={gotoItem}
        onDetails={(id) => (id === 'projects' ? navigate({ name: 'nexus' })
                                             : setParam('stats', id))}
      />

      <CredentialBanner onOpenSetup={() => navigate({ name: 'setup' })} />
      <OrphanedRepoBanner />

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
        {/* Stacked and open, the chat IS the surface. `hidden` rather than a conditional render,
            so the surface keeps its state. */}
        <main className={frame.stacked && chatOpen ? 'hidden' : 'min-w-0 flex-1 overflow-hidden'}>{Main()}</main>

        {/* kept mounted: a full panel when open, a slim quick-switch rail when collapsed */}
        {/* drag handle — only while the rail is open (the collapsed rail has a fixed width) */}
        {chatOpen && !frame.stacked && (
          <div
            onMouseDown={startDrag}
            title="Drag to resize the chat"
            // 8px grab target — a hairline is accurate to render but miserable to actually hit.
            className="w-2 shrink-0 cursor-col-resize bg-transparent transition hover:bg-accent/40"
          />
        )}
        {/* Three states, one element. The width is `frame.railWidth`, never the raw preference. */}
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
        // The chat rail follows you to the repo you open, handled once by the arrival effect above.
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
          // The project you are looking at is the project you came here to configure.
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
            // Refresh the context list and the roster, so the new repo is selectable and its node
            // appears at once.
            listContexts().then((cs) => cs.length && setContexts(cs)).catch(() => {})
            invalidate(topicSystem)
          }}
        />
      )}

      {/* One connection status for the whole app, at the bottom edge and always visible: stale
          data must never look current. */}
      <StatusBar />
    </div>
  )
}

// There is no onboarding kickoff prompt: onboarding is a repo STATE, and the daemon already treats
// every session in an unestablished repo as one.

