// Time formatting — timestamps are stored UTC (ISO 8601); always render in the owner's
// local timezone. Keep all human-facing time display going through these helpers so the
// dashboard never shows raw UTC.

/** A short local date+time, e.g. "Jun 19, 11:17". Empty string for missing/invalid input. */
export function fmtLocal(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * How long ago, and a plain date past a week, since by then "9d" is harder to place than the date
 * itself.
 */
export function fmtAge(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const mins = Math.floor((Date.now() - d.getTime()) / 60_000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m`
  if (mins < 60 * 24) return `${Math.floor(mins / 60)}h`
  const days = Math.floor(mins / (60 * 24))
  if (days < 7) return `${days}d`
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** A local date only, e.g. "Jun 19, 2026". */
export function fmtLocalDate(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/** Compact token count, e.g. 1234 → "1.2k", 1500000 → "1.5M", 0 → "0". */
export function fmtTokens(n?: number | null): string {
  const v = n ?? 0
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`
  return String(v)
}

// The ONE source of truth for every model picker, keyed by the CONCRETE id that runs that version.
export type ModelKey = 'claude-opus-5' | 'claude-sonnet-5' | 'claude-haiku-4-5'

export const MODELS: { key: ModelKey; label: string; blurb: string }[] = [
  { key: 'claude-opus-5', label: 'Opus 5', blurb: 'Most capable — everyday complex work' },
  { key: 'claude-sonnet-5', label: 'Sonnet 5', blurb: 'Balanced — efficient for routine tasks' },
  { key: 'claude-haiku-4-5', label: 'Haiku 4.5', blurb: 'Fastest for quick answers' },
]

// The FE default pick (mirrors backend DEFAULT_MODEL = MODEL_TIERS["sonnet"]). Concrete so it matches a
// picker option key; the backend still normalizes any value at consumption. Bump with MODELS above.
export const DEFAULT_MODEL: ModelKey = 'claude-sonnet-5'

// Aliases are the canonical stored form; the backend resolves them to the latest concrete at
// consumption.
const MODEL_LABEL: Record<string, string> = {
  ...Object.fromEntries(MODELS.map((m) => [m.key, m.label])),
  opus: 'Opus 5', sonnet: 'Sonnet 5', haiku: 'Haiku 4.5',
}

// A stored alias must map to a concrete option key, or the picker shows nothing selected.
const ALIAS_KEY: Record<string, ModelKey> = {
  opus: 'claude-opus-5', sonnet: 'claude-sonnet-5', haiku: 'claude-haiku-4-5',
}
/** Normalize a stored model value (tier alias or concrete id) to its concrete picker-option key.
 * Empty string for missing (so it clears a Dropdown to no-selection). */
export function toModelKey(id?: string | null): string {
  return id ? (ALIAS_KEY[id] ?? id) : ''
}

// Reasoning effort — the second axis next to model, controllable everywhere model is. Default
// "medium". (The SDK also accepts xhigh/max; we expose the common low/medium/high range.)
export type EffortKey = 'low' | 'medium' | 'high'
export const DEFAULT_EFFORT: EffortKey = 'medium'
export const EFFORTS: { key: EffortKey; label: string; blurb: string }[] = [
  { key: 'low', label: 'Low', blurb: 'Least reasoning — fastest, cheapest' },
  { key: 'medium', label: 'Medium', blurb: 'Balanced reasoning (default)' },
  { key: 'high', label: 'High', blurb: 'Most reasoning — hardest problems' },
]

/** A model label in canonical "Name Version" form. Handles both a bare alias ("sonnet" →
 * "Sonnet 5", the catalog's latest) and a full resolved id ("claude-opus-4-8" → "Opus 4.8",
 * "claude-haiku-4-5-20251001" → "Haiku 4.5"). Empty for missing. */
export function fmtModel(id?: string | null): string {
  if (!id) return ''
  // Preserve a resolved id's real version — a historical run may predate the latest — but drop
  // trailing dates.
  const m = id.match(/^claude-([a-z]+)-(\d+)(?:-(\d+))?/)
  if (m) {
    const name = m[1].charAt(0).toUpperCase() + m[1].slice(1)
    return m[3] ? `${name} ${m[2]}.${m[3]}` : `${name} ${m[2]}`
  }
  // A bare alias — surface the catalog's latest label for that tier.
  return MODEL_LABEL[id.toLowerCase()] ?? id.charAt(0).toUpperCase() + id.slice(1)
}

/** A live ticker's elapsed SECONDS: tenths while it still reads as a moment ("8.4s"), minutes once
 *  it doesn't ("1m 49s", "2h 3m"). A bare "108.6s" makes the reader do the division. */
export function fmtElapsed(seconds: number): string {
  const total = Math.max(0, seconds)
  if (total < 60) return `${total.toFixed(1)}s`
  if (total < 3600) return `${Math.floor(total / 60)}m ${Math.floor(total % 60)}s`
  return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`
}

/** A duration in ms as "m:ss" (or "h:mm:ss" past an hour), e.g. 83000 → "1:23". */
export function fmtDuration(ms?: number | null): string {
  const total = Math.max(0, Math.floor((ms ?? 0) / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (x: number) => String(x).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}
