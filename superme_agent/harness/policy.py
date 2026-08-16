"""Permission policy — part of the agent's portable harness.

Decides which tool calls auto-run vs. need human approval. The approval
*mechanism* lives in runtime/permissions.py; the *policy* (what's safe) lives here
so it travels with the agent regardless of workspace. Future per-path / per-workspace
write rules belong here too.
"""

# The subagent spawn. `Agent` is the SDK's name for it; `Task` is the older one, and both are
# accepted because a CLI upgrade must not silently uncap the fleet.
SUBAGENT_TOOLS = {"Agent", "Task"}

# How many subagents ONE turn may spawn, counting nested spawns (a subagent's own tool calls pass
# through the same callback). NOT a concurrency limit — twelve at once and twelve in sequence spend
# the same budget; nothing bounds how many run in parallel.
#
# 8, set by the owner 2026-08-14 (down from the 12 first proposed). Measured fan-out on a real
# codebase is 5–8, so this sits ON the observed ceiling rather than above it: it will occasionally
# refuse a legitimate thirteenth split, and that is the intent — a run wanting more than eight
# readers is a run worth interrupting. The refusal is declared to the agent (below), so a sweep
# capped here says so at the gate instead of reading as a complete one.
#
# Why a cap exists at all (2026-08-14): the `Agent` tool sits in SAFE_TOOLS below — auto-allowed,
# unlimited, no timeout — and a run that spawned five Explore subagents over the SuperMe hub went
# silent for 24 minutes and burned 452k tokens with nothing to show. The stall itself is the
# watchdog's problem (daemon/services/watchdog.py); this is the other half — the number of agents
# a single turn can put in flight, which nothing bounded before.
#
# The cap is DECLARED to the agent when it bites (see permissions.build_can_use_tool), never
# silent: a reader that was cut off must be able to say so in its report, or the gate reads a
# narrowed surface as a complete one.
MAX_SUBAGENTS = 8

