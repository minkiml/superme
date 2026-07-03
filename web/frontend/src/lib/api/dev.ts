import { getJSON, sendJSON } from './client'
import type { Schema } from './generated'

// Dev knowledge — the dev-dashboard surface (v2 work-item model, D-018). One context's
// work-items/ tree (each folder a work-item, nesting = branch-off) + the inbox queue +
// the BE-derived glance view.
//
// Transport shapes derive from the daemon OpenAPI (`Schema<...>`, regenerate via `npm run gen:api`).
// STRICT schemas are derived directly; LOOSE schemas (`extra="allow"`: WorkItem, the memory
// Proposal, the model manifest, the glance) stay as hand-written VIEW types — the backend itself
// permits any key on those until R5 tightens them — with FE enum-narrowing layered on top.

export type WorkPhase = 'plan_design' | 'build_eval' | 'done'
export type WorkStatus = 'queued' | 'in_progress' | 'waiting' | 'dropped'

// An item.md `artifacts` entry, NORMALIZED on read by the daemon (R5): always `{type, path}` on the
// wire now (the legacy bare-string form is coerced server-side). `artifactPath` stays as the accessor.
export type Artifact = Schema<'ArtifactRef'>
export function artifactPath(a: Artifact): string {
  return a.path
}

// VIEW type over Schema<'WorkItem'> (extra='allow'). Carries the base item.md fields plus the
// BE-derived tree fields (blocked/depth/children) and run telemetry the daemon attaches at read
// time. Kept hand-written so the FE can rely on narrowed phase/status/children (R5 will pin these
// server-side, at which point this can re-derive from the generated type directly).
export type WorkItem = {
  id: string
  root_id: string
  parent_id: string | null // presence = a branch-off
  title?: string
  description?: string // the item.md body
  phase: WorkPhase
  status: WorkStatus | null // null when completed (done_at) or unset
  done_at?: string | null // ticked out of the Done phase = completed
  artifacts?: Artifact[] // normalized {type, path} refs (R5); read the path via artifactPath
  blocked_by?: string[]
  session_id?: string | null // the agent session this item originated in (session:<id>)
  created_at?: string | null
  updated_at?: string | null
  // BE-derived (never stored):
  blocked: boolean // an unresolved blocked_by dependency
  depth: number
  children: string[] // child (branch-off) ids
  folder?: string
  // Run telemetry (daemon-attached, never stored in item.md):
  running?: boolean // an agent run is in flight on this item right now
  run_started_at?: number | null // epoch seconds the current run began (for a live timer)
  run_tokens?: number | null // live token count for the current run
  run_model?: string | null // model of the in-flight run
  run_context_pct?: number | null // live context-window fill of the in-flight run
  model?: string | null // model to show on the card (live run's, else last run's)
  effort?: string | null // configured reasoning effort (low|medium|high) its runs use
  context_pct?: number | null // context fill to show on the card (live run's, else last run's)
  total_tokens?: number // accumulated tokens across all finished runs
  last_run?: { tokens: number; duration_ms: number | null; model?: string | null; context_pct?: number | null } | null
  tasks?: { done: number; total: number } | null // tasks.md checklist progress (null = no tasks.md)
}

export type InboxKind = 'note' | 'idea' | 'todo' | 'question'
// open = awaiting push · pushed = promoted to a work-item. (Drop = hard delete, not a status.)
export type InboxStatus = 'open' | 'pushed'
export type InboxOrigin = 'user' | 'agent' // who created it (user-made vs agent branch-off proposal)

// Derived straight from the transport type — kind/status/origin are now backend Literals (R5).
export type InboxEntry = Schema<'InboxRow'>

export type DevGlance = {
  by_status: Record<string, number> // queued/in_progress/waiting/dropped/done counts
  by_phase: Record<string, number>
  in_progress: { id: string; title?: string }[]
  waiting: { id: string; blocked_by: string[] }[]
  blocked: { id: string; blocked_by: string[] }[]
  inbox_open: number
  counts: { work_items: number }
}

export type DevData = {
  context_id: string
  root: string
  exists: boolean
  work_items: WorkItem[]
  inbox: InboxEntry[]
  glance: DevGlance
  running?: string[] // work-item ids with a headless /plan turn in flight
}

// The event LOG (PRD §4.9) — the append-only activity firehose. `scope`/`item_id` give the
// containment read: item view = events for one work-item; dev view = all repo events incl.
// dev-native (item_id null: inbox triage / merges / cleanups).
// Derived straight from the transport types — scope/actor are now backend Literals (R5).
export type DevEvent = Schema<'DevLogEvent'>
export type DevLogData = Schema<'DevLogResponse'>

