import { useEffect, useState, type ReactNode } from 'react'
import { Bot, Brain, FileText, Package, ScanSearch, ScrollText, Settings2, SlidersHorizontal, Sparkles, type LucideIcon } from 'lucide-react'
import Modal from '@/ui/Modal'
import Dropdown from '@/ui/Dropdown'
import { RepoIcon } from '@/lib/repoIcons'
import type { OrbitRepo } from '@/features/shell/useCommandStats'
import { CONFIG_SECTIONS, useParam, setParam, type ConfigSection } from '@/lib/router'
import { useContainerWidth } from '@/lib/layout'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { getMemoryStats } from '@/lib/api'
import General from './sections/General'
import LearningConfig from './sections/Learning'
import Identity from './sections/Identity'
import Constitution from './sections/Constitution'
import Plugins from './sections/Plugins'
import ProjectSettings from './sections/ProjectSettings'
import ProjectLearning from './sections/ProjectLearning'
import ProjectArtifacts from './sections/ProjectArtifacts'
import ProjectXray from './sections/ProjectXray'

// The System config popup — every knob SuperMe has, in one overlay, grouped by the SCOPE it acts at.
//
// Scope is the organising axis because it is the only thing that actually distinguished the five
// surfaces this replaces: the same three artifact nouns existed twice (once universal, once
// per-repo) and a repo's own settings were split across three screens. So each sidebar GROUP
// declares a scope and the rows under it are sections at that scope.
//
// The scope lives on the group rather than in a popup-wide switch: sections that don't take a repo
// would leave such a switch inert, and a control that sometimes does nothing is worse than one that
// only appears where it applies.
//
// It is an OVERLAY (`?config=<section>`), not a page — it opens over whatever you were looking at,
// the way `?stats=` does, so closing it returns you there rather than guessing.

type Row = { id: ConfigSection; label: string; icon: LucideIcon }
type Group = { name: string; rows: Row[]; project?: boolean }

const GROUPS: Group[] = [
  {
    name: 'System',
    rows: [
      { id: 'general', label: 'General', icon: SlidersHorizontal },
      { id: 'learning', label: 'Auto-learning', icon: Brain },
    ],
  },
  {
    name: 'System artifacts',
    rows: [
      { id: 'identity', label: 'Identity & charters', icon: FileText },
      { id: 'constitution', label: 'Constitution', icon: ScrollText },
      { id: 'skills', label: 'Skills', icon: Sparkles },
      { id: 'agents', label: 'Agents', icon: Bot },
    ],
  },
  {
    name: 'Project',
    project: true,
    rows: [
      { id: 'psettings', label: 'Settings', icon: Settings2 },
      { id: 'plearning', label: 'Learning', icon: Brain },
      { id: 'partifacts', label: 'Artifacts', icon: Package },
      { id: 'pxray', label: 'Prompt X-ray', icon: ScanSearch },
    ],
  },
]

/** Which sections are scoped to the picked repo — i.e. need one before they can render. */
const PROJECT_SECTIONS = new Set(GROUPS.filter((g) => g.project).flatMap((g) => g.rows.map((r) => r.id)))

/**
 * What each section renders. A RECORD keyed by the router's vocabulary rather than a chain of
 * conditionals, so a section that is addressable but has no pane is a type error rather than a
 * blank popup. Project sections receive the picked repo; system ones ignore it.
 */
const PANES: Record<ConfigSection, (repo: OrbitRepo, label: string) => ReactNode> = {
  general: () => <General />,
  learning: () => <LearningConfig />,
  identity: () => <Identity />,
  constitution: () => <Constitution />,
  skills: () => <Plugins only="skill" />,
  agents: () => <Plugins only="agent" />,
  psettings: (repo) => <ProjectSettings repo={repo} />,
  plearning: (repo, label) => <ProjectLearning contextId={repo.id} repoLabel={label} />,
  partifacts: (repo, label) => <ProjectArtifacts contextId={repo.id} repoLabel={label} />,
  pxray: (repo, label) => <ProjectXray contextId={repo.id} repoLabel={label} />,
}

