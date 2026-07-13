import { useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import ThemeToggle from '@/ui/ThemeToggle'

export type NavRow = { id: string; label: string; icon: LucideIcon; badge?: string }

type Props = {
  items: NavRow[]
  active: string
  onSelect: (id: string) => void
}

const COLLAPSE_KEY = 'superme.nav.collapsed'

// The System & Dev local nav (left column). The orbit is the repo navigator, so there's
// no separate Functional tier here — Me + projects are reached from orbit nodes. Collapsible to an
// icon rail (persisted); collapsed hides the labels + the light/dark toggle.
export default function NavColumn({ items, active, onSelect }: Props) {
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSE_KEY) === '1' } catch { return false }
  })
  function toggle() {
    setCollapsed((c) => {
      const next = !c
      try { localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0') } catch { /* private mode */ }
      return next
    })
  }

  // Collapsed-rail hover tooltip (same pattern as the chat rail): fixed-position so it's never clipped
  // and shows instantly. Anchored to the RIGHT of the icon since this rail sits on the left. `top` is
  // the button's vertical centre. Only used while collapsed (expanded shows the label inline).
  const [hover, setHover] = useState<{ id: string; top: number; left: number } | null>(null)

  return (
    <nav className={`flex shrink-0 flex-col border-r border-line bg-sidebar py-4 transition-[width] ${collapsed ? 'w-14 px-2' : 'w-48 px-3'}`}>
      {/* Header row: the "Navigate" label (expanded only) + the collapse control. */}
      <div className={`flex items-center pb-2 ${collapsed ? 'justify-center' : 'justify-between px-2.5'}`}>
        {!collapsed && <p className="text-[12px] font-semibold uppercase tracking-wider text-faint">Navigate</p>}
        <button
          onClick={toggle}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          className="rounded-md p-1 text-accent-text transition-colors hover:bg-accent/15 hover:text-accent-text"
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
      </div>

      <div className="space-y-0.5">
        {items.map((row) => {
          const on = active === row.id
          const Icon = row.icon
          return (
            <button
              key={row.id}
              onClick={() => onSelect(row.id)}
              aria-label={row.label}
              onMouseEnter={
                collapsed
                  ? (e) => {
                      const r = e.currentTarget.getBoundingClientRect()
                      setHover({ id: row.id, top: r.top + r.height / 2, left: r.right })
                    }
                  : undefined
              }
              onMouseLeave={collapsed ? () => setHover((h) => (h?.id === row.id ? null : h)) : undefined}
              className={`group flex w-full items-center rounded-lg py-2 text-[15px] transition-colors ${
                collapsed ? 'justify-center px-0' : 'gap-2.5 px-2.5'
              } ${on ? 'bg-hover text-fg' : 'text-muted hover:bg-hover/60 hover:text-fg'}`}
            >
              <Icon size={16} className={on ? 'text-fg' : 'text-faint group-hover:text-muted'} />
              {!collapsed && <span className="flex-1 truncate text-left">{row.label}</span>}
              {!collapsed && row.badge && (
                <span className="rounded-full bg-hover px-1.5 font-mono text-[12px] text-muted">{row.badge}</span>
              )}
            </button>
          )
        })}
      </div>

      {/* Light/dark toggle lives in the footer — hidden entirely when the rail is collapsed. */}
      {!collapsed && (
        <div className="mt-auto px-1">
          <ThemeToggle />
        </div>
      )}

      {/* Fixed-position hover tooltip for the collapsed rail — shows the label to the right of the icon,
          rendered outside the nav so it never gets clipped and appears instantly. */}
      {collapsed &&
        hover &&
        (() => {
          const row = items.find((r) => r.id === hover.id)
          if (!row) return null
          const Icon = row.icon
          return (
            <div
              style={{ top: hover.top, left: hover.left + 8 }}
              className="pointer-events-none fixed z-50 flex max-w-[240px] -translate-y-1/2 items-center gap-1.5 whitespace-nowrap rounded-md border border-line bg-surface px-2 py-1 text-[11px] shadow-lg"
            >
              <Icon size={11} className="shrink-0 text-faint" />
              <span className="truncate text-fg">{row.label}</span>
            </div>
          )
        })()}
    </nav>
  )
}