# Tools with no side effects on the machine or the outside world: auto-allow.
# Everything else (Write, Edit, Bash, …) is gated behind a human in Slack.
SAFE_TOOLS = {
    "Read", "Glob", "Grep", "NotebookRead",
    "WebSearch", "WebFetch", "TodoWrite", "Skill", "Agent",
    # Base tools (every mode): read-only on-demand loader for a constitution body by name, and the
    # read-only asset-pool ranker (suggests relevant pooled knowledge). Must never prompt — the
    # frontmatter-first model depends on cheap pulls, and onboarding calls suggest_assets each run.
    "mcp__superme__pull_constitution",
    "mcp__superme__suggest_assets",
    # Run-transport endings (workflow-renovation-v2 §3.2, run_tools.py): each writes only its own
    # per-run sink (payload → DB run.report / verdict executor). They ARE the sanctioned structured
    # endings of kernel-fired runs — a prompt here would park every background run on a human.
    "mcp__run__report_completion",
    "mcp__deputy__deputy_verdict",
    # The agent's read-only Slack readers (in-process MCP tools).
    "mcp__slack__read_channel", "mcp__slack__read_thread",
    # The dev agent's read-only dev event-log reader (PRD §4.9).
    "mcp__dev__read_dev_log",
    # The dev agent's read-only inbox reader (context-model-spec §5) — scoped to its own queue.
    "mcp__dev__read_inbox",
    # Read-only learning-pool readers (2026-07-11): candidate pool + standing OPEN proposals. Moved
    # into the general dev set so any session can answer "what learning is pending?" — mutate nothing.
    "mcp__dev__read_candidates",
    "mcp__dev__read_proposals",
    # Read-only run inspector (2026-07-12): one run's trace (calls + outcome) or the recent-run list,
    # scoped to this repo — the "what did activity #N do / why did it fail?" read + diagnose substrate.
    "mcp__dev__read_run",
    # The repo's verification library (verification-model §8) — read-only on `general/verification.md`
    # plus this item's own nominations. Plan calls it before authoring a check and close calls it to
    # write nominations in, so a prompt here denies BOTH in a background run: caught live on
    # 2026-08-03, where close reported "read_verification_library was unavailable this run (denied)"
    # and the library could never have been written to even if vet had nominated.
    "mcp__dev__read_verification_library",
    # The SANCTIONED itemize writes (work-item-session-recognition-prd): create one inbox item from a
    # discussion, or APPEND new discussion onto an existing item (the dedup path). Auto-allowed so a
    # general session can ticket work without a prompt; the one exemption to the general-session
    # guardrail (they can touch nothing but the inbox — create + append-only, never edit).
    "mcp__dev__create_inbox_item",
    "mcp__dev__append_inbox_item",
    # Capture SWEEP (WI-8) — the `capture` sub-agent's pen; files a candidate row from a swept
    # conversation slice. Nothing is applied here (the owner gate is downstream).
    "mcp__dev__file_candidate",
    # Memory PROCESSING (PRD §4.10.2) — file consolidated proposals from the candidate pool.
    # Still pre-gate: a proposal is a reversible draft awaiting the owner's accept/reject; the
    # apply step (which writes memory/ files) is what's actually gated, downstream.
    "mcp__dev__propose_memory",
    # Cross-run consolidation (2026-07-11): fold a recurring learning into the OPEN proposal that
    # already covers it, instead of minting a parallel proposal. Pre-gate and DB-only — mutates
    # un-ratified proposal rows (reverts a re-enriched draft to proposed for re-forge); nothing applied.
    "mcp__dev__merge_into_proposal",
    # Distill's gate (2026-07-10): permanently drop candidates that fail the four-test filter, so
    # noise never accretes in the pool. Pre-gate and self-contained — deletes only un-ratified
    # candidate rows (never run/transcript telemetry); no disk write, nothing applied.
    "mcp__dev__drop_candidates",
    # WRITE phase (WI-8 §Phase 3) — the `write` sub-agent's pen; stages the authored artifact into
    # the proposal row (→ drafted). Still pre-gate: staging writes only to the DB, never to disk;
    # the disk write (publish) is the owner-gated step downstream.
    "mcp__dev__stage_artifact",
    # Work-item phase-session pens (workspace-workflow S2/S5/S6): each enforces its own
    # bound_item_id scope (a session may touch only ITS item) and writes only inside that item's
    # own folder daemon-side — the sanctioned autonomous writes the D5 scaffold-then-fill playbook
    # depends on. Must never prompt: a background phase run has nothing to approve it, and an
    # interactive one shouldn't page the owner for the item's own artifacts.
    # Triage's recording surface: kind + existing-deliverable onto the item's own yaml, triage
    # phase only (the gate that follows is the human confirmation).
    "mcp__dev__set_triage_classification",
    "mcp__dev__scaffold_artifact",
    "mcp__dev__record_verification",
    # Build's own validation runs, recorded as data (same append-only item-scoped pen). It fires
    # several times a cycle inside a human-free loop, and a prompt here would park the loop on the
    # record that vet's audit depends on.
    "mcp__dev__record_validation",
    # Plan's smoke test of the `run:` blocks it just wrote. It executes ONLY strings already in
    # plan.md, through the same sandbox as every kernel check, and records nothing — the narrow
    # capability the plan phase was reaching for when it tried (and was denied) a raw shell.
    "mcp__dev__dry_run_checks",
    # Vet's reading of a failure (verification-model §5) — the same append-only, item-scoped pen as
    # the verdict above, and the vet report refuses without it, so a prompt here would park the loop
    # on a human at the exact moment it has something useful to hand back.
    "mcp__dev__record_diagnosis",
    # The standing lenses (verification-model §3) — same item-scoped append-only pen, owed on every
    # cycle including a `depth: none` one, so a prompt here would park the loop on the one record
    # that is never optional.
    "mcp__dev__record_lens",
    # A library nomination (verification-model §8) writes nothing outside the item's own ledger —
    # close is what puts an entry in `general/`, behind the owner's review gate. Prompting here
    # would stall a background vet cycle on a record that changes nothing yet.
    "mcp__dev__nominate_check",
    # The wall-handling ledgers (BV-A1/A2): append-only, item-scoped, pre-gate records. These
    # exist precisely so an autonomous run NEVER stalls on a person — a prompt here recreates the
    # stall they replace (and a background run's deny_all silently swallows the record).
    "mcp__dev__request_authorization",
    # Vet's report pen (build-vet-loop §4b): writes only the item's own vet-report-<n>.md, with the
    # verdicts mechanically cross-checked against the evidence ledger daemon-side — and the vet
    # session's file-write tools are denied outright, so this pen is its ONLY way to produce the
    # handoff. A prompt here would park every background vet cycle on a human.
    "mcp__dev__file_vet_report",
    # Plan's report pen — same shape: writes only the item's own report-plan.md, and everything
    # factual in it is derived from plan.md rather than asserted. A background plan run would
    # otherwise stall on the last step of its own work.
    "mcp__dev__file_plan_report",
    "mcp__dev__write_checkpoint",
    # Agent-run freshness sync (D9): merges trunk INTO the item's own worktree only — trunk itself
    # is never written; conflicts abort-and-report by default.
    "mcp__dev__sync_from_main",
    # Stages edit ops item-local (applied only later, atomically with the owner's merge).
    "mcp__dev__apply_knowledge_delta",
    # The surgical plan editor (§2.1): writes only the item's own plan.md, through validated
    # section/task ops. It is the re-plan's ONLY sanctioned way to change the file, so prompting
    # here would push the agent toward the whole-file rewrite this tool exists to prevent.
    "mcp__dev__revise_plan",
    # Proposal-only: drafts the close record + pages the owner; never sets an item terminal.
}


def is_safe(tool_name: str, input_data: dict) -> bool:
    """True if the tool may run without human approval."""
    return tool_name in SAFE_TOOLS
