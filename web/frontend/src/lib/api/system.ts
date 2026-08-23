import { getJSON, sendJSON } from './client'
import type { Schema } from './generated'

// The system spine's read surface: what the system IS, what it HAS, and what it is DOING.
//
// Transport shapes come from the daemon OpenAPI; the one override is the scope keyset the UI
// indexes by.

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

// `global` is aliased on the wire because it is a reserved word, so the generated type carries
// `global`.
export type TokenUsage = Schema<'TokenUsageResponse'>
export type RepoTokens = Schema<'RepoTokens'>
export type TokenBucket = Schema<'TokenBucket'>
export type TokenTypeSplit = Schema<'TokenTypeSplit'>
export type CategoryNode = Schema<'CategoryNode'>
export type TokenTimeseries = Schema<'TokenTimeseriesResponse'>
export type TokenDay = Schema<'TokenDay'>

// Whether SuperMe holds an Anthropic credential at all. Every agent action reads this: with no
// credential the daemon refuses the run outright, so the surface greys it rather than letting the
// owner start something that cannot finish.
export type AuthStatus = Schema<'AuthStatusResponse'>

export function getAuthStatus(refresh = false): Promise<AuthStatus> {
  return getJSON(`/api/system/auth${refresh ? '?refresh=true' : ''}`)
}

export function getSystem(): Promise<SystemOverview> {
  return getJSON('/api/system')
}

export function getRepos(): Promise<RepoOverview[]> {
  return getJSON('/api/repos')
}

// --- the attention centre ---
//
// System-wide, distinct from the per-repo engine: every hold across every connected repo,
// classified by WHY it is parked. `kind` drives the action set, `actor` says who parked it.
export type SystemHoldKind = 'question' | 'escalation' | 'paged' | 'review' | 'gate'
// The four fields the tool enforces. Only `question` is guaranteed: a report predating the typed
// shape carries prose.
export type SystemHoldQuestion = { question: string; recommend?: string; why?: string; instead?: string }
export type SystemHold = {
  id: string
  title: string
  session_id: string | null // the item's own dev session — Open binds the chat to it, not the general thread
  phase: string | null
  cohort: string | null
  kind: SystemHoldKind
  reason: string
  actor: string
  questions?: SystemHoldQuestion[] // kind 'question' only — the plan agent's clarifying questions (ask-card)
}
export type SystemRepoAttention = { repo_id: string; repo_label: string; holds: SystemHold[] }
// Fail-soft: a down daemon should never blank the whole shell, so the bell shows nothing until the
// feed resolves.
export function getSystemAttention(): Promise<SystemRepoAttention[]> {
  return getJSON<SystemRepoAttention[]>('/api/system/attention').catch(() => [])
}

// --- connect a domain (register a new repo) + the folder picker it uses -----------------------
export type FsBrowse = Schema<'FsBrowseResponse'>
export type ConnectedRepo = Schema<'RepoConnectResponse'>

// Browse directories under the owner's home (bounded there) for the connect folder picker.
export function browseFs(path?: string): Promise<FsBrowse> {
  return getJSON(`/api/fs/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`)
}
// `new` creates an empty dir, `existing` points at code. The choice is stored and drives the
// onboarding front door.
export function connectRepo(body: { path: string; label?: string; kind: 'new' | 'existing' }): Promise<ConnectedRepo> {
  return sendJSON('/api/repos', 'POST', body)
}

export type DisconnectReceipt = Schema<'RepoDisconnectResponse'>
// IRREVERSIBLE: forgets the registration, knowledge home, harness cell and sessions, though run
// traces are preserved.
//
// The project folder itself is untouched. `confirm` must be the repo id.
export function disconnectRepo(id: string): Promise<DisconnectReceipt> {
  return sendJSON(`/api/repos/${id}?confirm=${encodeURIComponent(id)}`, 'DELETE')
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

// Set or clear one repo's model override, for one ROLE. Omit `role` for the project's own tier;
// `vet` is the reviewer's, which resolves on its own chain and never inherits the project's.
export function setRepoModel(repoId: string, model: ModelAlias | null, role = 'default'): Promise<ModelSetResult> {
  return sendJSON(`/api/repos/${encodeURIComponent(repoId)}/model`, 'POST', { model, role })
}

type EffortSetResult = { ok: boolean; effort: string | null; effective: string }

// Set or clear one repo's reasoning-effort override, for one ROLE — see setRepoModel.
export function setRepoEffort(repoId: string, effort: string | null, role = 'default'): Promise<EffortSetResult> {
  return sendJSON(`/api/repos/${encodeURIComponent(repoId)}/effort`, 'POST', { effort, role })
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

// The global deputy dial: whether a deputy judges autopilot gates, and how
// readily it escalates PER GATE (triage/plan/review, each low·medium·high·extra). Partial — send
// only what changes; `strictness` is a partial map (only the gates that moved).
export function setSystemDeputy(
  // The deputy's OWN tier: one judge across every project, set here and never following one.
  patch: { enabled?: boolean; strictness?: Record<string, string>; model?: string | null; effort?: string | null },
): Promise<{ ok: boolean; deputy_enabled: boolean; deputy_strictness: Record<string, string>
             deputy_model: string | null; deputy_effort: string | null }> {
  return sendJSON('/api/system/deputy', 'POST', patch)
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

// Compaction runtime knobs: the fill % at which a work-item session
// auto-compacts (per-kind overrides) + the effectiveness threshold. The backend refuses (409)
// any trigger at/below the incompressible floor (floor_pct) — the knob is safe by construction.
export type CompactionConfig = Schema<'CompactionConfigResponse'>
export function getCompactionConfig(): Promise<CompactionConfig> {
  return getJSON('/api/system/compaction')
}
export function setCompactionConfig(patch: { trigger_pct?: number; by_kind?: Record<string, number>; min_gain_pct?: number | 'auto' }): Promise<CompactionConfig> {
  return sendJSON('/api/system/compaction', 'POST', patch)
}

// The two knobs that govern how work lands: whether the diff gets its own review gate, and what it
// lands on.
export type RepoGit = Schema<'RepoGitResponse'>
export function setRepoGit(repoId: string, patch: { review_mode?: string; anchor_branch?: string }): Promise<RepoGit> {
  return sendJSON(`/api/repos/${encodeURIComponent(repoId)}/git`, 'POST', patch)
}

// Read live off git: the anchor REFUSES on a branch that does not exist, so a stale list offers a
// failing setting.
export type RepoBranches = Schema<'RepoBranchesResponse'>
export function getRepoBranches(repoId: string): Promise<RepoBranches> {
  return getJSON(`/api/repos/${encodeURIComponent(repoId)}/branches`)
}

// Set a repo's VISUAL tag (owner-defined color + icon). Omit a field (undefined) to leave it as-is;
// pass '' to clear it (back to the hashed-palette default / no icon).
export type RepoMeta = Schema<'RepoMetaResponse'>
export function setRepoMeta(repoId: string, patch: { color?: string; icon?: string }): Promise<RepoMeta> {
  return sendJSON(`/api/repos/${encodeURIComponent(repoId)}/meta`, 'POST', patch)
}
