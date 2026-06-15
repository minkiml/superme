// The "/" command popover above the composer. Purely presentational — the composer owns
// the open/filter/keyboard state and feeds the matched items in.
export default function CommandPalette({
  items,
  activeIndex,
  onHover,
  onPick,
}: {
  items: string[]
  activeIndex: number
  onHover: (i: number) => void
  onPick: (name: string) => void
}) {
  return (
    <div className="absolute bottom-full left-0 mb-2 w-full overflow-hidden rounded-lg border border-line bg-surface shadow-lg">
      <div className="border-b border-line px-3 py-1 text-[10px] uppercase tracking-wide text-muted">
        commands &amp; skills
      </div>
      {items.map((c, i) => (
        <button
          key={c}
          className={`block w-full truncate px-3 py-1.5 text-left text-xs ${
            i === activeIndex ? 'bg-accent text-on-accent' : 'text-fg hover:bg-hover'
          }`}
          onMouseEnter={() => onHover(i)}
          onClick={() => onPick(c)}
        >
          /{c}
        </button>
      ))}
    </div>
  )
}
