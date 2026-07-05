import { useState } from 'react'
import { Database, PanelLeftOpen } from 'lucide-react'
import KnowledgeTree from './KnowledgeTree'
import FileEditor from './FileEditor'
import InjectForm from './InjectForm'
import PageHeader from '@/ui/PageHeader'
import Dropdown from '@/ui/Dropdown'
import { GLOBAL, type ContextRef } from '@/lib/contexts'

// System space: browse / edit / inject the RAW knowledge files of a context — the native
// file view that used to be the whole "Me" tab. The context (SuperMe Hub + each connected
// domain) is picked from the dropdown.
export default function ManageKnowledge({
  domains = [],
  initialContext = GLOBAL.id,
}: {
  domains?: ContextRef[]
  initialContext?: string
}) {
  const subs = [{ id: GLOBAL.id, label: 'SuperMe Hub' }, ...domains.map((d) => ({ id: d.id, label: d.label }))]
  const [ctx, setCtx] = useState(initialContext)
  const [selected, setSelected] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [filesOpen, setFilesOpen] = useState(true)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        icon={Database}
        title="Manage Knowledge"
        subtitle="Raw knowledge files — browse, edit, and inject"
        right={
          <Dropdown
            value={ctx}
            align="right"
            options={subs.map((s) => ({ value: s.id, label: s.label }))}
            onChange={(v) => {
              setCtx(v)
              setSelected(null)
            }}
          />
        }
      />
      <div className="flex min-h-0 flex-1">
        {filesOpen ? (
          <div className="flex w-[264px] shrink-0 flex-col overflow-hidden border-r border-line bg-surface">
            <KnowledgeTree
              key={ctx}
              contextId={ctx}
              selected={selected}
              onSelect={setSelected}
              refreshKey={refreshKey}
              onCollapse={() => setFilesOpen(false)}
            />
            <InjectForm
              contextId={ctx}
              onInjected={(p) => {
                setRefreshKey((k) => k + 1)
                setSelected(p)
              }}
            />
          </div>
        ) : (
          <div className="flex w-10 shrink-0 flex-col items-center border-r border-line bg-surface py-3">
            <button
              onClick={() => setFilesOpen(true)}
              title="Show files panel"
              aria-label="Show files panel"
              className="rounded-md p-1.5 text-muted hover:bg-hover hover:text-fg"
            >
              <PanelLeftOpen size={18} />
            </button>
          </div>
        )}
        <div className="min-w-0 flex-1">
          <FileEditor key={ctx} contextId={ctx} path={selected} />
        </div>
      </div>
    </div>
  )
}
