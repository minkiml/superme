import { useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { type MemoryProposal } from '@/lib/api'
import { ProposalModal } from './ProposalModal'
import { AgentWorking, FORM_TINT, StageBadge, forgePhaseShort } from './bits'

// Proposals awaiting a verdict, one card each.

export function ReviewQueue({
  proposals,
  contextId,
  onChange,
}: {
  proposals: MemoryProposal[]
  contextId: string
  onChange: () => void
}) {
  const [open, setOpen] = useState<MemoryProposal | null>(null)
  // The tile counts every OPEN proposal, so this queue breaks that same set down by badge state.
  const proposed = proposals.filter((p) => p.status === 'proposed').length
  const drafted = proposals.filter((p) => p.status === 'drafted').length
  const writing = proposals.filter((p) => p.status === 'writing').length
  return (
    <section className="mb-6 rounded-xl border border-line p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-accent-text">
        <Sparkles size={12} /> Review queue <span className="text-faint">
          · {proposed} proposed{writing ? ` · ${writing} writing` : ''}{drafted ? ` · ${drafted} drafted` : ''}
        </span>
      </div>
      <p className="mb-3 text-[11px] text-faint">
        The <code className="text-muted">distill</code> agent proposed these from captured candidates.
        Click one to review its details and the candidates behind it, then accept, reject, or drop.
      </p>
      <div className="grid cols-mid gap-2">
        {proposals.map((p) => (
          <ProposalCard key={p.id} p={p} onOpen={() => setOpen(p)} />
        ))}
      </div>
      {open && (
        <ProposalModal
          p={open}
          contextId={contextId}
          onClose={() => setOpen(null)}
          onDone={() => {
            setOpen(null)
            onChange()
          }}
        />
      )}
    </section>
  )
}

// One proposal as a card: badge, title, then the footer. Detail lives in the popup.
function ProposalCard({ p, onOpen }: { p: MemoryProposal; onOpen: () => void }) {
  const writing = p.status === 'writing'
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!writing) {
      setElapsed(0)
      return
    }
    const t = setInterval(() => setElapsed((e) => e + 1), 1000)
    return () => clearInterval(t)
  }, [writing])
  return (
    <button
      onClick={onOpen}
      className={`flex h-full flex-col gap-2 rounded-xl border bg-surface px-3 py-2.5 text-left shadow-sm transition hover:bg-hover ${
        writing ? 'border-accent' : 'border-line hover:border-accent'
      }`}
    >
      <div className="flex items-center gap-1.5">
        <span className={`font-mono text-[10px] uppercase ${FORM_TINT[p.output_form] ?? 'text-muted'}`}>
          {p.output_form}
        </span>
        <span className="text-[10px] text-faint">· {p.target_scope}</span>
        {writing ? (
          <AgentWorking size={12} className="ml-auto text-[10px] font-medium">{forgePhaseShort(elapsed)}</AgentWorking>
        ) : (
          <StageBadge status={p.status} className="ml-auto" />
        )}
      </div>
      <span className="line-clamp-2 text-sm font-medium leading-snug text-fg">{p.title}</span>
      <div className="mt-auto flex items-center gap-1.5 text-[10px] text-faint">
        {p.confidence && <span>{p.confidence} confidence</span>}
        {p.cluster && (
          <span className="ml-auto rounded bg-hover px-1.5 py-0.5 font-mono text-muted">{p.cluster}</span>
        )}
      </div>
    </button>
  )
}
