import { Check, ShieldCheck, AlertTriangle, MessageSquare, CornerUpLeft } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import { type DevEvent } from '@/lib/api'
import { PHASES, type Phase } from '@/lib/router'
import { fmtLocal } from '@/lib/format'
import { Empty, Section } from './bits'

// What the deputy decided, and whether the phase has settled since.

// The deputy's governance trail, in one place and OUT of the chat: the conversation lives there,
// this is the record of why.
const DEPUTY_ROW: Record<string, { icon: typeof ShieldCheck; label: string; tint: string }> = {
  'deputy.approve': { icon: Check, label: 'Approved', tint: 'text-success' },
  'deputy.escalate': { icon: AlertTriangle, label: 'Escalated to you', tint: 'text-danger' },
  'deputy.query': { icon: MessageSquare, label: 'Sent feedback to the agent', tint: 'text-accent-text' },
  'deputy.send_back': { icon: CornerUpLeft, label: 'Sent back', tint: 'text-warn' },
}

// An escalation is resolved once the item leaves the gate it was raised at, and nothing records
// that on the event.
//
// Unmarked, a log of past pages reads as a queue of things still owed.
function isSettled(gate: string | undefined, phase: string): boolean {
  const at = PHASES.indexOf(gate as Phase)
  const now = PHASES.indexOf(phase as Phase)
  return at >= 0 && now >= 0 && now > at
}

export function DeputyLogPane({ events, phase }: { events: DevEvent[]; phase: string }) {
  const rows = events
    .filter((e) => String(e.kind).startsWith('deputy') && !e.kind.endsWith('.start') && !e.kind.endsWith('.end'))
    // Newest first, like every log surface here: the row the owner needs is the one just written.
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
  if (!rows.length) return <Empty>The deputy hasn’t acted on this item yet.</Empty>
  return (
    <Section icon={ShieldCheck} title="Deputy log">
      <ol className="space-y-2">
        {rows.map((e) => {
          const m = (e.meta ?? {}) as Record<string, unknown>
          let row = DEPUTY_ROW[e.kind] ?? { icon: ShieldCheck, label: e.kind, tint: 'text-muted' }
          const gate = (m.gate ?? m.phase ?? m.origin_gate) as string | undefined
          // An answered page goes quiet and says so: it is real history, but it stops competing.
          const settled = e.kind === 'deputy.escalate' && isSettled(gate, phase)
          if (settled) row = { ...row, icon: Check, label: 'Escalated to you · resolved',
                               tint: 'text-muted' }
          const Icon = row.icon
          const str = (k: string) => (typeof m[k] === 'string' ? String(m[k]).trim() : '')
          // `because` is the headline the owner read; the detail lives in `checked`.
          const because = str('because') || str('escalation') || str('speech') || e.summary
          const more = [['checked', str('checked')], ['asked for', str('change')],
                        ['escalation', str('escalation')]].filter(([, v]) => v && v !== because)
          return (
            <li key={e.id} className="rounded-md border border-line bg-sunken px-3 py-2">
              <div className="flex items-center gap-2">
                <Icon size={13} className={`shrink-0 ${row.tint}`} />
                <span className={`text-[13px] font-semibold ${row.tint}`}>{row.label}</span>
                {gate && <span className="rounded bg-hover px-1.5 py-px text-[10px] font-medium text-muted">{gate}</span>}
                <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-faint">{fmtLocal(e.created_at)}</span>
              </div>
              {/* Body copy reads at full contrast: the dim tier is for chrome, and this is what
                  you came to read. */}
              {because && <div className="mt-1 text-[13px] leading-snug text-fg"><Markdown text={because} tone="dev" /></div>}
              {more.length > 0 && (
                <details className="mt-1">
                  <summary className="cursor-pointer select-none text-[10px] font-medium tracking-wide text-faint hover:text-fg">
                    Detail
                  </summary>
                  <dl className="mt-1 space-y-1 text-[11px]">
                    {more.map(([k, v]) => (
                      <div key={k} className="flex gap-2">
                        <dt className="w-20 shrink-0 text-faint">{k}</dt>
                        <dd className="min-w-0 flex-1 text-fg">{v}</dd>
                      </div>
                    ))}
                  </dl>
                </details>
              )}
            </li>
          )
        })}
      </ol>
    </Section>
  )
}
