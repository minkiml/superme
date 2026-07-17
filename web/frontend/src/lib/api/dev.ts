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

// Workspace-workflow enums (S1, 2026-07-15). `phase` = the per-KIND pipeline stage
// (implementation: triage→plan→build→validate→deliver→close · research: triage→plan→investigate→
// report→close; the union type below). `status` = the runnable axis — only `awaiting_human` pages
// the owner; running-right-now is derived from live runs, not a status. `outcome` stamps how a
// terminal (status done) item ended.
export type WorkKind = 'implementation' | 'research'
export type WorkPhase =
  | 'triage' | 'plan' | 'build' | 'validate' | 'deliver' | 'investigate' | 'report' | 'close'
export type WorkStatus = 'active' | 'awaiting_child' | 'awaiting_human' | 'done'
export type WorkOutcome = 'completed' | 'abandoned' | 'superseded'
export type SpawnRelation = 'blocking' | 'parallel' | 'spawn'
// D3 branch-off provenance, child-side: which item this one spawned from and how.
export type SpawnedFrom = { item: string; relation: SpawnRelation; note?: string | null }

// An item.md `artifacts` entry, NORMALIZED on read by the daemon (R5): always `{type, path}` on the
// wire now (the legacy bare-string form is coerced server-side). `artifactPath` stays as the accessor.
export type Artifact = Schema<'ArtifactRef'>
export function artifactPath(a: Artifact): string {
  return a.path
}

// VIEW type over Schema<'WorkItem'> (extra='allow'). Carries the base item.md fields plus the
// BE-derived tree fields (depth/children) and run telemetry the daemon attaches at read
// time. Kept hand-written so the FE can rely on narrowed phase/status/children (R5 will pin these
// server-side, at which point this can re-derive from the generated type directly).
export type WorkItem = {
  id: string
  root_id: string
  parent_id: string | null // presence = a branch-off
  wave?: string | null // anchor-scaffold pointer on a root: the roadmap wave this item instances
  deliverable?: string | null // …or a deliverable directly when no wave applies (set in S2)
  title?: string
  description?: string // the item.md body
  kind?: WorkKind | null // machinery selector (null on pre-workflow items = implementation)
  phase: WorkPhase
  status: WorkStatus | null // runnable axis (done = terminal); null only on pre-workflow items
  outcome?: WorkOutcome | null // set with status done: how the item ended
  spawned_from?: SpawnedFrom | null // branch-off provenance edge (D3)
  superseded_by?: string | null // set when outcome = superseded
  inbox_id?: number | null // originating inbox row (trace)
  done_at?: string | null // terminal stamp
  // Git record (S4/D4) — written at build entry / the deliver merge; kept at terminal (trace):
  git_branch?: string | null
  git_worktree?: string | null
  git_base?: string | null
  git_merge_commit?: string | null
  git_merged_at?: string | null
  git_backup_ref?: string | null
  artifacts?: Artifact[] // normalized {type, path} refs (R5); read the path via artifactPath
  session_id?: string | null // the agent session this item originated in (session:<id>)
  created_at?: string | null
  updated_at?: string | null
  // BE-derived (never stored):
  depth: number
  children: string[] // child (branch-off) ids
  folder?: string
  // Run telemetry (daemon-attached, never stored in item.md):
  running?: boolean // an agent run is in flight on this item right now
  run_started_at?: number | null // epoch seconds the current run began (for a live timer)
  run_tokens?: number | null // live token count for the current run
  run_model?: string | null // model of the in-flight run
  run_ctx_pct?: number | null // live context-window fill of the in-flight run
  model?: string | null // model to show on the card (live run's, else last run's)
  effort?: string | null // configured reasoning effort (low|medium|high) its runs use
  ctx_pct?: number | null // context fill to show on the card (live run's, else last run's)
  total_tokens?: number // accumulated tokens across all finished runs
  phase_tokens?: Record<string, number> // per-phase 3-type Σ {phase → tokens}; card shows current phase's
  phase_tokens_4type?: Record<string, number> // per-phase 4-type Σ (3-type + cache_read), recorded behind
  last_run?: { tokens: number; duration_ms: number | null; model?: string | null; ctx_pct?: number | null } | null
  tasks?: { done: number; total: number } | null // tasks.md checklist progress (null = no tasks.md)
  seen_at?: string | null // owner-opened read receipt (S7 attention: terminal + unseen = unread)
}

