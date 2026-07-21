import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Radar, Layers, Activity, SlidersHorizontal, Boxes } from 'lucide-react'
import TopBar from '@/features/shell/TopBar'
import GlobalStrip from '@/features/shell/GlobalStrip'
import NavColumn, { type NavRow } from '@/features/shell/NavColumn'
import Nexus from '@/features/shell/Nexus'
import RepoInspector from '@/features/shell/RepoInspector'
import TokenDrilldown from '@/features/shell/TokenDrilldown'
import AgentsDrilldown from '@/features/shell/AgentsDrilldown'
import LearningDrilldown from '@/features/shell/LearningDrilldown'
import { useCommandStats, type OrbitRepo } from '@/features/shell/useCommandStats'
import DevWorkspace from '@/features/dev/DevWorkspace'
import CoreDashboard from '@/features/core/CoreDashboard'
import Foundations from '@/features/foundations/Foundations'
import GlobalActivity from '@/features/activity/GlobalActivity'
import QuickConfig from '@/features/config/QuickConfig'
import Internals from '@/features/internals/Internals'
import ChatPanel, { type DevBinding, type SeedTurn } from '@/features/chat/ChatPanel'
import ConnectModal from '@/features/shell/ConnectModal'
import { GLOBAL, type ContextRef } from '@/lib/contexts'
import { listContexts, type ChatMode, type Run, type SystemHold } from '@/lib/api'

// System & Dev local nav. Nexus (orbit) is the main entry; Me + projects are reached from orbit
// nodes, so there's no separate Functional tier in the nav.
const NAV: NavRow[] = [
  { id: 'nexus', label: 'Nexus', icon: Radar },
  { id: 'foundations', label: 'Foundations', icon: Layers },
  { id: 'activity', label: 'Activity', icon: Activity },
  { id: 'config', label: 'Quick config', icon: SlidersHorizontal },
  { id: 'internals', label: 'Internals', icon: Boxes }, // TEMPORARY internals inventory — deletable
]

// Chat-rail drag bounds: wide enough to read a long agent turn, never so wide the main area dies.
const CHAT_MIN = 360
const CHAT_MAX = 900

