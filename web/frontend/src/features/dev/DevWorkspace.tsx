import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, GitBranch, Map, Brain, Package, Activity, ScanSearch, ChevronsUpDown, Check, Loader2 } from 'lucide-react'
import { colorFor } from '@/lib/palette'
import { RepoIcon } from '@/lib/repoIcons'
import type { OrbitRepo } from '@/features/shell/useCommandStats'
import { getProjectStatus, getAttention, type AttentionBadge, type WorkItem } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import TabBar from '@/ui/TabBar'
import DevDashboard from './DevDashboard'
import RoadmapTab from './RoadmapTab'
import { MemoryGovernance, PublishedInventory } from './LearningGovernance'
import ArtifactsTab from './ArtifactsTab'
import ActivityLog from './ActivityLog'
import PromptXrayTab from './PromptXrayTab'
import OnboardingLanding, { type OnboardMode } from './OnboardingLanding'

// The Dev workspace — the per-repo Tier-2 detail surface, reached from an orbit node's inspector
// ("Open dev workspace"). It takes over the main area (the shell owns the header + tabs) and holds
// the three heavy dev surfaces for ONE repo: the plan→build pipeline, the learning governance queue,
// and the per-repo activity log. Scoped by contextId (the repo id; only global is fully wired today).

// Graph is no longer a tab — it's the Kanban⇄Graph toggle inside the Pipeline workspace panel
// (same population, two projections).
//
// `project` is the home of this repo's GENERAL knowledge — what we're building and why — as opposed
// to Pipeline's work-in-flight. Roadmap is its first section; deliverables, open questions and the
// decision ledger join it as the general/ docs become addressable records rather than prose.
// `workspace` is Pipeline's other PANE, not a seventh tab: it addresses the board where `pipeline`
// addresses the capture queue. Both light the same rail entry — see `pipelineTab` below.
type Tab = 'pipeline' | 'workspace' | 'project' | 'learning' | 'artifacts' | 'activity' | 'promptxray'
const TABS: { id: Exclude<Tab, 'workspace'>; label: string; icon: typeof GitBranch }[] = [
  { id: 'pipeline', label: 'Pipeline', icon: GitBranch },
  { id: 'project', label: 'Project', icon: Map },
  { id: 'learning', label: 'Learning', icon: Brain },
  { id: 'artifacts', label: 'Artifacts', icon: Package },
  { id: 'activity', label: 'Activity', icon: Activity },
  { id: 'promptxray', label: 'Prompt X-ray', icon: ScanSearch },
]