export type InboxKind = 'note' | 'idea' | 'todo' | 'question'
// open = awaiting push · pushed = promoted to a work-item. (Drop = hard delete, not a status.)
export type InboxStatus = 'open' | 'pushed'
export type InboxOrigin = 'user' | 'agent' // who created it (user-made vs agent branch-off proposal)

// Derived straight from the transport type — kind/status/origin are now backend Literals (R5).
export type InboxEntry = Schema<'InboxRow'>

export type DevGlance = {
  by_status: Record<string, number> // active/awaiting_*/done counts
  by_phase: Record<string, number>
  active: { id: string; title?: string }[]
  awaiting_human: { id: string; title?: string }[] // the attention bucket (pages the owner)
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
// A single host's OWN local-harness skills + agents (its local-harness/<id>/dev tree) — the per-repo
// Artifacts tab. Flat (no scope split; the dev workspace is already dev-scoped).
export function getLocalPlugins(contextId = 'global'): Promise<{ context_id: string; skills: HarnessEntry[]; agents: HarnessEntry[] }> {
  return getJSON(`/api/dev/harness/local-plugins?context_id=${q(contextId)}`)
}
// Foundations — SuperMe's universal identity + charter files (SELF.md + per-mode charters,
// hand-authored + editable) plus the LEARNED universal constitution (always-on rules), per mode.
export type FoundationFile = Schema<'FoundationFile'>
// `foundational` (charter-pinned, not disable-able) is served by the daemon; extended inline until
// the next gen:api folds it into the generated ConstitutionEntry.
export type FoundationConstitution = Schema<'ConstitutionEntry'> & { foundational?: boolean }
export function getFoundation(): Promise<{ files: FoundationFile[]; constitutions: FoundationConstitution[] }> {
  return getJSON('/api/dev/harness/foundation')
}
// Save an identity/charter file (key = self | dev-charter | core-charter). Effective next turn.
export function saveFoundationFile(key: string, content: string): Promise<{ ok: boolean; key: string }> {
  return sendJSON('/api/dev/harness/foundation', 'PUT', { key, content })
}

export type HarnessFile = { scope: string; kind: string; name: string; path: string; content: string }
// `scope='local'` reads/writes a host's own local-harness tree — pass its contextId.
export function getHarnessFile(scope: string, kind: string, name: string, contextId = 'global'): Promise<HarnessFile> {
  return getJSON(`/api/dev/harness/plugin-file?scope=${q(scope)}&kind=${q(kind)}&name=${q(name)}&context_id=${q(contextId)}`)
}
export function saveHarnessFile(scope: string, kind: string, name: string, content: string, contextId = 'global'): Promise<{ ok: boolean }> {
  return sendJSON('/api/dev/harness/plugin-file', 'PUT', { scope, kind, name, content, context_id: contextId })
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
// Constitutions — govern ALL constitutions by (scope, slug), not just learning-published ones.
// A disk scan, so it covers hand-authored + system + learned alike: universal (dev) + this host's
// local. Disabling flips the `enabled` frontmatter flag the catalog + pull both honor (fully inert).
export type ManagedConstitution = Schema<'ManagedConstitution'>
export function getConstitutions(contextId = 'global'): Promise<{ context_id: string; constitutions: ManagedConstitution[] }> {
  return getJSON(`/api/dev/harness/constitutions?context_id=${q(contextId)}`)
}
export function toggleConstitution(
  slug: string,
  scope: string,
  enabled: boolean,
  contextId = 'global',
): Promise<{ ok: boolean; slug: string; scope: string; present: boolean; enabled: boolean }> {
  return sendJSON(`/api/dev/harness/constitutions/${q(slug)}`, 'PATCH', { enabled, scope, context_id: contextId })
}
// The raw markdown (frontmatter intact) of one constitution — the popup's edit source + save. The
// catalog GET returns only the stripped body, so editing pulls/saves the whole file through here.
export function getConstitutionFile(slug: string, scope: string, contextId = 'global'): Promise<{ slug: string; scope: string; path: string; content: string }> {
  return getJSON(`/api/dev/harness/constitution-file?slug=${q(slug)}&scope=${q(scope)}&context_id=${q(contextId)}`)
}
export function saveConstitutionFile(slug: string, scope: string, content: string, contextId = 'global'): Promise<{ ok: boolean; slug: string; scope: string }> {
  return sendJSON('/api/dev/harness/constitution-file', 'PUT', { slug, scope, content, context_id: contextId })
}

// Asset pool — opt-in constitutional knowledge, adopted + enabled PER REPO (no body copy).
export type AssetItem = Schema<'AssetItem'>
export type AssetAction = 'adopt' | 'enable' | 'disable' | 'drop'
export function getAssets(contextId = 'global'): Promise<{ context_id: string; assets: AssetItem[] }> {
  return getJSON(`/api/dev/harness/assets?context_id=${q(contextId)}`)
}
export function assetAction(slug: string, action: AssetAction, contextId = 'global'): Promise<{ ok: boolean; slug: string; action: string; adopted: boolean; enabled: boolean }> {
  return sendJSON(`/api/dev/harness/assets/${q(slug)}`, 'PATCH', { action, context_id: contextId })
}

// Read / edit a published artifact's raw markdown source (Published-tab preview + edit).
export type PublishedFile = Schema<'PublishedFileResponse'>
export function getPublishedFile(proposalId: number, contextId = 'global'): Promise<PublishedFile> {
  return getJSON(`/api/dev/harness/published/${proposalId}/file?context_id=${q(contextId)}`)
}
export function savePublishedFile(proposalId: number, content: string, contextId = 'global'): Promise<{ ok: boolean; proposal_id: number }> {
  return sendJSON(`/api/dev/harness/published/${proposalId}/file`, 'PUT', { content, context_id: contextId })
}
// --- general/ anchor docs + the roadmap board ---------------------------------
// The per-repo anchor knowledge (project-prd · spec · roadmap · architecture · resources) + the
// deliverable → wave → work-item-instance board join. All strict transport types (no extra='allow').
export type GeneralDocMeta = Schema<'GeneralDoc'>
export type ProjectStatus = Schema<'ProjectStatusResponse'>
export type RoadmapBoard = Schema<'RoadmapBoardResponse'>
export type BoardDeliverable = Schema<'BoardDeliverable'>
export type BoardWave = Schema<'BoardWave'>
export type BoardItem = Schema<'BoardItem'>

export function getGeneralDocs(contextId = 'global'): Promise<{ docs: GeneralDocMeta[] }> {
  return getJSON(`/api/dev/general?context_id=${q(contextId)}`)
}
// Whether this project's memory is established (PRD has ≥1 deliverable) — the dev workspace gates
// the work surfaces on it, showing the onboarding front door until it's true.
export function getProjectStatus(contextId = 'global'): Promise<ProjectStatus> {
  return getJSON(`/api/dev/project-status?context_id=${q(contextId)}`)
}
export function getGeneralDoc(name: string, contextId = 'global'): Promise<{ name: string; content: string | null }> {
  return getJSON(`/api/dev/general/${q(name)}?context_id=${q(contextId)}`)
}
export function saveGeneralDoc(name: string, content: string, contextId = 'global'): Promise<{ ok: boolean; name: string }> {
  return sendJSON(`/api/dev/general/${q(name)}`, 'PUT', { content, context_id: contextId })
}
export function getRoadmap(contextId = 'global'): Promise<RoadmapBoard> {
  return getJSON(`/api/dev/roadmap?context_id=${q(contextId)}`)
}
// Set a root work-item's anchor pointer — pass `wave` (resolves its deliverable) or `deliverable`.
export function setWorkItemScaffold(
  itemId: string, opts: { wave?: string | null; deliverable?: string | null }, contextId = 'global',
): Promise<Schema<'WorkItemScaffoldResponse'>> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/scaffold`, 'POST', { ...opts, context_id: contextId })
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

// Push an inbox item to the workspace — the owner's push (spawn branch-offs wait for this).
// One shared transaction: work-item at triage/active + the brief folder moved to preliminary/.
export type PushResult = { ok: boolean; work_item: { id: string; folder: string }; inbox: InboxEntry }
export function pushInbox(id: number, contextId = 'global'): Promise<PushResult> {
  return sendJSON(`/api/dev/inbox/${id}/push`, 'POST', { context_id: contextId })
}

// The DERIVED WorkGraph projection (D3): repo root · deliverables · work-items · unpushed
// spawn rows, with contains / spawned_from(relation) / supersedes edges. Assembled on demand —
// nothing stored. Cycles are reported as data. The graph view (S7) renders this.
export type WorkGraphNode = {
  id: string
  kind: 'repo_root' | 'deliverable' | 'work_item' | 'inbox_spawn'
  label?: string | null
  // work_item decoration:
  item_kind?: WorkKind | null
  phase?: WorkPhase | null
  status?: WorkStatus | null
  outcome?: WorkOutcome | null
  inbox_id?: number
  slug?: string
  git_branch?: string | null // S4 decoration on work_item nodes
  git_merged?: boolean
}
export type WorkGraphEdge = {
  src: string
  dst: string
  kind: 'contains' | 'spawned_from' | 'supersedes'
  relation?: SpawnRelation
}
export type WorkGraphData = {
  context_id: string
  nodes: WorkGraphNode[]
  edges: WorkGraphEdge[]
  cycles: string[][]
  topo: string[] | null
}
export function getWorkGraph(contextId = 'global'): Promise<WorkGraphData> {
  return getJSON(`/api/dev/workgraph?context_id=${q(contextId)}`)
}

// --- attention engine (S7/D10) ------------------------------------------------------------
// Every item in at most one bucket, strict priority needs_you > running > unread, derived from
// durable state. `badge` = the top non-empty tier only (one color, one count).
export type AttentionRow = Schema<'AttentionRow'>
export type AttentionBadge = Schema<'AttentionBadge'>
export type AttentionData = Schema<'AttentionResponse'>
export function getAttention(contextId = 'global'): Promise<AttentionData> {
  return getJSON(`/api/dev/attention?context_id=${q(contextId)}`)
}

// Compact NOW (S8): run the checkpoint-first compaction sequence on the item's bound session.
export function compactWorkItem(itemId: string, contextId = 'global'): Promise<PlanResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/compact`, 'POST', { context_id: contextId })
}

