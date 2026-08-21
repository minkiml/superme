import { ScrollText } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import { TraceRows } from '../ExecutionTrace'
import { pairTrace } from '@/lib/trace'
import { getWorkItemDetail, getWorkItemArtifacts, type WorkItem, type WorkItemDetail, type DevEvent, type RunArtifact, type RunHeader } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { fmtLocal } from '@/lib/format'
import { TimelinePane } from './TimelinePane'
import { Empty } from './bits'

// The Trace tab: every run this item spent, and the artifacts they left.

export function TraceTab({ it, contextId, rate, events, pane }: {
  it: WorkItem; contextId: string; rate: number; events: DevEvent[]
  pane: 'runs' | 'timeline'
}) {
  // Lazy twice over: the call-trail is the heaviest feed and only one pane renders it.
  const artQ = useLive(pane === 'runs' ? K.itemArtifacts(contextId, it.id) : null,
                       () => getWorkItemArtifacts(it.id, contextId), rate)
  const detailQ = useLive<WorkItemDetail>(pane === 'runs' ? K.itemDetail(contextId, it.id) : null,
                                          () => getWorkItemDetail(it.id, contextId), 30000)
  if (pane === 'timeline') return <TimelinePane events={events} />
  return <TracePane artifacts={artQ.data?.artifacts ?? []} runs={artQ.data?.runs ?? []}
                    execution={detailQ.data?.execution ?? null} />
}

// ── panes carried over ──────────────────────────────────────────────────────────────────────────

// What each run WAS. `build` appears twice on purpose: cycle 1 invokes the skill, later cycles
// resume that thread.
const RUN_KIND: Record<string, string> = {
  chat: 'chat',
  resolve: 'conflict resolver',
  deputy: 'deputy judgment',
  compact: 'compaction',
  triage: 'triage', plan: 'plan', build: 'build cycle', vet: 'vet', review: 'review', close: 'close',
  investigate: 'investigate',
}

// The raw call-trail, grouped by run; a completed item falls back to the execution snapshot
// clearance wrote.
//
// A cleared item's live rows are released, but its history must still be readable.
function TracePane({ artifacts, runs, execution }: {
  artifacts: RunArtifact[]; runs: RunHeader[]; execution: string | null
}) {
  const byId = new Map(runs.map((r) => [r.id, r]))
  if (artifacts.length === 0) {
    if (execution) {
      return (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
            <ScrollText size={12} /> Execution snapshot
            <span className="font-mono text-[10px] normal-case text-faint">artifacts/execution.md</span>
          </div>
          <Markdown text={execution} variant="doc" tone="dev" />
        </div>
      )
    }
    return <Empty>No calls recorded yet — they’re captured while an agent works this item.</Empty>
  }
  const groups: { run: number | null; items: RunArtifact[] }[] = []
  for (const a of artifacts) {
    const g = groups[groups.length - 1]
    if (g && g.run === (a.run_id ?? null)) g.items.push(a)
    else groups.push({ run: a.run_id ?? null, items: [a] })
  }
  return (
    <div className="space-y-4">
      {groups.map((g, gi) => {
        const calls = pairTrace(g.items)
        const meta = g.run != null ? byId.get(g.run) : undefined
        // A chat turn is named for the lane it interrupted, so it can be placed against the runs
        // around it.
        const kind = meta?.feature ? RUN_KIND[meta.feature] ?? meta.feature : null
        const what = kind && meta?.feature === 'chat' && meta.phase ? `${meta.phase}:${kind}` : kind
        return (
          <div key={gi}>
            <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-faint">
              {g.run != null ? `Run #${g.run}` : 'Unattached'}
              {what && <span className="text-muted"> · {what}</span>}
              {' · '}{calls.length} call{calls.length === 1 ? '' : 's'}
            </div>
            {calls.length === 0
              ? <div className="pl-1 text-[11px] text-faint">No tool calls — this run only exchanged text.</div>
              : <TraceRows rows={calls} time={(a) => fmtLocal(a.created_at)} />}
          </div>
        )
      })}
    </div>
  )
}
