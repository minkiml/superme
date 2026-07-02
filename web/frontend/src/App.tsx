import { useEffect, useMemo, useState } from 'react'
import { Radar, Layers, Activity, SlidersHorizontal, MessageSquareText } from 'lucide-react'
import TopBar from '@/features/shell/TopBar'
import GlobalStrip from '@/features/shell/GlobalStrip'
import NavColumn, { type NavRow } from '@/features/shell/NavColumn'
import Nexus from '@/features/shell/Nexus'
import RepoInspector from '@/features/shell/RepoInspector'
import TokenDrilldown from '@/features/shell/TokenDrilldown'
import { useCommandStats, type OrbitRepo } from '@/features/shell/useCommandStats'
import DevWorkspace from '@/features/dev/DevWorkspace'
import CoreDashboard from '@/features/core/CoreDashboard'
import Foundations from '@/features/foundations/Foundations'
import GlobalActivity from '@/features/activity/GlobalActivity'
import QuickConfig from '@/features/config/QuickConfig'
import ChatPanel, { type DevBinding } from '@/features/chat/ChatPanel'
import { GLOBAL, type ContextRef } from '@/lib/contexts'
import { listContexts, type ChatMode } from '@/lib/api'

// System & Dev local nav. Nexus (orbit) is the main entry; Me + projects are reached from orbit
// nodes, so there's no separate Functional tier in the nav.
const NAV: NavRow[] = [
  { id: 'nexus', label: 'Nexus', icon: Radar },
  { id: 'foundations', label: 'Foundations', icon: Layers },
  { id: 'activity', label: 'Activity', icon: Activity },
  { id: 'config', label: 'Quick config', icon: SlidersHorizontal },
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
  const [tokenDrill, setTokenDrill] = useState(false) // Tokens tile → drill-in
  // Per-repo destination that takes over the main area (Dev workspace / Core dashboard).
  const [dest, setDest] = useState<{ repo: OrbitRepo; kind: 'dev' | 'core' } | null>(null)
  // Chat ⇄ work-item binding: clicking a work-item takes the chat rail over as that item's dev
  // thread (opens its session, tags sends). Lifted here so DevWorkspace and ChatPanel can share it.
  const [binding, setBinding] = useState<DevBinding | null>(null)
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
    return <Nexus stats={stats} selectedId={selectedId} onSelectRepo={setSelectedId} onConnect={() => setConnecting(true)} />
  }

  return (
    <div className="flex h-full flex-col bg-app font-sans text-fg">
      <TopBar />
      <GlobalStrip stats={stats} onDetails={(id) => id === 'tokens' && setTokenDrill(true)} />

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
              onSwitch={(r) => setDest({ repo: r, kind: 'dev' })}
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
            modelOverride={chatRepo?.modelOverride ?? null}
            effortOverride={chatRepo?.effortOverride ?? null}
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
        }}
        onOpenCore={(r) => {
          setSelectedId(null)
          setDest({ repo: r, kind: 'core' })
        }}
        onTagSaved={(id, patch) => setTagOverrides((o) => ({ ...o, [id]: patch }))}
      />
      {tokenDrill && <TokenDrilldown stats={stats} onClose={() => setTokenDrill(false)} />}
      {connecting && <ConnectModal onClose={() => setConnecting(false)} />}
    </div>
  )
}

// Placeholder: connecting a new domain is real backend work (repo registration) deferred to the
// end of the renovation. For now this just explains what it will do.
function ConnectModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-6 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-sm rounded-2xl border border-line bg-app p-6 text-center shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-xl bg-iris" />
        <h2 className="text-[15px] font-semibold text-fg">Connect a domain</h2>
        <p className="mt-2 text-[13px] text-muted">
          Point SuperMe at a new project repo to add it to the orbit. Registration is wired up at the
          end of the renovation — for now, domains are connected in the repo config.
        </p>
        <button onClick={onClose} className="mt-5 rounded-lg border border-line bg-surface px-4 py-2 text-[13px] font-medium text-fg hover:border-faint">
          Got it
        </button>
      </div>
    </div>
  )
}
