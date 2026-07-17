import { useEffect, useState, type ReactNode } from 'react'
import { SlidersHorizontal, Loader2, Check } from 'lucide-react'
import Dropdown from '@/ui/Dropdown'
import Toggle from '@/ui/Toggle'
import { RepoIcon } from '@/lib/repoIcons'
import { MODELS as MODEL_CATALOG, EFFORTS as EFFORT_CATALOG, fmtModel, toModelKey } from '@/lib/format'
import {
  getSystem, setSystemModel, setSystemLearning, setRepoModel, setRepoLearning, setSweepConfig,
  setSystemEffort, setRepoEffort, getAgentModels, setAgentModel, setAgentEffort,
  getCompactionConfig, setCompactionConfig,
  type SystemOverview, type ModelAlias, type AgentModels, type CompactionConfig,
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
// Background-agent model pickers: the VALUE is the tier (`sonnet` — the daemon auto-tracks it to the
// latest concrete), but the LABEL shows the concrete version ("Sonnet 5") to read consistently with
// the per-repo pickers. Tier family is parsed from each catalog id (`claude-sonnet-5` → `sonnet`).
const familyOf = (id: string) => (id.startsWith('claude-') ? id.split('-')[1] : id)
const AGENT_TIERS = MODEL_CATALOG.map((m) => ({ value: familyOf(m.key), label: m.label }))

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

        {/* Compaction runtime */}
        <section className="mb-8">
          <div className="mb-1 text-[12px] font-semibold uppercase tracking-wider text-muted">Compaction</div>
          <p className="mb-3 text-[12px] text-faint">
            When a work-item session auto-compacts. System-wide; each session still raises its own
            effective trigger above its measured floor.
          </p>
          <CompactionTuning />
        </section>

        {/* Background agents */}
        <section className="mb-8">
          <div className="mb-1 text-[12px] font-semibold uppercase tracking-wider text-muted">Background agents</div>
          <p className="mb-3 text-[12px] text-faint">
            The model tier each universal sub-agent (Capture, Distill, Forge) runs on when the daemon fires it
            autonomously. Pick a tier — it auto-tracks that tier’s latest version. Writes the agent’s own <code>.md</code>.
          </p>
          <BackgroundAgents />
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

// The background-agent model table — one row per autonomous learning agent (sweep/distill/write).
// Each runs its code-level PRESET unless overridden; "Preset" clears the override. Self-contained
// (fetches its own state) since these live outside the SystemOverview payload.
function BackgroundAgents() {
  const [data, setData] = useState<AgentModels | null>(null)

  useEffect(() => {
    let alive = true
    getAgentModels().then((d) => alive && setData(d)).catch(() => {})
    return () => { alive = false }
  }, [])

  function changeModel(feature: string, v: string) {
    // Optimistic: reflect the pick immediately, then persist to the agent's .md. The response
    // carries the re-read rows (source of truth = the frontmatter).
    setData((d) => d && { agents: d.agents.map((a) => (a.feature === feature ? { ...a, tier: v } : a)) })
    setAgentModel(feature, v).then(setData).catch(() => {})
  }
  function changeEffort(feature: string, v: string) {
    setData((d) => d && { agents: d.agents.map((a) => (a.feature === feature ? { ...a, effort: v } : a)) })
    setAgentEffort(feature, v).then(setData).catch(() => {})
  }

  if (!data) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted">
        <Loader2 size={14} className="animate-spin" /> Loading…
      </div>
    )
  }
  return (
    <div className="overflow-hidden rounded-xl border border-line">
      {data.agents.map((a, i) => (
          <div
            key={a.feature}
            className={`flex items-center justify-between gap-3 bg-surface px-4 py-3 ${i < data.agents.length - 1 ? 'border-b border-line' : ''}`}
          >
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="text-[14px] text-fg">{a.label}</span>
                {/* Scope tag (muted) — these learning sub-agents are universal, dev-scope. */}
                <span className="text-[12px] text-faint capitalize">– {a.scope}</span>
              </div>
              <div className="text-[12px] text-faint">runs on {fmtModel(a.model)}</div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Dropdown
                value={a.tier}
                options={AGENT_TIERS}
                onChange={(v) => changeModel(a.feature, v)}
                align="right"
                width="w-32"
                title={`${a.label} agent model tier`}
              />
              <Dropdown
                value={a.effort}
                options={SYSTEM_EFFORTS}
                onChange={(v) => changeEffort(a.feature, v)}
                align="right"
                width="w-28"
                title={`${a.label} agent reasoning effort`}
              />
            </div>
          </div>
      ))}
    </div>
  )
}

// A labeled config row. MODULE-LEVEL on purpose: defining this inside a card component gives it a
// new identity every render, and the stats poll re-renders the page every ~5s — React then
// REMOUNTS the row's subtree each cycle, resetting inputs and stealing focus mid-typing (the
// "can't type into the trigger field" bug).
function ConfigRow({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <div className="text-[14px] text-fg">{title}</div>
        <div className="text-[12px] text-faint">{hint}</div>
      </div>
      {children}
    </div>
  )
}

