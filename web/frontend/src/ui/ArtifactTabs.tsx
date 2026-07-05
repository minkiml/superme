import type { LucideIcon } from 'lucide-react'

// The underline tab strip used across the artifact-management surfaces (Foundations, the per-repo
// Artifacts tab, the Published inventory) so Constitution / Skills / Agents read identically
// everywhere. Icon + label + optional count; the active tab underlines in the given scope tint.
export type ArtifactTab<T extends string> = {
  key: T
  label: string
  icon: LucideIcon
  count?: number | null
}

const TINT: Record<string, { border: string; text: string }> = {
  universal: { border: 'border-universal', text: 'text-universal' },
  dev: { border: 'border-dev', text: 'text-dev' },
  core: { border: 'border-core', text: 'text-core' },
}

export default function ArtifactTabs<T extends string>({
  tabs,
  value,
  onChange,
  tint = 'universal',
  className = '',
}: {
  tabs: readonly ArtifactTab<T>[]
  value: T
  onChange: (t: T) => void
  tint?: 'universal' | 'dev' | 'core'
  className?: string
}) {
  const t = TINT[tint] ?? TINT.universal
  return (
    <div className={`flex items-center gap-1 border-b border-line ${className}`}>
      {tabs.map((tab) => {
        const active = value === tab.key
        const Icon = tab.icon
        return (
          <button
            key={tab.key}
            onClick={() => onChange(tab.key)}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] font-medium transition ${
              active ? `${t.border} text-fg` : 'border-transparent text-muted hover:text-fg'
            }`}
          >
            <Icon size={14} className={active ? t.text : ''} />
            {tab.label}
            {tab.count != null && <span className="text-[11px] text-faint">({tab.count})</span>}
          </button>
        )
      })}
    </div>
  )
}
