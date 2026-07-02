import { useEffect, useState, type ReactNode } from 'react'
import { SlidersHorizontal, Loader2, Check } from 'lucide-react'
import Dropdown from '@/ui/Dropdown'
import { RepoIcon } from '@/lib/repoIcons'
import { MODELS as MODEL_CATALOG, EFFORTS as EFFORT_CATALOG } from '@/lib/format'
import {
  getSystem, setSystemModel, setSystemLearning, setRepoModel, setRepoLearning, setSweepConfig,
  setSystemEffort, setRepoEffort,
  type SystemOverview, type ModelAlias,
} from '@/lib/api'
import type { CommandStats, OrbitRepo } from '@/features/shell/useCommandStats'

// Quick config — the command-centre settings surface (Tier-2 nav). System defaults (model +
// learning master switch) up top, then a per-repo override table (model + auto-learning). Every
// control writes straight through to the spine; the per-repo rows mirror the inspector's quick
// config so a repo can be tuned from either place.

// System-level pickers offer the concrete tiers only — the SYSTEM default can't "inherit a system
// default" (it IS the floor), so there's no "System default" option here.
const SYSTEM_MODELS: { value: string; label: string }[] = MODEL_CATALOG.map((m) => ({ value: m.key, label: m.label }))
const SYSTEM_EFFORTS: { value: string; label: string }[] = EFFORT_CATALOG.map((e) => ({ value: e.key, label: e.label }))
// Per-repo pickers add "System default" ("" = inherit the system default set above).
const MODELS = [{ value: '', label: 'System default' }, ...SYSTEM_MODELS]
const EFFORTS = [{ value: '', label: 'System default' }, ...SYSTEM_EFFORTS]

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!on)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${on ? 'bg-core' : 'bg-hover'}`}
    >
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-app transition-all ${on ? 'left-[18px]' : 'left-0.5'}`} />
    </button>
  )
}

