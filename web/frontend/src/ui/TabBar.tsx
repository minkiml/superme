// The segmented tab and filter control. Two chromes, and generic over the tab key so callers keep
// their unions.
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
  // Each variant's selected fill contrasts its shell, so the active tab always reads as distinct.
  const shell = variant === 'outlined' ? 'border border-line bg-surface' : 'bg-hover'
  const selected = variant === 'outlined' ? 'bg-hover text-fg' : 'bg-surface text-fg'
  return (
    // A bar too wide for its column WRAPS: a tab behind a scrollbar cannot be seen to exist.
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
