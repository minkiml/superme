import { useEffect, useState } from 'react'
import { Coins, FolderKanban, Bot, GraduationCap } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import AttentionCenter from './AttentionCenter'
import { fmtTokens } from '@/lib/format'
import type { CommandStats } from './useCommandStats'
import { getTokenTimeseries, type SystemHold, type TokenTimeseries } from '@/lib/api'

// Full-width top bar: the brand, the four system stats, and the attention center (Pass 2 · Q2) — a
// bell that surfaces every item across all projects that's holding for the owner. Theme lives in
// the nav footer.
//
// The stats live HERE, in the bar, not in a strip of their own (owner, 2026-07-31). As full-width
// tiles they cost ~140px of vertical height on every screen — permanently — to show four numbers
// that move slowly, and that height is exactly what the surfaces below were short of. As chips they
// cost nothing and stay legible.
//
// Hover is a real POPOVER, not the browser's `title=` (owner, 2026-08-01): the native tooltip waits
// about a second, renders in the OS chrome rather than the app's, and can only say text. It borrows
// the pattern the token drill-in's over-time bars already use — `group` + an absolutely positioned
// `group-hover:block` panel — so hovering a stat feels the same wherever you do it.
//
// Only stats that HAVE a drill-in are clickable. Projects had none and briefly navigated to Nexus
// instead, which reads as a misfire: a control that answers a different question than the one you
// clicked. It is now a read-out with a label, nothing more.
type Chip = {
  id: string
  label: string
  icon: LucideIcon
  drill?: boolean            // has a drill-in overlay ⇒ clickable
  value: (s: CommandStats) => string
}

const CHIPS: Chip[] = [
  // `Coins`, not a dollar sign and not a bare circle: these are counted units, not money, and an
  // empty outline said nothing at all.
  { id: 'tokens', label: 'Token usage', icon: Coins, drill: true,
    value: (s) => fmtTokens(s.tokensTotal) },
  { id: 'projects', label: 'Connected projects', icon: FolderKanban,
    value: (s) => String(s.projects) },
  // `Bot` is the app's agent glyph EVERYWHERE — the chat rail's speaker, the activity log, the
  // artifact author, the harness list. This chip counts agents, so it wears the same face; `Cpu`
  // read as machine load and was the only place an agent looked like something else.
  { id: 'ops', label: 'Agents running / live', icon: Bot, drill: true,
    value: (s) => `${s.opsRunning}/${s.opsLive}` },
  { id: 'learning', label: 'Learning · candidate / pending / drafted / learned',
    icon: GraduationCap, drill: true,
    value: (s) => `${s.learn.candidates}/${s.learn.pending}/${s.learn.drafted}/${s.learn.learned}` },
]

// The token-type colours, 3-type (no cache read — the "new work" split the chip's number is). Same
// hexes as the drill-in's TYPE_META so the sparkline and the full chart read as one thing.
const SPARK_TYPES = [
  { key: 'input', color: '#6ea8fe' },
  { key: 'cache_creation', color: '#e0a35a' },
  { key: 'output', color: '#5fe3b3' },
  { key: 'legacy', color: '#4b5563' },
]

// The token chip's popover body: the drill-in's Over time → By type chart, stripped to the bars.
// No axis, no dates, no values — at this size a shape is all that reads, and the drill-in is one
// click away for the numbers.
function TokenSpark({ ts }: { ts: TokenTimeseries | null }) {
  const days = ts?.days ?? []
  if (!days.length) return <div className="text-[11px] text-muted">No usage yet</div>
  const total = (d: TokenTimeseries['days'][number]) =>
    SPARK_TYPES.reduce((s, t) => s + ((d[t.key as keyof typeof d] as number) ?? 0), 0)
  const max = Math.max(1, ...days.map(total))
  return (
    <div className="flex h-12 w-40 items-end gap-px">
      {days.map((d) => (
        <div key={d.day} className="flex h-full flex-1 flex-col justify-end" style={{ minWidth: 3 }}>
          {SPARK_TYPES.map((t) => {
            const v = (d[t.key as keyof typeof d] as number) ?? 0
            return v ? (
              <div key={t.key}
                   style={{ height: `${Math.max(1, (v / max) * 100)}%`, backgroundColor: t.color }} />
            ) : null
          })}
        </div>
      ))}
    </div>
  )
}

export default function TopBar({ stats, onDetails, onGoto }: {
  stats: CommandStats
  onDetails: (id: string) => void
  onGoto: (repoId: string, hold: SystemHold) => void
}) {
  // Fetched once for the sparkline. Not on the live cache: this is chrome, it moves once a day, and
  // a per-day series has nothing to gain from the board's polling cadence.
  const [ts, setTs] = useState<TokenTimeseries | null>(null)
  useEffect(() => { getTokenTimeseries().then(setTs).catch(() => {}) }, [])

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line bg-sidebar px-4">
      <div className="flex items-center gap-2.5">
        <span className="h-7 w-7 rounded-lg bg-iris shadow-sm" />
        <span className="text-[17px] font-semibold tracking-tight text-fg">superme</span>
      </div>
      <div className="ml-auto flex items-center">
        {CHIPS.map((c) => {
          const Icon = c.icon
          const body = (
            <>
              <Icon size={14} className="shrink-0" />
              <span className="font-mono text-[13px] tabular-nums">
                {stats.loading ? '—' : c.value(stats)}
              </span>
            </>
          )
          const pop = (
            <div className="pointer-events-none absolute right-0 top-full z-30 mt-1.5 hidden w-max rounded-md border border-line bg-surface px-2.5 py-1.5 shadow-lg group-hover:block">
              <div className="text-[11px] text-muted">{c.label}</div>
              {c.id === 'tokens' && <div className="mt-1.5"><TokenSpark ts={ts} /></div>}
            </div>
          )
          return c.drill ? (
            <button key={c.id} onClick={() => onDetails(c.id)}
                    className="group relative flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-muted transition-colors hover:bg-hover hover:text-fg">
              {body}{pop}
            </button>
          ) : (
            <span key={c.id}
                  className="group relative flex cursor-default items-center gap-1.5 rounded-lg px-2 py-1.5 text-muted">
              {body}{pop}
            </span>
          )
        })}
        <span className="mx-2 h-4 w-px bg-line" />
        <AttentionCenter onGoto={onGoto} />
      </div>
    </header>
  )
}
