import { useEffect, useState } from 'react'
import Dropdown from '@/ui/Dropdown'
import Toggle from '@/ui/Toggle'
import { EFFORTS as EFFORT_CATALOG, MODELS as MODEL_CATALOG, fmtModel } from '@/lib/format'
import { invalidate, useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import {
  getSystem, setSystemLearning, setSweepConfig,
  getAgentModels, setAgentModel, setAgentEffort,
  type SystemOverview, type AgentModels,
} from '@/lib/api'
import {
  ApplyBar, Card, ConfigRow, Divider, Loading, NumberField, PaneHead, SectionLabel, W_MAIN, W_SUB,
} from '../controls'

// System › Learning — everything that governs how SuperMe learns, in the order it happens: whether
// learning runs at all, when the sweep fires, and which model each learning agent runs on. These
// were three separate places (a switch on one page, a card below it, a table below that, all named
// after mechanism rather than subject); the subject is one thing, so it is one pane.

const EFFORTS = EFFORT_CATALOG.map((e) => ({ value: e.key, label: e.label }))
// The agent pickers store a TIER (`sonnet` — the daemon auto-tracks it to the latest concrete), but
// the label shows the concrete version so it reads consistently with every other model picker.
const familyOf = (id: string) => (id.startsWith('claude-') ? id.split('-')[1] : id)
const TIERS = MODEL_CATALOG.map((m) => ({ value: familyOf(m.key), label: m.label }))

export default function LearningConfig() {
  const sys = useLive(K.systemOverview, getSystem, 0)
  return (
    <>
      <PaneHead
        title="Auto-learning"
        lede="How SuperMe learns on its own. What each project learned lives under that project."
      />
      {sys.error && !sys.data ? (
        <div className="text-sm text-danger">Couldn’t load system config — {String(sys.error)}</div>
      ) : !sys.data ? (
        <Loading />
      ) : (
        <>
          <MasterSwitch sys={sys.data} />
          <SectionLabel
            title="Capture sweep"
            hint="When the automatic sweep runs. Only fires while the switch above is on."
          />
          <Sweep sys={sys.data} />
        </>
      )}
      <SectionLabel
        title="Learning agents"
        hint="The tier each agent runs on. A tier always tracks its own latest version."
      />
      <Agents />
    </>
  )
}

function MasterSwitch({ sys }: { sys: SystemOverview }) {
  const [on, setOn] = useState(sys.learning_enabled)
  useEffect(() => { setOn(sys.learning_enabled) }, [sys])
  return (
    <Card>
      <ConfigRow title="Auto-learning" hint="Off suspends learning for every project. Nothing below fires.">
        <Toggle
          on={on}
          onChange={(v) => { setOn(v); setSystemLearning(v).then(() => invalidate(K.systemOverview)).catch(() => {}) }}
        />
      </ConfigRow>
    </Card>
  )
}

// Idle threshold + heartbeat cadence (both shown in minutes; stored as seconds) and the
// min-new-user-messages gate. Edits stage into a draft; Apply is what writes to the spine.
function Sweep({ sys }: { sys: SystemOverview }) {
  const saved = {
    idle: Math.round(sys.sweep_idle_seconds / 60),
    poll: Math.round(sys.sweep_poll_seconds / 60),
    msgs: sys.sweep_min_user_msgs,
  }
  const [draft, setDraft] = useState(saved)
  const [saving, setSaving] = useState(false)
  useEffect(() => { setDraft(saved) }, [sys])  // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = draft.idle !== saved.idle || draft.poll !== saved.poll || draft.msgs !== saved.msgs

  async function apply() {
    setSaving(true)
    try {
      await setSweepConfig({ idle_seconds: draft.idle * 60, poll_seconds: draft.poll * 60, min_user_msgs: draft.msgs })
      invalidate(K.systemOverview)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <ConfigRow title="Idle time" hint="How long a dev session sits quiet before it is swept.">
        <NumberField value={draft.idle} min={1} max={240} unit="min" onChange={(v) => setDraft((d) => ({ ...d, idle: v }))} />
      </ConfigRow>
      <Divider />
      <ConfigRow title="Heartbeat" hint="How often the daemon scans for idle sessions.">
        <NumberField value={draft.poll} min={1} max={60} unit="min" onChange={(v) => setDraft((d) => ({ ...d, poll: v }))} />
      </ConfigRow>
      <Divider />
      <ConfigRow title="Min conversation" hint="How many new messages must accrue before a sweep.">
        <NumberField value={draft.msgs} min={0} max={50} unit="msgs" onChange={(v) => setDraft((d) => ({ ...d, msgs: v }))} />
      </ConfigRow>
      <ApplyBar dirty={dirty} saving={saving} onReset={() => setDraft(saved)} onApply={apply} />
    </Card>
  )
}

// One row per autonomous learning agent. Each runs its code-level preset unless overridden; the
// response carries the re-read rows, because the frontmatter is the source of truth.
function Agents() {
  const [data, setData] = useState<AgentModels | null>(null)

  useEffect(() => {
    let alive = true
    getAgentModels().then((d) => alive && setData(d)).catch(() => {})
    return () => { alive = false }
  }, [])

  if (!data) return <Loading />

  const patch = (feature: string, field: 'tier' | 'effort', v: string) =>
    setData((d) => d && { agents: d.agents.map((a) => (a.feature === feature ? { ...a, [field]: v } : a)) })

  return (
    <div className="overflow-hidden rounded-xl border border-line">
      {data.agents.map((a, i) => (
        <div
          key={a.feature}
          className={`flex flex-wrap items-center justify-between gap-x-3 gap-y-2 bg-surface px-4 py-3 ${
            i < data.agents.length - 1 ? 'border-b border-line' : ''
          }`}
        >
          <div className="min-w-[9rem] flex-1">
            <div className="flex items-center gap-1.5">
              <span className="text-[14px] text-fg">{a.label}</span>
              <span className="text-[12px] capitalize text-faint">– {a.scope}</span>
            </div>
            <div className="text-[12px] text-faint">Runs on {fmtModel(a.model)}</div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Dropdown
              value={a.tier}
              options={TIERS}
              onChange={(v) => { patch(a.feature, 'tier', v); setAgentModel(a.feature, v).then(setData).catch(() => {}) }}
              align="right"
              width={W_MAIN}
              title={`${a.label} agent model tier`}
            />
            <Dropdown
              value={a.effort}
              options={EFFORTS}
              onChange={(v) => { patch(a.feature, 'effort', v); setAgentEffort(a.feature, v).then(setData).catch(() => {}) }}
              align="right"
              width={W_SUB}
              title={`${a.label} agent reasoning effort`}
            />
          </div>
        </div>
      ))}
    </div>
  )
}
