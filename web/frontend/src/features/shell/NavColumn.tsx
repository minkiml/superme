import type { LucideIcon } from 'lucide-react'
import ThemeToggle from '@/ui/ThemeToggle'

export type NavRow = { id: string; label: string; icon: LucideIcon; badge?: string }

type Props = {
  items: NavRow[]
  active: string
  onSelect: (id: string) => void
}

// The System & Dev local nav (left column). The orbit is the repo navigator, so there's
// no separate Functional tier here — Me + projects are reached from orbit nodes.
export default function NavColumn({ items, active, onSelect }: Props) {
  return (
    <nav className="flex w-48 shrink-0 flex-col border-r border-line bg-sidebar px-3 py-4">
      <p className="px-2.5 pb-2 text-[12px] font-semibold uppercase tracking-wider text-faint">Navigate</p>
      <div className="space-y-0.5">
        {items.map((row) => {
          const on = active === row.id
          const Icon = row.icon
          return (
            <button
              key={row.id}
              onClick={() => onSelect(row.id)}
              className={`group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[15px] transition-colors ${
                on ? 'bg-hover text-fg' : 'text-muted hover:bg-hover/60 hover:text-fg'
              }`}
            >
              <Icon size={16} className={on ? 'text-fg' : 'text-faint group-hover:text-muted'} />
              <span className="flex-1 truncate text-left">{row.label}</span>
              {row.badge && (
                <span className="rounded-full bg-hover px-1.5 font-mono text-[12px] text-muted">{row.badge}</span>
              )}
            </button>
          )
        })}
      </div>
      <div className="mt-auto px-1">
        <ThemeToggle />
      </div>
    </nav>
  )
}
