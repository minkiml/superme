import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Check } from 'lucide-react'

export type Option = { value: string; label: string }

// A small, themed dropdown — replaces the native <select> so it reads as one connected
// control. `bare` makes the trigger look like an inline heading (used for the chat
// context); default is a boxed control.
export default function Dropdown({
  value,
  options,
  onChange,
  variant = 'boxed',
  disabled = false,
  align = 'left',
  title,
  width,
}: {
  value: string
  options: Option[]
  onChange: (value: string) => void
  variant?: 'boxed' | 'bare'
  disabled?: boolean
  align?: 'left' | 'right'
  title?: string
  width?: string // a Tailwind width class (e.g. 'w-36') — gives a fixed, aligned trigger
}) {
  const [open, setOpen] = useState(false)
  // Where the menu opens + how tall it may be — decided from the trigger's viewport position so the
  // list is never clipped off the bottom (or top) of the screen.
  const [placement, setPlacement] = useState<{ up: boolean; maxH: number }>({ up: false, maxH: 240 })
  const ref = useRef<HTMLDivElement>(null)
  const current = options.find((o) => o.value === value)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  // Flip up when there isn't room below; cap the height to whichever side we open toward.
  function toggle() {
    setOpen((o) => {
      const next = !o
      if (next && ref.current) {
        const r = ref.current.getBoundingClientRect()
        const below = window.innerHeight - r.bottom
        const above = r.top
        const want = Math.min(240, options.length * 34 + 8) // desired list height (~2.1rem rows + padding)
        const up = below < want + 12 && above > below
        setPlacement({ up, maxH: Math.max(120, Math.min(240, (up ? above : below) - 12)) })
      }
      return next
    })
  }

  // Both variants share ONE width so the trigger and menu edges line up. With an explicit `width`
  // the container is fixed; otherwise a hidden sizer (all option labels, non-wrapping, padded for
  // the chevron + check) sets the container to the widest option so nothing truncates — then the
  // trigger and menu are both w-full. `bare` differs only in trigger chrome (no border/bg).
  const triggerCls =
    variant === 'bare'
      ? 'flex w-full items-center justify-between gap-1 text-sm font-medium text-fg disabled:opacity-50'
      : 'flex w-full items-center justify-between gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-fg hover:bg-hover disabled:opacity-50'
  return (
    <div ref={ref} className={`relative inline-block ${width ?? ''}`}>
      {!width && (
        <div aria-hidden className="invisible h-0 overflow-hidden">
          {options.map((o) => (
            <div key={o.value} className="whitespace-nowrap px-3 pr-9 text-sm font-medium">
              {o.label}
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        disabled={disabled}
        title={title}
        onClick={toggle}
        className={triggerCls}
      >
        <span className="min-w-0 truncate">{current?.label ?? value}</span>
        <ChevronDown size={15} className={`shrink-0 text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div
          style={{ maxHeight: placement.maxH }}
          className={`absolute z-30 w-full overflow-y-auto overflow-x-hidden rounded-lg border border-line bg-surface py-1 shadow-lg ${
            placement.up ? 'bottom-full mb-1.5' : 'top-full mt-1.5'
          } ${align === 'right' ? 'right-0' : 'left-0'}`}
        >
          {options.map((o) => (
            <MenuItem key={o.value} o={o} sel={o.value === value} onPick={() => { onChange(o.value); setOpen(false) }} />
          ))}
        </div>
      )}
    </div>
  )
}

function MenuItem({ o, sel, onPick }: { o: Option; sel: boolean; onPick: () => void }) {
  return (
    <button
      type="button"
      onClick={onPick}
      className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${
        sel ? 'bg-accent-soft text-accent-text' : 'text-fg hover:bg-hover'
      }`}
    >
      <span className="min-w-0 flex-1 truncate">{o.label}</span>
      {sel && <Check size={14} className="shrink-0" />}
    </button>
  )
}