export default function DevWorkspace({
  repo,
  tab,
  onTabChange,
  onExit,
  repos = [],
  onSwitch,
  onBindItem,
  onUnbindItem,
  boundItemId,
}: {
  repo: OrbitRepo
  // The tab is an ADDRESS (`/repo/:id/dev/:tab`), not local state — so it survives a refresh and is
  // linkable. Owned by the router; this component only asks to change it.
  tab: Tab
  onTabChange: (tab: Tab) => void
  onExit: () => void
  repos?: OrbitRepo[] // the full roster (hub + connected projects) — powers the quick-switch dropdown
  onSwitch?: (repo: OrbitRepo) => void // jump this workspace to another repo's dev dashboard
  onBindItem?: (it: WorkItem, contextId: string) => void // clicking a work-item binds the chat to it
  onUnbindItem?: () => void
  boundItemId?: string | null
}) {
  // (There is no "snap to the pipeline tab" effect any more: an item drilldown IS an address whose
  // tab is Pipeline by construction — `App` derives the tab from the route — so arriving from the
  // attention centre cannot land on the wrong tab in the first place.)
  const isHub = repo.id === 'global'
  const c = colorFor(repo.id)
  // Either of Pipeline's two panes means the Pipeline tab is what's showing.
  const pipelineTab = tab === 'pipeline' || tab === 'workspace'

  // Attention badge (S7/D10): the top non-empty tier's color + count, read from durable state.
  // Same cache key DevDashboard subscribes to, so the two surfaces cost ONE request between them
  // rather than the two they used to fire on separate clocks.
  const attn = useLive(K.devAttention(repo.id), () => getAttention(repo.id))
  const badge: AttentionBadge | null = attn.data?.badge ?? null

  // Onboarding gate (S5·B): a repo whose project memory isn't established yet (PRD defines no
  // deliverables) shows the onboarding front door instead of the work tabs — you can't take on work
  // before there's memory. `undefined` (nothing fetched yet) = still checking.
  //
  // The cadence is the gate itself: while the front door is up, establishment can arrive at any
  // moment (the onboarding agent writes the PRD's first deliverable) and the tabs must flip without
  // a manual "Recheck" — so it polls. Once established, the answer cannot change back, so the poll
  // stops entirely rather than asking forever. That replaces the old pair of effects (a one-shot
  // check plus a second interval that existed only to watch for the flip).
  const [settled, setSettled] = useState(false)
  const status = useLive(
    K.projectStatus(repo.id),
    () => getProjectStatus(repo.id),
    settled ? 0 : 5000,
  )
  useEffect(() => {
    if (status.data?.established) setSettled(true)
  }, [status.data?.established])
  // A repo switch re-arms the gate: the new repo's answer is unknown again.
  useEffect(() => { setSettled(false) }, [repo.id])
  // Fail-open, as before: a status hiccup must not wall off the workspace. An error with no data is
  // treated as established; last-good data survives a blip on its own.
  const established: boolean | null =
    status.data ? status.data.established : status.error ? true : null
  const onboardMode = (status.data?.onboard_mode as OnboardMode | null) ?? null
  const checkStatus = status.refresh

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
            <div className="flex items-center gap-2">
              <span className="truncate text-[15px] font-semibold text-fg">{isHub ? 'SuperMe Hub' : repo.label}</span>
              {badge && (
                <span
                  title={`${badge.count} item(s) in the '${badge.tier}' attention tier`}
                  className={`inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1 text-[10px] font-bold text-on-accent ${
                    badge.tier === 'error' ? 'bg-danger' : badge.tier === 'needs_you' ? 'bg-warn' : badge.tier === 'deputy_working' ? 'bg-deputy' : badge.tier === 'running' ? 'bg-success' : 'bg-accent'
                  }`}
                >
                  {badge.count}
                </span>
              )}
            </div>
            <div className="text-[12px] text-faint">dev workspace</div>
          </div>
          {onSwitch && others.length > 0 && (
            <div className="ml-auto">
              <RepoSwitcher current={repo} others={others} onSwitch={onSwitch} />
            </div>
          )}
        </div>

        {/* the work tabs appear only once memory is established — the gate hides them otherwise */}
        {established === true && (
          <div className="mt-3 flex gap-1">
            {TABS.map((t) => {
              const Icon = t.icon
              // Pipeline stays lit for either of its panes — the rail names the tab, not the pane.
              const on = t.id === 'pipeline' ? pipelineTab : tab === t.id
              return (
                <button
                  key={t.id}
                  onClick={() => onTabChange(t.id)}
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
        )}
        {established !== true && <div className="h-3" />}
      </div>

      {/* body — onboarding front door until memory is established, then the work tabs */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {established === null ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted">
            <Loader2 size={15} className="animate-spin" /> Checking project memory…
          </div>
        ) : established === false ? (
          <OnboardingLanding repoLabel={isHub ? 'SuperMe Hub' : repo.label} mode={onboardMode} />
        ) : (
          <>
            {pipelineTab && (
              <DevDashboard
                contextId={repo.id}
                embedded
                onBindItem={onBindItem}
                onUnbindItem={onUnbindItem}
                boundItemId={boundItemId}
              />
            )}
            {tab === 'project' && <RoadmapTab contextId={repo.id} />}
            {tab === 'learning' && <LearningTab contextId={repo.id} />}
            {tab === 'artifacts' && <ArtifactsTab contextId={repo.id} />}
            {tab === 'activity' && <ActivityLog contextId={repo.id} />}
            {tab === 'promptxray' && <PromptXrayTab contextId={repo.id} />}
          </>
        )}
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
// (The pooled-knowledge asset pool lives in Artifacts → Constitution — it's a constitution surface,
// not a learning one.)
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
        {view === 'review' && <MemoryGovernance contextId={contextId} />}
        {view === 'published' && <PublishedInventory contextId={contextId} />}
      </div>
    </div>
  )
}
