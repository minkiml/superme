import { featureColor, featureLabel } from '@/lib/palette'
import { fmtTokens } from '@/lib/format'

// How token spend reads as a list of OPERATIONS, used by every surface that shows one.
//
// Each category says whether it renders as one bar or one per feature; a renderer re-deciding that
// is how surfaces disagree.

export type OperationRow = { key: string; label: string; value: number; color: string; title?: string }

type Category = { total?: number; features?: Record<string, number>; label?: string; collapsed?: boolean }

// A collapsed category needs a colour clearly not one of the operations beside it.
const CATEGORY_COLOR: Record<string, string> = {
  learning: '#7c8cf8',
  other: '#8b93a7',
}

/**
 * `cacheRead` adds each feature's cache_read on top: pass it only in 4-type mode, and only where
 * the bucket carries the map.
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
