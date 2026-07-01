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

/** A short model label: a full id "claude-sonnet-4-6" → "sonnet 4.6"; an alias "haiku" →
 * "Haiku" (capitalized); empty for missing. */
export function fmtModel(id?: string | null): string {
  if (!id) return ''
  const m = id.match(/claude-([a-z]+)-(\d+)-(\d+)/)
  if (m) return `${m[1]} ${m[2]}.${m[3]}`
  return id.charAt(0).toUpperCase() + id.slice(1)
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
