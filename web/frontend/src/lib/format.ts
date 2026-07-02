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

// Canonical model catalog — proper "Name Version" labels, keyed by the CONCRETE model id that
// actually runs that version. This is the ONE source of truth for every model picker (config
// select, /model chat picker, work-item run picker) and every model label. We send the CONCRETE
// id (NOT a bare alias): the CLI's `sonnet`/`opus` aliases LAG (they resolve to claude-sonnet-4-6 /
// claude-opus-4-7, not the labels below), so picking an alias would silently run an older model.
// Mirror core/models.py MODEL_TIERS. Bump both when a newer tier ships.
export type ModelKey = 'claude-opus-4-8' | 'claude-sonnet-5' | 'claude-haiku-4-5'

export const MODELS: { key: ModelKey; label: string; blurb: string }[] = [
  { key: 'claude-opus-4-8', label: 'Opus 4.8', blurb: 'Most capable — everyday complex work' },
  { key: 'claude-sonnet-5', label: 'Sonnet 5', blurb: 'Balanced — efficient for routine tasks' },
  { key: 'claude-haiku-4-5', label: 'Haiku 4.5', blurb: 'Fastest for quick answers' },
]

// Labels by picker key (concrete id) PLUS the legacy bare aliases, so a historical override still
// stored as an alias ("sonnet") renders to a sensible label rather than a raw token.
const MODEL_LABEL: Record<string, string> = {
  ...Object.fromEntries(MODELS.map((m) => [m.key, m.label])),
  opus: 'Opus', sonnet: 'Sonnet', haiku: 'Haiku',
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
const EFFORT_LABEL: Record<string, string> = Object.fromEntries(EFFORTS.map((e) => [e.key, e.label]))

/** A reasoning-effort label ("medium" → "Medium"). Empty for missing. */
export function fmtEffort(id?: string | null): string {
  if (!id) return ''
  return EFFORT_LABEL[id.toLowerCase()] ?? id.charAt(0).toUpperCase() + id.slice(1)
}

/** A model label in canonical "Name Version" form. Handles both a bare alias ("sonnet" →
 * "Sonnet 5", the catalog's latest) and a full resolved id ("claude-opus-4-8" → "Opus 4.8",
 * "claude-haiku-4-5-20251001" → "Haiku 4.5"). Empty for missing. */
export function fmtModel(id?: string | null): string {
  if (!id) return ''
  // A full resolved id: claude-<tier>-<major>[-<minor>][-<date>]. Preserve its real version
  // (a historical run may predate the current latest) but capitalize + drop trailing dates.
  const m = id.match(/^claude-([a-z]+)-(\d+)(?:-(\d+))?/)
  if (m) {
    const name = m[1].charAt(0).toUpperCase() + m[1].slice(1)
    return m[3] ? `${name} ${m[2]}.${m[3]}` : `${name} ${m[2]}`
  }
  // A bare alias — surface the catalog's latest label for that tier.
  return MODEL_LABEL[id.toLowerCase()] ?? id.charAt(0).toUpperCase() + id.slice(1)
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
