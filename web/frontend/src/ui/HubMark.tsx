import { useId } from 'react'

// The hub's mark: two rings, one offset from the other. Every other repo carries a tag icon it
// was given; the hub is the one context nobody names, so it carries this.
//
// The gradient reads the brand stops from CSS, since SVG cannot take a gradient function.
export default function HubMark({ size = 16, className }: { size?: number; className?: string }) {
  const gid = `hubmark-${useId()}`
  return (
    <svg
      width={size} height={size} viewBox="0 0 40 40"
      className={className} role="img" aria-label="SuperMe hub"
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--c-iris-1)" />
          <stop offset="0.38" stopColor="var(--c-iris-2)" />
          <stop offset="0.68" stopColor="var(--c-iris-3)" />
          <stop offset="1" stopColor="var(--c-iris-4)" />
        </linearGradient>
      </defs>
      {/* The stroke widths are set for the smallest slot this renders in; a hairline ring fills
          in and reads as a dot. */}
      <circle cx="15" cy="20" r="9.5" fill="none" stroke="currentColor" strokeOpacity="0.4" strokeWidth="3.6" />
      <circle cx="25" cy="20" r="9.5" fill="none" stroke={`url(#${gid})`} strokeWidth="4" />
    </svg>
  )
}
