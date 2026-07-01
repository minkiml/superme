import { getJSON, sendJSON } from './client'
import type { Schema } from './generated'

// The system spine read-surface (PRD §4.11.3, WI-5). Three read-only views over the
// System / Repo / Session / Run model: what the system IS (config), what it HAS (the repo ×
// scope lattice), and what it's DOING / HAS DONE (live + recent runs). Powers the Monitor.
//
// Transport shapes come from the daemon OpenAPI (`Schema<...>`). After R5 the backend pins the
// run mode/status enums to Literals, so these derive directly — the only remaining override is
// `RepoOverview.scopes`, narrowed to the {core, dev} keyset the UI indexes by.

export type Scope = 'core' | 'dev'

// One in-flight or finished execution (RunRow.mode/status are now Literal unions from OpenAPI).
export type Run = Schema<'RunRow'>

export type SystemOverview = Schema<'SystemResponse'>

// A model alias the UI offers. `null` means "inherit the next level down" (clear the override).
export type ModelAlias = 'haiku' | 'sonnet' | 'opus'

// A repo×scope cell: the home pointers (target relocation paths) + computed live status + counts.
export type ScopeStatus = Schema<'RepoScope'>

export type RepoOverview = Omit<Schema<'RepoOverview'>, 'scopes'> & { scopes: Record<Scope, ScopeStatus> }

export type RunsData = Schema<'RunsResponse'>

export function getSystem(): Promise<SystemOverview> {
  return getJSON('/api/system')
}

export function getRepos(): Promise<RepoOverview[]> {
  return getJSON('/api/repos')
}

export function getRuns(contextId?: string, limit = 50): Promise<RunsData> {
  const ctx = contextId ? `context_id=${encodeURIComponent(contextId)}&` : ''
  return getJSON(`/api/runs?${ctx}limit=${limit}`)
}

type ModelSetResult = { ok: boolean; model: string | null; effective: string | null }

// Set (model alias) or clear (null) the system-wide default model — the floor below per-repo
// overrides. Clearing falls back to config/system.yaml, then the host default.
export function setSystemModel(model: ModelAlias | null): Promise<ModelSetResult> {
  return sendJSON('/api/system/model', 'POST', { model })
}

// Set or clear one repo's model override. Clearing falls back to the system default.
export function setRepoModel(repoId: string, model: ModelAlias | null): Promise<ModelSetResult> {
  return sendJSON(`/api/repos/${encodeURIComponent(repoId)}/model`, 'POST', { model })
}

// Flip the automatic-learning master switch (idle / phase / completion capture sweeps). Off by
// default — capture is fully automatic, so this governs all of it.
export function setSystemLearning(enabled: boolean): Promise<{ ok: boolean; learning_enabled: boolean }> {
  return sendJSON('/api/system/learning', 'POST', { enabled })
}
