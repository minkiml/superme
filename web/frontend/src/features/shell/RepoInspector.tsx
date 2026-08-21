import { useState } from 'react'
import { X, ArrowRight, Pencil, Unplug, Loader2 } from 'lucide-react'
import { disconnectRepo } from '@/lib/api'
import { fmtTokens } from '@/lib/format'
import { operationRows } from '@/lib/tokens'
import { RepoIcon } from '@/lib/repoIcons'
import Modal from '@/ui/Modal'
import TagEditor from './TagEditor'
import type { OrbitRepo } from './useCommandStats'

// A per-repo summary and launchpad: token usage, the by-operation breakdown, and the two navigators
// into the repo's surfaces.

function ScopeCol({ name, color, s }: { name: string; color: string; s?: { sessions?: number; agents?: number; running?: number } }) {
  const rows: [string, number][] = [
    // Conversations, not sessions: the count excludes headless agent threads, matching the picker
    ['conversations', s?.sessions ?? 0],
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
  onDisconnected,
}: {
  repo: OrbitRepo | null
  onClose: () => void
  onOpenDev?: (repo: OrbitRepo) => void
  onOpenCore?: (repo: OrbitRepo) => void
  onTagSaved?: (repoId: string, patch: { color: string; icon: string | null }) => void
  onDisconnected?: (repoId: string) => void
}) {
  const [editingTag, setEditingTag] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  if (!repo) return null
  const isHub = repo.id === 'global'
  const ops = operationRows(repo.byCategory)
  const opMax = ops.length ? ops[0].value : 1
  const splitTotal = repo.dev + repo.core || 1

  return (
    <>
    <Modal onClose={onClose} maxW="max-w-md">
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
                  {ops.map((o) => (
                    <div key={o.key} title={o.title} className="flex items-center gap-2.5 text-[14px]">
                      <span className="h-2 w-2 shrink-0 rounded-[3px]" style={{ backgroundColor: o.color }} />
                      <span className="w-16 shrink-0 truncate text-muted">{o.label}</span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-hover">
                        <div className="h-full rounded-full" style={{ width: `${(o.value / opMax) * 100}%`, backgroundColor: o.color }} />
                      </div>
                      <span className="w-12 shrink-0 text-right font-mono text-muted">{fmtTokens(o.value)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>

          {/* per-scope live stats */}
          <section className="flex gap-3">
            <ScopeCol name="dev" color="rgb(var(--c-dev))" s={repo.scopes.dev} />
            <ScopeCol name="core" color="rgb(var(--c-core))" s={repo.scopes.core} />
          </section>

          {/* navigators — dev (blue) first, then core (mint) */}
          <section className="flex gap-3">
            <button
              onClick={() => onOpenDev?.(repo)}
              style={{ borderColor: 'rgb(var(--c-dev))' }}
              className="flex flex-1 items-center justify-between rounded-lg border bg-surface px-3.5 py-2.5 text-[14px] font-medium text-fg transition hover:bg-hover"
            >
              Open dev workspace <ArrowRight size={15} className="text-dev" />
            </button>
            <button
              onClick={() => onOpenCore?.(repo)}
              style={{ borderColor: 'rgb(var(--c-core))' }}
              className="flex flex-1 items-center justify-between rounded-lg border bg-surface px-3.5 py-2.5 text-[14px] font-medium text-fg transition hover:bg-hover"
            >
              Open core dashboard <ArrowRight size={15} className="text-core" />
            </button>
          </section>

          {/* danger zone — projects only (the hub is not disconnectable) */}
          {!isHub && (
            <button
              onClick={() => setDisconnecting(true)}
              className="flex w-full items-center gap-2 rounded-lg border border-line bg-surface px-3.5 py-2 text-[13px] text-muted transition hover:border-warn hover:text-warn"
            >
              <Unplug size={14} /> Disconnect project…
            </button>
          )}
        </div>
    </Modal>
    {editingTag && (
      <TagEditor repo={repo} onClose={() => setEditingTag(false)} onSaved={(patch) => onTagSaved?.(repo.id, patch)} />
    )}
    {disconnecting && (
      <DisconnectConfirm
        repo={repo}
        onClose={() => setDisconnecting(false)}
        onDisconnected={(id) => {
          setDisconnecting(false)
          onDisconnected?.(id)
        }}
      />
    )}
    </>
  )
}

// Irreversible by design, so the owner types the repo's label; the API demands the same
// confirmation.
function DisconnectConfirm({
  repo,
  onClose,
  onDisconnected,
}: {
  repo: OrbitRepo
  onClose: () => void
  onDisconnected: (repoId: string) => void
}) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const armed = typed.trim() === repo.label && !busy

  async function run() {
    if (!armed) return
    setBusy(true)
    setError(null)
    try {
      await disconnectRepo(repo.id)
      onDisconnected(repo.id)
    } catch (e) {
      // Either way nothing was removed past the failure point, so the repo stays connected.
      setBusy(false)
      setError(e instanceof Error && e.message.includes('409')
        ? 'Work is still running in this project — stop or finish it first.'
        : 'Disconnect failed — nothing was removed. Check the daemon and retry.')
    }
  }

  return (
    <Modal onClose={busy ? () => {} : onClose} maxW="max-w-md">
      <div className="flex items-center gap-3 border-b border-line px-5 py-4">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-warn/15 text-warn">
          <Unplug size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[15px] font-semibold text-fg">Disconnect “{repo.label}”?</div>
          <div className="text-[13px] text-muted">This cannot be undone</div>
        </div>
        {!busy && (
          <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-hover hover:text-fg">
            <X size={18} />
          </button>
        )}
      </div>

      <div className="space-y-4 p-5">
        <div className="space-y-1.5 text-[13.5px] leading-relaxed text-muted">
          <p>
            SuperMe forgets this project entirely: its <span className="text-fg">knowledge, work items,
            inbox, learned artifacts and chat sessions</span> are permanently deleted. There is no rollback.
          </p>
          <p>
            The project folder on disk is <span className="text-fg">not touched</span>, and past activity
            logs are kept. Connecting it again later starts over as a fresh project (onboarding included).
          </p>
        </div>

        <div>
          <label className="mb-1.5 block text-[12px] font-medium uppercase tracking-wider text-faint">
            Type <span className="normal-case text-fg">{repo.label}</span> to confirm
          </label>
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            disabled={busy}
            autoFocus
            placeholder={repo.label}
            className="w-full rounded-lg border border-line bg-app px-3 py-2 text-[14px] text-fg outline-none placeholder:text-faint focus:border-warn"
          />
        </div>

        {error && <div className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[13px] text-warn">{error}</div>}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={busy}
            className="rounded-lg border border-line bg-surface px-3.5 py-2 text-[13px] text-fg hover:bg-hover disabled:opacity-50"
          >
            Keep connected
          </button>
          <button
            onClick={run}
            disabled={!armed}
            className="flex items-center gap-2 rounded-lg bg-warn px-3.5 py-2 text-[13px] font-medium text-on-accent transition disabled:opacity-40"
          >
            {busy && <Loader2 size={14} className="animate-spin" />}
            {busy ? 'Disconnecting…' : 'Disconnect permanently'}
          </button>
        </div>
      </div>
    </Modal>
  )
}
