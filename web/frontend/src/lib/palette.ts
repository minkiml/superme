// A categorical color palette for distinct items (repos, operations, bars) — vibrant on the
// near-black surface, so the dashboard reads dynamic rather than mono-blue/green. Used via inline
// styles (Tailwind can't do dynamic per-item classes).

export const PALETTE = [
  '#6ea8fe', // blue
  '#f472b6', // pink
  '#a78bfa', // violet
  '#34d399', // emerald
  '#fbbf24', // amber
  '#22d3ee', // cyan
  '#fb7185', // rose
  '#c084fc', // purple
  '#f59e0b', // orange
  '#4fd1c5', // teal
]

function hash(key: string): number {
  let h = 0
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0
  return h
}

// Stable color for any key (repo id, etc.) — same key always maps to the same hue.
export function colorFor(key: string): string {
  return PALETTE[hash(key) % PALETTE.length]
}

// Fixed colors for the known run features (SuperMe operations). Deliberately a warm + purple
// family — NO blue and NO mint/green — so an operation bar never reads as the dev (blue) or core
// (mint) scope color they sit beside. Fallback for an unknown feature stays a hashed hue.
const FEATURE: Record<string, string> = {
  sweep: '#fbbf24', capture: '#fbbf24',   // amber (sweep = capture agent)
  plan: '#a78bfa',                        // violet
  distill: '#d946ef',                     // fuchsia
  write: '#f97316', forge: '#f97316',     // orange (write = forge agent)
  chat: '#fb7185',                        // rose
  design: '#f472b6',                      // pink
}

export function featureColor(feature: string): string {
  return FEATURE[feature] ?? colorFor(feature)
}

// Runs are stamped with the canonical feature key (`sweep`, `write`), but the owner-facing
// vocabulary is the agent name (`capture`, `forge`) shown everywhere else (Quick config, skills,
// agent .md). Map key → display label so the Activity chips read consistently. Unknown keys pass
// through unchanged.
const FEATURE_LABEL: Record<string, string> = {
  sweep: 'capture',
  write: 'forge',
  'prompt-extraction': 'probe',   // the throwaway Prompt X-ray run — one word in the feed
}

export function featureLabel(feature: string): string {
  return FEATURE_LABEL[feature] ?? feature
}