// Read receipt: the owner opened this item's drilldown — clears it from `unread`.
export function markWorkItemSeen(itemId: string, contextId = 'global'): Promise<{ ok: boolean }> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/seen?context_id=${q(contextId)}`, 'POST')
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

// Hard-delete a pre-build work-item and erase its trace (folder + session transcript +
// originating inbox row). Backend refuses (409) once the item leaves triage/plan.
export type DeleteResult = Schema<'WorkItemDeleteResponse'>
export function deleteWorkItem(itemId: string, contextId = 'global'): Promise<DeleteResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}?context_id=${q(contextId)}`, 'DELETE')
}

// A work-item's review payload — the structured artifact content the review popup renders:
// plan.md / prd.md as Markdown bodies (frontmatter stripped), tasks.md as a checklist.
export type TaskItem = Schema<'TaskItem'>
// COMPUTED per-artifact status (S2): derived at read time from file existence + self-check +
// evidence freshness — never stored, so it can't drift.
export type ArtifactStatusRow = {
  required: boolean
  present: boolean
  status: 'ok' | 'incomplete' | 'missing'
  issues?: string[] | null
  evidence?: { status: 'passed' | 'stale' | 'failed' | 'unverified'; entries: number } | null
}
export type WorkItemDetail = {
  item: WorkItem
  plan: string | null
  prd: string | null
  tasks: TaskItem[] | null
  execution: string | null // the archived execution trace (present once completed)
  artifact_status?: Record<string, ArtifactStatusRow> | null
  // S7 drilldown: raw gate-doc texts (null while un-emitted) + the checkpoint continuity feed.
  docs?: Record<string, string | null> | null
  checkpoints?: CheckpointStub[] | null
}
export type CheckpointStub = Schema<'CheckpointStub'>
export function getWorkItemDetail(itemId: string, contextId = 'global'): Promise<WorkItemDetail> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/detail?context_id=${q(contextId)}`)
}

// One tool / sub-agent / skill call an item's run made — the CLI-style call-trail.
// Derived straight from the transport type — `kind` is now a backend Literal (R5).
export type RunArtifact = Schema<'ArtifactCall'>
export function getWorkItemArtifacts(itemId: string, contextId = 'global'): Promise<{ artifacts: RunArtifact[] }> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/artifacts?context_id=${q(contextId)}`)
}

