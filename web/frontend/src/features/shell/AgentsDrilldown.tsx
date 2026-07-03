import Modal from '@/ui/Modal'
import Bars, { type BarRow } from '@/ui/Bars'
import Empty from '@/ui/Empty'
import type { CommandStats, OrbitRepo } from './useCommandStats'

// The Agents tile drill-in — where the live agents are. One bar per repo with live work (its count
// of running/live SuperMe agents). The run-by-run detail lives in the Activity log, not here.
export default function AgentsDrilldown({ stats, onClose }: { stats: CommandStats; onClose: () => void }) {
  const rows: BarRow[] = [stats.hub, ...stats.nodes]
    .filter((r): r is OrbitRepo => !!r && r.agents > 0)
    .map((r) => ({ key: r.id, label: r.id === 'global' ? 'SuperMe Hub' : r.label, value: r.agents, color: r.color }))
    .sort((a, b) => b.value - a.value)

  return (
    <Modal onClose={onClose} title="Agents">
      <div className="max-h-[60vh] overflow-y-auto p-5">
        {rows.length ? <Bars rows={rows} fmt={String} /> : <Empty>No live agents right now.</Empty>}
      </div>
    </Modal>
  )
}
