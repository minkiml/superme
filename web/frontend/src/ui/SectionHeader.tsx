import type { ReactNode } from 'react'

// The uppercase eyebrow label that titles a section / panel group. One place for the repeated
// `text-[12px] font-semibold uppercase tracking-wider text-muted` treatment.
export default function SectionHeader({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`text-[12px] font-semibold uppercase tracking-wider text-muted ${className}`}>{children}</div>
}
