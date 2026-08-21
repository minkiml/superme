import { History } from 'lucide-react'
import { type DevEvent } from '@/lib/api'
import { fmtLocal } from '@/lib/format'
import { Empty, Section } from './bits'
import { EVENT_CAP } from '.'

// The item's milestones in order — the short version of the trace.

// The uniform timeline strip: the item's dev-event trail as one glyph-coded feed. Density by
// omission — `.start` rows say nothing their `.end` twin doesn't, so they are dropped.
const MILESTONE_KINDS = new Set([
  'phase.advance', 'git.merge', 'git.pr', 'git.worktree', 'git.revert', 'item.complete', 'item.abandon',
  'close.proposed', 'review.route', 'inbox.push', 'item.await',
])

export function TimelinePane({ events }: { events: DevEvent[] }) {
  const rows = events.filter((e) => !e.kind.endsWith('.start'))
  if (!rows.length) {
    return <Empty>Nothing has happened to this item yet — phase moves, gate decisions and git acts land here.</Empty>
  }
  return (
    <Section icon={History} title={`Timeline · ${rows.length} event${rows.length === 1 ? '' : 's'}`}>
      {/* Never let a cap read as "that's all there was" — say it out loud if we ever hit it. */}
      {events.length >= EVENT_CAP && (
        <p className="mb-2 text-[11px] text-warn">
          Showing the newest {EVENT_CAP} events — this item has more history than the feed carries.
        </p>
      )}
      <ol className="space-y-1">
        {rows.map((e) => {
          const milestone = MILESTONE_KINDS.has(e.kind)
          const owner = e.actor === 'owner'
          return (
            <li key={e.id} className="flex items-baseline gap-2 text-[13px]">
              <span className={`w-2 shrink-0 text-center text-[10px] ${
                owner ? 'text-accent' : milestone ? 'text-success' : 'text-line'
              }`}>
                {owner ? '◆' : milestone ? '●' : '·'}
              </span>
              <span className="shrink-0 font-mono text-[10px] tabular-nums text-faint">{fmtLocal(e.created_at)}</span>
              <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] ${
                milestone ? 'bg-hover text-fg' : 'bg-sunken text-faint'
              }`}>{e.kind}</span>
              <span className={`min-w-0 flex-1 truncate ${milestone ? 'text-fg' : 'text-muted'}`}>{e.summary}</span>
            </li>
          )
        })}
      </ol>
    </Section>
  )
}
