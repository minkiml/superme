import type { ReactNode } from 'react'

// The shared empty-state line — a centered, muted note where a list or breakdown has no rows yet.
export default function Empty({ children = 'Nothing here yet.', className = '' }: { children?: ReactNode; className?: string }) {
  return <p className={`py-8 text-center text-[13px] text-faint ${className}`}>{children}</p>
}