// A compact labeled integer stepper with a unit suffix. The text is FREE while typing and clamps
// to [min, max] only on blur/Enter — clamping per keystroke makes fields with a high `min`
// untypeable (typing "60" into min=26 became 26 → 260 → 95 before the fix). The ± buttons still
// clamp-commit instantly.
function NumberField({ value, min, max, unit, onChange }: {
  value: number; min: number; max: number; unit: string; onChange: (v: number) => void
}) {
  const clamp = (v: number) => Math.max(min, Math.min(max, v))
  const [text, setText] = useState(String(value))
  useEffect(() => { setText(String(value)) }, [value])  // ± / reset / apply re-sync the draft
  function commit() {
    const n = parseInt(text, 10)
    const v = clamp(Number.isNaN(n) ? value : n)
    setText(String(v))  // explicit — the effect won't fire when the clamped value is unchanged
    if (v !== value) onChange(v)
  }
  const btn = 'grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line text-muted hover:bg-hover hover:text-fg'
  return (
    // Fixed widths so the −/value/unit/+ columns line up across every row.
    <div className="flex shrink-0 items-center gap-1.5">
      <button onClick={() => onChange(clamp(value - 1))} className={btn}>−</button>
      <div className="flex w-[5.25rem] items-baseline gap-1 rounded-md border border-line bg-sunken px-2 py-1">
        <input
          type="number"
          value={text}
          min={min}
          max={max}
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
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

  const Row = ConfigRow

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

// Compaction tuning card (S8/D11): the system-wide auto-compaction trigger + the effectiveness
// threshold. Global only by design — per-session floors already adapt the effective trigger, so a
// per-repo override adds config surface without adding control. Draft+Apply like SweepTuning; a
// floor-violating trigger is REFUSED by the daemon (409) and the reason is surfaced inline.
function CompactionTuning() {
  // min gain draft: auto (default — verdict judged against the session's reclaimable space) or a
  // manual flat %. `gain` keeps the last manual value so toggling auto off restores it.
  const [cfg, setCfg] = useState<CompactionConfig | null>(null)
  const [draft, setDraft] = useState({ trigger: 80, auto: true, gain: 30 })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const fromCfg = (c: CompactionConfig) => ({
    trigger: c.trigger_pct,
    auto: c.min_gain_pct === 'auto',
    gain: typeof c.min_gain_pct === 'number' ? c.min_gain_pct : 30,
  })

  useEffect(() => {
    let alive = true
    getCompactionConfig().then((c) => {
      if (!alive) return
      setCfg(c)
      setDraft(fromCfg(c))
    }).catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [])

  if (err && !cfg) return <div className="text-sm text-danger">Couldn’t load compaction config — {err}</div>
  if (!cfg) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted">
        <Loader2 size={14} className="animate-spin" /> Loading…
      </div>
    )
  }

  const draftGain: number | 'auto' = draft.auto ? 'auto' : draft.gain
  const dirty = draft.trigger !== cfg.trigger_pct || draftGain !== cfg.min_gain_pct

  async function apply() {
    setSaving(true)
    setErr(null)
    try {
      const next = await setCompactionConfig({ trigger_pct: draft.trigger, min_gain_pct: draftGain })
      setCfg(next)
      setDraft(fromCfg(next))
    } catch (e) {
      setErr(String(e)) // e.g. the daemon's floor refusal — shown verbatim, draft kept for editing
    } finally {
      setSaving(false)
    }
  }

  const Row = ConfigRow

  return (
    <div className="space-y-3 rounded-xl border border-line bg-surface p-4">
      <Row title="Trigger" hint={`context fill that fires auto-compaction (floor ${cfg.floor_pct}% — lower values are refused)`}>
        <NumberField value={draft.trigger} min={cfg.floor_pct + 1} max={95} unit="%" onChange={(v) => setDraft((d) => ({ ...d, trigger: v }))} />
      </Row>
      <div className="h-px bg-line" />
      <Row
        title="Min gain"
        hint={draft.auto
          ? 'auto: a compaction must reclaim ≥50% of what the session could shed (its fill beyond the incompressible preload) — two strikes parks the session'
          : 'manual: a compaction shrinking less than this flat % counts as ineffective (two strikes parks the session)'}
      >
        <div className="flex shrink-0 items-center gap-2.5">
          <span className="text-[12px] text-faint">Auto</span>
          <Toggle on={draft.auto} onChange={(v) => setDraft((d) => ({ ...d, auto: v }))} />
          {!draft.auto && (
            <NumberField value={draft.gain} min={1} max={95} unit="%" onChange={(v) => setDraft((d) => ({ ...d, gain: v }))} />
          )}
        </div>
      </Row>
      {err && <div className="text-[12px] text-danger">{err}</div>}
      <div className="flex items-center justify-end gap-2 border-t border-line pt-3">
        {dirty && !saving && (
          <button onClick={() => { setDraft(fromCfg(cfg)); setErr(null) }} className="rounded-md px-2.5 py-1.5 text-[13px] text-muted hover:text-fg">
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
  const [model, setModel] = useState(toModelKey(repo.modelOverride))
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
