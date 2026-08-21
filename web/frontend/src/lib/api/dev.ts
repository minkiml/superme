import { getJSON, sendJSON } from './client'
import type { Schema } from './generated'

// Dev knowledge — one context's work-items tree, its inbox queue, and the derived glance view.
//
// Transport shapes derive from the daemon OpenAPI; loose ones stay hand-written VIEW types with FE
// narrowing on top.

// `phase` is the per-kind pipeline stage; `status` the runnable axis; `outcome` how a terminal item
// ended.
export type WorkKind = 'implementation' | 'research'
export type WorkPhase =
  | 'triage' | 'plan' | 'build' | 'vet' | 'review' | 'investigate' | 'close'
export type WorkStatus = 'active' | 'awaiting_child' | 'awaiting_upstream' | 'awaiting_slot' | 'awaiting_human' | 'done'
export type WorkOutcome = 'completed' | 'abandoned' | 'superseded'
export type SpawnRelation = 'blocking' | 'parallel' | 'spawn'
// Branch-off provenance, child-side: which item this one spawned from and how.
export type SpawnedFrom = { item: string; relation: SpawnRelation; note?: string | null }

// Normalized on read by the daemon: always `{type, path}` on the wire, with the legacy bare string
// coerced server-side.
export type Artifact = Schema<'ArtifactRef'>
export function artifactPath(a: Artifact): string {
  return a.path
}

// A VIEW type over the generated one, carrying the base fields plus the tree fields and run
// telemetry the daemon attaches.
//
// Hand-written so the FE can rely on narrowed phase, status and children.
export type WorkItem = {
  id: string
  root_id: string
  parent_id: string | null // presence = a branch-off
  wave?: string | null // anchor-scaffold pointer on a root: the roadmap wave this item instances
  deliverable?: string | null // …or a deliverable directly when no wave applies
  title?: string
  description?: string // the item.md body
  kind?: WorkKind | null // machinery selector (null on pre-workflow items = implementation)
  // Absent on implementation items. Free-form, so a new family renders unstyled rather than needing
  // an edit.
  research_kind?: string | null
  phase: WorkPhase
  status: WorkStatus | null // runnable axis (done = terminal); null only on pre-workflow items
  // Present ONLY while status is `error`, cleared as the item leaves it, so it never describes a
  // resolved stop.
  error_reason?: string | null
  outcome?: WorkOutcome | null // set with status done: how the item ended
  after?: string[] | null // peer-sequencing edge: ids this item may not start before
  autopilot?: boolean | null // per-item policy: drive its gates without a click
  spawned_from?: SpawnedFrom | null // branch-off provenance edge
  superseded_by?: string | null // set when outcome = superseded
  inbox_id?: number | null // originating inbox row (trace)
  done_at?: string | null // terminal stamp
  // Git record — written at build entry / the review merge; kept at terminal (trace):
  git_branch?: string | null
  git_worktree?: string | null
  git_base?: string | null
  git_merge_commit?: string | null
  git_merged_at?: string | null
  git_backup_ref?: string | null
  // Set with no merge commit means the PR is open, which is what activates the Git tab's PR
  // actions.
  git_pr_opened_at?: string | null
  artifacts?: Artifact[] // normalized {type, path} refs; read the path via artifactPath
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
  run_feature?: string | null // the live run's role (triage/plan/build/vet/review/close/deputy)
  model?: string | null // model to show on the card (live run's, else last run's)
  effort?: string | null // configured reasoning effort (low|medium|high) its runs use
  ctx_pct?: number | null // context fill to show on the card (live run's, else last run's)
  total_tokens?: number // accumulated tokens across all finished runs
  phase_tokens?: Record<string, number> // per-phase 3-type Σ {phase → tokens}; card shows current phase's
  phase_tokens_4type?: Record<string, number> // per-phase 4-type Σ (3-type + cache_read), recorded behind
  // `ended_at` is epoch SECONDS, so the card can count between polls instead of freezing at fetch
  // time.
  last_run?: { tokens: number; duration_ms: number | null; model?: string | null
               ctx_pct?: number | null; ended_at?: number | null } | null
  tasks?: { done: number; total: number } | null // tasks.md checklist progress (null = no tasks.md)
  seen_at?: string | null // owner-opened read receipt: terminal and unseen means unread
}

// `item` becomes a work-item when pushed; `note` is the owner's own, never pushed.
export type InboxKind = 'item' | 'note'
// open = awaiting push · pushed = promoted to a work-item. (Drop = hard delete, not a status.)
export type InboxStatus = 'open' | 'pushed'
export type InboxOrigin = 'user' | 'agent' // who created it (user-made vs agent branch-off proposal)

