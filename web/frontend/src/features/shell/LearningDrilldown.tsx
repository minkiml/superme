import { useEffect, useState } from 'react'
import Modal from '@/ui/Modal'
import TabBar from '@/ui/TabBar'
import Empty from '@/ui/Empty'
import { getLearningRollup, type LearningRollup } from '@/lib/api'

// The Learning tile drill-in — the two pipeline pools as PER-REPO counts, each split dev/core:
// Candidates (captured, awaiting distill/ratify) and Learned (published artifacts). Counts only —
// the item-level detail lives in each repo's Dev workspace, not here. dev = repo_dev + universal_dev.

type ScopeCount = LearningRollup['candidates']
type Tab = 'candidates' | 'learned'

const label = (repoId: string, l: string) => (repoId === 'global' ? 'SuperMe Hub' : l)

// One repo's count as a dev/core split bar. Blue = dev, mint = core (matches the scope colors).
function RepoScopeRow({ name, c }: { name: string; c: ScopeCount }) {
  const total = c.total || 1
  return (
    <div className="flex items-center gap-3 text-[13px]">
      <span className="w-28 shrink-0 truncate text-fg">{name}</span>
      <div className="flex h-2 flex-1 overflow-hidden rounded-full bg-hover">
        <div className="h-full bg-dev" style={{ width: `${(c.dev / total) * 100}%` }} />
        <div className="h-full bg-core" style={{ width: `${(c.core / total) * 100}%` }} />
      </div>
      <span className="w-24 shrink-0 text-right font-mono text-faint">
        <span className="text-dev">{c.dev}</span> · <span className="text-core">{c.core}</span>
      </span>
      <span className="w-8 shrink-0 text-right font-mono text-muted">{c.total}</span>
    </div>
  )
}

function Pool({ rows, total, noun }: { rows: { repo_id: string; label: string; c: ScopeCount }[]; total: ScopeCount; noun: string }) {
  const active = rows.filter((r) => r.c.total > 0).sort((a, b) => b.c.total - a.c.total)
  if (!total.total) return <Empty>{`No ${noun} yet.`}</Empty>
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-[13px] text-muted">
        <span className="font-mono text-fg">{total.total}</span> {noun}
        <span className="text-faint">· <span className="text-dev">Dev {total.dev}</span> · <span className="text-core">Core {total.core}</span></span>
      </div>
      <div className="space-y-2.5 border-t border-line pt-3">
        {active.map((r) => (
          <RepoScopeRow key={r.repo_id} name={label(r.repo_id, r.label)} c={r.c} />
        ))}
      </div>
    </div>
  )
}

export default function LearningDrilldown({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('candidates')
  const [data, setData] = useState<LearningRollup | null>(null)

  useEffect(() => {
    getLearningRollup().then(setData).catch(() => {})
  }, [])

  return (
    <Modal onClose={onClose} title="Learning">
      <div className="px-5 pt-4">
        <TabBar tabs={[['candidates', 'Candidates'], ['learned', 'Learned']]} value={tab} onChange={setTab} />
      </div>
      <div className="max-h-[60vh] overflow-y-auto p-5">
        {!data ? (
          <Empty>Loading…</Empty>
        ) : tab === 'candidates' ? (
          <Pool rows={data.repos.map((r) => ({ repo_id: r.repo_id, label: r.label, c: r.candidates }))} total={data.candidates} noun="candidates" />
        ) : (
          <Pool rows={data.repos.map((r) => ({ repo_id: r.repo_id, label: r.label, c: r.learned }))} total={data.learned} noun="learned" />
        )}
      </div>
    </Modal>
  )
}
