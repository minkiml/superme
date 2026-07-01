import { useEffect, useState } from 'react'
import { User, FolderTree, Wrench, Hammer, UserCircle, SlidersHorizontal, Activity, MessageSquareText, BookOpen, Gauge } from 'lucide-react'
import Sidebar, { type NavItem } from '@/ui/Sidebar'
import MePage from '@/features/scenes/MePage'
import DomainsPage from '@/features/scenes/DomainsPage'
import DomainScene from '@/features/scenes/DomainScene'
import PlaceholderScene from '@/features/scenes/PlaceholderScene'
import DevDashboard from '@/features/dev/DevDashboard'
import DevModel from '@/features/dev/DevModel'
import ManageHarness from '@/features/dev/ManageHarness'
import System from '@/features/system/System'
import DocsPage from '@/features/docs/DocsPage'
import ChatPanel, { type DevBinding } from '@/features/chat/ChatPanel'
import { GLOBAL, type ContextRef } from '@/lib/contexts'
import { listContexts, listDocs, type WorkItem, type ChatMode, type DocMeta } from '@/lib/api'

// Me's sub-categories — placeholder scenes for now (the design of how we present each
// slice of global knowledge is still open).
const ME_SUBS = [
  { id: 'me/profile', label: 'Profile', icon: UserCircle, blurb: 'Who I am — identity, role, the durable facts.' },
  { id: 'me/preferences', label: 'Preferences', icon: SlidersHorizontal, blurb: 'How I like to work and be worked with.' },
  { id: 'me/activity', label: 'Activity', icon: Activity, blurb: 'Recent threads, decisions, and what changed.' },
]

// System space (manage the machinery itself). Harness + Configuration are stubs.
// (There's no standalone Activity dashboard: the event LOG is consumed by the agent via the
// `dev_log` tool to answer "what happened…" queries, and a work-item's own timeline shows in
// its review modal — a global "everything that happened" view isn't useful to the owner.)
const SYSTEM: NavItem[] = [
  { id: 'system', label: 'System', icon: Gauge },
  {
    id: 'development',
    label: 'Development',
    icon: Hammer,
    children: [
      { id: 'development/model', label: 'Model' },
    ],
  },
  { id: 'harness', label: 'Manage Harness', icon: Wrench },
]

// Docs is its own top-level section: the parent shows the overview (superme-docs/README.md),
// each non-overview doc is a sub-page. Children are fetched at runtime (like Domains).
function docsNav(docs: DocMeta[]): NavItem {
  return {
    id: 'docs',
    label: 'Docs',
    icon: BookOpen,
    children: docs.filter((d) => d.slug !== 'overview').map((d) => ({ id: `docs/${d.slug}`, label: d.title })),
  }
}

// The cockpit shell: a two-tier accordion sidebar · the active scene · a persistent chat
// rail whose context is selectable and detached from whichever scene is showing.
export default function App() {
  const [active, setActive] = useState('me')
  const [contexts, setContexts] = useState<ContextRef[]>([GLOBAL])
  const [chatContext, setChatContext] = useState(GLOBAL.id)
  const [docs, setDocs] = useState<DocMeta[]>([])
  const [chatOpen, setChatOpen] = useState(true)
  const [chatMode, setChatMode] = useState<ChatMode>('core') // Core (twin) | Dev (builds SuperMe)
  const [devBinding, setDevBinding] = useState<DevBinding | null>(null) // chat taken over by a work-item

  function bindWorkItem(it: WorkItem, ctxId: string) {
    setDevBinding({ workItemId: it.id, sessionId: it.session_id ?? null, title: it.title || it.id, contextId: ctxId })
    setChatContext(ctxId)
    setChatMode('dev') // binding a card is always a dev-mode interaction
    setChatOpen(true)
  }

  // Switching mode from the chat toggle starts that mode fresh (drops any card binding).
  function changeChatMode(m: ChatMode) {
    setChatMode(m)
    setDevBinding(null)
  }

  useEffect(() => {
    listContexts()
      .then((cs) => cs.length && setContexts(cs))
      .catch(() => {
        /* daemon may be down; the global seed still works */
      })
    listDocs()
      .then((d) => setDocs(d.docs))
      .catch(() => {
        /* daemon may be down; Docs just shows empty until it's reachable */
      })
  }, [])

  const domains = contexts.filter((c) => c.id !== GLOBAL.id)

  const presentation: NavItem[] = [
    { id: 'me', label: 'Me', icon: User, hint: 'global', children: ME_SUBS.map((s) => ({ id: s.id, label: s.label })) },
    {
      id: 'domains',
      label: 'Domains',
      icon: FolderTree,
      hint: 'projects',
      children: domains.map((d) => ({ id: `domain/${d.id}`, label: d.label })),
    },
  ]

  function Scene() {
    if (active === 'me') return <MePage />
    if (active.startsWith('me/')) {
      const sub = ME_SUBS.find((s) => s.id === active)
      return <PlaceholderScene title={sub?.label ?? 'Me'} blurb={sub?.blurb ?? ''} icon={sub?.icon} />
    }
    if (active === 'domains') return <DomainsPage domains={domains} />
    if (active.startsWith('domain/')) {
      const id = active.slice('domain/'.length)
      const domain = domains.find((d) => d.id === id)
      if (domain) return <DomainScene domain={domain} />
      return <DomainsPage domains={domains} />
    }
    if (active === 'system') return <System />
    if (active === 'development')
      return (
        <DevDashboard
          contextId="global"
          onBindItem={bindWorkItem}
          onUnbindItem={() => setDevBinding(null)}
          boundItemId={devBinding?.workItemId ?? null}
        />
      )
    if (active === 'development/model')
      return <DevModel contextId="global" />
    if (active === 'harness')
      return <ManageHarness contextId="global" />
    if (active === 'docs' || active.startsWith('docs/')) {
      const slug = active === 'docs' ? 'overview' : active.slice('docs/'.length)
      const openDoc = (s: string) => setActive(s === 'overview' ? 'docs' : `docs/${s}`)
      return <DocsPage key={active} slug={slug} onOpenDoc={openDoc} />
    }
    return <MePage />
  }

  return (
    <div className="flex h-full bg-app text-fg">
      <Sidebar presentation={presentation} system={[...SYSTEM, docsNav(docs)]} active={active} onSelect={setActive} />
      <main className="min-w-0 flex-1 overflow-hidden">
        {/* Call inline (not <Scene/>) so scene state survives App re-renders — a nested
            component type would remount the whole scene on every chat/nav toggle. */}
        {Scene()}
      </main>
      {/* Kept mounted (display:none when collapsed) so the chat WebSocket + live turn survive. */}
      <div className={`shrink-0 ${chatOpen ? 'w-[380px]' : 'hidden'}`}>
        <ChatPanel
          key={`${chatContext}:${chatMode}`}
          contextId={chatContext}
          contexts={contexts}
          onContextChange={(id) => {
            setDevBinding(null) // switching context drops any item binding
            setChatContext(id)
          }}
          onCollapse={() => setChatOpen(false)}
          mode={chatMode}
          onModeChange={changeChatMode}
          binding={devBinding}
          onUnbind={() => setDevBinding(null)}
          onBindingSession={(sid) => setDevBinding((b) => (b ? { ...b, sessionId: sid } : b))}
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
  )
}
