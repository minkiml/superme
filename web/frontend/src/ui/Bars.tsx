import { fmtTokens } from '@/lib/format'

// A horizontal bar list — one row per item, normalized to the largest value. Used by every stat
// drill-in (tokens, projects, agents, learning) so the breakdown views read identically. Each row:
// a color dot, a mono label, the proportional bar, a right-aligned value, and an optional sub-note.
// `fmt` formats the value (defaults to compact token counts); pass String for plain counts.
//
// `title` is for a row that STANDS FOR several things — a collapsed group. Collapsing keeps a list
// readable, but a bar whose parts can never be seen is a bar you cannot act on, so hovering names
// them. Omit it wherever the label already IS the whole answer.
export type BarRow = { key: string; label: string; sub?: string; value: number; color: string; title?: string }

export default function Bars({
  rows,
  fmt = fmtTokens,
  labelWidth = 'w-32',
}: {
  rows: BarRow[]
  fmt?: (n: number) => string
  labelWidth?: string
}) {
  const max = rows.length ? Math.max(...rows.map((r) => r.value)) || 1 : 1
  return (
    <div className="space-y-2.5">
      {rows.map((r) => (
        <div key={r.key} title={r.title} className="flex items-center gap-3 text-[14px]">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: r.color }} />
          <span className={`${labelWidth} shrink-0 truncate font-mono text-fg`}>{r.label}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-hover">
            <div className="h-full rounded-full" style={{ width: `${(r.value / max) * 100}%`, backgroundColor: r.color }} />
          </div>
          <span className="w-14 shrink-0 text-right font-mono text-muted">{fmt(r.value)}</span>
          {r.sub && <span className="w-24 shrink-0 truncate text-[13px] text-faint">{r.sub}</span>}
        </div>
      ))}
    </div>
  )
}
