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

// --- connect a domain (register a new repo) + the folder picker it uses -----------------------
export type FsBrowse = Schema<'FsBrowseResponse'>
export type ConnectedRepo = Schema<'RepoConnectResponse'>

// Browse directories under the owner's home (bounded there) for the connect folder picker.
export function browseFs(path?: string): Promise<FsBrowse> {
  return getJSON(`/api/fs/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`)
}
// Register a new repo. kind='new' creates an (empty) dir → project-init; 'existing' points at
// code → retrofit. The choice is stored and drives the repo's onboarding front door.
export function connectRepo(body: { path: string; label?: string; kind: 'new' | 'existing' }): Promise<ConnectedRepo> {
  return sendJSON('/api/repos', 'POST', body)
}

export function getTokens(): Promise<TokenUsage> {
  return getJSON('/api/tokens')
}

// TEMPORARY internals inventory — live snapshot of the DB schemas + agent tool surface (Internals
// tab). Types inlined (not codegen) since this whole surface is a deletable scaffold.
export type InvColumn = { name: string; type: string; pk: boolean; notnull: boolean }
export type InvTable = { name: string; columns: InvColumn[]; sql: string | null; rows: number | null }
export type InvDatabase = { name: string; path: string; present: boolean; tables: InvTable[] }
export type InvTool = { name: string; description: string; surface: string; params: { name: string; required: boolean }[] }
export type Inventory = { databases: InvDatabase[]; tools: InvTool[] }
export function getInventory(): Promise<Inventory> {
  return getJSON('/api/system/inventory')
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

// One run's per-run event trail (prompt · reply · tool/skill/agent calls) for the Activity trace popup.
export type RunEvent = Schema<'RunEventRow'>
export type RunTrace = Schema<'RunTraceResponse'>
export function getRunTrace(runId: number): Promise<RunTrace> {
  return getJSON(`/api/runs/${runId}/trace`)
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

// The tunable background agents (sweep/distill/write) — each runs on a code-level preset model,
// optionally overridden by the owner. Reading returns each agent's preset, override, and effective
// concrete model; writing sets (or clears, with null) one agent's override.
export type AgentModels = Schema<'AgentModelsResponse'>
export type AgentModelRow = Schema<'AgentModelRow'>
export function getAgentModels(): Promise<AgentModels> {
  return getJSON('/api/system/agent-models')
}
export function setAgentModel(feature: string, model: string | null): Promise<AgentModels> {
  return sendJSON(`/api/system/agent-models/${encodeURIComponent(feature)}`, 'POST', { model })
}
export function setAgentEffort(feature: string, effort: string): Promise<AgentModels> {
  return sendJSON(`/api/system/agent-models/${encodeURIComponent(feature)}`, 'POST', { effort })
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
