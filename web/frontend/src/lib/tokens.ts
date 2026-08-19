import { featureColor, featureLabel } from '@/lib/palette'
import { fmtTokens } from '@/lib/format'

// How token spend reads as a list of OPERATIONS — one rule, used by every surface that shows one
// (the Tokens drill-in, the repo inspector). The shape is not decided here: each category in the
// payload says whether it renders as ONE bar or one bar per feature, because that is a taxonomy
// call (superme_agent/core/token_taxonomy) and a renderer re-deciding it is how two surfaces come
// to disagree about the same number.
//
// A collapsed row carries its members in `title`, so the group can be opened by hovering it. A bar
// that stands for several things and can never be opened is a bar you cannot act on.

export type OperationRow = { key: string; label: string; value: number; color: string; title?: string }

type Category = { total?: number; features?: Record<string, number>; label?: string; collapsed?: boolean }

// A collapsed category is a bar in its own right, so it needs a colour clearly not one of the
// operations beside it: grey for maintenance, one muted hue for the background habit.
const CATEGORY_COLOR: Record<string, string> = {
  learning: '#7c8cf8',
  other: '#8b93a7',
}

/**
 * `byCategory` is a bucket's category tree. `cacheRead` adds each feature's cache_read on top —
 * pass it only in 4-type mode, and only where the bucket actually carries the map (the global
 * bucket does; per-repo ones don't, and read 3-type).
 * Sorted largest-first, zero rows dropped.
 */
export function operationRows(
  byCategory: Record<string, Category> | undefined,
  cacheRead?: Record<string, number>,
): OperationRow[] {
  const rows: OperationRow[] = []
  const val = (f: string, n: number) => n + (cacheRead?.[f] ?? 0)
  for (const [key, node] of Object.entries(byCategory ?? {})) {
    const feats = Object.entries(node.features ?? {})
    if (node.collapsed) {
      const parts = feats
        .map(([f, n]) => [featureLabel(f), val(f, n)] as const)
        .filter(([, v]) => v > 0)
        .sort((a, b) => b[1] - a[1])
      rows.push({
        key,
        label: node.label || key,
        color: CATEGORY_COLOR[key] ?? '#8b93a7',
        value: parts.reduce((sum, [, v]) => sum + v, 0),
        title: parts.map(([l, v]) => `${l} ${fmtTokens(v)}`).join(' · '),
      })
    } else {
      for (const [f, n] of feats) {
        rows.push({ key: f, label: featureLabel(f), value: val(f, n), color: featureColor(f) })
      }
    }
  }
  return rows.filter((r) => r.value > 0).sort((a, b) => b.value - a.value)
}