// Approve → advance a work-item to its KIND's next phase (KIND_PROFILES sequencing). The
// owner's forward gate; backend 409s at the final phase, on terminal items, or mid-run.
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

// --- work-item git layer (workspace-workflow S4/D4) -------------------------------------
// The worktree is created automatically at build entry (the advance route); these are the
// owner's git surface: live health, freshness sync, the deliver merge (auto-routed: blocking
// child → parent branch, else → trunk with a backup ref), revert, and Resolve-with-Agent.

export type GitHealth = Schema<'GitHealthResponse'>
export function getWorkItemGit(itemId: string, contextId = 'global'): Promise<GitHealth> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/git?context_id=${q(contextId)}`)
}

export type GitSyncResult = Schema<'GitSyncResponse'>
export function syncWorkItemGit(itemId: string, contextId = 'global'): Promise<GitSyncResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/git/sync`, 'POST', { context_id: contextId })
}

export type GitMergeResult = Schema<'GitMergeResponse'>
export function mergeWorkItemGit(itemId: string, contextId = 'global'): Promise<GitMergeResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/git/merge`, 'POST', { context_id: contextId })
}

export type GitRevertResult = Schema<'GitRevertResponse'>
export function revertWorkItemGit(itemId: string, contextId = 'global'): Promise<GitRevertResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/git/revert`, 'POST', { context_id: contextId })
}

