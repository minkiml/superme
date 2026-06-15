import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

// Consistent scene header: an accent-tinted icon tile, a title (+ optional badge), a
// muted subtitle, and an optional right-aligned control. Gives every scene the same
// visual hierarchy instead of a flat wall of same-coloured text.
export default function PageHeader({
  icon: Icon,
  title,
  subtitle,
  badge,
  right,
}: {
  icon: LucideIcon
  title: string
  subtitle?: string
  badge?: string
  right?: ReactNode
}) {
  return (
    <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-6 py-4">
      <div className="flex min-w-0 items-center gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-text">
          <Icon size={18} strokeWidth={1.75} />
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-lg font-medium text-fg">{title}</h2>
            {badge && (
              <span className="shrink-0 rounded-full bg-hover px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted">
                {badge}
              </span>
            )}
          </div>
          {subtitle && <p className="truncate text-xs text-muted">{subtitle}</p>}
        </div>
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  )
}
