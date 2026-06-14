import { useState } from 'react'
import KnowledgeTree from './KnowledgeTree'
import FileEditor from './FileEditor'
import InjectForm from './InjectForm'
import ChatPanel from './ChatPanel'

// The "Me" (global) dashboard: browse/edit knowledge · inject · chat with global SuperMe.
export default function MeDashboard() {
  const [selected, setSelected] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  return (
    <div className="grid h-full min-h-0" style={{ gridTemplateColumns: '260px 1fr 380px' }}>
      <div className="flex min-h-0 flex-col overflow-hidden bg-slate-900">
        <KnowledgeTree selected={selected} onSelect={setSelected} refreshKey={refreshKey} />
        <InjectForm
          onInjected={(p) => {
            setRefreshKey((k) => k + 1)
            setSelected(p)
          }}
        />
      </div>
      <FileEditor path={selected} />
      <ChatPanel />
    </div>
  )
}
