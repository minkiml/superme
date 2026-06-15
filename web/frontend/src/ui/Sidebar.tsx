import { useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { ChevronRight } from 'lucide-react'
import ThemeToggle from './ThemeToggle'

export type NavItem = {
  id: string // also the scene id
  label: string
  hint?: string
  icon?: LucideIcon
  enabled?: boolean // default true; false = visible but not selectable
  children?: NavItem[] // sub-categories — each is its own scene
}

function inBranch(item: NavItem, active: string): boolean {
  if (item.id === active) return true
  return (item.children ?? []).some((c) => c.id === active)
}

function Row({
  item,
  depth,
  active,
  onSelect,
  open,
  onToggle,
}: {
  item: NavItem
  depth: number
  active: string
  onSelect: (id: string) => void
  open: boolean
  onToggle: () => void
}) {
  const enabled = item.enabled !== false
  const hasKids = !!item.children?.length
  const isSub = depth > 0
  const isActive = active === item.id
  const Icon = item.icon
  const stateClass = !enabled
    ? 'cursor-default text-faint'
    : isActive
      ? 'bg-accent-soft text-accent-text font-medium'
      : isSub
        ? 'text-muted hover:bg-hover hover:text-fg'
        : 'text-fg/90 hover:bg-hover'
  return (
    <button
      disabled={!enabled}
      onClick={() => enabled && onSelect(item.id)}
      className={`group flex w-full items-center gap-2.5 rounded-md py-2 pr-2 ${
        isSub ? 'pl-9 text-[13px]' : 'pl-2.5 text-sm'
      } ${stateClass}`}
    >
      {!isSub &&
        (Icon ? (
          <Icon size={17} strokeWidth={1.75} className="shrink-0" />
        ) : (
          <span className="w-[17px] shrink-0" />
        ))}
      <span className="flex-1 truncate text-left">{item.label}</span>
      {item.hint && !isActive && (
        <span className="shrink-0 text-[10px] uppercase tracking-wide text-faint">{item.hint}</span>
      )}
      {hasKids && (
        <span
          role="button"
          tabIndex={-1}
          onClick={(e) => {
            e.stopPropagation()
            onToggle()
          }}
          className="shrink-0 text-faint transition-transform hover:text-fg"
          style={{ transform: open ? 'rotate(90deg)' : 'none' }}
        >
          <ChevronRight size={14} />
        </span>
      )}
    </button>
  )
}

function Group({ items, active, onSelect }: { items: NavItem[]; active: string; onSelect: (id: string) => void }) {
  const [openIds, setOpenIds] = useState<Record<string, boolean>>({})
  const toggle = (id: string) => setOpenIds((m) => ({ ...m, [id]: !m[id] }))

  return (
    <div className="space-y-0.5">
      {items.map((item) => {
        const open = item.id in openIds ? openIds[item.id] : inBranch(item, active)
        return (
          <div key={item.id}>
            <Row item={item} depth={0} active={active} onSelect={onSelect} open={open} onToggle={() => toggle(item.id)} />
            {open &&
              item.children?.map((c) => (
                <Row key={c.id} item={c} depth={1} active={active} onSelect={onSelect} open={false} onToggle={() => {}} />
              ))}
          </div>
        )
      })}
    </div>
  )
}

// Two-tier nav: presentation dashboards on top, a System space below the divider.
export default function Sidebar({
  presentation,
  system,
  active,
  onSelect,
}: {
  presentation: NavItem[]
  system: NavItem[]
  active: string
  onSelect: (id: string) => void
}) {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-sidebar">
      <div className="px-4 py-4 text-lg font-semibold tracking-tight">
        Super<span className="text-accent">Me</span>
      </div>
      <nav className="flex-1 overflow-y-auto px-2.5">
        <Group items={presentation} active={active} onSelect={onSelect} />
        <div className="my-3 flex items-center gap-2 px-2.5">
          <span className="text-[10px] font-medium uppercase tracking-wider text-faint">System</span>
          <span className="h-px flex-1 bg-line" />
        </div>
        <Group items={system} active={active} onSelect={onSelect} />
      </nav>
      <div className="space-y-2 border-t border-line px-3 py-3">
        <ThemeToggle />
        <div className="px-1 text-[11px] text-faint">localhost · single-owner</div>
      </div>
    </aside>
  )
}
