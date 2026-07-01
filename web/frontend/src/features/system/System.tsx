import { useCallback, useEffect, useState } from 'react'
import { Gauge, RefreshCw, Boxes, Plus, Brain } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'
import Dropdown from '@/ui/Dropdown'
import {
  getSystem,
  getRepos,
  setRepoModel,
  setSystemLearning,
  type SystemOverview,
  type RepoOverview,
  type ScopeStatus,
  type Scope,
  type ModelAlias,
} from '@/lib/api'
import { fmtLocal, fmtModel } from '@/lib/format'

// The System cockpit (PRD §4.11.3) — one surface onto the System / Repo / Session / Run spine.
// A single live view: the automatic-learning switch on top, then the repos × scope lattice (status
// + counts), with each repo's default-model override edited inline on its card. SuperMe's own
// system default stays the host default and is not surfaced here.

const SCOPES: Scope[] = ['core', 'dev']
const POLL_MS = 4000 // keep the live counts fresh while the dashboard is open

export default function System() {
  const [system, setSystem] = useState<SystemOverview | null>(null)
  const [repos, setRepos] = useState<RepoOverview[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    setErr(null)
    try {
      const [s, r] = await Promise.all([getSystem(), getRepos()])
      setSystem(s)
      setRepos(r)
    } catch (e) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [load])

  const running = system?.running ?? 0

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        icon={Gauge}
        title="System"
        right={
          <div className="flex items-center gap-2">
            {running > 0 && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-accent-soft px-2.5 py-1 text-xs text-accent-text">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-text" />
                {running} live
              </span>
            )}
            <button
              onClick={load}
              disabled={loading}
              title="Refresh"
              aria-label="Refresh"
              className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-muted hover:bg-hover hover:text-fg disabled:opacity-50"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
            </button>
          </div>
        }
      />

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-5xl space-y-5 p-6">
          {loading && !system ? (
            <div className="text-sm text-muted">Loading…</div>
          ) : err ? (
            <Empty>Couldn’t reach the spine — {err}</Empty>
          ) : (
            system && (
              <>
                <LearningBar enabled={system.learning_enabled} onChanged={load} />

                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-faint">Projects</h3>
                  <ConnectButton />
                </div>

                {repos.length === 0 ? (
                  <Empty>No projects registered.</Empty>
                ) : (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    {repos.map((r) => (
                      <RepoCard key={r.id} repo={r} onChanged={load} />
                    ))}
                  </div>
                )}
              </>
            )
          )}
        </div>
      </div>
    </div>
  )
}

// ============================================================= automatic learning

function LearningBar({ enabled, onChanged }: { enabled: boolean; onChanged: () => void | Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [on, setOn] = useState(enabled)
  useEffect(() => setOn(enabled), [enabled])
  async function toggle() {
    setBusy(true)
    const next = !on
    setOn(next) // optimistic
    try {
      await setSystemLearning(next)
      await onChanged()
    } catch {
      setOn(!next) // revert on failure
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex items-center gap-3 rounded-lg border border-line bg-surface px-4 py-3">
      <Brain size={16} className={on ? 'text-accent-text' : 'text-muted'} />
      <div className="min-w-0">
        <div className="text-sm font-medium text-fg">Automatic learning</div>
        <div className="truncate text-[11px] text-faint">
          {on
            ? 'On — background sweeps capture learnings on idle / advance / completion'
            : 'Off — automatic capture is paused; nothing is learned'}
        </div>
      </div>
      <button
        onClick={toggle}
        disabled={busy}
        role="switch"
        aria-checked={on}
        className={`ml-auto relative h-5 w-9 shrink-0 rounded-full transition disabled:opacity-50 ${
          on ? 'bg-accent' : 'bg-hover'
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
            on ? 'left-[18px]' : 'left-0.5'
          }`}
        />
      </button>
    </div>
  )
}

function ConnectButton() {
  return (
    <button
      disabled
      title="Connecting a new project is planned"
      className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-line px-2.5 py-1.5 text-xs text-faint"
    >
      <Plus size={13} /> Connect a project
      <span className="rounded-full bg-hover px-1.5 py-0.5 text-[9px] uppercase tracking-wide">soon</span>
    </button>
  )
}

// =================================================================== project cards

function RepoCard({ repo, onChanged }: { repo: RepoOverview; onChanged: () => void | Promise<void> }) {
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <Boxes size={15} className="text-muted" />
        <span className="text-sm font-semibold text-fg">{repo.label}</span>
        <span className="rounded bg-hover px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-muted">{repo.layer}</span>
        <ModelSelect
          current={(repo.model_override as ModelAlias) ?? null}
          onSet={async (m) => {
            await setRepoModel(repo.id, m)
            await onChanged()
          }}
        />
      </div>
      <div className="divide-y divide-line">
        {SCOPES.map((scope) => (
          <ScopeCell key={scope} scope={scope} st={repo.scopes[scope]} />
        ))}
      </div>
    </div>
  )
}

const MODEL_OPTIONS: ModelAlias[] = ['haiku', 'sonnet', 'opus']

function ModelSelect({
  current,
  onSet,
}: {
  current: ModelAlias | null
  onSet: (m: ModelAlias | null) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  async function change(v: string) {
    setBusy(true)
    try {
      await onSet(v === '' ? null : (v as ModelAlias))
    } finally {
      setBusy(false)
    }
  }
  const options = [
    { value: '', label: 'model · inherit' },
    ...MODEL_OPTIONS.map((m) => ({ value: m, label: `model · ${fmtModel(m)}` })),
  ]
  return (
    <div className="ml-auto">
      <Dropdown
        value={current ?? ''}
        options={options}
        onChange={change}
        disabled={busy}
        align="right"
        title="Default model for this project"
      />
    </div>
  )
}

function ScopeCell({ scope, st }: { scope: Scope; st: ScopeStatus }) {
  if (!st) return <div className="p-3 text-[11px] text-faint">—</div>
  return (
    <div className="space-y-1.5 p-3">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">{scope}</span>
        {st.running > 0 && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-text" aria-hidden />}
        <span className="ml-auto text-[10.5px] text-faint">{fmtLocal(st.last_activity) || 'no activity'}</span>
      </div>
      <div className="flex flex-wrap gap-3 text-[11px]">
        <Count n={st.sessions} label="sessions" />
        <Count n={st.agents} label="agents" />
        <Count n={st.running} label="running" accent={st.running > 0} />
      </div>
      {st.current_item && (
        <div className="truncate text-[11px] text-accent-text" title={st.current_item}>
          ▶ {st.current_item}
        </div>
      )}
    </div>
  )
}

function Count({ n, label, accent }: { n: number; label: string; accent?: boolean }) {
  return (
    <span className="text-muted">
      <span className={`font-mono ${accent ? 'text-accent-text' : 'text-fg'}`}>{n}</span> {label}
    </span>
  )
}

// =================================================================== shared bits

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-surface p-6 text-sm text-muted">{children}</div>
  )
}
