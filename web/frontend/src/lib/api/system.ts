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

// System-wide token usage (v1 observability): global bucket + one per repo. `global` is aliased
// to `global_` on the wire (reserved word), so the generated type carries `global`.
export type TokenUsage = Schema<'TokenUsageResponse'>
export type RepoTokens = Schema<'RepoTokens'>
export type TokenBucket = Schema<'TokenBucket'>
export type TokenTypeSplit = Schema<'TokenTypeSplit'>
export type CategoryNode = Schema<'CategoryNode'>
export type TokenTimeseries = Schema<'TokenTimeseriesResponse'>
export type TokenDay = Schema<'TokenDay'>

export function getSystem(): Promise<SystemOverview> {
  return getJSON('/api/system')
}

export function getRepos(): Promise<RepoOverview[]> {
  return getJSON('/api/repos')
}

export function getTokens(): Promise<TokenUsage> {
  return getJSON('/api/tokens')
}

// Per-day token usage for the trend graph. Sends the browser's local tz as minutes to ADD to UTC
// (−getTimezoneOffset()), so the daemon buckets days on the owner's local day.
export function getTokenTimeseries(): Promise<TokenTimeseries> {
  const tz = -new Date().getTimezoneOffset()
  return getJSON(`/api/tokens/timeseries?tz_offset=${tz}`)
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

type EffortSetResult = { ok: boolean; effort: string | null; effective: string }

// Set (low|medium|high) or clear (null) the system-wide default reasoning effort — the floor
// below per-repo overrides. Clearing falls back to config/system.yaml, then "medium".
export function setSystemEffort(effort: string | null): Promise<EffortSetResult> {
  return sendJSON('/api/system/effort', 'POST', { effort })
}

// Set or clear one repo's reasoning-effort override. Clearing falls back to the system default.
export function setRepoEffort(repoId: string, effort: string | null): Promise<EffortSetResult> {
  return sendJSON(`/api/repos/${encodeURIComponent(repoId)}/effort`, 'POST', { effort })
}

// Opt one repo in/out of automatic capture (the global master switch still gates everything).
export function setRepoLearning(repoId: string, enabled: boolean): Promise<{ ok: boolean; repo_id: string; learning_enabled: boolean }> {
  return sendJSON(`/api/repos/${encodeURIComponent(repoId)}/learning`, 'POST', { enabled })
}

// Flip the automatic-learning master switch (idle / phase / completion capture sweeps). Off by
// default — capture is fully automatic, so this governs all of it.
export function setSystemLearning(enabled: boolean): Promise<{ ok: boolean; learning_enabled: boolean }> {
  return sendJSON('/api/system/learning', 'POST', { enabled })
}

// Tune the capture-sweep triggers: idle threshold (s), heartbeat cadence (s), and the min new
// user-message gate. Omit a field to leave it unchanged. Takes effect without a daemon restart.
export type SweepConfig = Schema<'SweepConfigResponse'>
export function setSweepConfig(patch: { idle_seconds?: number; poll_seconds?: number; min_user_msgs?: number }): Promise<SweepConfig> {
  return sendJSON('/api/system/sweep', 'POST', patch)
}

// Set a repo's VISUAL tag (owner-defined color + icon). Omit a field (undefined) to leave it as-is;
// pass '' to clear it (back to the hashed-palette default / no icon).
export type RepoMeta = Schema<'RepoMetaResponse'>
export function setRepoMeta(repoId: string, patch: { color?: string; icon?: string }): Promise<RepoMeta> {
  return sendJSON(`/api/repos/${encodeURIComponent(repoId)}/meta`, 'POST', patch)
}
