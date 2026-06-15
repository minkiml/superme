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
}: {
  value: string
  options: Option[]
  onChange: (value: string) => void
  variant?: 'boxed' | 'bare'
  disabled?: boolean
  align?: 'left' | 'right'
  title?: string
}) {
  const [open, setOpen] = useState(false)
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

  const trigger =
    variant === 'bare'
      ? 'flex items-center gap-1 text-sm font-medium text-fg disabled:opacity-50'
      : 'flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-fg hover:bg-hover disabled:opacity-50'

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        title={title}
        onClick={() => setOpen((o) => !o)}
        className={trigger}
      >
        <span className="min-w-0 truncate">{current?.label ?? value}</span>
        <ChevronDown
          size={15}
          className={`shrink-0 text-muted transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && (
        <div
          className={`absolute z-30 mt-1.5 min-w-[11rem] overflow-hidden rounded-lg border border-line bg-surface py-1 shadow-lg ${
            align === 'right' ? 'right-0' : 'left-0'
          }`}
        >
          {options.map((o) => {
            const sel = o.value === value
            return (
              <button
                key={o.value}
                type="button"
                onClick={() => {
                  onChange(o.value)
                  setOpen(false)
                }}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${
                  sel ? 'bg-accent-soft text-accent-text' : 'text-fg hover:bg-hover'
                }`}
              >
                <span className="min-w-0 flex-1 truncate">{o.label}</span>
                {sel && <Check size={14} className="shrink-0" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