// Derived straight from the transport type; kind, status and origin are backend enums.
export type InboxEntry = Schema<'InboxRow'>

// The daemon owns the count; this mirror keeps the tile's number and the list's rows agreeing.
export function isShipped(w: { done_at?: string | null; outcome?: WorkOutcome | null }): boolean {
  return !!w.done_at && (w.outcome ?? 'completed') === 'completed'
}

export type DevGlance = {
  by_status: Record<string, number> // active/awaiting_*/done counts — `done` is EVERY ended item
  shipped: number                   // ended AND landed; abandoned/superseded are excluded
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
  running?: string[] // work-item ids with a background /plan turn in flight
}

// The append-only activity firehose. `scope` and `item_id` give the containment read: one item's
// timeline, or the whole repo.
export type DevEvent = Schema<'DevLogEvent'>
export type DevLogData = Schema<'DevLogResponse'>

// Selective read, never a full dump: pass `itemId` for one timeline, or `since`/`until` for a
// window.
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

// Deputy mandate — this repo's standing acceptance bar (Artifacts → Deputy subtab). A governance
// artifact in the harness cell (local-harness/<id>/dev/deputy/mandate.md); seeded on connect, wiped
// on disconnect. Effective on the next deputy dispatch (read per gate).
export function getDeputyMandate(contextId = 'global'): Promise<{ context_id: string; path: string; content: string }> {
  return getJSON(`/api/dev/harness/deputy?context_id=${q(contextId)}`)
}
export function saveDeputyMandate(content: string, contextId = 'global'): Promise<{ ok: boolean; context_id: string }> {
  return sendJSON('/api/dev/harness/deputy', 'PUT', { content, context_id: contextId })
}

export type HarnessFile = { scope: string; kind: string; name: string; path: string; content: string }
// `scope='local'` reads/writes a host's own local-harness tree — pass its contextId.
export function getHarnessFile(scope: string, kind: string, name: string, contextId = 'global'): Promise<HarnessFile> {
  return getJSON(`/api/dev/harness/plugin-file?scope=${q(scope)}&kind=${q(kind)}&name=${q(name)}&context_id=${q(contextId)}`)
}
export function saveHarnessFile(scope: string, kind: string, name: string, content: string, contextId = 'global'): Promise<{ ok: boolean }> {
  return sendJSON('/api/dev/harness/plugin-file', 'PUT', { scope, kind, name, content, context_id: contextId })
}

// The LEARNED artifacts the owner published, keyed by proposal, so shipped harness skills never
// appear.
//
// A constitution disables via a frontmatter flag; a skill or agent moves into a `.disabled/` shadow
// the scanner ignores.
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
// All constitutions by scope and slug, from a disk scan, so hand-authored, system and learned alike
// are covered.
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

// The VERIFICATION LIBRARY — this repo's proven checks. `standing` entries are attached to every
// implementation plan; `available` ones are cited by id. Promoting is the owner's call only.
export type LibraryEntry = Schema<'LibraryEntry'>
export type VerificationLibrary = Schema<'VerificationLibraryResponse'>
export function getVerificationLibrary(contextId = 'global'): Promise<VerificationLibrary> {
  return getJSON(`/api/dev/verification?context_id=${q(contextId)}`)
}
// Every call the owner has ruled on, newest first. Read-only by contract: entries are append-only
// and reversed by appending.
//
// The kernel is the only writer, so there is no save counterpart here and should never be one.
export type DecisionEntry = Schema<'DecisionEntry'>
export function getDecisions(contextId = 'global'): Promise<{ decisions: DecisionEntry[] }> {
  return getJSON(`/api/dev/decisions?context_id=${q(contextId)}`)
}
export function moveLibraryEntry(entryId: string, tier: 'standing' | 'available', contextId = 'global'): Promise<{ ok: boolean; name: string }> {
  return sendJSON(`/api/dev/verification/${q(entryId)}`, 'PATCH', { tier, context_id: contextId })
}
export function dropLibraryEntry(entryId: string, contextId = 'global'): Promise<{ ok: boolean; name: string }> {
  return sendJSON(`/api/dev/verification/${q(entryId)}?context_id=${q(contextId)}`, 'DELETE', undefined)
}

// The PORTRAIT — what this project IS, in six bands assembled from the anchor docs. Read-only:
// the docs are the store, so nothing here is edited in place.
export type Portrait = Schema<'PortraitResponse'>
export function getPortrait(contextId = 'global'): Promise<Portrait> {
  return getJSON(`/api/dev/portrait?context_id=${q(contextId)}`)
}

