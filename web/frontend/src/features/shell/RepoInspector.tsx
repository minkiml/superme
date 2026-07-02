import { useState } from 'react'
import { X, ArrowRight, Pencil } from 'lucide-react'
import { fmtTokens } from '@/lib/format'
import { featureColor } from '@/lib/palette'
import { RepoIcon } from '@/lib/repoIcons'
import TagEditor from './TagEditor'
import type { OrbitRepo } from './useCommandStats'

// Per-repo drill-in popup: a summary + launchpad. Token usage (card) + by-operation breakdown
// (scrollable) + per-scope live stats, then the two navigators into the Core dashboard / Dev
// workspace. Per-repo config lives in the Quick config dashboard now, not here. Opens over a scrim.

function ScopeCol({ name, color, s }: { name: string; color: string; s?: { sessions?: number; agents?: number; running?: number } }) {
  const rows: [string, number][] = [
    ['sessions', s?.sessions ?? 0],
    ['agents', s?.agents ?? 0],
    ['running', s?.running ?? 0],
  ]
  return (
    <div className="flex-1 rounded-lg border border-line bg-surface p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wider" style={{ color }}>
        <span className="h-2 w-2 rounded-[3px]" style={{ backgroundColor: color }} />
        {name}
      </div>
      <div className="space-y-1.5">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between text-[14px]">
            <span className="text-muted">{k}</span>
            <span className="font-mono text-fg">{v}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function RepoInspector({
  repo,
  onClose,
  onOpenDev,
  onOpenCore,
  onTagSaved,
}: {
  repo: OrbitRepo | null
  onClose: () => void
  onOpenDev?: (repo: OrbitRepo) => void
  onOpenCore?: (repo: OrbitRepo) => void
  onTagSaved?: (repoId: string, patch: { color: string; icon: string | null }) => void
}) {
  const [editingTag, setEditingTag] = useState(false)
  if (!repo) return null
  const isHub = repo.id === 'global'
  const ops = Object.entries(repo.byFeature).sort((a, b) => b[1] - a[1])
  const opMax = ops.length ? ops[0][1] : 1
  const splitTotal = repo.dev + repo.core || 1

  return (
    <>
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-6 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-md overflow-hidden rounded-2xl border border-line bg-app shadow-2xl" onClick={(e) => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-center gap-3 border-b border-line px-5 py-4">
          {/* the visual tag — click to edit its color / icon */}
          <button
            onClick={() => setEditingTag(true)}
            title="Edit tag color & icon"
            className="group relative grid h-9 w-9 shrink-0 place-items-center rounded-lg text-[13px] font-semibold text-app"
            style={isHub ? { backgroundImage: 'var(--grad-iris)' } : { backgroundColor: repo.color }}
          >
            {repo.icon ? <RepoIcon name={repo.icon} size={18} className="text-app" /> : isHub ? '' : repo.label.slice(0, 2).toUpperCase()}
            <span className="absolute -bottom-1 -right-1 grid h-4 w-4 place-items-center rounded-full border border-line bg-surface opacity-0 transition group-hover:opacity-100">
              <Pencil size={9} className="text-muted" />
            </span>
          </button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-semibold text-fg">{isHub ? 'SuperMe Hub' : repo.label}</div>
            <div className="text-[13px] text-muted">{isHub ? 'global' : repo.layer}</div>
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-hover hover:text-fg">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-4 p-5">
          {/* token usage + by-operation — one card; the operation list scrolls (4 rows in view) */}
          <section className="space-y-3 rounded-xl border border-line bg-surface p-3.5">
            <div>
              <div className="mb-2 flex items-baseline justify-between">
                <span className="text-[12px] font-semibold uppercase tracking-wider text-muted">Token usage</span>
                <span className="font-mono text-[20px] font-medium text-fg">{fmtTokens(repo.tokens)}</span>
              </div>
              <div className="flex h-2 w-full overflow-hidden rounded-full bg-hover">
                <div className="h-full bg-dev" style={{ width: `${(repo.dev / splitTotal) * 100}%` }} />
                <div className="h-full bg-core" style={{ width: `${(repo.core / splitTotal) * 100}%` }} />
              </div>
              <div className="mt-1.5 flex justify-between text-[13px] text-faint">
                <span><span className="text-dev">dev</span> {fmtTokens(repo.dev)}</span>
                <span><span className="text-core">core</span> {fmtTokens(repo.core)}</span>
              </div>
            </div>

            {ops.length > 0 && (
              <div className="border-t border-line pt-3">
                <div className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-muted">By operation</div>
                <div className="max-h-[7.5rem] space-y-1.5 overflow-y-auto pr-1">
                  {ops.map(([feat, val]) => (
                    <div key={feat} className="flex items-center gap-2.5 text-[14px]">
                      <span className="h-2 w-2 shrink-0 rounded-[3px]" style={{ backgroundColor: featureColor(feat) }} />
                      <span className="w-16 shrink-0 truncate text-muted">{feat}</span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-hover">
                        <div className="h-full rounded-full" style={{ width: `${(val / opMax) * 100}%`, backgroundColor: featureColor(feat) }} />
                      </div>
                      <span className="w-12 shrink-0 text-right font-mono text-muted">{fmtTokens(val)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>

          {/* per-scope live stats */}
          <section className="flex gap-3">
            <ScopeCol name="dev" color="var(--c-dev)" s={repo.scopes.dev} />
            <ScopeCol name="core" color="var(--c-core)" s={repo.scopes.core} />
          </section>

          {/* navigators — dev (blue) first, then core (mint) */}
          <section className="flex gap-3">
            <button
              onClick={() => onOpenDev?.(repo)}
              style={{ borderColor: 'var(--c-dev)' }}
              className="flex flex-1 items-center justify-between rounded-lg border bg-surface px-3.5 py-2.5 text-[14px] font-medium text-fg transition hover:bg-hover"
            >
              Open dev workspace <ArrowRight size={15} className="text-dev" />
            </button>
            <button
              onClick={() => onOpenCore?.(repo)}
              style={{ borderColor: 'var(--c-core)' }}
              className="flex flex-1 items-center justify-between rounded-lg border bg-surface px-3.5 py-2.5 text-[14px] font-medium text-fg transition hover:bg-hover"
            >
              Open core dashboard <ArrowRight size={15} className="text-core" />
            </button>
          </section>
        </div>
      </div>
    </div>
    {editingTag && (
      <TagEditor repo={repo} onClose={() => setEditingTag(false)} onSaved={(patch) => onTagSaved?.(repo.id, patch)} />
    )}
    </>
  )
}
