import { useEffect, useState } from 'react'
import Toggle from '@/ui/Toggle'
import { invalidate, useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import {
  getSystem, setSystemDeputy, getCompactionConfig, setCompactionConfig,
  type SystemOverview, type CompactionConfig,
} from '@/lib/api'
import {
  ApplyBar, Card, ConfigRow, Divider, GaugeBar, Loading, NumberField, PaneHead, SectionLabel,
} from '../controls'

// System › General — the settings that are genuinely system-shaped: who judges the autopilot gates
// and how readily, and when a long session compacts itself.
//
// Model and effort are NOT here. They were, and every repo overrode them — three projects choosing
// the same value independently, which is what a default is, set one tier too low. A repo picks its
// own; one that picks nothing runs the declared default (config/system.yaml, else the built-in
// floor), which is what Project · Settings names in its inherit option.

// The deputy escalation dial is set PER GATE, because a project can want a light touch at triage
// and a cautious hand at review. The refusal floor holds at every level; this only moves the
// discretionary band.
const DEPUTY_GATES = [
  { key: 'triage', label: 'Triage' },
  { key: 'plan', label: 'Plan' },
  { key: 'review', label: 'Review' },
]

export default function General() {
  const sys = useLive(K.systemOverview, getSystem, 0)
  return (
    <>
      <PaneHead
        title="General"
        scope="System"
        lede="System-wide behaviour: who judges the autopilot gates on your behalf, and when a long session compacts itself. A project’s own model and effort are set on that project."
      />
      {sys.error && !sys.data ? (
        <div className="text-sm text-danger">Couldn’t load system config — {String(sys.error)}</div>
      ) : !sys.data ? (
        <Loading />
      ) : (
        <Defaults sys={sys.data} />
      )}
      <SectionLabel
        title="Compaction"
        hint="When a work-item session auto-compacts. System-wide — the runtime honours this number as given."
      />
      <Compaction />
    </>
  )
}

function Defaults({ sys }: { sys: SystemOverview }) {
  // Held locally so a pick answers immediately; the cache it came from refreshes on its own clock.
  const [deputy, setDeputy] = useState(sys.deputy_enabled ?? true)
  const [strict, setStrict] = useState<Record<string, string>>(sys.deputy_strictness ?? {})
  useEffect(() => {
    setDeputy(sys.deputy_enabled ?? true)
    setStrict(sys.deputy_strictness ?? {})
  }, [sys])

  const after = () => invalidate(K.systemOverview)

  return (
    <>
      <Card>
        <ConfigRow title="Deputy" hint="judges autopilot gates on your behalf — off runs autopilot unsupervised">
          <Toggle
            on={deputy}
            onChange={(v) => { setDeputy(v); setSystemDeputy({ enabled: v }).then(after).catch(() => {}) }}
          />
        </ConfigRow>
      </Card>

      {deputy && (
        <>
          <SectionLabel
            title="Deputy strictness"
            hint="Per gate — how readily it calls you. Low delegates most, extra calls you soonest."
          />
          <Card>
            {DEPUTY_GATES.map((g, i) => (
              <div key={g.key}>
                {i > 0 && <div className="mb-3"><Divider /></div>}
                <ConfigRow title={g.label}>
                  <GaugeBar
                    level={strict[g.key] ?? 'medium'}
                    onPick={(l) => {
                      setStrict((s) => ({ ...s, [g.key]: l }))
                      setSystemDeputy({ strictness: { [g.key]: l } }).then(after).catch(() => {})
                    }}
                  />
                </ConfigRow>
              </div>
            ))}
          </Card>
        </>
      )}
    </>
  )
}

// Compaction is global by design — per-session floors already adapt the effective trigger, so a
// per-repo override would add config surface without adding control. Edits stage into a draft; a
// floor-violating trigger is REFUSED by the daemon (409) and the reason is surfaced inline.
function Compaction() {
  const [cfg, setCfg] = useState<CompactionConfig | null>(null)
  const [draft, setDraft] = useState({ trigger: 80, auto: true, gain: 30 })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const fromCfg = (c: CompactionConfig) => ({
    trigger: c.trigger_pct,
    auto: c.min_gain_pct === 'auto',
    // Keep the last manual value, so toggling auto off restores it rather than a constant.
    gain: typeof c.min_gain_pct === 'number' ? c.min_gain_pct : 30,
  })

  useEffect(() => {
    let alive = true
    getCompactionConfig()
      .then((c) => { if (alive) { setCfg(c); setDraft(fromCfg(c)) } })
      .catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [])

  if (err && !cfg) return <div className="text-sm text-danger">Couldn’t load compaction config — {err}</div>
  if (!cfg) return <Loading />

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

  return (
    <Card>
      {/* `min` is the SERVER's accepted minimum, never `floor_pct + 1`. Clearing the incompressible
          floor is not the same as leaving working room: a trigger just above it lands the session
          near the floor and one exchange puts it straight back over, so it re-fires every turn. */}
      <ConfigRow
        title="Trigger"
        hint={`context fill that fires auto-compaction (min ${cfg.min_pct}% — the incompressible floor is ${cfg.floor_pct}%, plus room to work)`}
      >
        <NumberField value={draft.trigger} min={cfg.min_pct} max={95} unit="%" onChange={(v) => setDraft((d) => ({ ...d, trigger: v }))} />
      </ConfigRow>
      <Divider />
      <ConfigRow
        title="Min gain"
        hint={draft.auto
          ? 'auto: a compaction must reclaim ≥50% of what the session could shed (its fill beyond the incompressible preload) — two strikes parks the session'
          : 'manual: a compaction shrinking less than this flat % counts as ineffective (two strikes parks the session)'}
      >
        <span className="text-[12px] text-faint">Auto</span>
        <Toggle on={draft.auto} onChange={(v) => setDraft((d) => ({ ...d, auto: v }))} />
        {!draft.auto && (
          <NumberField value={draft.gain} min={1} max={95} unit="%" onChange={(v) => setDraft((d) => ({ ...d, gain: v }))} />
        )}
      </ConfigRow>
      {err && <div className="text-[12px] text-danger">{err}</div>}
      <ApplyBar dirty={dirty} saving={saving} onReset={() => { setDraft(fromCfg(cfg)); setErr(null) }} onApply={apply} />
    </Card>
  )
}