// Knowledge health — findings over general/, derived fresh (never stored, so never stale).
export type LintFinding = Schema<'LintFinding'>
export type LintReport = Schema<'LintResponse'>
export function getKnowledgeLint(contextId = 'global'): Promise<LintReport> {
  return getJSON(`/api/dev/lint?context_id=${q(contextId)}`)
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

// The review queue: the distill agent files proposals, and the owner gate lives here. `output_form`
// is a loose backend string.
export type ProposalScope = 'repo_dev' | 'universal_dev' | 'core'
export type ProposalStatus =
  | 'proposed' | 'writing' | 'drafted' | 'published' | 'rejected' | 'dropped' | 'superseded' | 'retired'
export type EvalReport = {
  form?: string
  verdict?: 'pass' | 'warn' | 'fail' | 'skipped' | 'unknown' | string
  summary?: string | string[] // bullets (new) or a single sentence (legacy)
  checks?: { name?: string; score?: number; note?: string }[]
  issues?: { severity?: 'high' | 'low' | string; what?: string; fix?: string }[]
  // The artifact's OWN cost: `run` is measured once, `overhead` is a constitution's always-on
  // per-turn cost.
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
  output_form: string // loose: constitution | skill | agent
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

// Quick capture, deliberately bare: kind, title, text. The per-item config is NOT set here.
//
// A row is born inheriting the repo's defaults; sending a concrete model on every capture pins the
// item to the FE's constant.
export function addInbox(
  input: {
    text: string; title?: string | null; kind?: InboxKind; tag?: string | null; origin?: InboxOrigin
  },
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

// Edit an inbox row or the per-item setting push locks into the work-item. An absent field is left
// alone.
//
// `work_kind` is the one field with a meaningful cleared state, which is why it is typed wider than
// the row's union.
export function updateInbox(
  id: number,
  patch: Partial<Pick<InboxEntry,
    'status' | 'kind' | 'tag' | 'text' | 'title' | 'routed_to' | 'model' | 'effort' | 'autopilot'
    | 'vet_model' | 'vet_effort' | 'deputy_model' | 'deputy_effort'>>
    & { work_kind?: string },
): Promise<InboxEntry> {
  return sendJSON(`/api/dev/inbox/${id}`, 'PATCH', patch)
}

export function deleteInbox(id: number): Promise<{ ok: boolean; id: number }> {
  return sendJSON(`/api/dev/inbox/${id}`, 'DELETE')
}

// The cold-start context the work-item it becomes reads first. A null body is legal, not missing.
//
// `editable` goes false at push: the file has moved into the item's read-only folder.
export type InboxBrief = { id: number; content: string | null; editable: boolean; path: string }
export function getInboxBrief(id: number): Promise<InboxBrief> {
  return getJSON(`/api/dev/inbox/${id}/brief`)
}

export function saveInboxBrief(id: number, content: string): Promise<{ ok: boolean; id: number }> {
  return sendJSON(`/api/dev/inbox/${id}/brief`, 'PUT', { content })
}

// Push an inbox item to the workspace — the owner's push (spawn branch-offs wait for this).
// One shared transaction: work-item at triage/active + the brief folder moved to preliminary/.
export type PushResult = { ok: boolean; work_item: { id: string; folder: string }; inbox: InboxEntry }
export function pushInbox(id: number, contextId = 'global'): Promise<PushResult> {
  return sendJSON(`/api/dev/inbox/${id}/push`, 'POST', { context_id: contextId })
}

// The DERIVED work-graph projection: repo root · deliverables · work-items · unpushed
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
  git_branch?: string | null // git decoration on work-item nodes
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

// --- attention engine ------------------------------------------------------------
// Every item in at most one bucket, strict priority needs_you > running > unread, derived from
// durable state. `badge` = the top non-empty tier only (one color, one count).
export type AttentionRow = Schema<'AttentionRow'>
export type AttentionBadge = Schema<'AttentionBadge'>
export type AttentionData = Schema<'AttentionResponse'>
export function getAttention(contextId = 'global'): Promise<AttentionData> {
  return getJSON(`/api/dev/attention?context_id=${q(contextId)}`)
}

// Compact NOW: run the checkpoint-first compaction sequence on the item's bound session.
export function compactWorkItem(itemId: string, contextId = 'global'): Promise<PlanResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/compact`, 'POST', { context_id: contextId })
}

// Read receipt: the owner opened this item's drilldown — clears it from `unread`.
export function markWorkItemSeen(itemId: string, contextId = 'global'): Promise<{ ok: boolean }> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/seen?context_id=${q(contextId)}`, 'POST')
}

// Fire the CURRENT phase's own background run — one call for every phase, returning immediately.
//
// The backend refuses a terminal, stopped, at-a-gate or already-running item, which is the rule the
// button reads.
export type PlanResult = Schema<'PlanResponse'>
// `model` / `effort` are the per-run picks; omit for the item's stored values.
export function runWorkItem(itemId: string, contextId = 'global', model?: string, effort?: string): Promise<PlanResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/run`, 'POST', {
    context_id: contextId,
    model: model ?? null,
    effort: effort ?? null,
  })
}

// The owner's restart of an item whose RUN stopped. Nothing is rewound: branch, worktree and
// artifacts stand.
//
// Reads alike to re-run and does the opposite — Resume re-runs a run that never finished.
export function resumeWorkItem(itemId: string, contextId = 'global'): Promise<PlanResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/resume`, 'POST', { context_id: contextId })
}

