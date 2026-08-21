import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, GitBranch, Map, Activity, ChevronsUpDown, Check, Loader2 } from 'lucide-react'
import { colorFor } from '@/lib/palette'
import { RepoIcon } from '@/lib/repoIcons'
import type { OrbitRepo } from '@/features/shell/useCommandStats'
import { getProjectStatus, getAttention, type AttentionBadge, type WorkItem } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import DevDashboard from './DevDashboard'
import RoadmapTab from './RoadmapTab'
import ActivityLog from './ActivityLog'
import SweepBar from './SweepBar'
import OnboardingLanding, { type OnboardMode } from './OnboardingLanding'
import { useContainerWidth, railTight } from '@/lib/layout'

// The Dev workspace — this repo's WORK: the pipeline, the general knowledge behind it, and its
// activity log.
//
// What stays here is what you watch while work is moving; the knobs and queues live in System
// config.

// `project` is this repo's GENERAL knowledge, as opposed to Pipeline's work-in-flight.
//
// `workspace` is Pipeline's other PANE, not a seventh tab: it addresses the board where `pipeline`
// addresses the queue.
type Tab = 'pipeline' | 'workspace' | 'project' | 'activity'

const TABS: { id: Exclude<Tab, 'workspace'>; label: string; icon: typeof GitBranch }[] = [
  { id: 'pipeline', label: 'Pipeline', icon: GitBranch },
  { id: 'project', label: 'Project', icon: Map },
  { id: 'activity', label: 'Activity', icon: Activity },
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
  onDiscussNote,
  boundItemId,
}: {
  repo: OrbitRepo
  // The tab is an ADDRESS, not local state, so it survives a refresh and is linkable.
  tab: Tab
  onTabChange: (tab: Tab) => void
  onExit: () => void
  repos?: OrbitRepo[] // the full roster (hub + connected projects) — powers the quick-switch dropdown
  onSwitch?: (repo: OrbitRepo) => void // jump this workspace to another repo's dev dashboard
  onBindItem?: (it: WorkItem, contextId: string) => void // clicking a work-item binds the chat to it
  onUnbindItem?: () => void
  onDiscussNote?: (inboxId: number, title: string) => void
  boundItemId?: string | null
}) {
  // An item drilldown IS an address whose tab is Pipeline by construction, so arriving cannot land
  // on the wrong tab.
  const isHub = repo.id === 'global'
  const c = colorFor(repo.id)
  // The header measures ITSELF: it can be 500px wide in a 1400px window, so the window is not the
  // question.
  const [headRef, headW] = useContainerWidth<HTMLDivElement>()
  // Same rule every tab rail uses: below this seat the labels go.
  const tight = railTight(headW, TABS.length, 140)
  // Either of Pipeline's two panes means the Pipeline tab is what's showing.
  const pipelineTab = tab === 'pipeline' || tab === 'workspace'

  // The top non-empty tier's colour and count. Same cache key the dashboard subscribes to, so the
  // two cost one request.
  const attn = useLive(K.devAttention(repo.id), () => getAttention(repo.id))
  const badge: AttentionBadge | null = attn.data?.badge ?? null

  // It polls while the door is up, since establishment can arrive at any moment and cannot revert.
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
  // Fail-open: a status hiccup must not wall off the workspace, so an error with no data reads as
  // established.
  const established: boolean | null =
    status.data ? status.data.established : status.error ? true : null
  const onboardMode = (status.data?.onboard_mode as OnboardMode | null) ?? null
  const checkStatus = status.refresh

  // The other repos you can hop to (current one excluded) — hub first, then connected projects.
  const others = repos.filter((r) => r.id !== repo.id)

  return (
    <div className="flex h-full flex-col overflow-hidden bg-app">
      {/* shell header — back · repo swatch · label · scope · quick-switch, then the tab rail */}
      <div ref={headRef} className="shrink-0 border-b border-line px-4 pt-3">
        {/* The identity block truncates and the controls stay whole; past that width they wrap to
            their own line */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
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
          {/* A floor, not just `min-w-0`: without one the row never wraps, it just crushes the
              name to an initial */}
          <div className="min-w-[10rem] flex-1 overflow-hidden">
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
            <div className="truncate text-[12px] text-faint">dev workspace</div>
          </div>
          {/* The cluster wraps inside itself and the pickers keep their widths: a squeezed picker
              names nothing. */}
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            {onSwitch && others.length > 0 && (
              <RepoSwitcher current={repo} others={others} onSwitch={onSwitch} />
            )}
          </div>
        </div>

        {/* the work tabs appear only once memory is established; the sweep bar rides the same row,
            right-aligned */}
        {established === true && (
          <div className="mt-3 flex flex-wrap items-center gap-1">
            {TABS.map((t) => {
              const Icon = t.icon
              // Pipeline stays lit for either of its panes — the rail names the tab, not the pane.
              const on = t.id === 'pipeline' ? pipelineTab : tab === t.id
              return (
                <button
                  key={t.id}
                  onClick={() => onTabChange(t.id)}
                  style={on ? { color: c, borderColor: c } : undefined}
                  title={t.label}
                  aria-label={t.label}
                  className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 py-2 text-[13px] font-medium transition ${
                    tight ? 'px-2' : 'px-3'
                  } ${on ? '' : 'border-transparent text-muted hover:text-fg'}`}
                >
                  <Icon size={14} /> {(!tight || on) && t.label}
                </button>
              )
            })}
            <SweepBar contextId={repo.id} />
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
                onDiscussNote={onDiscussNote}
                boundItemId={boundItemId}
              />
            )}
            {tab === 'project' && <RoadmapTab contextId={repo.id} />}
            {tab === 'activity' && <ActivityLog contextId={repo.id} />}
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
