import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, Check } from 'lucide-react'

export type Option = { value: string; label: string }

// A small themed dropdown, so it reads as one connected control.
//
// The open menu renders in a PORTAL, fixed-positioned from the trigger's rect, so an
// `overflow-hidden` ancestor can never clip it.
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
  // Decided from the trigger's rect, so the list is never clipped off the bottom of the screen.
  const [pos, setPos] = useState({ left: 0, top: 0, bottom: 0, width: 0, up: false, maxH: 240 })
  const ref = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const current = options.find((o) => o.value === value)

  // Recompute placement from the current trigger rect.
  function place() {
    if (!ref.current) return
    const r = ref.current.getBoundingClientRect()
    const below = window.innerHeight - r.bottom
    const above = r.top
    const want = Math.min(240, options.length * 34 + 8) // desired list height (~2.1rem rows + padding)
    const up = below < want + 12 && above > below
    setPos({
      left: r.left,
      top: r.bottom,
      bottom: window.innerHeight - r.top,
      width: r.width,
      up,
      maxH: Math.max(120, Math.min(240, (up ? above : below) - 12)),
    })
  }

  useLayoutEffect(() => {
    if (open) place()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    if (!open) return
    // Outside-click closes — but a click INSIDE the trigger or the (portalled) menu must not.
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (ref.current?.contains(t) || menuRef.current?.contains(t)) return
      setOpen(false)
    }
    // An ancestor scroll detaches a fixed menu, so close on one — but exempt scrolling inside the
    // menu's own list.
    const onMove = (e?: Event) => {
      const t = e?.target
      if (t instanceof Node && menuRef.current?.contains(t)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [open])

  // One width, so the trigger and menu edges line up; a hidden sizer takes the widest option.
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
        onClick={() => setOpen((o) => !o)}
        className={triggerCls}
      >
        <span className="min-w-0 truncate">{current?.label ?? value}</span>
        <ChevronDown size={15} className={`shrink-0 text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            style={{
              position: 'fixed',
              left: align === 'right' ? undefined : pos.left,
              right: align === 'right' ? window.innerWidth - (pos.left + pos.width) : undefined,
              width: pos.width,
              maxHeight: pos.maxH,
              ...(pos.up ? { bottom: pos.bottom + 6 } : { top: pos.top + 6 }),
            }}
            className="z-50 overflow-y-auto overflow-x-hidden rounded-lg border border-line bg-surface py-1 shadow-lg"
          >
            {options.map((o) => (
              <MenuItem key={o.value} o={o} sel={o.value === value} onPick={() => { onChange(o.value); setOpen(false) }} />
            ))}
          </div>,
          document.body,
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