// Start the item over in place. DESTRUCTIVE and irreversible: artifacts, reports, checkpoints and
// sessions go.
//
// The id, branch, run history and graph relations stay — that is what makes it a re-run and not a
// new item.
export function rerunWorkItem(itemId: string, contextId = 'global'): Promise<PlanResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/rerun`, 'POST', { context_id: contextId })
}

// The owner's grant or deny on a deferred authorization at review.
//
// A grant routes the item back to perform the change; a deny waives the check, on the record. The
// owner grants unconditionally.
export function authorizeWorkItem(
  itemId: string, authId: string, decision: 'granted' | 'denied', contextId = 'global',
): Promise<PlanResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/authorize`, 'POST',
    { auth_id: authId, decision, context_id: contextId })
}

// A work-item's review payload — the structured artifact content the review popup renders:
// plan.md / prd.md as Markdown bodies (frontmatter stripped), tasks.md as a checklist.
export type TaskItem = Schema<'TaskItem'>
// COMPUTED per-artifact status, derived at read time from file existence + self-check +
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
  execution: string | null // the execution SNAPSHOT clearance writes at terminal (artifacts/execution.md)
  artifact_status?: Record<string, ArtifactStatusRow> | null
  // Raw gate-doc texts (null while un-emitted) + the checkpoint continuity feed.
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
// What each run WAS — the group header's label. Without it "Run #653" opening on a shell command
// looks like a bug rather than a chat turn.
export type RunHeader = Schema<'RunHeader'>
export function getWorkItemArtifacts(
  itemId: string, contextId = 'global',
): Promise<{ artifacts: RunArtifact[]; runs: RunHeader[] }> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/artifacts?context_id=${q(contextId)}`)
}

// The unified timeline: every run of this item, oldest-first, phase/role-tagged with its turn events —
// the read-only history the chat panel loads before live-streaming new frames from the socket.
export type WorkItemTimeline = Schema<'WorkItemTimelineResponse'>
export type TimelineRun = Schema<'TimelineRun'>
export type TimelineEvent = Schema<'TimelineEvent'>
export function getWorkItemTimeline(itemId: string, contextId = 'global'): Promise<WorkItemTimeline> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/timeline?context_id=${q(contextId)}`)
}

// Prompt X-ray (repo-level): fire a THROWAWAY prompt-extraction probe (a disposable work-item that
// runs the real lifecycle to capture each phase's actual input prompt, then self-destructs), and
// read its state — the captured "A" input-page links survive the probe's teardown.
export type PromptExtractionStatus = Schema<'PromptExtractionStatusResponse'>
export function runPromptExtraction(contextId = 'global'): Promise<PromptExtractionStatus> {
  return sendJSON(`/api/dev/prompt-extraction/run?context_id=${q(contextId)}`, 'POST', {})
}
export function getPromptExtractionStatus(contextId = 'global'): Promise<PromptExtractionStatus> {
  return getJSON(`/api/dev/prompt-extraction/status?context_id=${q(contextId)}`)
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

// Enrol or un-enrol a work-item in autopilot. Pre-build only (triage/plan) — 409 past that.
export type AutopilotResult = Schema<'WorkItemAutopilotResponse'>
export function setWorkItemAutopilot(
  itemId: string, on: boolean, contextId = 'global',
): Promise<AutopilotResult> {
  return sendJSON(
    `/api/dev/work-items/${q(itemId)}/autopilot?context_id=${q(contextId)}`, 'POST', { on })
}


// --- work-item git layer ---
//
// The worktree is created automatically at build entry; these are the owner's surface: health,
// sync, the merge, revert and resolve.

export type GitHealth = Schema<'GitHealthResponse'>
export function getWorkItemGit(itemId: string, contextId = 'global'): Promise<GitHealth> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/git?context_id=${q(contextId)}`)
}

export type GitMergeResult = Schema<'GitMergeResponse'>
export function mergeWorkItemGit(itemId: string, contextId = 'global'): Promise<GitMergeResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/git/merge`, 'POST', { context_id: contextId })
}

