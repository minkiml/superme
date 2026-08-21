import type { ReactNode } from 'react'

// The uppercase eyebrow that titles a section. It is `text-fg`, not muted: a heading's job is to be
// FOUND.
//
// Caps and tracking mark it as structure; the colour is what makes it survivable at a glance.
export default function SectionHeader({ children, className = '' }: { children: ReactNode; className?: string }) {
  // The BODY step, not a size of its own: caps and weight already mark it as a heading.
  return <div className={`text-[13px] font-semibold uppercase tracking-wider text-fg ${className}`}>{children}</div>
}
