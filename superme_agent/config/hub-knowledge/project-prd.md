# SuperMe — project PRD

SuperMe is a local-only, dashboard-driven personal agent, hosted onto every repository its owner owns, that lets one person turn their scattered AI use into a single accumulating digital twin without becoming the bottleneck on the work it produces.

## Identity
- **Who it's for**: a single owner, running SuperMe locally against their own repos — built to be usable by exactly one person, but prepared so another owner could run their own instance.
- **Why it exists**: work isn't one repo — it's projects, side work, a business, and the tooling that runs them, each under its own scope, and none of it connects. Separately, an agent produces more in an hour than a person can read in a day and works on several things at once, which either makes the owner read everything slowly, skim and hope, or become the bottleneck. SuperMe exists to be the join across scopes and the engineering that keeps the owner in the loop without that cost.

## Goals
- Every gate report is written for the owner, not a log: what happened, what to push back on, how much to trust each claim, and where it leaves the project.
- Mechanical bookkeeping — missing artifacts, stale evidence, unresolved authorizations — is settled before the owner is asked anything, so their attention goes to judgment.
- A work-item is self-carrying: its brief, plan, decisions, checkpoints and history travel with it, so returning to it after time away is reading, not reconstructing.
- Every run keeps a full trace — prompts, tool calls, results, sub-agents, tokens, context fill — so nothing is a black box when it goes wrong.

## Direction
- Multi-LLM engine support, starting with the OpenAI model family.
- The operational knowledge and memory structure for core mode's twin — the taxonomy and retrieval model for the non-code half of the owner's estate.
- Better comprehension through visualization, so the state of the work is something the owner sees rather than reconstructs.
- Integrations with the other places the owner's work already lives (Notion, Slack, and similar), so knowledge outside their repos joins the estate too.

## Non-goals
- Fully autonomous gate-crossing — human-in-the-loop is the stated principle, not an interim limitation; work nobody verified is risk, not speed, so no design goal removes the owner from a gate.
- API-key or multi-tenant SaaS billing — SuperMe runs on the owner's own Claude plan credential only.
- A shared, multi-user workspace inside one instance — SuperMe is architected per single owner; running "for others" means others hosting their own instance, not several people sharing one.

## Deliverables
The chunks of intended value. Each `<slug>` is stable; roadmap waves and work-items point at it.

- **d-digital-twin** — Cross-repo identity join
  - **Value**: everything done with AI across every owned repo accumulates into one identity SuperMe can read, instead of staying siloed per scope.
  - **Needs**: none
- **d-gated-pipeline** — Self-carrying, gated work-item pipeline
  - **Value**: the owner reviews trusted, mechanically-checked work at a small number of gates, without reconstructing context each time.
  - **Needs**: none
- **d-observability** — Full execution trace
  - **Value**: the owner can see exactly what an agent did — prompts, tool calls, sub-agents, tokens, context — when something needs checking.
  - **Needs**: none
- **d-review-loop** — Interactive review
  - **Value**: the owner reads a diff, leaves feedback in the item, and that feedback becomes a check the next build/vet cycle satisfies.
  - **Needs**: d-gated-pipeline
- **d-multi-llm** — Multi-LLM engine support
  - **Value**: the owner can run SuperMe's agents on more than one model family, starting with OpenAI.
  - **Needs**: none
- **d-twin-memory** — Operational knowledge & memory structure for the twin
  - **Value**: the owner's non-code estate (identity, journal, accrued knowledge) gets a taxonomy and retrieval model as real as the code side already has.
  - **Needs**: d-digital-twin
- **d-comprehension-viz** — Visualized comprehension
  - **Value**: the owner sees the shape of the work — where things stand across items and repos — instead of reconstructing it from reading.
  - **Needs**: none
- **d-integrations** — External integrations
  - **Value**: knowledge that lives outside the owner's repos (Notion, Slack, and similar) joins the estate SuperMe can read.
  - **Needs**: d-twin-memory

## Success signals
| Deliverable | Success signal |
|-------------|----------------|
| d-digital-twin | one identity reads across every connected repo, including SuperMe's own |
| d-gated-pipeline | the owner reaches a gate and finds mechanical bookkeeping already settled |
| d-observability | any run's full trace is retrievable after the fact, with nothing missing |
| d-review-loop | feedback left on a diff shows up as a check the next build/vet cycle addresses |
| d-multi-llm | an agent turn runs to completion on a non-Anthropic model family |
| d-twin-memory | the non-code estate has a taxonomy an owner or agent can retrieve from, not just accrue into |
| d-comprehension-viz | the owner grasps the state of the work by looking, not by reading |
| d-integrations | knowledge from outside a repo (e.g. Notion or Slack) appears in the estate SuperMe reads |