// Selective read over the log — never a full dump. Pass `itemId` for one item's timeline,
// `since`/`until` (ISO) for a date window (e.g. "what happened yesterday").
export function getDevLog(
  contextId = 'global',
  opts: { since?: string; until?: string; scope?: string; itemId?: string; limit?: number } = {},
): Promise<DevLogData> {
  const p = new URLSearchParams({ context_id: contextId })
  if (opts.since) p.set('since', opts.since)
  if (opts.until) p.set('until', opts.until)
  if (opts.scope) p.set('scope', opts.scope)
  if (opts.itemId) p.set('item_id', opts.itemId)
  if (opts.limit) p.set('limit', String(opts.limit))
  return getJSON(`/api/dev/log?${p.toString()}`)
}

// (The legacy dev/memory applied-fact store + its client fns are retired — WI-8. Learned content is
// constitution/skill/agent, surfaced via the Published inventory.)

// Manage-Harness "Skills & Agents" tab — SuperMe's OWN universal skills/agents per scope.
export type HarnessEntry = {
  kind: 'skill' | 'agent'
  name: string
  description: string
  category?: string | null
  model?: string | null
  tools?: string | null
}
export type HarnessScope = {
  scope: 'dev' | 'core' | 'shared'
  label: string
  plugin: string
  note: string
  skills: HarnessEntry[]
  agents: HarnessEntry[]
}
export function getHarnessPlugins(): Promise<{ scopes: HarnessScope[] }> {
  return getJSON('/api/dev/harness/plugins')
}
// Foundations — SuperMe's universal identity + charter files (SELF.md + per-mode charters,
// hand-authored + editable) plus the LEARNED universal constitution (always-on rules), per mode.
export type FoundationFile = Schema<'FoundationFile'>
export type FoundationConstitution = Schema<'ConstitutionEntry'>
export function getFoundation(): Promise<{ files: FoundationFile[]; constitutions: FoundationConstitution[] }> {
  return getJSON('/api/dev/harness/foundation')
}
// Save an identity/charter file (key = self | dev-charter | core-charter). Effective next turn.
export function saveFoundationFile(key: string, content: string): Promise<{ ok: boolean; key: string }> {
  return sendJSON('/api/dev/harness/foundation', 'PUT', { key, content })
}

export type HarnessFile = { scope: string; kind: string; name: string; path: string; content: string }
export function getHarnessFile(scope: string, kind: string, name: string): Promise<HarnessFile> {
  return getJSON(`/api/dev/harness/plugin-file?scope=${q(scope)}&kind=${q(kind)}&name=${q(name)}`)
}
export function saveHarnessFile(scope: string, kind: string, name: string, content: string): Promise<{ ok: boolean }> {
  return sendJSON('/api/dev/harness/plugin-file', 'PUT', { scope, kind, name, content })
}

