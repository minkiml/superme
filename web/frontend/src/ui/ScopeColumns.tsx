import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { useContainerWidth, PANE } from '@/lib/layout'

// One column per LOADING SCOPE, each grouped by category — the layout every system-artifact surface
// uses. It exists as one component because the same three nouns (constitution, skills, agents) are
// rendered at two scopes by two different callers: when each drew its own list, the universal and
// per-repo views of the same artifact drifted into looking like different things.
//
// The shape is given, not decided here. A caller passes its columns and groups already ordered;
// this only lays them out, so the ordering rule stays with the data that knows it.

export type ScopeCard = {
  key: string
  name: string
  /** Chips after the name — kind, model, `learned`. */
  badges?: ReactNode
  /** A control at the row's right edge (a toggle, a pin). Its own clicks never reach the card. */
  trailing?: ReactNode
  onClick?: () => void
}

export type ScopeGroup = {
  /** The category chip. A group with no label draws none — the column heading already named it. */
  label?: string
  /** `learned` marks artifacts the learning loop published, wherever their scope. */
  tone?: 'scope' | 'learned'
  cards: ScopeCard[]
}

export type ScopeColumn = {
  key: string
  name: string
  /** One line under the heading — what loads this scope. */
  note: string
  tint: 'universal' | 'dev' | 'core'
  icon: LucideIcon
  groups: ScopeGroup[]
  /** Shown in place of the groups when the scope holds nothing. */
  empty?: string
}

const CHIP: Record<string, string> = {
  universal: 'bg-universal/10 text-universal',
  dev: 'bg-dev/10 text-dev',
  core: 'bg-core/10 text-core',
}
const TEXT: Record<string, string> = {
  universal: 'text-universal',
  dev: 'text-dev',
  core: 'text-core',
}

export default function ScopeColumns({ columns }: { columns: ScopeColumn[] }) {
  // The pane measures itself: this can be 500px wide inside a 1400px window, and a pane never
  // adapts by growing a sideways scrollbar (`lib/layout`) — the columns stack instead.
  const [ref, w] = useContainerWidth<HTMLDivElement>()
  const cols = w && w < PANE.narrow ? 1 : w && w < PANE.mid ? Math.min(2, columns.length) : columns.length
  return (
    <div ref={ref} className="grid items-start gap-3.5" style={{ gridTemplateColumns: `repeat(${cols || 1}, minmax(0, 1fr))` }}>
      {columns.map((col) => (
        <section key={col.key} className="min-w-0 rounded-xl border border-line bg-surface p-3.5">
          <h3 className={`text-[13px] font-semibold ${TEXT[col.tint]}`}>{col.name}</h3>
          <div className="mb-3 text-[11px] text-faint">{col.note}</div>
          {col.groups.every((g) => !g.cards.length) ? (
            <p className="text-[12px] text-faint">{col.empty ?? 'None in this scope.'}</p>
          ) : (
            col.groups
              .filter((g) => g.cards.length)
              .map((g, i) => (
                <div key={g.label ?? i} className={i ? 'mt-4' : ''}>
                  {g.label && (
                    <div className="mb-1.5 flex items-center gap-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
                          g.tone === 'learned' ? 'bg-warn/15 text-warn' : CHIP[col.tint]
                        }`}
                      >
                        {g.label}
                      </span>
                      <span className="text-[10px] text-faint">{g.cards.length}</span>
                    </div>
                  )}
                  <div className="space-y-1.5">
                    {g.cards.map((c) => (
                      <Card key={c.key} card={c} icon={col.icon} />
                    ))}
                  </div>
                </div>
              ))
          )}
        </section>
      ))}
    </div>
  )
}

function Card({ card, icon: Icon }: { card: ScopeCard; icon: LucideIcon }) {
  return (
    <div
      onClick={card.onClick}
      role={card.onClick ? 'button' : undefined}
      tabIndex={card.onClick ? 0 : undefined}
      onKeyDown={card.onClick ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); card.onClick?.() } } : undefined}
      className={`flex items-center gap-2 rounded-lg border border-line bg-sunken px-3 py-2 ${
        card.onClick ? 'cursor-pointer transition hover:border-faint' : ''
      }`}
    >
      <Icon size={13} className="shrink-0 text-faint" />
      <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-fg">{card.name}</span>
      {card.badges}
      {/* A control inside a clickable row must not also open it — flipping a toggle is not a request
          to read the file. */}
      {card.trailing && (
        <span className="shrink-0" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          {card.trailing}
        </span>
      )}
    </div>
  )
}
