import type { ReactNode } from 'react'

// The uppercase eyebrow label that titles a section / panel group.
//
// It is `text-fg`, not muted (owner, 2026-08-01). A heading's job is to be FOUND — you scan for it
// to know where you are. Greying it made a section title read as small dim prose, indistinguishable
// from the secondary text underneath, so a screen of four sections had no visible skeleton. The
// eyebrow treatment (caps + tracking + 11px) is what marks it as structure rather than content;
// the colour is what makes it survivable at a glance. Grey belongs on labels, not on headings.
export default function SectionHeader({ children, className = '' }: { children: ReactNode; className?: string }) {
  // 13px — the BODY step of the type scale, not a size of its own. Caps + tracking + weight already
  // mark it as a heading; a half-pixel bump on top of that just adds a fifth size to the ramp.
  return <div className={`text-[13px] font-semibold uppercase tracking-wider text-fg ${className}`}>{children}</div>
}
