import type { ReactNode } from 'react'

// A small status / kind chip. `pill` rounds it fully (used for soft category tags); the default is
// a squared badge. `color` is a text-color class (the fill stays neutral `bg-hover`); pass a custom
// `tone` to override the fill for emphasis (e.g. 'bg-warn/15 text-warn').
export default function Badge({
  children,
  color = 'text-faint',
  tone,
  pill = false,
  className = '',
}: {
  children: ReactNode
  color?: string
  tone?: string // overrides the default `bg-hover ${color}` fill+text
  pill?: boolean
  className?: string
}) {
  const shape = pill ? 'rounded-full' : 'rounded'
  const paint = tone ?? `bg-hover ${color}`
  return (
    <span
      className={`shrink-0 whitespace-nowrap ${shape} ${paint} px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${className}`}
    >
      {children}
    </span>
  )
}
