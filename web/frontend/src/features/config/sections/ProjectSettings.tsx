import { useEffect, useState } from 'react'
import Dropdown from '@/ui/Dropdown'
import Toggle from '@/ui/Toggle'
import { MODELS as MODEL_CATALOG, EFFORTS as EFFORT_CATALOG, toModelKey } from '@/lib/format'
import { invalidate } from '@/lib/live'
import { K } from '@/lib/live/keys'
import { setRepoModel, setRepoEffort, setRepoLearning, type ModelAlias } from '@/lib/api'
import type { OrbitRepo } from '@/features/shell/useCommandStats'
import { Card, ConfigRow, Divider, PaneHead, W_WIDE } from '../controls'

// Project › Settings — what ONE repo inherits and what it overrides. "" means inherit, so the
// per-repo pickers carry a System-default option the system-level ones cannot have.
//
// Neither landing knob (review mode, anchor branch) is here: both are decided in that repo's dev
// workspace header, beside the work they govern.

const MODELS = [{ value: '', label: 'System default' }, ...MODEL_CATALOG.map((m) => ({ value: m.key, label: m.label }))]
const EFFORTS = [{ value: '', label: 'System default' }, ...EFFORT_CATALOG.map((e) => ({ value: e.key, label: e.label }))]

export default function ProjectSettings({ repo }: { repo: OrbitRepo }) {
  const name = repo.id === 'global' ? 'SuperMe Hub' : repo.label
  const [model, setModel] = useState(toModelKey(repo.modelOverride))
  const [effort, setEffort] = useState(repo.effortOverride ?? '')
  const [learning, setLearning] = useState(repo.learningEnabled)
  // The picker follows the repo: without this, switching projects in the sidebar would show the
  // previous one's overrides.
  useEffect(() => {
    setModel(toModelKey(repo.modelOverride))
    setEffort(repo.effortOverride ?? '')
    setLearning(repo.learningEnabled)
  }, [repo.id, repo.modelOverride, repo.effortOverride, repo.learningEnabled])

  const after = () => invalidate(K.repos)

  return (
    <>
      <PaneHead
        title="Settings"
        scope={name}
        lede={`What ${name} inherits, and what it overrides. Leave a picker on “System default” to inherit.`}
      />
      <Card>
        <ConfigRow title="Model" hint="the model this project's turns run on">
          <Dropdown
            value={model}
            options={MODELS}
            onChange={(v) => { setModel(v); setRepoModel(repo.id, (v || null) as ModelAlias | null).then(after).catch(() => {}) }}
            align="right"
            width={W_WIDE}
            title={`${name} model`}
          />
        </ConfigRow>
        <Divider />
        <ConfigRow title="Reasoning effort" hint="how hard it thinks on this project">
          <Dropdown
            value={effort}
            options={EFFORTS}
            onChange={(v) => { setEffort(v); setRepoEffort(repo.id, v || null).then(after).catch(() => {}) }}
            align="right"
            width={W_WIDE}
            title={`${name} reasoning effort`}
          />
        </ConfigRow>
        <Divider />
        <ConfigRow title="Auto-learning" hint="also needs the system master switch on">
          <Toggle
            on={learning}
            onChange={(v) => { setLearning(v); setRepoLearning(repo.id, v).then(after).catch(() => {}) }}
          />
        </ConfigRow>
      </Card>
    </>
  )
}
