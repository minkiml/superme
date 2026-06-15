import { useEffect, useState } from 'react'
import { User, FolderTree, Database, Wrench, Settings, UserCircle, SlidersHorizontal, Activity, MessageSquareText } from 'lucide-react'
import Sidebar, { type NavItem } from '@/ui/Sidebar'
import MePage from '@/features/scenes/MePage'
import DomainsPage from '@/features/scenes/DomainsPage'
import DomainScene from '@/features/scenes/DomainScene'
import PlaceholderScene from '@/features/scenes/PlaceholderScene'
import ComingSoon from '@/features/scenes/ComingSoon'
import ManageKnowledge from '@/features/knowledge/ManageKnowledge'
import ChatPanel from '@/features/chat/ChatPanel'
import { GLOBAL, type ContextRef } from '@/lib/contexts'
import { listContexts } from '@/lib/api'

// Me's sub-categories — placeholder scenes for now (the design of how we present each
// slice of global knowledge is still open).
const ME_SUBS = [
  { id: 'me/profile', label: 'Profile', icon: UserCircle, blurb: 'Who I am — identity, role, the durable facts.' },
  { id: 'me/preferences', label: 'Preferences', icon: SlidersHorizontal, blurb: 'How I like to work and be worked with.' },
  { id: 'me/activity', label: 'Activity', icon: Activity, blurb: 'Recent threads, decisions, and what changed.' },
]

// System space (manage the machinery itself). Harness + Configuration are stubs.
const SYSTEM: NavItem[] = [
  { id: 'knowledge', label: 'Manage Knowledge', icon: Database },
  { id: 'harness', label: 'Manage Harness', icon: Wrench, hint: 'soon' },
  { id: 'config', label: 'Configuration', icon: Settings, hint: 'soon' },
]

// The cockpit shell: a two-tier accordion sidebar · the active scene · a persistent chat
// rail whose context is selectable and detached from whichever scene is showing.
export default function App() {
  const [active, setActive] = useState('me')
  const [contexts, setContexts] = useState<ContextRef[]>([GLOBAL])
  const [chatContext, setChatContext] = useState(GLOBAL.id)
  const [kbContext, setKbContext] = useState(GLOBAL.id) // Manage Knowledge's opening sub-tab
  const [chatOpen, setChatOpen] = useState(true)

  useEffect(() => {
    listContexts()
      .then((cs) => cs.length && setContexts(cs))
      .catch(() => {
        /* daemon may be down; the global seed still works */
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

  function openDomainKnowledge(id: string) {
    setKbContext(id)
    setActive('knowledge')
  }

  function Scene() {
    if (active === 'me') return <MePage onOpenKnowledge={() => openDomainKnowledge('global')} />
    if (active.startsWith('me/')) {
      const sub = ME_SUBS.find((s) => s.id === active)
      return <PlaceholderScene title={sub?.label ?? 'Me'} blurb={sub?.blurb ?? ''} icon={sub?.icon} />
    }
    if (active === 'domains') return <DomainsPage domains={domains} />
    if (active.startsWith('domain/')) {
      const id = active.slice('domain/'.length)
      const domain = domains.find((d) => d.id === id)
      if (domain) return <DomainScene domain={domain} onManageKnowledge={() => openDomainKnowledge(domain.id)} />
      return <DomainsPage domains={domains} />
    }
    if (active === 'knowledge')
      return <ManageKnowledge key={kbContext} domains={domains} initialContext={kbContext} />
    if (active === 'harness')
      return (
        <ComingSoon
          icon={Wrench}
          title="Manage Harness"
          blurb="View and edit the machinery SuperMe runs on, in one place."
          items={[
            'View and edit SuperMe’s artifacts (skills, subagents).',
            'View and edit the prompt layers loaded into SuperMe — per access point (web vs Slack) when they differ.',
            'Inspect tools, policy, and what’s active in each context.',
          ]}
        />
      )
    if (active === 'config')
      return (
        <ComingSoon
          icon={Settings}
          title="Configuration"
          blurb="Set up new surfaces and workspaces from pre-defined workflows."
          items={[
            'Add a new project and its base dashboard page.',
            'Configure a new workspace with pre-loaded or pre-defined workflows.',
            'Manage connections, environment, and defaults.',
          ]}
        />
      )
    return <MePage onOpenKnowledge={() => openDomainKnowledge('global')} />
  }

  return (
    <div className="flex h-full bg-app text-fg">
      <Sidebar presentation={presentation} system={SYSTEM} active={active} onSelect={setActive} />
      <main className="min-w-0 flex-1 overflow-hidden">
        {/* Call inline (not <Scene/>) so scene state survives App re-renders — a nested
            component type would remount the whole scene on every chat/nav toggle. */}
        {Scene()}
      </main>
      {/* Kept mounted (display:none when collapsed) so the chat WebSocket + live turn survive. */}
      <div className={`shrink-0 ${chatOpen ? 'w-[380px]' : 'hidden'}`}>
        <ChatPanel
          key={chatContext}
          contextId={chatContext}
          contexts={contexts}
          onContextChange={setChatContext}
          onCollapse={() => setChatOpen(false)}
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
