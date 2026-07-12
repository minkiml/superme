import { useEffect, useMemo, useState } from 'react'
import { Radar, Layers, Activity, SlidersHorizontal, MessageSquareText, Boxes } from 'lucide-react'
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
import ChatPanel, { type DevBinding } from '@/features/chat/ChatPanel'
import ConnectModal from '@/features/shell/ConnectModal'
import { GLOBAL, type ContextRef } from '@/lib/contexts'
import { listContexts, type ChatMode } from '@/lib/api'

// System & Dev local nav. Nexus (orbit) is the main entry; Me + projects are reached from orbit
// nodes, so there's no separate Functional tier in the nav.
const NAV: NavRow[] = [
  { id: 'nexus', label: 'Nexus', icon: Radar },
  { id: 'foundations', label: 'Foundations', icon: Layers },
  { id: 'activity', label: 'Activity', icon: Activity },
  { id: 'config', label: 'Quick config', icon: SlidersHorizontal },
  { id: 'internals', label: 'Internals', icon: Boxes }, // TEMPORARY internals inventory — deletable
]

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
  // A one-shot prompt to seed into the chat rail (onboarding launch) — set here, sent once by
  // ChatPanel when its socket is ready, then cleared via onSeedConsumed.
  const [seedPrompt, setSeedPrompt] = useState<string | null>(null)
  // Optimistic tag overrides — a saved tag shows instantly, before the /repos poll confirms it.
  const [tagOverrides, setTagOverrides] = useState<Record<string, { color: string; icon: string | null }>>({})
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

  const selectedRepo = selectedId ? [stats.hub, ...stats.nodes].find((r) => r?.id === selectedId) ?? null : null
  // The tag of the repo the chat rail is currently talking to (for the chater header).
  const chatRepo = [stats.hub, ...stats.nodes].find((r) => r?.id === chatContext) ?? null
  const chatTag = chatRepo ? { color: chatRepo.color, icon: chatRepo.icon, isHub: chatRepo.id === 'global' } : undefined

  function Section() {
    if (active === 'nexus')
      return <Nexus stats={stats} selectedId={selectedId} onSelectRepo={setSelectedId} onConnect={() => setConnecting(true)} />
    if (active === 'foundations') return <Foundations />
    if (active === 'activity') return <GlobalActivity stats={stats} />
    if (active === 'config') return <QuickConfig stats={stats} />
    if (active === 'internals') return <Internals />
    return <Nexus stats={stats} selectedId={selectedId} onSelectRepo={setSelectedId} onConnect={() => setConnecting(true)} />
  }

  return (
    <div className="flex h-full flex-col bg-app font-sans text-fg">
      <TopBar />
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
              onStartOnboarding={(repoId, mode) => {
                // Point the chat rail at this repo's dev thread and seed the onboarding kickoff.
                setBinding(null)
                setChatContext(repoId)
                setChatMode('dev')
                setChatOpen(true)
                setSeedPrompt(onboardingKickoff(mode))
              }}
            />
          ) : dest?.kind === 'core' ? (
            <CoreDashboard repo={dest.repo} onExit={() => setDest(null)} />
          ) : (
            Section()
          )}
        </main>

        {/* persistent chater rail — under the stats strip; kept mounted (hidden when collapsed). */}
        <div className={`shrink-0 border-l border-line ${chatOpen ? 'w-[480px]' : 'hidden'}`}>
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
            seedPrompt={seedPrompt}
            onSeedConsumed={() => setSeedPrompt(null)}
          />
        </div>
        {!chatOpen && (
          <div className="flex w-11 shrink-0 flex-col items-center border-l border-line bg-surface py-3">
            <button
              onClick={() => setChatOpen(true)}
              title="Open chat"
              aria-label="Open chat"
              className="rounded-md p-1.5 text-muted hover:bg-hover hover:text-fg"
            >
              <MessageSquareText size={18} />
            </button>
          </div>
        )}
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

// The onboarding kickoff message seeded into the dev chat — names the skill explicitly so routing is
// reliable, and states the project's starting condition so the skill picks the right posture.
function onboardingKickoff(mode: 'project-init' | 'retrofit'): string {
  return mode === 'project-init'
    ? "This project has no SuperMe memory yet, and it's a new/greenfield project. Run **project-init**: grill me to establish the anchor docs (PRD, spec, roadmap, architecture), then draft them for my approval."
    : "This project has no SuperMe memory yet, and it's an existing codebase. Run **retrofit**: comprehend the code, clarify the intent with me, then draft the anchor docs for my approval."
}

