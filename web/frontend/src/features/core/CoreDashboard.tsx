import { ArrowLeft, LayoutDashboard } from 'lucide-react'
import { colorFor } from '@/lib/palette'
import type { OrbitRepo } from '@/features/shell/useCommandStats'

// The Core dashboard — the owner's day-to-day view of one SuperMe. A placeholder for now, with the
// shell and entry wired.

export default function CoreDashboard({ repo, onExit }: { repo: OrbitRepo; onExit: () => void }) {
  const isHub = repo.id === 'global'
  const c = colorFor(repo.id)
  return (
    <div className="flex h-full flex-col overflow-hidden bg-app">
      <div className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-3">
        <button
          onClick={onExit}
          title="Back to Nexus"
          aria-label="Back to Nexus"
          className="rounded-md border border-line bg-surface p-1.5 text-muted hover:bg-hover hover:text-fg"
        >
          <ArrowLeft size={16} />
        </button>
        <span
          className="h-6 w-6 shrink-0 rounded-[5px]"
          style={isHub ? { backgroundImage: 'var(--grad-iris)' } : { backgroundColor: c }}
        />
        <div className="min-w-0">
          <div className="truncate text-[15px] font-semibold text-fg">{isHub ? 'SuperMe Hub' : repo.label}</div>
          <div className="text-[12px] text-faint">core dashboard</div>
        </div>
      </div>

      <div className="grid flex-1 place-items-center p-6">
        <div className="max-w-sm text-center">
          <span className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl border border-line bg-surface">
            <LayoutDashboard size={20} style={{ color: isHub ? 'rgb(var(--c-universal))' : c }} />
          </span>
          <h2 className="text-[15px] font-semibold text-fg">Core dashboard</h2>
          <p className="mt-2 text-[13px] leading-relaxed text-muted">
            This repo's day-to-day core surface — its knowledge, artifacts, and core-mode work — lands
            here. The functional tier is shell-only this renovation; the Dev workspace holds the live
            surfaces for now.
          </p>
        </div>
      </div>
    </div>
  )
}
