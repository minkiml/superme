import type { ReactNode } from 'react'
import type { WorkItem } from '@/lib/api'

// Shared dev-knowledge primitives (the v2 work-item model — D-018).

// The per-kind phase pipelines (workspace-workflow D2). The kanban renders the UNION in pipeline
// order; a column only shows when it has items (mid-pipeline research stages sit beside their
// implementation counterparts). Board redesign lands with the workflow's surface stage (S7).
// `vet`/`review` are the wire enums too (build-vet-loop O1) — the old `validate`/`deliver` values
// were renamed end-to-end (enums + stored frontmatter, via migration), not just at this edge.
export const PHASES: { key: string; label: string }[] = [
  { key: 'triage', label: 'Triage' },
  { key: 'plan', label: 'Plan' },
  { key: 'build', label: 'Build' },
  { key: 'investigate', label: 'Investigate' },
  { key: 'vet', label: 'Vet' },
  { key: 'report', label: 'Report' },
  { key: 'review', label: 'Review' },
  { key: 'close', label: 'Close' },
]

// The four phases that END at a briefed human gate (mirrors core/gate_briefs.GATE_FOR_PHASE). The
// owner's Approve button belongs on THESE and nowhere else: build→vet and vet→review
// are the autonomous loop's edges, and rendering a gate button there invents a decision the owner
// was never asked to make.
export const GATED_PHASES = new Set(['triage', 'plan', 'review', 'close'])
export const PHASE_LABEL: Record<string, string> = Object.fromEntries(PHASES.map((p) => [p.key, p.label]))

// Per-phase accent token (dot + column rail) — reads left→right as the pipeline advances:
// intake/plan = dev-blue (thinking), mid-flight = warn-amber, review/close = success-green.
// Display status = the runnable axis (D2), plus the derived terminal state `done`.
// `awaiting_human` is the attention color — it pages the owner.
export const STATUS_COLOR: Record<string, string> = {
  active: 'text-accent-text',
  awaiting_human: 'text-warn',
  awaiting_child: 'text-muted',
  awaiting_upstream: 'text-muted',
  awaiting_slot: 'text-muted',
  done: 'text-success',
}

// Left-edge accent stripe per display status — a fast scan cue on work-cards (literal classes so
// Tailwind keeps them). Overrides only the card's left border color; width comes from `border-l-2`.
export const STATUS_STRIPE: Record<string, string> = {
  active: 'border-l-accent',
  awaiting_human: 'border-l-warn',
  awaiting_child: 'border-l-line',
  awaiting_upstream: 'border-l-line',
  awaiting_slot: 'border-l-line',
  done: 'border-l-success',
}

// Status labels are the OWNER's words, not the schema's: `active` says nothing about what's
// happening, and `awaiting_child` asks the reader to know our data model. The wire values keep
// their names; only this render boundary is friendly.
export const STATUS_LABEL: Record<string, string> = {
  active: 'in progress',
  awaiting_human: 'needs you',
  awaiting_child: 'blocked on sub-item',
  awaiting_upstream: 'queued behind another item',
  awaiting_slot: 'queued (autopilot busy)',
  done: 'done',
}

// What an agent is DOING while a run is in flight — the badge shows this instead of the status,
// because "an agent is working on it right now" is the more useful fact and `active` can't say it.
export const PHASE_VERB: Record<string, string> = {
  triage: 'triaging',
  plan: 'planning',
  build: 'building',
  vet: 'vetting',
  investigate: 'investigating',
  report: 'reporting',
  review: 'reviewing',
  close: 'closing',
}

// The phases where a resting item is waiting for a PERSON (or the deputy acting for them). build
// and vet are excluded on purpose: the loop chains them, so an item between those two runs is
// mid-flight, not parked, and must not flash "needs you" for the second it takes to hand over.
const GATE_PHASES = new Set(['triage', 'plan', 'review', 'close'])

// The item's primary display status. Completion (done_at) reads as `done`; otherwise the stored
// status — EXCEPT that `active` at a gate with nothing running is derived back to `awaiting_human`.
//
// Why derive rather than trust the stored word: liveness is already derived (the live run row), so
// the board could show IN PROGRESS beside "AGENTS 0/4 running" — a contradiction on one screen.
// `active` means "being worked", and at a gate with no run there is nothing working. Deriving also
// heals rows that lost their hold before the ws.py fix landed, with no migration: the hold is
// re-read from the facts each render instead of being restored by a one-off script.
export function primaryStatus(it: WorkItem, running?: boolean): string {
  if (it.done_at) return 'done'
  const s = it.status ?? ''
  if (s === 'active' && !running && GATE_PHASES.has(it.phase ?? '')) return 'awaiting_human'
  return s
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-surface p-6 text-sm text-muted">{children}</div>
  )
}