export type GitRevertResult = Schema<'GitRevertResponse'>
export function revertWorkItemGit(itemId: string, contextId = 'global'): Promise<GitRevertResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/git/revert`, 'POST', { context_id: contextId })
}

// The review report plus the branch's diff walkthrough, GROUPED BY TASK off the commits' trailers.
//
// Per-file patches are fetched on expand: a whole diff is the one thing a review page must not make
// the reader wait for.
export type PrView = Schema<'PrViewResponse'>
export function getWorkItemPr(itemId: string, contextId = 'global'): Promise<PrView> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/pr?context_id=${q(contextId)}`)
}

export type PrDiff = Schema<'PrDiffResponse'>
export function getWorkItemPrDiff(itemId: string, path: string, task: string | null,
                                  contextId = 'global'): Promise<PrDiff> {
  const t = task ? `&task=${q(task)}` : ''
  return getJSON(`/api/dev/work-items/${q(itemId)}/pr/diff?context_id=${q(contextId)}`
    + `&path=${q(path)}${t}`)
}

// Resolve-with-Agent: re-runs the sync leaving conflicts in the worktree and fires a background
// resolution run (409 when the sync is actually clean). Poll getDev (running) for progress.
export type GitResolveResult = Schema<'GitResolveResponse'>
export function resolveWorkItemGit(itemId: string, contextId = 'global'): Promise<GitResolveResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/git/resolve`, 'POST', { context_id: contextId })
}

// --- the drilldown ---
//
// ONE payload for the whole work-item surface, computed server-side, including every control's
// activation AND its reason.
//
// Never derive activation from this payload — read `active`.

export type Drilldown = Schema<'DrilldownResponse'>
export type DrilldownAction = Drilldown['actions'][number]
export type GateCheck = Drilldown['checks'][number]
export type ProofRow = Drilldown['proof'][number]
export type AttentionCard = NonNullable<Drilldown['attention']>
export function getWorkItemDrilldown(itemId: string, contextId = 'global'): Promise<Drilldown> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/drilldown?context_id=${q(contextId)}`)
}

// One phase's user-facing report for the Reports tab, plus the path to the full agent-facing
// contract behind it. Only called for phases `drilldown.reports`
// lists — the tab greys the rest rather than probing for a 404.
export type PhaseReport = Schema<'PhaseReportResponse'>
export function getWorkItemReport(itemId: string, phase: string,
                                  contextId = 'global'): Promise<PhaseReport> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/report/${q(phase)}?context_id=${q(contextId)}`)
}

// The one section of any report the OWNER writes, and the only place their words reach plan as
// instruction.
//
// HUMAN-ONLY, with no agent tool behind the write. Adding and deleting both PUT the whole pair of
// lists.
export type OwnerInput = Schema<'OwnerInputResponse'>
export type OwnerReference = Schema<'OwnerReference'>
export type OwnerNote = Schema<'OwnerNote'>
export function getWorkItemOwnerInput(itemId: string, contextId = 'global'): Promise<OwnerInput> {
  return getJSON(`/api/dev/work-items/${q(itemId)}/from-you?context_id=${q(contextId)}`)
}
export function saveWorkItemOwnerInput(itemId: string, references: OwnerReference[],
                                       notes: OwnerNote[],
                                       contextId = 'global'): Promise<OwnerInput> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/from-you`, 'PUT',
                  { context_id: contextId, references, notes })
}

// Human-only, from any non-terminal phase: ends runs, removes the worktree, notes the reason, flips
// terminal.
//
// The response is the abandon brief — blocking children listed for the owner's disposal.
export type AbandonResult = Schema<'AbandonResponse'>
export function abandonWorkItem(itemId: string, reason = '', contextId = 'global',
                                supersededBy?: string): Promise<AbandonResult> {
  return sendJSON(`/api/dev/work-items/${q(itemId)}/abandon`, 'POST',
    { context_id: contextId, reason, superseded_by: supersededBy ?? null })
}

// Per-work-item model and effort setters are gone — run config is chosen at capture (addInbox
// model/effort) and locked in at push. The work-item carries it immutably from then on.
