import { useEffect, useState } from 'react'
import Dropdown from '@/ui/Dropdown'
import Toggle from '@/ui/Toggle'
import { MODELS as MODEL_CATALOG, EFFORTS as EFFORT_CATALOG, DEFAULT_MODEL, DEFAULT_EFFORT, toModelKey } from '@/lib/format'
import { invalidate, useLive } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { getSystem, setRepoModel, setRepoEffort, setRepoLearning, type ModelAlias } from '@/lib/api'
import { setRepoGit, getRepoBranches } from '@/lib/api/system'
import type { OrbitRepo } from '@/features/shell/useCommandStats'
import { Card, ConfigRow, Divider, PaneHead, SectionLabel, W_WIDE } from '../controls'

// Project › Settings — everything that is true of ONE repo: what it inherits, what it overrides,
// and how its work lands. "" means inherit, so the per-repo pickers carry a System-default option
// the system-level ones cannot have.

// A picker shows the value IN FORCE, never an "inherit" row beside the value it inherits — one
// answer wearing two labels.
const MODEL_OPTS = MODEL_CATALOG.map((m) => ({ value: m.key, label: m.label }))
const EFFORT_OPTS = EFFORT_CATALOG.map((e) => ({ value: e.key, label: e.label }))

// HOW this repo's work lands: does the diff get its own review gate
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
        lede={`What ${name} runs on, what checks it, and how its work lands.`}
      />
      <Inheritance repo={repo} name={name} />
      <SectionLabel
        title="Vet"
        hint="Vet checks what build produced. It runs on its own model, not this project's."
      />
      <Vet repo={repo} name={name} />
      <SectionLabel
        title="Landing"
        hint="How this project's work lands, and where."
      />
      <Landing repo={repo} />
    </>
  )
}

function Inheritance({ repo, name }: { repo: OrbitRepo; name: string }) {
  // What this repo runs if it overrides nothing, on the key General already reads.
  const sys = useLive(K.systemOverview, getSystem, 0)
  // Unset shows what unset RUNS. An empty pick and an unresolved default are different states.
  const fallbackModel = toModelKey(sys.data?.default_model) || DEFAULT_MODEL
  const fallbackEffort = sys.data?.default_effort || DEFAULT_EFFORT
  const [model, setModel] = useState(toModelKey(repo.modelOverride) || fallbackModel)
  const [effort, setEffort] = useState(repo.effortOverride || fallbackEffort)
  const [learning, setLearning] = useState(repo.learningEnabled)
  // The controls follow the repo: without this, switching projects in the sidebar would show the
  // previous one's overrides.
  useEffect(() => {
    setModel(toModelKey(repo.modelOverride) || fallbackModel)
    setEffort(repo.effortOverride || fallbackEffort)
    setLearning(repo.learningEnabled)
  }, [repo.id, repo.modelOverride, repo.effortOverride, repo.learningEnabled, fallbackModel, fallbackEffort])

  const after = () => invalidate(K.repos)

  return (
    <Card>
      <ConfigRow title="Model" hint="The model this project runs on.">
        <Dropdown
          value={model}
          options={MODEL_OPTS}
          onChange={(v) => { setModel(v); setRepoModel(repo.id, v as ModelAlias).then(after).catch(() => {}) }}
          align="right"
          width={W_WIDE}
          title={`${name} model`}
        />
      </ConfigRow>
      <Divider />
      <ConfigRow title="Reasoning effort" hint="How hard it thinks on this project.">
        <Dropdown
          value={effort}
          options={EFFORT_OPTS}
          onChange={(v) => { setEffort(v); setRepoEffort(repo.id, v).then(after).catch(() => {}) }}
          align="right"
          width={W_WIDE}
          title={`${name} reasoning effort`}
        />
      </ConfigRow>
      <Divider />
      <ConfigRow title="Auto-learning" hint="Also needs the system master switch on.">
        <Toggle
          on={learning}
          onChange={(v) => { setLearning(v); setRepoLearning(repo.id, v).then(after).catch(() => {}) }}
        />
      </ConfigRow>
    </Card>
  )
}

// Deliberately NOT under Inheritance: everything there is what this project runs, and vet is the
// check ON what it runs.
function Vet({ repo, name }: { repo: OrbitRepo; name: string }) {
  const sys = useLive(K.systemOverview, getSystem, 0)
  const fallbackModel = toModelKey(sys.data?.default_model) || DEFAULT_MODEL
  const fallbackEffort = sys.data?.default_effort || DEFAULT_EFFORT
  const [model, setModel] = useState(toModelKey(repo.vetModel) || fallbackModel)
  const [effort, setEffort] = useState(repo.vetEffort || fallbackEffort)
  useEffect(() => {
    setModel(toModelKey(repo.vetModel) || fallbackModel)
    setEffort(repo.vetEffort || fallbackEffort)
  }, [repo.id, repo.vetModel, repo.vetEffort, fallbackModel, fallbackEffort])
  const after = () => invalidate(K.repos)
  return (
    <Card>
      <ConfigRow title="Model" hint="The model vet runs on when it checks this project's work.">
        <Dropdown
          value={model}
          options={MODEL_OPTS}
          onChange={(v) => { setModel(v); setRepoModel(repo.id, v as ModelAlias, 'vet').then(after).catch(() => {}) }}
          align="right"
          width={W_WIDE}
          title={`${name} vet model`}
        />
      </ConfigRow>
      <Divider />
      <ConfigRow title="Reasoning effort" hint="How hard vet thinks about what it is checking.">
        <Dropdown
          value={effort}
          options={EFFORT_OPTS}
          onChange={(v) => { setEffort(v); setRepoEffort(repo.id, v, 'vet').then(after).catch(() => {}) }}
          align="right"
          width={W_WIDE}
          title={`${name} vet reasoning effort`}
        />
      </ConfigRow>
    </Card>
  )
}

// The two landing knobs, here because every other per-repo setting has a home here.
function Landing({ repo }: { repo: OrbitRepo }) {
  const [mode, setMode] = useState(repo.reviewMode)
  useEffect(() => { setMode(repo.reviewMode) }, [repo.id, repo.reviewMode])

  // The anchor shows what git actually targets — the RESOLVED branch, not the stored setting, which
  // is null until pinned.
  const branches = useLive(K.repoBranches(repo.id), () => getRepoBranches(repo.id), 0)
  const [anchor, setAnchor] = useState('')
  useEffect(() => {
    setAnchor(repo.anchorBranch ?? branches.data?.anchor ?? repo.resolvedAnchor ?? '')
  }, [repo.id, repo.anchorBranch, repo.resolvedAnchor, branches.data?.anchor])
  // The list is fetched once, so keep the CURRENT anchor in the options even when the list predates
  // it.
  const options = Array.from(
    new Set([...(branches.data?.branches ?? []), ...(anchor ? [anchor] : [])]),
  ).map((b) => ({ value: b, label: b }))

  return (
    <Card>
      <ConfigRow
        title="Review mode"
        hint="Fast merges an item when you approve it. Strict opens a PR for you to merge."
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
          ? `Branch not found. ${repo.anchorError}`
          : 'The branch every git action targets.'}
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
          // No branch list yet, so say so: an empty picker looks like a repo with no branches.
          <span className="text-[12px] text-faint">{branches.error ? 'no branches readable' : 'reading branches…'}</span>
        )}
      </ConfigRow>
    </Card>
  )
}
