import { Check, Loader2, Ban, ShieldCheck } from 'lucide-react'
import SectionHeader from '@/ui/SectionHeader'
import { type Drilldown } from '@/lib/api'
import { Empty, sentence } from './bits'

// Authorizations: what the item asked permission for, and my answer.

type AuthRow = Drilldown['authorizations'][number]

// A sub rather than a banner, so it costs nothing on the items with no request. Every row is a
// decision still owed.
export function AuthorizationsPane({ auths, busy, onDecide }: {
  auths: AuthRow[]
  busy: string | null
  onDecide: (id: string, decision: 'granted' | 'denied') => void
}) {
  if (!auths.length) {
    return <Empty>Nothing is waiting on your authorization — this item asked for no contract change.</Empty>
  }
  return (
    <div className="space-y-2">
      <section className="rounded-md border border-warn/40 bg-warn/5 px-3 py-2.5">
        <SectionHeader className="flex items-center gap-1.5 text-warn">
          <ShieldCheck size={13} /> Your call — {auths.length > 1 ? `${auths.length} requests` : '1 request'}
        </SectionHeader>
        <p className="mt-1 text-[13px] leading-snug text-muted">
          The build found that finishing this work would change what the project PROMISES, and
          deferred rather than deciding for you. Nothing is written either way until you approve the
          item: <span className="text-fg">grant</span> and close performs the change when it merges;{' '}
          <span className="text-fg">deny</span> and the code ships with the gap on record.
        </p>
      </section>
      {auths.map((a) => (
        <div key={a.id} className="rounded-md border border-line bg-sunken px-3 py-2.5">
          <p className="text-[13px] font-medium leading-snug text-fg">{sentence(a.what)}</p>
          <p className="mt-1 text-[13px] leading-snug text-muted">{sentence(a.why)}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-faint">
            {a.doc && <span>doc: <code className="text-muted">{a.doc}</code></span>}
            <span>scope: <code className="text-muted">{a.scope}</code></span>
            <span className={a.delegable ? 'text-muted' : 'font-medium text-warn'}>
              {a.delegable ? 'sync-to-reality' : 'owner-reserved — escalated to you'}
            </span>
          </div>
          <div className="mt-2.5 flex items-center gap-2">
            <button onClick={() => onDecide(a.id, 'granted')} disabled={!!busy}
                    className="inline-flex items-center gap-1.5 rounded-md bg-accent px-2.5 py-1 text-[13px] font-semibold text-on-accent transition hover:opacity-90 disabled:opacity-50">
              {busy === a.id ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Grant
            </button>
            <button onClick={() => onDecide(a.id, 'denied')} disabled={!!busy}
                    className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1 text-[13px] text-muted transition hover:text-danger disabled:opacity-50">
              <Ban size={13} /> Deny
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
