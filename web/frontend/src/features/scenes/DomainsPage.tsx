import { FolderTree } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'
import type { ContextRef } from '@/lib/contexts'

// "Domains" — the overview scene for connected local projects. Individual domains are
// navigated from the sidebar; each domain (not this overview) carries its own knowledge
// shortcut.
export default function DomainsPage({ domains }: { domains: ContextRef[] }) {
  return (
    <div className="flex h-full flex-col">
      <PageHeader icon={FolderTree} title="Domains" subtitle="Connected local projects · project-level knowledge" />
      <div className="flex-1 overflow-auto p-6">
        {domains.length === 0 ? (
          <div className="max-w-xl rounded-xl border border-dashed border-line bg-surface p-6 text-center">
            <div className="text-sm text-fg">No domains connected yet</div>
            <div className="mt-1 text-xs text-muted">
              Connect a local project in the registry and it’ll appear in the sidebar.
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted">
            {domains.length} connected — open one from the sidebar to see its overview.
          </p>
        )}
      </div>
    </div>
  )
}
