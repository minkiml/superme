import { useEffect, useState } from 'react'
import Dropdown from '@/ui/Dropdown'
import Toggle from '@/ui/Toggle'
import { MODELS as MODEL_CATALOG, EFFORTS as EFFORT_CATALOG, fmtModel, toModelKey } from '@/lib/format'
import { invalidate, useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { getSystem, setRepoModel, setRepoEffort, setRepoLearning, type ModelAlias } from '@/lib/api'
import { setRepoGit, getRepoBranches } from '@/lib/api/system'
import type { OrbitRepo } from '@/features/shell/useCommandStats'
import { Card, ConfigRow, Divider, PaneHead, SectionLabel, W_WIDE } from '../controls'

// Project › Settings — everything that is true of ONE repo: what it inherits, what it overrides,
// and how its work lands. "" means inherit, so the per-repo pickers carry a System-default option
// the system-level ones cannot have.

// The inherit option NAMES the value it inherits. "System default" was a pointer to a setting that
// no longer exists — a repo that picks nothing runs the declared default, and the picker should say
// which one that is rather than making you go somewhere else to find out.
const modelOptions = (fallback: string) => [
  { value: '', label: `Default · ${fmtModel(fallback)}` },
  ...MODEL_CATALOG.map((m) => ({ value: m.key, label: m.label })),
]
const effortOptions = (fallback: string) => [
  { value: '', label: `Default · ${EFFORT_CATALOG.find((e) => e.key === fallback)?.label ?? fallback}` },
  ...EFFORT_CATALOG.map((e) => ({ value: e.key, label: e.label })),
]

// HOW this repo's work lands (workflow-renovation-v2 §2.2): does the diff get its own review gate
// before it lands. Every repo starts on `fast`.
const REVIEW_MODES = [
  { value: 'fast', label: 'Fast' },
  { value: 'strict', label: 'Strict' },
]

export default function ProjectSettings({ repo }: { repo: OrbitRepo }) {
  const name = repo.id === 'global' ? 'SuperMe Hub' : repo.label
  return (
    <>
      <PaneHead
        title="Settings"
        lede={`What ${name} runs on, and how its work lands. A picker left on its Default option follows the declared default rather than pinning a value here.`}
      />
      <Inheritance repo={repo} name={name} />
      <SectionLabel
        title="Landing"
        hint="How this project's work reaches the anchor, and which branch that is."
      />
      <Landing repo={repo} />
    </>
  )
}

function Inheritance({ repo, name }: { repo: OrbitRepo; name: string }) {
  // What this repo runs if it overrides nothing. Same cached key General reads, so naming it here
  // costs no extra request.
  const sys = useLive(K.systemOverview, getSystem, 0)
  const [model, setModel] = useState(toModelKey(repo.modelOverride))
  const [effort, setEffort] = useState(repo.effortOverride ?? '')
  const [learning, setLearning] = useState(repo.learningEnabled)
  // The controls follow the repo: without this, switching projects in the sidebar would show the
  // previous one's overrides.
  useEffect(() => {
    setModel(toModelKey(repo.modelOverride))
    setEffort(repo.effortOverride ?? '')
    setLearning(repo.learningEnabled)
  }, [repo.id, repo.modelOverride, repo.effortOverride, repo.learningEnabled])

  const after = () => invalidate(K.repos)

  return (
    <Card>
      <ConfigRow title="Model" hint="The model this project's turns run on">
        <Dropdown
          value={model}
          options={modelOptions(sys.data?.default_model ?? '')}
          onChange={(v) => { setModel(v); setRepoModel(repo.id, (v || null) as ModelAlias | null).then(after).catch(() => {}) }}
          align="right"
          width={W_WIDE}
          title={`${name} model`}
        />
      </ConfigRow>
      <Divider />
      <ConfigRow title="Reasoning effort" hint="How hard it thinks on this project">
        <Dropdown
          value={effort}
          options={effortOptions(sys.data?.default_effort ?? 'medium')}
          onChange={(v) => { setEffort(v); setRepoEffort(repo.id, v || null).then(after).catch(() => {}) }}
          align="right"
          width={W_WIDE}
          title={`${name} reasoning effort`}
        />
      </ConfigRow>
      <Divider />
      <ConfigRow title="Auto-learning" hint="Also needs the system master switch on">
        <Toggle
          on={learning}
          onChange={(v) => { setLearning(v); setRepoLearning(repo.id, v).then(after).catch(() => {}) }}
        />
      </ConfigRow>
    </Card>
  )
}

// The two landing knobs. They lived in the dev-workspace header — beside the work they govern —
// until every other per-repo setting had a home here and they were the only two you had to go
// somewhere else for. One project, one page.
function Landing({ repo }: { repo: OrbitRepo }) {
  const [mode, setMode] = useState(repo.reviewMode)
  useEffect(() => { setMode(repo.reviewMode) }, [repo.id, repo.reviewMode])

  // The anchor shows what git actually targets — the RESOLVED branch, not the stored setting, which
  // is null until someone pins one. Options come from the repo's real branches: the anchor refuses
  // on a branch that doesn't exist, so a free-text field could only ever store a future failure.
  const branches = useLive(K.repoBranches(repo.id), () => getRepoBranches(repo.id), 0)
  const [anchor, setAnchor] = useState('')
  useEffect(() => {
    setAnchor(repo.anchorBranch ?? branches.data?.anchor ?? repo.resolvedAnchor ?? '')
  }, [repo.id, repo.anchorBranch, repo.resolvedAnchor, branches.data?.anchor])
  // The list is fetched once (branches change rarely), so it can be older than the anchor the
  // server just told us about. Keep the CURRENT anchor in the options whatever the list says: a
  // dropdown showing a value it cannot offer is a one-way door.
  const options = Array.from(
    new Set([...(branches.data?.branches ?? []), ...(anchor ? [anchor] : [])]),
  ).map((b) => ({ value: b, label: b }))

  return (
    <Card>
      <ConfigRow
        title="Review mode"
        hint="Fast: approving an item merges it. Strict: approving opens a PR, and you merge from the PR page."
      >
        <Dropdown
          value={mode}
          options={REVIEW_MODES}
          onChange={(v) => { setMode(v); setRepoGit(repo.id, { review_mode: v }).then(() => invalidate(K.repos)).catch(() => {}) }}
          align="right"
          width={W_WIDE}
          title="How this project's work lands"
        />
      </ConfigRow>
      <Divider />
      <ConfigRow
        title="Anchor branch"
        hint={repo.anchorError
          ? `Not found: ${repo.anchorError}`
          : 'What every git site targets: branch-from base, sync source, merge target'}
      >
        {options.length > 0 ? (
          <Dropdown
            value={anchor}
            options={options}
            onChange={(v) => {
              setAnchor(v)
              setRepoGit(repo.id, { anchor_branch: v })
                .then(() => invalidate(K.repoBranches(repo.id), K.repos))
                .catch(() => {})
            }}
            align="right"
            width={W_WIDE}
            title="Anchor branch"
          />
        ) : (
          // No branch list yet (still loading, or git can't answer) — say so rather than showing an
          // empty picker that looks like a repo with no branches.
          <span className="text-[12px] text-faint">{branches.error ? 'no branches readable' : 'reading branches…'}</span>
        )}
      </ConfigRow>
    </Card>
  )
}