// Published inventory (#6 runtime management) — the LEARNED artifacts the owner published, keyed by
// their proposal (so SuperMe's shipped harness skills never appear). Constitution disables via a
// frontmatter flag the loader honors; skills/agents disable by moving into a `.disabled/` shadow the
// plugin scanner ignores. Delete removes the file and retires the proposal (history kept).
export type PublishedForm = 'constitution' | 'skill' | 'agent'
export type PublishedItem = {
  proposal_id: number
  form: PublishedForm
  scope: ProposalScope
  slug: string
  title: string
  summary?: string | null
  created?: string | null
  present: boolean
  enabled: boolean
}
// The chat "/" palette for a (context, mode) — mode-correct, fresh from disk, internal
// (category:learning) skills filtered out, learned skills included. See daemon /dev/palette.
export function getPalette(contextId = 'global', mode: 'core' | 'dev' = 'dev'): Promise<{ context_id: string; mode: string; commands: string[] }> {
  return getJSON(`/api/dev/palette?context_id=${q(contextId)}&mode=${q(mode)}`)
}
export function getPublished(contextId = 'global'): Promise<{ context_id: string; published: PublishedItem[] }> {
  return getJSON(`/api/dev/harness/published?context_id=${q(contextId)}`)
}
export function togglePublished(
  proposalId: number,
  enabled: boolean,
  contextId = 'global',
): Promise<{ ok: boolean; proposal_id: number; present: boolean; enabled: boolean }> {
  return sendJSON(`/api/dev/harness/published/${proposalId}`, 'PATCH', { enabled, context_id: contextId })
}
export function deletePublished(
  proposalId: number,
  contextId = 'global',
): Promise<{ ok: boolean; proposal_id: number; removed: boolean }> {
  return sendJSON(`/api/dev/harness/published/${proposalId}?context_id=${q(contextId)}`, 'DELETE', undefined)
}
// Read / edit a published artifact's raw markdown source (Published-tab preview + edit).
export type PublishedFile = Schema<'PublishedFileResponse'>
export function getPublishedFile(proposalId: number, contextId = 'global'): Promise<PublishedFile> {
  return getJSON(`/api/dev/harness/published/${proposalId}/file?context_id=${q(contextId)}`)
}
export function savePublishedFile(proposalId: number, content: string, contextId = 'global'): Promise<{ ok: boolean; proposal_id: number }> {
  return sendJSON(`/api/dev/harness/published/${proposalId}/file`, 'PUT', { content, context_id: contextId })
}
// Manage-Harness stat tiles — the candidate pool + learned-knowledge gauges, with drill-down.
// All strict transport types (the stats payload is a fixed shape), derived directly.
export type CandidateItem = Schema<'CandidateStatItem'>
export type KnowledgeItem = Schema<'KnowledgeStatItem'>
export type MemoryStats = Schema<'MemoryStatsResponse'>
export type DistillResult = Omit<Schema<'DistillResponse'>, 'status'> & {
  status: 'started' | 'already_running' | 'no_candidates'
}
export function getMemoryStats(contextId = 'global'): Promise<MemoryStats> {
  return getJSON(`/api/dev/memory/stats?context_id=${q(contextId)}`)
}
// Per-repo learning rollup — candidate + learned-artifact counts across all repos, each split
// dev/core, plus the cross-repo totals. Powers the Learning tile drill-in.
export type LearningRollup = Schema<'LearningRollupResponse'>
export function getLearningRollup(): Promise<LearningRollup> {
  return getJSON('/api/dev/memory/rollup')
}
export function runDistill(contextId = 'global'): Promise<DistillResult> {
  return sendJSON(`/api/dev/memory/distill?context_id=${q(contextId)}`, 'POST', {})
}

// Tier-C review queue (PRD §4.10) — the `distill` sub-agent files proposals; the owner gate
// (accept → apply to memory/, reject) lives here.
// `output_form` is a loose backend string (R5 will pin it to the WI-8 set: constitution|skill|agent).
// The pre-WI-8 `ProposalForm` ('fact' | 'contract' | …) and `RecallType` enums are RETIRED (WI-8) —
// decision/reference are now auto-accrued knowledge, not gated proposals.
export type ProposalScope = 'repo_dev' | 'universal_dev' | 'core'
export type ProposalStatus =
  | 'proposed' | 'writing' | 'drafted' | 'published' | 'rejected' | 'dropped' | 'superseded' | 'retired'
