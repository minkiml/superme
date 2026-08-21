import type { ReactNode } from 'react'
import type { WorkItem } from '@/lib/api'

// Shared dev-knowledge primitives (the v2 work-item model — D-018).

// The per-kind phase pipelines. The kanban renders the UNION in pipeline order, and a column shows
// only when it has items.
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

export const PHASE_LABEL: Record<string, string> = Object.fromEntries(PHASES.map((p) => [p.key, p.label]))

// Reads left to right as the pipeline advances: thinking is blue, mid-flight amber, finishing
// green.
export const STATUS_COLOR: Record<string, string> = {
  active: 'text-accent-text',
  // `error` is the only red here: the work STOPPED, which is louder than resting at a gate by
  // design.
  error: 'text-danger',
  awaiting_human: 'text-warn',
  awaiting_child: 'text-muted',
  awaiting_upstream: 'text-muted',
  awaiting_slot: 'text-muted',
  done: 'text-success',
}

// A fast scan cue on work-cards. Literal classes, so Tailwind keeps them; width comes from the
// border utility.
export const STATUS_STRIPE: Record<string, string> = {
  active: 'border-l-accent',
  error: 'border-l-danger',
  awaiting_human: 'border-l-warn',
  awaiting_child: 'border-l-line',
  awaiting_upstream: 'border-l-line',
  awaiting_slot: 'border-l-line',
  done: 'border-l-success',
}

// Status labels are the OWNER's words: the wire values keep their names, and only this boundary is
// friendly.
export const STATUS_LABEL: Record<string, string> = {
  active: 'in progress',
  error: 'stopped',
  awaiting_human: 'needs you',
  awaiting_child: 'blocked on sub-item',
  awaiting_upstream: 'queued behind another item',
  awaiting_slot: 'queued (autopilot busy)',
  done: 'done',
}

// What an agent is DOING while a run is in flight — a more useful fact than `active`, which cannot
// say it.
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

// READ from the item's attention tier, never re-derived: a second copy of the rule put three
// answers on one screen.
export function primaryStatus(it: WorkItem, bucket?: string): string {
  if (it.done_at) return 'done'
  // READ the tier, never re-derive it. `error` outranks the rest, so an item in this bucket is
  // stopped.
  if (bucket === 'error') return 'error'
  if (bucket === 'needs_you') return 'awaiting_human'
  return it.status ?? ''
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-surface p-6 text-sm text-muted">{children}</div>
  )
}

// ── work-kind labelling ──
//
// Two chips, one axis, sharing a hue so they read as one statement. The status and scope hues mean
// other things.
const KIND_CHIP: Record<string, string> = {
  implementation: 'bg-kind-build/10 text-kind-build',
  research: 'bg-kind-research/10 text-kind-research',
}

// The family's own word, not the slug. An unknown value renders as-is rather than vanishing
// unlabelled.
const RESEARCH_KIND_LABEL: Record<string, string> = {
  audit: 'Audit',
  refactoring: 'Refactoring',
  housekeeping: 'Housekeeping',
  security: 'Security',
  'deep-diagnosis': 'Deep diagnosis',
  study: 'Study',
}

export function kindChipClass(kind?: string | null): string {
  return KIND_CHIP[kind ?? 'implementation'] ?? KIND_CHIP.implementation
}

// The same two hues as bare text, for rows that label inline. One source, so a kind cannot read
// blue here and green there.
export const KIND_TEXT: Record<string, string> = {
  implementation: 'text-kind-build',
  research: 'text-kind-research',
}

export function workKindLabel(kind?: string | null): string | null {
  if (!kind) return null
  return kind.charAt(0).toUpperCase() + kind.slice(1)
}

export function researchKindLabel(rk?: string | null): string | null {
  if (!rk) return null
  return RESEARCH_KIND_LABEL[rk] ?? rk.replace(/-/g, ' ')
}

// Deliberately coarse: the reader asks whether this is still warm, and a per-second tick makes the
// board twitch.
export function agoLabel(epochSeconds?: number | null): string | null {
  if (!epochSeconds) return null
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds))
  if (secs < 60) return 'just now'
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return days < 7 ? `${days}d ago` : `${Math.floor(days / 7)}w ago`
}