export default function SystemConfig({ repos, initialRepoId, onClose }: {
  repos: OrbitRepo[]
  /** The repo the surface underneath is about, if any — the picker opens on it. */
  initialRepoId?: string | null
  onClose: () => void
}) {
  const param = useParam('config')
  // An unknown section is corrected in place rather than rendered blank: the popup is addressable,
  // so a stale or mistyped link must still land somewhere real.
  const section = (CONFIG_SECTIONS as readonly string[]).includes(param ?? '')
    ? (param as ConfigSection)
    : 'general'
  useEffect(() => {
    if (param && param !== section) setParam('config', section)
  }, [param, section])

  // The picked repo is popup state, not an address: the SECTION is what you link to, and carrying
  // a repo in the URL would fight the repo already named by the path you opened this over.
  const [repoId, setRepoId] = useState(initialRepoId ?? 'global')
  const repo = repos.find((r) => r.id === repoId) ?? repos.find((r) => r.id === 'global') ?? repos[0] ?? null

  // What is waiting on YOU in the picked repo — proposals at gate 1 plus drafts at gate 2. It is
  // read by the SHELL rather than the pane, because the point of a badge is to be seen before you
  // click: a queue you only discover by opening it is a queue you forget. Same cache key the
  // Learning pane subscribes to, so the two cost one request between them.
  const mem = useLive(repo ? K.memoryStats(repo.id) : null, () => getMemoryStats(repo!.id), 0)
  const gateCount = (mem.data?.candidates.pending_proposals ?? 0) + (mem.data?.candidates.drafted_proposals ?? 0)
  const badges: Partial<Record<ConfigSection, number>> = { plearning: gateCount }

  function name(r: OrbitRepo) { return r.id === 'global' ? 'SuperMe Hub' : r.label }

  // The popup measures ITSELF, not the window: it lives inside the frame's main band, so the width
  // it actually got is the only honest input. Two steps, in the app's own order — the rail yields
  // before the content does. Tight, it is an ICON STRIP: every row keeps its icon and its tooltip,
  // and the group headings become hairlines, since a heading is the first thing a strip cannot hold.
  const [bodyRef, bodyW] = useContainerWidth<HTMLDivElement>()
  const railIcons = bodyW > 0 && bodyW < 520
  const railNarrow = bodyW > 0 && bodyW < 760

  // The project picker rides with the sections it governs. In the strip there is no room for it, so
  // it moves to the top of the pane — the same control, still next to what it changes.
  const picker = repo && (
    <Dropdown
      value={repo.id}
      options={repos.map((r) => ({ value: r.id, label: name(r) }))}
      onChange={setRepoId}
      width="w-full"
      title="Which project these sections configure"
    />
  )

  return (
    <Modal onClose={onClose} title="System config" maxW="max-w-5xl" column fill>
      <div ref={bodyRef} className="flex min-h-0 flex-1">
        <nav
          className={`shrink-0 overflow-y-auto border-r border-line bg-sidebar py-3 ${
            railIcons ? 'w-[52px] px-1.5' : railNarrow ? 'w-44 px-2' : 'w-56 px-2.5'
          }`}
        >
          {GROUPS.map((g, gi) => (
            <div key={g.name}>
              {railIcons ? (
                gi > 0 && <div className="mx-1.5 my-2 h-px bg-line" />
              ) : (
                <div className="px-2.5 pb-1.5 pt-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint first:pt-0.5">
                  {g.name}
                </div>
              )}
              {g.project && picker && !railIcons && <div className="px-1 pb-1.5">{picker}</div>}
              {g.rows.map((r) => {
                const Icon = r.icon
                const on = section === r.id
                return (
                  <button
                    key={r.id}
                    onClick={() => setParam('config', r.id)}
                    title={railIcons ? r.label : undefined}
                    className={`flex w-full items-center rounded-lg text-left text-[13.5px] transition-colors ${
                      railIcons ? 'justify-center px-0 py-2' : 'gap-2.5 px-2.5 py-[7px]'
                    } ${on ? 'bg-hover font-medium text-fg' : 'text-muted hover:bg-hover/60 hover:text-fg'}`}
                  >
                    <span className="relative shrink-0">
                      <Icon size={15} className={on ? 'text-fg' : 'text-faint'} />
                      {/* In the strip the count has nowhere to sit, so it becomes a dot on the icon:
                          the point of the badge is that something is waiting, not how many. */}
                      {railIcons && !!badges[r.id] && (
                        <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-warn" />
                      )}
                    </span>
                    {!railIcons && (
                      <>
                        <span className="min-w-0 flex-1 truncate">{r.label}</span>
                        {!!badges[r.id] && (
                          <span
                            title={`${badges[r.id]} waiting at a gate`}
                            className="shrink-0 rounded-full bg-warn px-1.5 text-[10px] font-bold text-on-accent"
                          >
                            {badges[r.id]}
                          </span>
                        )}
                        {g.project && repo && repo.id !== 'global' && repo.icon && (
                          <RepoIcon name={repo.icon} size={12} color={repo.color} className="shrink-0 opacity-70" />
                        )}
                      </>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </nav>

        <div className={`min-w-0 flex-1 overflow-y-auto py-5 ${railIcons ? 'px-4' : 'px-6'}`}>
          {/* A project section with no roster yet has nothing to configure — the picker above it is
              empty for the same reason, so say so rather than rendering controls bound to nothing. */}
          {PROJECT_SECTIONS.has(section) && !repo ? (
            <p className="py-8 text-center text-[13px] text-faint">No projects connected yet.</p>
          ) : (
            <>
              {railIcons && PROJECT_SECTIONS.has(section) && picker && (
                <div className="mb-4">{picker}</div>
              )}
              {PANES[section](repo as OrbitRepo, repo ? name(repo) : '')}
            </>
          )}
        </div>
      </div>
    </Modal>
  )
}
