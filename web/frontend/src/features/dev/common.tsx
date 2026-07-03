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

// Per-phase accent token (dot + column rail) — reads left→right as the pipeline advances:
// plan = dev-blue (thinking), build = warn-amber (in flight), done = success-green (shipped).
export const PHASE_ACCENT: Record<string, string> = {
  plan_design: 'dev',
  build_eval: 'warn',
  done: 'success',
}

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

// Left-edge accent stripe per display status — a fast scan cue on work-cards (literal classes so
// Tailwind keeps them). Overrides only the card's left border color; width comes from `border-l-2`.
export const STATUS_STRIPE: Record<string, string> = {
  queued: 'border-l-line',
  in_progress: 'border-l-accent',
  waiting: 'border-l-warn',
  dropped: 'border-l-faint',
  done: 'border-l-success',
  blocked: 'border-l-danger',
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