// Resolve-with-Agent: re-runs the sync leaving conflicts in the worktree and fires a headless
// resolution run (409 when the sync is actually clean). Poll getDev (running) for progress.
export type GitResolveResult = Schema<'GitResolveResponse'>
export function resolveWorkItemGit(itemId: string, contextId = 'global'): Promise<GitResolveResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/git/resolve`, 'POST', { context_id: contextId })
}

// --- gates & lifecycle (workspace-workflow S6/D8/D10) ------------------------------------
// The four human gates' decision surface: a kernel-assembled brief (continuity → delta →
// narrative → the uniform decision block) answerable without opening code, plus the human-only
// abandon path. UI lands in S7 — the drilldown leads with the newest brief.

export type GateBrief = Schema<'GateBriefResponse'>
export function getWorkItemGateBrief(itemId: string, contextId = 'global'): Promise<GateBrief> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/gate-brief?context_id=${q(contextId)}`)
}

// Abandon (human-only, any non-terminal phase): ends runs/session, removes the worktree (branch
// kept), notes the reason into closeout.md, flips terminal. Pass supersededBy to record a
// `superseded` outcome instead. The response is the abandon brief — blocking children listed
// for the owner's disposal; parallel children continue untouched.
export type AbandonResult = Schema<'AbandonResponse'>
export function abandonWorkItem(itemId: string, reason = '', contextId = 'global',
                                supersededBy?: string): Promise<AbandonResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/abandon`, 'POST',
    { context_id: contextId, reason, superseded_by: supersededBy ?? null })
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
