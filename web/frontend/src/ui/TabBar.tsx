// The segmented tab / filter control used across drill-ins and filter rows. Two chromes:
// `filled` (pill group on a hover fill — the drilldown default) and `outlined` (bordered surface —
// used by inline filter rows). Generic over the tab key so callers keep their string-literal unions.
export default function TabBar<T extends string>({
  tabs,
  value,
  onChange,
  size = 'md',
  variant = 'filled',
  className = '',
}: {
  tabs: readonly (readonly [T, string])[]
  value: T
  onChange: (t: T) => void
  size?: 'sm' | 'md'
  variant?: 'filled' | 'outlined'
  className?: string
}) {
  const text = size === 'sm' ? 'text-[12px]' : 'text-[13px]'
  const pad = size === 'sm' ? 'px-2.5 py-1' : 'px-3 py-1'
  // Each variant's selected fill contrasts its shell: a filled shell (bg-hover) raises to bg-surface;
  // an outlined shell (bg-surface) recesses to bg-hover — so the active tab always reads as distinct.
  const shell = variant === 'outlined' ? 'border border-line bg-surface' : 'bg-hover'
  const selected = variant === 'outlined' ? 'bg-hover text-fg' : 'bg-surface text-fg'
  return (
    <div className={`inline-flex rounded-lg ${shell} p-0.5 ${text} ${className}`}>
      {tabs.map(([t, lbl]) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          className={`rounded-md ${pad} font-medium ${value === t ? selected : 'text-muted hover:text-fg'}`}
        >
          {lbl}
        </button>
      ))}
    </div>
  )
}
