import { useEffect, useState } from 'react'
import { Bot, Brain, FileText, ScrollText, Settings2, SlidersHorizontal, Sparkles, type LucideIcon } from 'lucide-react'
import Modal from '@/ui/Modal'
import Dropdown from '@/ui/Dropdown'
import { RepoIcon } from '@/lib/repoIcons'
import type { OrbitRepo } from '@/features/shell/useCommandStats'
import { useParam, setParam, type ConfigSection } from '@/lib/router'
import General from './sections/General'
import LearningConfig from './sections/Learning'
import Identity from './sections/Identity'
import Constitution from './sections/Constitution'
import Plugins from './sections/Plugins'
import ProjectSettings from './sections/ProjectSettings'

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
      { id: 'learning', label: 'Learning', icon: Brain },
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
    rows: [{ id: 'psettings', label: 'Settings', icon: Settings2 }],
  },
]

const ROWS = GROUPS.flatMap((g) => g.rows)
/** Which sections are scoped to the picked repo — i.e. need one before they can render. */
const PROJECT_SECTIONS = new Set(GROUPS.filter((g) => g.project).flatMap((g) => g.rows.map((r) => r.id)))

export default function SystemConfig({ repos, initialRepoId, onClose }: {
  repos: OrbitRepo[]
  /** The repo the surface underneath is about, if any — the picker opens on it. */
  initialRepoId?: string | null
  onClose: () => void
}) {
  const param = useParam('config')
  // An unknown section is corrected in place rather than rendered blank: the popup is addressable,
  // so a stale or mistyped link must still land somewhere real.
  const section = (ROWS.find((r) => r.id === param)?.id ?? 'general') as ConfigSection
  useEffect(() => {
    if (param && param !== section) setParam('config', section)
  }, [param, section])

  // The picked repo is popup state, not an address: the SECTION is what you link to, and carrying
  // a repo in the URL would fight the repo already named by the path you opened this over.
  const [repoId, setRepoId] = useState(initialRepoId ?? 'global')
  const repo = repos.find((r) => r.id === repoId) ?? repos.find((r) => r.id === 'global') ?? repos[0] ?? null

  const name = (r: OrbitRepo) => (r.id === 'global' ? 'SuperMe Hub' : r.label)

  return (
    <Modal onClose={onClose} title="System config" maxW="max-w-5xl" column fill>
      <div className="flex min-h-0 flex-1">
        <nav className="w-56 shrink-0 overflow-y-auto border-r border-line bg-sidebar px-2.5 py-3">
          {GROUPS.map((g) => (
            <div key={g.name}>
              <div className="px-2.5 pb-1.5 pt-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint first:pt-0.5">
                {g.name}
              </div>
              {g.project && repo && (
                <div className="px-1 pb-1.5">
                  <Dropdown
                    value={repo.id}
                    options={repos.map((r) => ({ value: r.id, label: name(r) }))}
                    onChange={setRepoId}
                    width="w-full"
                    title="Which project these sections configure"
                  />
                </div>
              )}
              {g.rows.map((r) => {
                const Icon = r.icon
                const on = section === r.id
                return (
                  <button
                    key={r.id}
                    onClick={() => setParam('config', r.id)}
                    className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-left text-[13.5px] transition-colors ${
                      on ? 'bg-hover font-medium text-fg' : 'text-muted hover:bg-hover/60 hover:text-fg'
                    }`}
                  >
                    <Icon size={15} className={on ? 'text-fg' : 'text-faint'} />
                    <span className="min-w-0 flex-1 truncate">{r.label}</span>
                    {g.project && repo && repo.id !== 'global' && repo.icon && (
                      <RepoIcon name={repo.icon} size={12} color={repo.color} className="shrink-0 opacity-70" />
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </nav>

        <div className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
          {section === 'general' && <General />}
          {section === 'learning' && <LearningConfig />}
          {section === 'identity' && <Identity />}
          {section === 'constitution' && <Constitution />}
          {section === 'skills' && <Plugins only="skill" />}
          {section === 'agents' && <Plugins only="agent" />}
          {/* A project section with no roster yet has nothing to configure — the picker above it is
              empty for the same reason, so say so rather than rendering controls bound to nothing. */}
          {PROJECT_SECTIONS.has(section) && !repo && (
            <p className="py-8 text-center text-[13px] text-faint">No projects connected yet.</p>
          )}
          {section === 'psettings' && repo && <ProjectSettings repo={repo} />}
        </div>
      </div>
    </Modal>
  )
}
