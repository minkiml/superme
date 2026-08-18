// The segmented tab / filter control used across drill-ins and filter rows. Two chromes:
// `filled` (pill group on a hover fill — the drilldown default) and `outlined` (bordered surface —
// used by inline filter rows). Generic over the tab key so callers keep their string-literal unions.
export default function TabBar<T extends string>({
  tabs,
  value,
  onChange,
  size = 'md',
  variant = 'filled',
  full = false,
  className = '',
}: {
  tabs: readonly (readonly [T, string])[]
  value: T
  onChange: (t: T) => void
  size?: 'sm' | 'md'
  variant?: 'filled' | 'outlined'
  full?: boolean          // stretch to fill the row, each tab an equal share (e.g. Review | Published)
  className?: string
}) {
  const text = size === 'sm' ? 'text-[12px]' : 'text-[13px]'
  const pad = size === 'sm' ? 'px-2.5 py-1' : 'px-3 py-1'
  // Each variant's selected fill contrasts its shell: a filled shell (bg-hover) raises to bg-surface;
  // an outlined shell (bg-surface) recesses to bg-hover — so the active tab always reads as distinct.
  const shell = variant === 'outlined' ? 'border border-line bg-surface' : 'bg-hover'
  const selected = variant === 'outlined' ? 'bg-hover text-fg' : 'bg-surface text-fg'
  return (
    // A bar too wide for its column WRAPS (`lib/layout`): it never scrolls sideways and never
    // pushes the column wider. A tab hidden behind a horizontal scrollbar is a destination the
    // reader cannot see exists, which is the one thing a set of tabs has to make obvious.
    <div className={`${full ? 'flex w-full' : 'inline-flex'} max-w-full flex-wrap rounded-lg ${shell} p-0.5 ${text} ${className}`}>
      {tabs.map(([t, lbl]) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          className={`shrink-0 whitespace-nowrap rounded-md ${pad} font-medium ${full ? 'flex-1 text-center' : ''} ${value === t ? selected : 'text-muted hover:text-fg'}`}
        >
          {lbl}
        </button>
      ))}
    </div>
  )
}
