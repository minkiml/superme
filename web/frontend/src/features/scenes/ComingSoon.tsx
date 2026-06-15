import { Wrench } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'

// A placeholder for admin dashboards not built yet (Manage Harness, Configuration).
export default function ComingSoon({
  title,
  blurb,
  items,
  icon,
}: {
  title: string
  blurb: string
  items: string[]
  icon?: LucideIcon
}) {
  return (
    <div className="flex h-full flex-col">
      <PageHeader icon={icon ?? Wrench} title={title} subtitle={blurb} badge="soon" />
      <div className="flex-1 overflow-auto p-6">
        <ul className="max-w-2xl space-y-2">
          {items.map((it) => (
            <li
              key={it}
              className="flex items-start gap-3 rounded-lg border border-line bg-surface px-4 py-3 text-sm text-fg"
            >
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
              <span>{it}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
