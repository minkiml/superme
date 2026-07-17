import type { ReactNode } from 'react'
import type { WorkItem } from '@/lib/api'

// Shared dev-knowledge primitives (the v2 work-item model — D-018).

// The per-kind phase pipelines (workspace-workflow D2). The kanban renders the UNION in pipeline
// order; a column only shows when it has items (mid-pipeline research stages sit beside their
// implementation counterparts). Board redesign lands with the workflow's surface stage (S7).
// `deliver` reads as "Review" to the owner: the phase IS the owner reading the finished work and
// deciding, and "deliver" named it from the agent's side. The wire value stays `deliver` — it's a
// stored contract enum in every item's frontmatter — so the rename lives here, at the render edge.
export const PHASES: { key: string; label: string }[] = [
  { key: 'triage', label: 'Triage' },
  { key: 'plan', label: 'Plan' },
  { key: 'build', label: 'Build' },
  { key: 'investigate', label: 'Investigate' },
  { key: 'validate', label: 'Validate' },
  { key: 'report', label: 'Report' },
  { key: 'deliver', label: 'Review' },
  { key: 'close', label: 'Close' },
]

// The four phases that END at a briefed human gate (mirrors core/gate_briefs.GATE_FOR_PHASE). The
// owner's Approve button belongs on THESE and nowhere else: build→validate and validate→deliver
// are the autonomous loop's edges, and rendering a gate button there invents a decision the owner
// was never asked to make.
export const GATED_PHASES = new Set(['triage', 'plan', 'deliver', 'close'])
export const PHASE_LABEL: Record<string, string> = Object.fromEntries(PHASES.map((p) => [p.key, p.label]))

// Per-phase accent token (dot + column rail) — reads left→right as the pipeline advances:
// intake/plan = dev-blue (thinking), mid-flight = warn-amber, deliver/close = success-green.
export const PHASE_ACCENT: Record<string, string> = {
  triage: 'dev',
  plan: 'dev',
  build: 'warn',
  investigate: 'warn',
  validate: 'warn',
  report: 'success',
  deliver: 'success',
  close: 'success',
}

// Display status = the runnable axis (D2), plus the derived terminal state `done`.
// `awaiting_human` is the attention color — it pages the owner.
export const STATUS_COLOR: Record<string, string> = {
  active: 'text-accent-text',
  awaiting_human: 'text-warn',
  awaiting_child: 'text-muted',
  done: 'text-success',
}

// Left-edge accent stripe per display status — a fast scan cue on work-cards (literal classes so
// Tailwind keeps them). Overrides only the card's left border color; width comes from `border-l-2`.
export const STATUS_STRIPE: Record<string, string> = {
  active: 'border-l-accent',
  awaiting_human: 'border-l-warn',
  awaiting_child: 'border-l-line',
  done: 'border-l-success',
}

// Status labels are the OWNER's words, not the schema's: `active` says nothing about what's
// happening, and `awaiting_child` asks the reader to know our data model. The wire values keep
// their names; only this render boundary is friendly.
export const STATUS_LABEL: Record<string, string> = {
  active: 'in progress',
  awaiting_human: 'needs you',
  awaiting_child: 'blocked on sub-item',
  done: 'done',
}

// What an agent is DOING while a run is in flight — the badge shows this instead of the status,
// because "an agent is working on it right now" is the more useful fact and `active` can't say it.
export const PHASE_VERB: Record<string, string> = {
  triage: 'triaging',
  plan: 'planning',
  build: 'building',
  validate: 'validating',
  investigate: 'investigating',
  report: 'reporting',
  deliver: 'reviewing',
  close: 'closing',
}

// The item's primary display status: completion (done_at) reads as `done`, else the active status.
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
