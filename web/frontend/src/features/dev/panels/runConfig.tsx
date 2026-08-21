import { type InboxKind } from '@/lib/api'
import { MODELS as MODEL_CATALOG, DEFAULT_MODEL, EFFORTS as EFFORT_CATALOG, DEFAULT_EFFORT } from '@/lib/format'

// The model, effort and kind a run can be given, and the shape that carries an override.

// The catalog's concrete ids; the daemon normalizes any value to the latest at consumption.
export const RUN_MODELS = MODEL_CATALOG.map((m) => ({ value: m.key, label: m.label }))

export const DEFAULT_RUN_MODEL = DEFAULT_MODEL

// Reasoning-effort levels selectable per run, alongside the model. Default "medium".
export const RUN_EFFORTS = EFFORT_CATALOG.map((e) => ({ value: e.key, label: e.label }))

export const DEFAULT_RUN_EFFORT = DEFAULT_EFFORT

// WHO runs, as data: a row's config is a model and an effort PER ROLE, because vet and the deputy
// deliberately do not run on what the work runs on. The Setting tab is a loop over this list.
export const RUN_ROLES = [
  { key: '', label: 'Work', hint: 'Every phase run of this item' },
  { key: 'vet', label: 'Vet', hint: 'Checks what build produced' },
  { key: 'deputy', label: 'Deputy', hint: 'Judges the gates' },
] as const

export type RunRole = (typeof RUN_ROLES)[number]['key']

/** A role's field name on the row: the work role owns the bare keys, the rest are prefixed. */
export const roleField = (role: RunRole, f: 'model' | 'effort') =>
  (role ? `${role}_${f}` : f) as 'model' | 'effort' | 'vet_model' | 'vet_effort' | 'deputy_model' | 'deputy_effort'

/** What each role runs when this row says nothing — the chain's answer, resolved by the caller. */
export type RoleDefaults = Partial<Record<RunRole, { model: string; effort: string }>>

// The PROPOSED kind. "Undecided" is a real answer — triage judges alone — so it leads the list.
export const WORK_KIND_OPTS = [
  { value: '', label: 'Undecided' },
  { value: 'implementation', label: 'Implementation' },
  { value: 'research', label: 'Research' },
]

// Content and setting travel together, because the card has one Save. `model` and `effort` are
// always concrete.
export type InboxConfigPatch = {
  title: string | null
  text: string
  kind: InboxKind
  model: string
  effort: string
  autopilot: boolean
  work_kind: string
  // The two roles that do NOT run on this item's model, which is why these carry an empty option.
  vet_model: string
  vet_effort: string
  deputy_model: string
  deputy_effort: string
}

// Shared dev-knowledge store views — the bodies for the board and the capture queue.
//
// `phase` is the per-kind pipeline; `status` the runnable axis, with `done` derived.

// --- status / branch-off chrome -------------------------------------------------

// A picker's own word for a stored value — so the read view says "Sonnet 5", not `sonnet-5`.
export function optLabel(opts: { value: string; label: string }[], value: string): string {
  return opts.find((o) => o.value === value)?.label ?? value ?? '—'
}
