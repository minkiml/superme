import { useState } from 'react'
import { Loader2, GitMerge, Undo2, ExternalLink } from 'lucide-react'
import { getWorkItemGit, resolveWorkItemGit, revertWorkItemGit, type WorkItem, type GitHealth, type DrilldownAction } from '@/lib/api'
import { useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { build } from '@/lib/router'
import { Empty, Loading } from './bits'

// The Git tab: branch health and the actions that resolve it.

// What a git action DID, in a sentence: the raw response is a debugging artifact, not a result.
//
// An unrecognised shape still shows its JSON — inventing a success sentence is how a silent failure
// gets reported as one.
function describeGit(action: string, r: unknown, trunk: string): string {
  const d = (r ?? {}) as Record<string, unknown>
  const conflicts = Array.isArray(d.conflicts) ? (d.conflicts as string[]) : []
  if (action === 'revert') {
    if (d.reverted) return `Reverted. ${trunk} is back at ${String(d.head ?? d.target ?? 'its pre-merge state')}.`
    return 'Nothing to revert — no recorded backup point for this item.'
  }
  if (action === 'resolve') {
    if (d.merged) return 'Conflicts resolved and the sync completed.'
    if (conflicts.length) return `Still conflicting in ${conflicts.join(', ')}.`
  }
  return JSON.stringify(r)
}

// Live git state + the owner's git actions. Activation comes from the drilldown payload, so the
// Merge button and the review gate's Approve can never disagree about the landing rule.
export function GitPane({ it, contextId, actions, busy, onAct, onChanged }: {
  it: WorkItem; contextId: string; actions: DrilldownAction[]
  busy: string | null; onAct: (id: string) => void; onChanged: () => void
}) {
  const [localBusy, setLocalBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  // Live, not fetch-once: ahead/behind and `dirty` move underneath an open tab whenever a cycle commits.
  const gitQ = useLive<GitHealth>(K.itemGit(contextId, it.id), () => getWorkItemGit(it.id, contextId), 10000)
  const health = gitQ.data ?? null
  async function local(name: string, fn: () => Promise<unknown>) {
    setLocalBusy(name)
    setMsg(null)
    try {
      const r = await fn()
      setMsg(describeGit(name, r, health?.trunk ?? 'the anchor'))
      onChanged()
      gitQ.refresh()
    } catch (e) {
      setMsg(String(e))
    } finally {
      setLocalBusy(null)
    }
  }
  if (!it.git_branch && !it.git_worktree) {
    return <Empty>No git record yet — the branch + worktree are created when build starts.</Empty>
  }
  // A worktree with no branch is a scratch checkout, so no landing row below applies.
  if (!it.git_branch) {
    return (
      <Empty>
        There is no branch and nothing to land; the tree is removed when the item closes.
        <div className="mt-2 font-mono text-[11px] text-faint">{it.git_worktree}</div>
      </Empty>
    )
  }
  if (!health) return <Loading />
  const gitActions = actions.filter((a) => a.home === 'git')
  const pr = gitActions.find((a) => a.id === 'pr')
  const rows: [string, React.ReactNode][] = [
    ['branch', <span className="font-mono">{health.branch ?? it.git_branch}</span>],
    ['worktree', health.dir_exists ? <span className="font-mono">{health.worktree}</span> : <span className="text-faint">removed (terminal)</span>],
    ['anchor', <span className="font-mono">{health.trunk ?? '—'}</span>],
    [`vs ${health.trunk ?? 'anchor'}`, `ahead ${health.ahead ?? 0} · behind ${health.behind ?? 0}${health.behind ? ' — sync first' : ''}`],
    ['merged', health.merged ? `yes${it.git_merge_commit ? ` (${String(it.git_merge_commit).slice(0, 10)})` : ''}` : 'not yet'],
    ['dirty', health.dirty?.length ? health.dirty.join(', ') : 'clean'],
    // NAME THE ACTOR: the owner's approve merges in BOTH modes, so a mode-only sentence contradicts
    // the gate button.
    ['landing', health.review_mode === 'strict'
      ? "strict — the deputy's approval only opens a PR; yours merges"
      : 'fast — either approval merges it'],
  ]
  return (
    <div className="space-y-3">
      <dl className="space-y-1 text-[13px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <dt className="w-20 shrink-0 text-faint">{k}</dt>
            <dd className="min-w-0 flex-1 text-fg">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap items-center gap-2">
        {/* No manual freshness sync: the build agent syncs itself, and the merge act only re-vets
            on overlap */}
        <GitBtn icon={GitMerge} label="Resolve with agent" busy={localBusy === 'resolve'}
                disabled={!!health.merged || !health.behind}
                onClick={() => local('resolve', () => resolveWorkItemGit(it.id, contextId))}
                title={health.merged ? 'Already merged — nothing to resolve'
                  : !health.behind ? 'Offered when the branch is behind, which is when a conflict is possible'
                  : 'Re-runs the sync leaving conflicts in the worktree, then an agent resolves them there. The daemon completes the merge and the item re-enters vet.'} />
        {/* Its own browser tab, because a diff wants the whole screen. A real path, so cmd-click
        {   works */}
        {pr && (
          <GitBtn icon={ExternalLink} label={pr.label} busy={false} disabled={!pr.active}
                  href={build({ name: 'pr', repoId: contextId, itemId: it.id })} title={pr.reason} />
        )}
        {/* No Merge button: this tab shows git state and repairs git problems; landing the work is
            the gate's act */}
        {it.git_backup_ref && (
          <GitBtn icon={Undo2} label="Revert merge" busy={localBusy === 'revert'}
                  onClick={() => local('revert', () => revertWorkItemGit(it.id, contextId))}
                  title="Restore the trunk to its pre-merge state via the recorded backup ref (safe-only)" />
        )}
      </div>
      {msg && <div className="rounded-md bg-sunken px-2.5 py-1.5 text-[13px] leading-snug text-fg">{msg}</div>}
    </div>
  )
}

function GitBtn({ icon: Icon, label, onClick, href, busy, title, accent, disabled }: {
  icon: typeof GitMerge; label: string; onClick?: () => void; busy?: boolean; title?: string
  // An anchor, not `window.open`: a scripted popup can be refused, and navigation comes with
  // cmd-click for free.
  href?: string
  accent?: boolean
  disabled?: boolean
}) {
  const cls = `inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] transition disabled:opacity-40 ${
    accent ? 'bg-accent font-medium text-on-accent hover:opacity-90'
           : 'border border-line bg-surface text-muted hover:bg-hover hover:text-fg'
  }`
  const inner = <>{busy ? <Loader2 size={13} className="animate-spin" /> : <Icon size={13} />} {label}</>
  if (href && !disabled && !busy) {
    return <a href={href} target="_blank" rel="noopener" title={title} className={cls}>{inner}</a>
  }
  return (
    <button onClick={onClick} disabled={!!busy || !!disabled} title={title} className={cls}>{inner}</button>
  )
}