// The renovated cockpit: full-width top bar + global stats strip, then a row of
// [navigate · orbit · chater rail] under the strip, over a slim status bar.
export default function App() {
  const [active, setActive] = useState('nexus')
  const [contexts, setContexts] = useState<ContextRef[]>([GLOBAL])
  const [chatContext, setChatContext] = useState(GLOBAL.id)
  const [chatOpen, setChatOpen] = useState(true)
  const [chatMode, setChatMode] = useState<ChatMode>('dev') // dev-chater | core-chater
  const [selectedId, setSelectedId] = useState<string | null>(null) // orbit node → inspector
  const [connecting, setConnecting] = useState(false)
  const [drill, setDrill] = useState<string | null>(null) // which global-strip tile is drilled in
  // Per-repo destination that takes over the main area (Dev workspace / Core dashboard).
  const [dest, setDest] = useState<{ repo: OrbitRepo; kind: 'dev' | 'core' } | null>(null)
  // Chat ⇄ work-item binding: clicking a work-item takes the chat rail over as that item's dev
  // thread (opens its session, tags sends). Lifted here so DevWorkspace and ChatPanel can share it.
  const [binding, setBinding] = useState<DevBinding | null>(null)
  // A one-shot turn to birth a fresh session in the chat rail (onboarding OR diagnosis launch) — set
  // here, sent once by ChatPanel when its socket is ready, then cleared via onSeedConsumed.
  const [seed, setSeed] = useState<SeedTurn | null>(null)
  // The attention center's "Open" jumps to a specific item's dev workspace: this holds the item to
  // auto-open in that workspace's pipeline (consumed once by DevDashboard, then cleared).
  const [focusItem, setFocusItem] = useState<{ repoId: string; itemId: string } | null>(null)
  // Optimistic tag overrides — a saved tag shows instantly, before the /repos poll confirms it.
  const [tagOverrides, setTagOverrides] = useState<Record<string, { color: string; icon: string | null }>>({})
  // Chat rail width — owner-draggable (the default 480px is cramped for reading long agent turns).
  // Persisted so it survives reloads; clamped so the rail can never eat or vanish from the layout.
  const [chatWidth, setChatWidth] = useState(() => {
    const saved = Number(localStorage.getItem('superme.chatWidth'))
    return saved >= CHAT_MIN && saved <= CHAT_MAX ? saved : 480
  })
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

  // Attention center → jump to an item: take the repo's dev workspace over and hand DevDashboard the
  // item to auto-open. No-op if the repo isn't in the roster yet (a poll race) — the bell stays put.
  function gotoItem(repoId: string, hold: SystemHold) {
    const repo = [stats.hub, ...stats.nodes].find((r) => r?.id === repoId)
    if (!repo) return
    setActive('nexus')
    setDest({ repo, kind: 'dev' })
    // Bind the chat to the ITEM's own session HERE, deterministically — don't rely on DevDashboard's
    // focus effect to bind after mount (it races the established-gate + data-load, and lost: the rail
    // fell back to the general thread). The hold carries session_id for exactly this.
    setBinding({ workItemId: hold.id, sessionId: hold.session_id ?? null, title: hold.title, contextId: repoId })
    setChatContext(repoId)
    setChatMode('dev')
    setChatOpen(true)
    setFocusItem({ repoId, itemId: hold.id })
  }

  const selectedRepo = selectedId ? [stats.hub, ...stats.nodes].find((r) => r?.id === selectedId) ?? null : null
  // The tag of the repo the chat rail is currently talking to (for the chater header).
  const chatRepo = [stats.hub, ...stats.nodes].find((r) => r?.id === chatContext) ?? null
  const chatTag = chatRepo ? { color: chatRepo.color, icon: chatRepo.icon, isHub: chatRepo.id === 'global' } : undefined

  function Section() {
    if (active === 'nexus')
      return <Nexus stats={stats} selectedId={selectedId} onSelectRepo={setSelectedId} onConnect={() => setConnecting(true)} />
    if (active === 'foundations') return <Foundations />
    if (active === 'activity') return <GlobalActivity stats={stats} onDiagnose={launchDiagnosis} />
    if (active === 'config') return <QuickConfig stats={stats} />
    if (active === 'internals') return <Internals />
    return <Nexus stats={stats} selectedId={selectedId} onSelectRepo={setSelectedId} onConnect={() => setConnecting(true)} />
  }

  return (
    <div className="flex h-full flex-col bg-app font-sans text-fg">
      <TopBar onGoto={gotoItem} />
      <GlobalStrip stats={stats} onDetails={setDrill} />

      <div className="flex min-h-0 flex-1">
        <NavColumn
          items={NAV}
          active={active}
          onSelect={(id) => {
            setActive(id)
            setDest(null) // leaving to a nav surface exits any repo takeover
          }}
        />
        <main className="min-w-0 flex-1 overflow-hidden">
          {dest?.kind === 'dev' ? (
            <DevWorkspace
              repo={dest.repo}
              onExit={() => setDest(null)}
              focusItemId={focusItem && focusItem.repoId === dest.repo.id ? focusItem.itemId : null}
              onFocusConsumed={() => setFocusItem(null)}
              repos={[stats.hub, ...stats.nodes].filter((r): r is OrbitRepo => !!r)}
              onSwitch={(r) => { setDest({ repo: r, kind: 'dev' }); setBinding(null); setChatContext(r.id); setChatMode('dev') }}
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
          ) : dest?.kind === 'core' ? (
            <CoreDashboard repo={dest.repo} onExit={() => setDest(null)} />
          ) : (
            Section()
          )}
        </main>

        {/* persistent chater rail — kept mounted; full panel when open, a slim quick-switch rail
            (recent sessions + dev/core toggle) when collapsed. */}
        {/* drag handle — only while the rail is open (the collapsed rail has a fixed width) */}
        {chatOpen && (
          <div
            onMouseDown={startDrag}
            title="Drag to resize the chat"
            // 8px grab target — a hairline is accurate to render but miserable to actually hit.
            className="w-2 shrink-0 cursor-col-resize bg-transparent transition hover:bg-accent/40"
          />
        )}
        <div
          style={chatOpen ? { width: chatWidth } : undefined}
          className={`shrink-0 border-l border-line ${chatOpen ? '' : 'w-14'}`}
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
        onClose={() => setSelectedId(null)}
        onOpenDev={(r) => {
          setSelectedId(null)
          setDest({ repo: r, kind: 'dev' })
          // The chat rail follows you to the repo you open (dev workspace → its dev thread).
          setBinding(null)
          setChatContext(r.id)
          setChatMode('dev')
        }}
        onOpenCore={(r) => {
          setSelectedId(null)
          setDest({ repo: r, kind: 'core' })
          setBinding(null)
          setChatContext(r.id)
          setChatMode('core')
        }}
        onTagSaved={(id, patch) => setTagOverrides((o) => ({ ...o, [id]: patch }))}
        onDisconnected={(id) => {
          // The repo is gone — drop every surface still pointing at it (the roster poll catches up).
          setSelectedId(null)
          if (dest?.repo.id === id) setDest(null)
          if (binding?.contextId === id) setBinding(null)
          if (chatContext === id) setChatContext('global')
        }}
      />
      {drill === 'tokens' && <TokenDrilldown stats={stats} onClose={() => setDrill(null)} />}
      {drill === 'ops' && <AgentsDrilldown stats={stats} onClose={() => setDrill(null)} />}
      {drill === 'learning' && <LearningDrilldown onClose={() => setDrill(null)} />}
      {connecting && (
        <ConnectModal
          onClose={() => setConnecting(false)}
          onConnected={() => {
            setConnecting(false)
            // Refresh the chat context list so the new repo is selectable; the orbit poll (/repos)
            // surfaces its node within a few seconds, from which the owner opens onboarding.
            listContexts().then((cs) => cs.length && setContexts(cs)).catch(() => {})
          }}
        />
      )}
    </div>
  )
}

// (There is no onboarding kickoff prompt. Onboarding is a repo STATE, not a launched action: while
// a dev repo's memory is unestablished, ws.py already treats every general session in it as an
// onboarding session and `onboarding_preamble(mode)` already carries the skill directive in the
// system prompt — invisible to the owner, absent from the transcript and the activity trail. The
// owner's first message is their project description, full stop. See OnboardingLanding.)

