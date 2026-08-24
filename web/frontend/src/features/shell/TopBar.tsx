import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Coins, FolderKanban, Bot, GraduationCap } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import AttentionCenter from './AttentionCenter'
import { fmtTokens } from '@/lib/format'
import type { CommandStats } from './useCommandStats'
import { getTokenTimeseries, type SystemHold, type TokenTimeseries } from '@/lib/api'

// Full-width top bar: the brand, the system stats, and the attention bell.
//
// The stats are chips rather than a strip, which would cost vertical height on every screen.
//
// Hover is a real POPOVER, centred and clamped.
type Chip = {
  id: string
  label: string
  icon: LucideIcon
  drill?: boolean            // has a drill-in overlay ⇒ clickable
  value: (s: CommandStats) => string
}

const CHIPS: Chip[] = [
  // Coins, not a dollar sign: these are counted units, not money, and an empty outline said
  // nothing.
  { id: 'tokens', label: 'Token usage', icon: Coins, drill: true,
    value: (s) => fmtTokens(s.tokensTotal) },
  { id: 'projects', label: 'Connected projects', icon: FolderKanban,
    value: (s) => String(s.projects) },
  // `Bot` is the app's agent glyph everywhere, so the chip that counts agents wears the same face.
  { id: 'ops', label: 'Agents running / live', icon: Bot, drill: true,
    value: (s) => `${s.opsRunning}/${s.opsLive}` },
  { id: 'learning', label: 'Learning · candidate / pending / drafted / learned',
    icon: GraduationCap, drill: true,
    value: (s) => `${s.learn.candidates}/${s.learn.pending}/${s.learn.drafted}/${s.learn.learned}` },
]

// The token-type colours, 3-type (no cache read — the "new work" split the chip's number is). Same
// hexes as the drill-in's TYPE_META so the sparkline and the full chart read as one thing.
// A fixed window, not the whole history: the series grows by a day forever, and a sparkline that
// grows with it outgrows its own panel.
const SPARK_DAYS = 30

const SPARK_TYPES = [
  { key: 'input', color: '#6ea8fe' },
  { key: 'cache_creation', color: '#e0a35a' },
  { key: 'output', color: '#5fe3b3' },
]

// Stripped to the bars: at this size a shape is all that reads, and the drill-in has the numbers.
function TokenSpark({ ts }: { ts: TokenTimeseries | null }) {
  const days = (ts?.days ?? []).slice(-SPARK_DAYS)
  if (!days.length) return <div className="text-[11px] text-muted">No usage yet</div>
  const total = (d: TokenTimeseries['days'][number]) =>
    SPARK_TYPES.reduce((s, t) => s + ((d[t.key as keyof typeof d] as number) ?? 0), 0)
  const max = Math.max(1, ...days.map(total))
  return (
    // The columns divide the width; none may claim a floor, or the row paints outside the panel.
    <div className="flex h-12 w-40 items-end gap-px overflow-hidden">
      {days.map((d) => (
        <div key={d.day} className="flex h-full min-w-0 flex-1 flex-col justify-end">
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

// Distance kept from the window edge when a popover would otherwise overhang it.
const EDGE = 8

/**
 * Fixed rather than absolute, so the bar's own box cannot clip it, then nudged back inside the
 * window.
 */
function Pop({ anchor, children }: { anchor: DOMRect; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [shift, setShift] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    let d = 0
    if (r.right > window.innerWidth - EDGE) d = window.innerWidth - EDGE - r.right
    if (r.left + d < EDGE) d = EDGE - r.left
    if (Math.abs(d) >= 0.5) setShift((s) => s + d)
  }, [anchor])
  return (
    <div ref={ref}
         className="pointer-events-none fixed z-50 w-max rounded-md border border-line bg-surface px-2.5 py-1.5 shadow-lg"
         style={{ left: anchor.left + anchor.width / 2, top: anchor.bottom + 6,
                  transform: `translateX(calc(-50% + ${shift}px))` }}>
      {children}
    </div>
  )
}

export default function TopBar({ stats, onDetails, onGoto }: {
  stats: CommandStats
  onDetails: (id: string) => void
  onGoto: (repoId: string, hold: SystemHold) => void
}) {
  // Fetched once, off the live cache: this is chrome, and it moves once a day.
  const [ts, setTs] = useState<TokenTimeseries | null>(null)
  useEffect(() => { getTokenTimeseries().then(setTs).catch(() => {}) }, [])

  // The rect is captured rather than re-read every frame: the bar does not scroll and the chips do
  // not move.
  const [hot, setHot] = useState<{ id: string; rect: DOMRect } | null>(null)
  useEffect(() => {
    const drop = () => setHot(null)
    window.addEventListener('resize', drop)
    return () => window.removeEventListener('resize', drop)
  }, [])
  const enter = (id: string) => (e: { currentTarget: HTMLElement }) =>
    setHot({ id, rect: e.currentTarget.getBoundingClientRect() })

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line bg-sidebar px-4">
      {/* No mark: the rule under the word is the device. */}
      <div className="flex flex-col items-start">
        <span className="font-display text-[21px] font-semibold leading-none tracking-[-.005em] text-fg">
          SuperMe
        </span>
        <span className="mt-[6px] h-[2.5px] w-full rounded-full bg-iris" />
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
          const pop = hot?.id === c.id ? (
            <Pop anchor={hot.rect}>
              <div className="text-[11px] text-muted">{c.label}</div>
              {c.id === 'tokens' && <div className="mt-1.5"><TokenSpark ts={ts} /></div>}
            </Pop>
          ) : null
          // Focus opens it too, so the two drillable chips explain themselves to a keyboard.
          const hover = { onMouseEnter: enter(c.id), onMouseLeave: () => setHot(null) }
          return c.drill ? (
            <button key={c.id} onClick={() => onDetails(c.id)} {...hover}
                    onFocus={enter(c.id)} onBlur={() => setHot(null)}
                    className="relative flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-muted transition-colors hover:bg-hover hover:text-fg">
              {body}{pop}
            </button>
          ) : (
            <span key={c.id} {...hover}
                  className="relative flex cursor-default items-center gap-1.5 rounded-lg px-2 py-1.5 text-muted">
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
