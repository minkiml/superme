import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, GitBranch, Brain, Package, Activity, ChevronsUpDown, Check } from 'lucide-react'
import { colorFor } from '@/lib/palette'
import { RepoIcon } from '@/lib/repoIcons'
import type { OrbitRepo } from '@/features/shell/useCommandStats'
import type { WorkItem } from '@/lib/api'
import TabBar from '@/ui/TabBar'
import DevDashboard from './DevDashboard'
import { MemoryGovernance, PublishedInventory } from './LearningGovernance'
import ArtifactsTab from './ArtifactsTab'
import ActivityLog from './ActivityLog'

// The Dev workspace — the per-repo Tier-2 detail surface, reached from an orbit node's inspector
// ("Open dev workspace"). It takes over the main area (the shell owns the header + tabs) and holds
// the three heavy dev surfaces for ONE repo: the plan→build pipeline, the learning governance queue,
// and the per-repo activity log. Scoped by contextId (the repo id; only global is fully wired today).

type Tab = 'pipeline' | 'learning' | 'artifacts' | 'activity'
const TABS: { id: Tab; label: string; icon: typeof GitBranch }[] = [
  { id: 'pipeline', label: 'Pipeline', icon: GitBranch },
  { id: 'learning', label: 'Learning', icon: Brain },
  { id: 'artifacts', label: 'Artifacts', icon: Package },
  { id: 'activity', label: 'Activity', icon: Activity },
]

export default function DevWorkspace({
  repo,
  onExit,
  repos = [],
  onSwitch,
  onBindItem,
  onUnbindItem,
  boundItemId,
}: {
  repo: OrbitRepo
  onExit: () => void
  repos?: OrbitRepo[] // the full roster (hub + connected projects) — powers the quick-switch dropdown
  onSwitch?: (repo: OrbitRepo) => void // jump this workspace to another repo's dev dashboard
  onBindItem?: (it: WorkItem, contextId: string) => void // clicking a work-item binds the chat to it
  onUnbindItem?: () => void
  boundItemId?: string | null
}) {
  const [tab, setTab] = useState<Tab>('pipeline')
  const isHub = repo.id === 'global'
  const c = colorFor(repo.id)

  // The other repos you can hop to (current one excluded) — hub first, then connected projects.
  const others = repos.filter((r) => r.id !== repo.id)

  return (
    <div className="flex h-full flex-col overflow-hidden bg-app">
      {/* shell header — back · repo swatch · label · scope · quick-switch, then the tab rail */}
      <div className="shrink-0 border-b border-line px-4 pt-3">
        <div className="flex items-center gap-3">
          <button
            onClick={onExit}
            title="Back to Nexus"
            aria-label="Back to Nexus"
            className="rounded-md border border-line bg-surface p-1.5 text-muted hover:bg-hover hover:text-fg"
          >
            <ArrowLeft size={16} />
          </button>
          <span
            className="grid h-6 w-6 shrink-0 place-items-center rounded-[5px]"
            style={isHub ? { backgroundImage: 'var(--grad-iris)' } : { backgroundColor: repo.icon ? 'transparent' : c }}
          >
            {!isHub && repo.icon && <RepoIcon name={repo.icon} size={16} color={c} />}
          </span>
          <div className="min-w-0">
            <div className="truncate text-[15px] font-semibold text-fg">{isHub ? 'SuperMe Hub' : repo.label}</div>
            <div className="text-[12px] text-faint">dev workspace</div>
          </div>
          {onSwitch && others.length > 0 && (
            <div className="ml-auto">
              <RepoSwitcher current={repo} others={others} onSwitch={onSwitch} />
            </div>
          )}
        </div>

        <div className="mt-3 flex gap-1">
          {TABS.map((t) => {
            const Icon = t.icon
            const on = tab === t.id
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                style={on ? { color: c, borderColor: c } : undefined}
                className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] font-medium transition ${
                  on ? '' : 'border-transparent text-muted hover:text-fg'
                }`}
              >
                <Icon size={14} /> {t.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* tab body */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {tab === 'pipeline' && (
          <DevDashboard
            contextId={repo.id}
            embedded
            onBindItem={onBindItem}
            onUnbindItem={onUnbindItem}
            boundItemId={boundItemId}
          />
        )}
        {tab === 'learning' && <LearningTab contextId={repo.id} />}
        {tab === 'artifacts' && <ArtifactsTab contextId={repo.id} />}
        {tab === 'activity' && <ActivityLog contextId={repo.id} />}
      </div>
    </div>
  )
}

// A repo swatch — hub gets the iris gradient; projects show their owner-set tag icon (on a
// transparent chip) when they have one, else fall back to the tag color (override or hashed).
function RepoSwatch({ repo, size = 16 }: { repo: OrbitRepo; size?: number }) {
  const isHub = repo.id === 'global'
  const c = repo.color || colorFor(repo.id)
  return (
    <span
      className="grid shrink-0 place-items-center rounded-[4px]"
      style={{
        width: size,
        height: size,
        ...(isHub ? { backgroundImage: 'var(--grad-iris)' } : { backgroundColor: repo.icon ? 'transparent' : c }),
      }}
    >
      {!isHub && repo.icon && <RepoIcon name={repo.icon} size={size - 2} color={c} />}
    </span>
  )
}

// Quick-switch to another repo's dev workspace — styled to match the shared Dropdown, but carries
// each repo's swatch so the roster stays visually identifiable.
function RepoSwitcher({ current, others, onSwitch }: { current: OrbitRepo; others: OrbitRepo[]; onSwitch: (repo: OrbitRepo) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])
  const name = (r: OrbitRepo) => (r.id === 'global' ? 'SuperMe Hub' : r.label)
  return (
    <div ref={ref} className="relative w-56">
      <button
        type="button"
        title="Switch to another repo's dev workspace"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[13px] text-fg hover:bg-hover"
      >
        <RepoSwatch repo={current} />
        <span className="min-w-0 flex-1 truncate text-left">{name(current)}</span>
        <ChevronsUpDown size={14} className="shrink-0 text-muted" />
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-1.5 max-h-[15rem] w-full overflow-y-auto overflow-x-hidden rounded-lg border border-line bg-surface py-1 shadow-lg">
          <div className="px-3 pb-1 pt-0.5 text-[10px] font-medium uppercase tracking-wider text-faint">Switch workspace</div>
          {others.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => {
                onSwitch(r)
                setOpen(false)
              }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] text-fg hover:bg-hover"
            >
              <RepoSwatch repo={r} />
              <span className="min-w-0 flex-1 truncate">{name(r)}</span>
              {r.id === current.id && <Check size={14} className="shrink-0 text-accent-text" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// Learning — the tier-C governance surface for this repo: the review queue (candidate/knowledge
// gauges + distill proposals through the two gates) and the published-artifact inventory. Skills &
// Agents live in Foundations (they're universal, not per-repo), so they're deliberately not here.
function LearningTab({ contextId }: { contextId: string }) {
  const [view, setView] = useState<'review' | 'published'>('review')
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl p-6">
        <TabBar
          className="mb-5"
          variant="outlined"
          full
          value={view}
          onChange={setView}
          tabs={[['review', 'Review'], ['published', 'Published']] as const}
        />
        {view === 'review' ? <MemoryGovernance contextId={contextId} /> : <PublishedInventory contextId={contextId} />}
      </div>
    </div>
  )
}