export type EvalReport = {
  form?: string
  verdict?: 'pass' | 'warn' | 'fail' | 'skipped' | 'unknown' | string
  summary?: string | string[] // bullets (new) or a single sentence (legacy)
  checks?: { name?: string; score?: number; note?: string }[]
  issues?: { severity?: 'high' | 'low' | string; what?: string; fix?: string }[]
  // The artifact's OWN run cost on a synthetic task. kind 'run' = skill/agent measured once
  // (tokens/time/cost); kind 'overhead' = constitution's always-on per-turn token cost (no run).
  metrics?: {
    kind?: 'run' | 'overhead'
    // run footprint (skill/agent): the honest figures, not the misleading cumulative re-read total
    context_tokens?: number // working set the worker held per turn (~ what the context showed)
    output_tokens?: number // net new text generated across the run
    turns?: number // agentic steps taken
    duration_s?: number
    cost_usd?: number
    tokens_per_turn?: number // constitution overhead (always-on per-turn cost)
    capped?: string // soft outcome (e.g. 'error_max_turns') — the figure is a floor, not a full run
    error?: string
    tokens?: number // legacy (pre-footprint proposals)
  }
  trial_task?: string // the synthetic task the run cost was measured on
  // what the review (judging) call itself cost — fine print, kept separate from the artifact's run
  eval_overhead?: { cost_usd?: number; duration_s?: number; tokens?: number }
  strengths?: string[] // legacy; no longer surfaced
}
export type ProposalCandidate = {
  id: number
  signal: string
  rationale: string | null
  evidence: string | string[] | null
  form_hint: string | null
  scope_hint: string | null
  status: string | null
  source: string | null
  captured_at: string | null
}
// VIEW type over Schema<'Proposal'> (extra='allow', rich JSON columns). Kept hand-written so the FE
// can rely on the structured fields/eval_report/candidates shapes the rows actually carry.
export type MemoryProposal = {
  id: number
  context_id: string
  created_at: string
  output_form: string // loose (WI-8 set: constitution|skill|agent; R5 pins it)
  target_scope: ProposalScope | string
  recall_type: string | null // retired field, still column-present on legacy rows
  apply_target: string | null
  cluster: string | null
  title: string
  body: string
  summary: string | null
  rationale: string | null
  confidence: string | null
  fields: Record<string, unknown> | null
  clarifications: { question: string; suggested?: string; blocking?: boolean }[] | null
  clarification_answers?: Record<string, string> | { question: string; answer: string }[] | null
  staged_artifact?: string | null
  staged_path?: string | null
  eval_report?: EvalReport | null
  candidate_ids: number[]
  candidates?: ProposalCandidate[]
  status: ProposalStatus | string
}
export type ProposalsData = { context_id: string; proposals: MemoryProposal[] }
export function getProposals(contextId = 'global', status?: string): Promise<ProposalsData> {
  const s = status ? `&status=${q(status)}` : ''
  return getJSON(`/api/dev/memory/proposals?context_id=${q(contextId)}${s}`)
}
// Gate 1 — approve the intent (+ optional clarification answers) → fires the write run (→ writing).
export function approveProposal(
  id: number,
  contextId = 'global',
  answers?: Record<string, string> | { question: string; answer: string }[],
): Promise<{ status: string; proposal_id: number }> {
  return sendJSON(`/api/dev/memory/proposals/${id}/approve`, 'POST', { context_id: contextId, answers })
}
// A proposal's execution trace — the lifecycle timeline (filed → approved → forge run → drafted →
// edited → published), reused from the events table keyed on meta.proposal_id.
export type ProposalStep = {
  kind: string
  actor: string
  summary: string
  created_at?: string | null
  meta?: Record<string, unknown> | null
}
export function getProposalExecution(
  id: number,
  contextId = 'global',
): Promise<{ proposal_id: number; status: string; steps: ProposalStep[] }> {
  return getJSON(`/api/dev/memory/proposals/${id}/execution?context_id=${q(contextId)}`)
}
// Gate 2 (refine) — edit the staged artifact before publishing (stays `drafted`).
export function updateStagedArtifact(
  id: number,
  content: string,
  contextId = 'global',
): Promise<{ ok: boolean; proposal: MemoryProposal }> {
  return sendJSON(`/api/dev/memory/proposals/${id}/artifact`, 'PATCH', { content, context_id: contextId })
}
// Gate 2 — publish the staged artifact to its live home (→ published).
export function publishProposal(
  id: number,
  contextId = 'global',
): Promise<{ ok: boolean; path: string; proposal: MemoryProposal }> {
  return sendJSON(`/api/dev/memory/proposals/${id}/publish`, 'POST', { context_id: contextId })
}
export function rejectProposal(
  id: number,
  contextId = 'global',
): Promise<{ ok: boolean; proposal: MemoryProposal }> {
  return sendJSON(`/api/dev/memory/proposals/${id}/reject`, 'POST', { context_id: contextId })
}
export function dropProposal(
  id: number,
  contextId = 'global',
): Promise<{ ok: boolean; proposal: MemoryProposal }> {
  return sendJSON(`/api/dev/memory/proposals/${id}/drop`, 'POST', { context_id: contextId })
}

const q = encodeURIComponent

export function getDev(contextId = 'global'): Promise<DevData> {
  return getJSON(`/api/dev?context_id=${q(contextId)}`)
}

// Quick-capture: add an item to the context's inbox queue (no approval gate). Title is
// entered manually; origin defaults to 'user' (manual capture).
export function addInbox(
  input: { text: string; title?: string | null; kind?: InboxKind; tag?: string | null; origin?: InboxOrigin },
  contextId = 'global',
): Promise<InboxEntry> {
  return sendJSON('/api/dev/inbox', 'POST', {
    text: input.text,
    title: input.title ?? null,
    kind: input.kind ?? 'note',
    tag: input.tag ?? null,
    origin: input.origin ?? 'user',
    context_id: contextId,
  })
}