export default function QuickConfig({ stats }: { stats: CommandStats }) {
  const [sys, setSys] = useState<SystemOverview | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    getSystem()
      .then((s) => alive && setSys(s))
      .catch((e) => alive && setErr(String(e)))
    return () => {
      alive = false
    }
  }, [])

  const repos = [stats.hub, ...stats.nodes].filter(Boolean) as OrbitRepo[]

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl p-6">
        <header className="mb-6 flex items-center gap-2.5">
          <SlidersHorizontal size={18} className="text-dev" />
          <h1 className="text-[17px] font-semibold text-fg">Quick config</h1>
          <span className="text-[13px] text-faint">command-centre settings</span>
        </header>

        {/* System defaults */}
        <section className="mb-8">
          <div className="mb-3 text-[12px] font-semibold uppercase tracking-wider text-muted">System defaults</div>
          {err ? (
            <div className="text-sm text-danger">Couldn’t load system config — {err}</div>
          ) : sys === null ? (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 size={14} className="animate-spin" /> Loading…
            </div>
          ) : (
            <SystemDefaults sys={sys} onChange={setSys} />
          )}
        </section>

        {/* Capture sweep tuning */}
        <section className="mb-8">
          <div className="mb-1 text-[12px] font-semibold uppercase tracking-wider text-muted">Capture sweep</div>
          <p className="mb-3 text-[12px] text-faint">
            When (and how eagerly) the automatic learning sweep runs. Only fires while auto-learning is on.
          </p>
          {err ? null : sys === null ? (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 size={14} className="animate-spin" /> Loading…
            </div>
          ) : (
            <SweepTuning sys={sys} onChange={setSys} />
          )}
        </section>

        {/* Per-repo overrides */}
        <section>
          <div className="mb-1 text-[12px] font-semibold uppercase tracking-wider text-muted">Per-repo overrides</div>
          <p className="mb-3 text-[12px] text-faint">
            Leave the model on “System default” to inherit; auto-learning also needs the master switch on.
          </p>
          {stats.loading ? (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 size={14} className="animate-spin" /> Loading…
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-line">
              {repos.map((r, i) => (
                <RepoRow key={r.id} repo={r} last={i === repos.length - 1} />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function SystemDefaults({ sys, onChange }: { sys: SystemOverview; onChange: (s: SystemOverview) => void }) {
  // The system default is always a concrete model/effort (the BE floors it) — no inherit state.
  const model = sys.default_model ?? ''
  const effort = sys.default_effort ?? 'medium'
  function changeModel(v: string) {
    onChange({ ...sys, default_model: v, default_model_overridden: true })
    setSystemModel(v as ModelAlias).catch(() => {})
  }
  function changeEffort(v: string) {
    onChange({ ...sys, default_effort: v, default_effort_overridden: true })
    setSystemEffort(v).catch(() => {})
  }
  function changeLearning(v: boolean) {
    onChange({ ...sys, learning_enabled: v })
    setSystemLearning(v).catch(() => {})
  }
  return (
    <div className="space-y-3 rounded-xl border border-line bg-surface p-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[14px] text-fg">Default model</div>
          <div className="text-[12px] text-faint">the model SuperMe runs unless a repo or turn overrides it</div>
        </div>
        <Dropdown value={model} options={SYSTEM_MODELS} onChange={changeModel} align="right" width="w-40" title="System default model" />
      </div>
      <div className="h-px bg-line" />
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[14px] text-fg">Default effort</div>
          <div className="text-[12px] text-faint">the reasoning effort SuperMe runs unless a repo or turn overrides it</div>
        </div>
        <Dropdown value={effort} options={SYSTEM_EFFORTS} onChange={changeEffort} align="right" width="w-40" title="System default reasoning effort" />
      </div>
      <div className="h-px bg-line" />
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[14px] text-fg">Auto-learning</div>
          <div className="text-[12px] text-faint">master switch — off suspends learning for every repo</div>
        </div>
        <Toggle on={sys.learning_enabled} onChange={changeLearning} />
      </div>
    </div>
  )
}

// A compact labeled integer stepper with a unit suffix. Clamps to [min, max]; commits on change.
function NumberField({ value, min, max, unit, onChange }: {
  value: number; min: number; max: number; unit: string; onChange: (v: number) => void
}) {
  const clamp = (v: number) => Math.max(min, Math.min(max, v))
  const btn = 'grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line text-muted hover:bg-hover hover:text-fg'
  return (
    // Fixed widths so the −/value/unit/+ columns line up across every row.
    <div className="flex shrink-0 items-center gap-1.5">
      <button onClick={() => onChange(clamp(value - 1))} className={btn}>−</button>
      <div className="flex w-[5.25rem] items-baseline gap-1 rounded-md border border-line bg-sunken px-2 py-1">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          onChange={(e) => onChange(clamp(parseInt(e.target.value || '0', 10)))}
          className="min-w-0 flex-1 bg-transparent text-right text-[13px] tabular-nums text-fg outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
        />
        <span className="w-8 shrink-0 text-left text-[11px] text-faint">{unit}</span>
      </div>
      <button onClick={() => onChange(clamp(value + 1))} className={btn}>+</button>
    </div>
  )
}

// Sweep tuning card: idle threshold + heartbeat cadence (both shown in minutes; stored as seconds)
// and the min-new-user-messages gate. Edits stage into a draft; the Apply button (disabled until
// something changes) is what writes to the spine.
function SweepTuning({ sys, onChange }: { sys: SystemOverview; onChange: (s: SystemOverview) => void }) {
  const saved = {
    idle: Math.round(sys.sweep_idle_seconds / 60),
    poll: Math.round(sys.sweep_poll_seconds / 60),
    msgs: sys.sweep_min_user_msgs,
  }
  const [draft, setDraft] = useState(saved)
  const [saving, setSaving] = useState(false)
  const dirty = draft.idle !== saved.idle || draft.poll !== saved.poll || draft.msgs !== saved.msgs

  async function apply() {
    setSaving(true)
    try {
      await setSweepConfig({ idle_seconds: draft.idle * 60, poll_seconds: draft.poll * 60, min_user_msgs: draft.msgs })
      onChange({ ...sys, sweep_idle_seconds: draft.idle * 60, sweep_poll_seconds: draft.poll * 60, sweep_min_user_msgs: draft.msgs })
    } finally {
      setSaving(false)
    }
  }

  const Row = ({ title, hint, children }: { title: string; hint: string; children: ReactNode }) => (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <div className="text-[14px] text-fg">{title}</div>
        <div className="text-[12px] text-faint">{hint}</div>
      </div>
      {children}
    </div>
  )

  return (
    <div className="space-y-3 rounded-xl border border-line bg-surface p-4">
      <Row title="Idle time" hint="a dev session quiet this long — with new content — gets swept">
        <NumberField value={draft.idle} min={1} max={240} unit="min" onChange={(v) => setDraft((d) => ({ ...d, idle: v }))} />
      </Row>
      <div className="h-px bg-line" />
      <Row title="Heartbeat" hint="how often the daemon scans for idle sessions (latency, not frequency)">
        <NumberField value={draft.poll} min={1} max={60} unit="min" onChange={(v) => setDraft((d) => ({ ...d, poll: v }))} />
      </Row>
      <div className="h-px bg-line" />
      <Row title="Min conversation" hint="only sweep once this many new user messages have accrued (0 = any new content)">
        <NumberField value={draft.msgs} min={0} max={50} unit="msgs" onChange={(v) => setDraft((d) => ({ ...d, msgs: v }))} />
      </Row>
      <div className="flex items-center justify-end gap-2 border-t border-line pt-3">
        {dirty && !saving && (
          <button onClick={() => setDraft(saved)} className="rounded-md px-2.5 py-1.5 text-[13px] text-muted hover:text-fg">
            Reset
          </button>
        )}
        <button
          onClick={apply}
          disabled={!dirty || saving}
          className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-[13px] font-medium text-on-accent transition enabled:hover:opacity-90 disabled:opacity-40"
        >
          {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Apply
        </button>
      </div>
    </div>
  )
}

function RepoRow({ repo, last }: { repo: OrbitRepo; last: boolean }) {
  const isHub = repo.id === 'global'
  const [model, setModel] = useState(repo.modelOverride ?? '')
  const [effort, setEffort] = useState(repo.effortOverride ?? '')
  const [learning, setLearning] = useState(repo.learningEnabled)

  function changeModel(v: string) {
    setModel(v)
    setRepoModel(repo.id, (v || null) as ModelAlias | null).catch(() => {})
  }
  function changeEffort(v: string) {
    setEffort(v)
    setRepoEffort(repo.id, v || null).catch(() => {})
  }
  function changeLearning(v: boolean) {
    setLearning(v)
    setRepoLearning(repo.id, v).catch(() => {})
  }

  return (
    <div className={`flex items-center gap-3 bg-surface px-4 py-2.5 ${last ? '' : 'border-b border-line'}`}>
      {repo.icon && !isHub ? (
        <RepoIcon name={repo.icon} size={15} color={repo.color} className="shrink-0" />
      ) : (
        <span
          className="h-4 w-4 shrink-0 rounded-[4px]"
          style={isHub ? { backgroundImage: 'var(--grad-iris)' } : { backgroundColor: repo.color }}
        />
      )}
      <span className="min-w-0 flex-1 truncate text-[14px] text-fg">{isHub ? 'SuperMe Hub' : repo.label}</span>
      <Dropdown value={model} options={MODELS} onChange={changeModel} align="right" width="w-36" title={`${isHub ? 'SuperMe Hub' : repo.label} model`} />
      <Dropdown value={effort} options={EFFORTS} onChange={changeEffort} align="right" width="w-32" title={`${isHub ? 'SuperMe Hub' : repo.label} reasoning effort`} />
      <Toggle on={learning} onChange={changeLearning} />
    </div>
  )
}
