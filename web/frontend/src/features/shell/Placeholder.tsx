import type { LucideIcon } from 'lucide-react'

type Props = {
  title: string
  subtitle?: string
  icon?: LucideIcon
  /** short note on what will live here (renovation is building surface-by-surface) */
  note?: string
  accent?: 'dev' | 'core' | 'universal' | 'default'
}

const RING: Record<string, string> = {
  dev: 'text-dev',
  core: 'text-core',
  universal: 'text-universal',
  default: 'text-accent-text',
}

// A titled empty scene — used for destinations not yet built out (Tier-1 functional
// dashboards + Universal/Activity/Quick-config while the renovation fills them in).
export default function Placeholder({ title, subtitle, icon: Icon, note, accent = 'default' }: Props) {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-line px-8 py-5">
        {Icon && (
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-surface ring-1 ring-line">
            <Icon size={18} className={RING[accent]} />
          </span>
        )}
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight text-fg">{title}</h1>
          {subtitle && <p className="text-xs text-muted">{subtitle}</p>}
        </div>
      </header>
      <div className="grid flex-1 place-items-center p-8">
        <div className="max-w-sm rounded-xl border border-dashed border-line bg-surface/40 px-6 py-8 text-center">
          <p className="text-sm text-muted">{note ?? 'Nothing here yet.'}</p>
        </div>
      </div>
    </div>
  )
}
