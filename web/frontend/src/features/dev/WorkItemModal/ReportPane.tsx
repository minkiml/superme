import { ExternalLink } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import { getWorkItemReport } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { fmtLocal } from '@/lib/format'
import { Empty, Loading } from './bits'
import { FromYouSlots } from './ownerInput'

// The Report tab: the item's own account of itself, rendered for a person.

// One phase's report, rendered 1:1, with a link to the full contract behind it.
//
// The leading title heading is dropped: the header and sub-tab already say it.
const LEAD_H1 = /^\s*#\s+.*\n+/

// Rendered by the editor below, not twice: the markdown and the textarea sat an inch apart.
const FROM_YOU_SECTION = /\n##\s+From you\s*\n[\s\S]*?(?=\n##\s|$)/

export function ReportPane({ itemId, contextId, phase, itemPhase }: {
  itemId: string; contextId: string; phase: string; itemPhase: string
}) {
  const q = useLive(K.itemReport(contextId, itemId, phase),
                    () => getWorkItemReport(itemId, phase, contextId), 15000)
  if (!q.data) return q.error ? <Empty>Couldn’t load report-{phase}.md — {String(q.error)}</Empty> : <Loading />
  const r = q.data
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-[10px] text-faint">
        <span className="rounded bg-hover px-1.5 py-0.5 font-mono">reports/{r.name}.md</span>
        <span>updated {fmtLocal(new Date(r.mtime * 1000).toISOString())}</span>
        {/* The contract is a DOC, so it opens as one: its own browser tab, served verbatim */}
        {r.contract && (
          <a href={`/api/dev/work-items/${encodeURIComponent(itemId)}/doc.html`
                 + `?path=${encodeURIComponent(r.contract)}&context_id=${encodeURIComponent(contextId)}`}
             target="_blank" rel="noreferrer"
             title="Open the agent-facing contract in a new tab"
             className="ml-auto inline-flex items-center gap-1 text-faint transition hover:text-accent-text">
            full contract: <code>{r.contract}</code> <ExternalLink size={11} />
          </a>
        )}
      </div>
      <Markdown text={r.text.replace(LEAD_H1, '').replace(FROM_YOU_SECTION, '')}
                variant="report" tone="dev" />
      {phase === 'triage' && (
        <FromYouSlots itemId={itemId} contextId={contextId} editable={itemPhase === 'triage'} />
      )}
    </div>
  )
}
