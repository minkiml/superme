import type { ReactNode } from 'react'
import type { WorkItem } from '@/lib/api'

// Shared dev-knowledge primitives (the v2 work-item model — D-018).

// The three lifecycle phases (Plan/Design → Build/Eval → Done), in order.
export const PHASES: { key: string; label: string }[] = [
  { key: 'plan_design', label: 'Plan / Design' },
  { key: 'build_eval', label: 'Build / Eval' },
  { key: 'done', label: 'Done' },
]
export const PHASE_LABEL: Record<string, string> = Object.fromEntries(PHASES.map((p) => [p.key, p.label]))

// Display status = active status, plus the two non-status display states: `done`
// (completion — derived from done_at) and `blocked` (derived overlay).
export const STATUS_COLOR: Record<string, string> = {
  queued: 'text-muted',
  in_progress: 'text-accent-text',
  waiting: 'text-warn',
  dropped: 'text-faint',
  done: 'text-success',
  blocked: 'text-danger',
}

export const STATUS_LABEL: Record<string, string> = {
  queued: 'queued',
  in_progress: 'in progress',
  waiting: 'waiting',
  dropped: 'dropped',
  done: 'done',
  blocked: 'blocked',
}

// The item's primary display status: completion (done_at) reads as `done`, else the active
// status. `blocked` is a separate overlay (see item.blocked), not the primary status.
export function primaryStatus(it: WorkItem): string {
  if (it.done_at) return 'done'
  return it.status ?? ''
}

export function Pill({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full bg-hover px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted">
      {children}
    </span>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-surface p-6 text-sm text-muted">{children}</div>
  )
}
