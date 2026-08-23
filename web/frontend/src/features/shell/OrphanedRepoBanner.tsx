import { FolderSearch } from 'lucide-react'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { getSystem } from '@/lib/api'

// A repo whose registry entry is gone still has its knowledge home and its worktrees, so its
// items exist and nothing can reach them. The dashboard cannot tell that from a repo nobody
// connected, which is why it says so here.
export default function OrphanedRepoBanner() {
  const { data } = useLive(K.systemOverview, getSystem, 0)
  const orphans = data?.orphaned_repos ?? []
  if (!orphans.length) return null

  const names = orphans.map((o) => o.repo_id).join(', ')
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-warn/40 bg-warn/10 px-3 py-1.5 text-[11px] text-fg">
      <FolderSearch size={13} className="shrink-0 text-warn" />
      <span className="min-w-0 flex-1 leading-relaxed">
        <span className="font-medium">
          {orphans.length === 1 ? `${names} has work on disk but no registry entry.` : null}
          {orphans.length > 1 ? `${orphans.length} repos have work on disk but no registry entry.` : null}
        </span>{' '}
        {orphans.length > 1 ? `${names}. ` : null}
        Connect it again to reach its items, or restore
        <code className="font-mono text-[10.5px] text-muted"> config/repos-backups/</code>.
      </span>
    </div>
  )
}