// Edit an inbox item: change title, text, kind, tag, or status.
export function updateInbox(
  id: number,
  patch: Partial<Pick<InboxEntry, 'status' | 'kind' | 'tag' | 'text' | 'title' | 'routed_to'>>,
): Promise<InboxEntry> {
  return sendJSON(`/api/dev/inbox/${id}`, 'PATCH', patch)
}

export function deleteInbox(id: number): Promise<{ ok: boolean; id: number }> {
  return sendJSON(`/api/dev/inbox/${id}`, 'DELETE')
}

// Push an inbox item to the workspace: stamps a queued work-item, marks the row pushed.
export type PushResult = { ok: boolean; work_item: { id: string; folder: string }; inbox: InboxEntry }
export function pushInbox(id: number, contextId = 'global'): Promise<PushResult> {
  return sendJSON(`/api/dev/inbox/${id}/push`, 'POST', { context_id: contextId })
}

// "Plan it" — fire a headless /plan turn for a queued work-item. Returns immediately; the
// agent works in the background and the item's status/artifacts update on their own. Poll
// getDev (DevData.running) for the live planning state.
export type PlanResult = Schema<'PlanResponse'>
// `model` is the per-run model choice (e.g. 'sonnet' | 'haiku' | 'opus'); omit for the
// default (latest Sonnet).
export function planWorkItem(itemId: string, contextId = 'global', model?: string, effort?: string): Promise<PlanResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/plan`, 'POST', {
    context_id: contextId,
    model: model ?? null,
    effort: effort ?? null,
  })
}

// Hard-delete a plan/design work-item and erase its trace (folder + session transcript +
// originating inbox row). Backend refuses (409) once the item leaves plan_design.
export type DeleteResult = Schema<'WorkItemDeleteResponse'>
export function deleteWorkItem(itemId: string, contextId = 'global'): Promise<DeleteResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}?context_id=${q(contextId)}`, 'DELETE')
}

// A work-item's review payload — the structured artifact content the review popup renders:
// plan.md / prd.md as Markdown bodies (frontmatter stripped), tasks.md as a checklist.
export type TaskItem = Schema<'TaskItem'>
export type WorkItemDetail = {
  item: WorkItem
  plan: string | null
  prd: string | null
  tasks: TaskItem[] | null
  execution: string | null // the archived execution trace (present once completed)
}
export function getWorkItemDetail(itemId: string, contextId = 'global'): Promise<WorkItemDetail> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/detail?context_id=${q(contextId)}`)
}

// One tool / sub-agent / skill call an item's run made — the CLI-style call-trail.
// Derived straight from the transport type — `kind` is now a backend Literal (R5).
export type RunArtifact = Schema<'ArtifactCall'>
export function getWorkItemArtifacts(itemId: string, contextId = 'global'): Promise<{ artifacts: RunArtifact[] }> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/artifacts?context_id=${q(contextId)}`)
}

// Approve → advance a work-item to its next phase (plan_design → build_eval today). The
// owner's forward gate; backend 409s if there's no next phase or a run is in flight.
export type AdvanceResult = Omit<Schema<'WorkItemAdvanceResponse'>, 'phase' | 'from'> & {
  phase: WorkPhase
  from: WorkPhase
}
export function advanceWorkItem(itemId: string, contextId = 'global'): Promise<AdvanceResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/advance?context_id=${q(contextId)}`, 'POST')
}

// Complete + archive a Done-phase item (the tick-out): snapshots the execution trace to a file,
// stamps done_at, and frees the session transcript + run/run_artifact rows to reclaim disk.
export type CompleteResult = Schema<'WorkItemCompleteResponse'>
export function completeWorkItem(itemId: string, contextId = 'global'): Promise<CompleteResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/complete?context_id=${q(contextId)}`, 'POST')
}

// Configure the model a work-item's runs use (plan + bound chat) — reconfigurable anytime
// from the review popup; persisted to item.md frontmatter.
export function setWorkItemModel(itemId: string, model: string, contextId = 'global'): Promise<{ ok: boolean; id: string; model: string }> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/model`, 'POST', { context_id: contextId, model })
}

// Configure the reasoning effort a work-item's runs use (plan + bound chat) — reconfigurable
// anytime from the review popup; persisted to item.md frontmatter.
export function setWorkItemEffort(itemId: string, effort: string, contextId = 'global'): Promise<{ ok: boolean; id: string; effort: string }> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/effort`, 'POST', { context_id: contextId, effort })
}
