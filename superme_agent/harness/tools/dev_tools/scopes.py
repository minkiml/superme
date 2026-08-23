"""Which tools exist on each surface, and which of them a session scope may see."""

from ..registry import ToolSpec, build_mcp_server
from .learning import (DevLogArgs, DropCandidatesArgs, FileCandidateArgs, MergeProposalArgs,
                       ProposeMemoryArgs, ReadRunArgs, ReviewCandidatesArgs, ReviewProposalsArgs,
                       StageArtifactArgs, _dev_log, _drop_candidates, _file_candidate,
                       _merge_into_proposal, _propose_memory, _read_run, _review_candidates,
                       _review_proposals, _stage_artifact)
from .inbox import (AppendInboxItemArgs, CreateInboxItemArgs, InboxArgs, ItemizeAndLaunchArgs,
                    PushInboxItemArgs, _append_inbox_item, _create_inbox_item,
                    _itemize_and_launch, _list_inbox, _push_inbox_item)
from .items import (ScaffoldArtifactArgs, SetTriageClassificationArgs, SyncFromMainArgs,
                    WriteCheckpointArgs, _scaffold_artifact, _set_triage_classification,
                    _sync_from_main, _write_checkpoint)
from .verification import (DryRunChecksArgs, NominateCheckArgs, ReadVerificationLibraryArgs,
                           RecordDiagnosisArgs, RecordLensArgs, RecordValidationArgs,
                           RecordVerificationArgs, _dry_run_checks, _nominate_check,
                           _read_verification_library, _record_diagnosis, _record_lens,
                           _record_validation, _record_verification)
from .records import (ReadDecisionsArgs, ReadResearchProposalsArgs, RequestAuthorizationArgs,
                      _read_decisions, _read_research_proposals, _request_authorization)
from .reports import (FileInvestigateReportArgs, FilePhaseReportArgs, FilePlanReportArgs,
                      FileVetReportArgs, _file_build_report, _file_close_report,
                      _file_investigate_report, _file_plan_report, _file_review_report,
                      _file_triage_report, _file_vet_report)
from .revisions import (ApplyKnowledgeDeltaArgs, RevisePlanArgs, _apply_knowledge_delta,
                        _revise_plan)

ITEM_DEV_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "set_triage_classification",
        "Record a triage session's classification onto its work-item: the confirmed kind "
        "(implementation | research) and, optionally, an EXISTING PRD deliverable it anchors to. "
        "Triage phase only — after the triage-exit gate the kind is fixed.",
        SetTriageClassificationArgs, _set_triage_classification,
    ),
    ToolSpec(
        "scaffold_artifact",
        "Scaffold a work-item artifact skeleton (brief/plan/investigation) — "
        "code owns the structure, you fill the <fill:…> prose slots.",
        ScaffoldArtifactArgs, _scaffold_artifact,
    ),
    ToolSpec(
        "dry_run_checks",
        "Run the `run:` blocks already written into this item's plan and report each exit code. "
        "Records nothing: at plan time the work does not exist, so a failing assertion is the "
        "expected answer — this catches the command that cannot run AT ALL, before a build⟷vet "
        "cycle is spent discovering it.",
        DryRunChecksArgs, _dry_run_checks,
    ),
    ToolSpec(
        "record_validation",
        "Record one of BUILD's own validation runs — the command, the machine result, pass/fail. "
        "Validation stays yours to run; recording it as data is what lets vet audit the claim "
        "instead of taking a sentence's word for it.",
        RecordValidationArgs, _record_validation,
    ),
    ToolSpec(
        "record_verification",
        "Record one machine-checked verification entry into the current cycle report "
        "(check + how + result + pass/fail; freshness-tracked against the repo state).",
        RecordVerificationArgs, _record_verification,
    ),
    ToolSpec(
        "record_diagnosis",
        "Record WHERE a failed check broke and WHY, plus what you could not determine. "
        "Never the fix: build reasons that out inside the current plan. Required before the vet "
        "report on every failing check, and it becomes the next build cycle's work order.",
        RecordDiagnosisArgs, _record_diagnosis,
    ),
    ToolSpec(
        "record_lens",
        "Record one standing lens's read of this cycle — what you probed, and what it found "
        "(nothing is a fine answer; never manufacture a finding). intent and safety gate on any "
        "finding, robustness on a high one, performance never. Every cycle owes all three standing "
        "lenses before the vet report will write.",
        RecordLensArgs, _record_lens,
    ),
    ToolSpec(
        "nominate_check",
        "Nominate one of this item's checks for the repo's VERIFICATION LIBRARY — the catalogue "
        "later items inherit from. Only a check that has actually passed here, and only when you "
        "can say what it defends about the REPO without mentioning this item. You nominate; close "
        "writes it in; the owner decides whether it becomes standing.",
        NominateCheckArgs, _nominate_check,
    ),
    ToolSpec(
        "read_verification_library",
        "Read this repo's verification library: the standing entries (already attached to every "
        "plan) and the available ones a plan can cite by id instead of re-authoring. At close, "
        "pass item_id to also get this item's nominations rendered as ready-to-write entries.",
        ReadVerificationLibraryArgs, _read_verification_library,
    ),
    ToolSpec(
        "read_decisions",
        "Read this project's decision ledger — the choices the owner has already ruled on. Call it "
        "BEFORE raising a question for the owner or scoping an item: a subject already settled here "
        "is answered, and re-asking it spends a decision they already made. Returns the index by "
        "default; pass `entry_id` for one entry in full.",
        ReadDecisionsArgs, _read_decisions,
    ),
    ToolSpec(
        "read_research_proposals",
        "Read an approved research review's proposed work, already split into what you may file "
        "and what you may not. A proposal that asks the owner a question and carries no answer is "
        "NOT yours to file — report it as withheld. Use this instead of judging the report "
        "yourself: the split is what stops a ticket claiming to be startable when its ruling was "
        "never given.",
        ReadResearchProposalsArgs, _read_research_proposals,
    ),
    ToolSpec(
        "request_authorization",
        "Request authorization for a contract change you can't self-authorize (an owner-reserved "
        "anchor-doc edit). The blocked vet check DEFERS instead of failing — never edit the vet "
        "plan or force the change. It surfaces at review, where the owner or a delegated deputy "
        "grants (routes back to you) or denies (accepts the gap).",
        RequestAuthorizationArgs, _request_authorization,
    ),
    ToolSpec(
        "file_plan_report",
        "File the plan gate's user report (plan phase, once the plan is finished): the coverage "
        "matrix — every task and the checks that will prove it — plus the depth call and the "
        "stats are derived from plan.md; you supply only the prose. A task with no check shows "
        "as a gap, which is the point.",
        FilePlanReportArgs, _file_plan_report,
    ),
    ToolSpec(
        "file_investigate_report",
        "File the investigation's user report (investigate phase, once the record is complete). "
        "You supply the whole body, filled from its template; the tool owns the path and refuses "
        "a report with an unfilled slot left in it.",
        FileInvestigateReportArgs, _file_investigate_report,
    ),
    # One pen, one contract: hand over the whole filled body; the tool owns the path and refuses
    # an unfilled slot.
    ToolSpec(
        "file_triage_report",
        "File triage's user report (triage phase, once the classification is settled). You supply "
        "the whole body, filled from its template; the tool owns the path and refuses a report "
        "with an unfilled slot left in it.",
        FilePhaseReportArgs, _file_triage_report,
    ),
    ToolSpec(
        "file_build_report",
        "File the build cycle's user report (build phase, at the end of a cycle). You supply the "
        "whole body, filled from its template; the tool owns the path and refuses a report with "
        "an unfilled slot left in it.",
        FilePhaseReportArgs, _file_build_report,
    ),
    ToolSpec(
        "file_review_report",
        "File the review's user report (review phase, once the verdict is drawn). You supply the "
        "whole body, filled from its template; the tool owns the path and refuses a report with "
        "an unfilled slot left in it.",
        FilePhaseReportArgs, _file_review_report,
    ),
    ToolSpec(
        "file_close_report",
        "File the close-out's user report (close phase, once the knowledge delta is applied). You "
        "supply the whole body, filled from its template; the tool owns the path and refuses a "
        "report with an unfilled slot left in it.",
        FilePhaseReportArgs, _file_close_report,
    ),
    ToolSpec(
        "file_vet_report",
        "File the verification report (vet phase, after every check is recorded): the verdict "
        "and check table are derived from the recorded entries; you supply only the "
        "observations (real concerns, never fixes).",
        FileVetReportArgs, _file_vet_report,
    ),
    ToolSpec(
        "write_checkpoint",
        "Bank a session-continuity checkpoint onto a work-item (working-on / decisions / "
        "remaining / notes) — what a fresh session cold-starts from.",
        WriteCheckpointArgs, _write_checkpoint,
    ),
    ToolSpec(
        "sync_from_main",
        "Freshness merge for a work-item's git worktree: merge the trunk INTO the item branch "
        "(run during long builds and before delivering; requires a clean tree — commit first).",
        SyncFromMainArgs, _sync_from_main,
    ),
    ToolSpec(
        "apply_knowledge_delta",
        "Write this item's changes into the general dev-knowledge anchor docs (structured edit "
        "ops), and record the entry in this week's change log. Close-phase only, and the only way "
        "those docs ever change — never edit them directly.",
        ApplyKnowledgeDeltaArgs, _apply_knowledge_delta,
    ),
    ToolSpec(
        "revise_plan",
        "Fold review feedback into an EXISTING plan.md — the only way a re-plan changes it. One "
        "entry per concern, each with its own scope (resume | targeted | redesign), so redesigning "
        "one part never resets the progress another part earned. Never rewrite the file: `## Tasks` "
        "takes task-level ops so the `- [x]` build earned survives. Appends the revision block the "
        "next build reads first, and opens a fresh build⟷vet generation.",
        RevisePlanArgs, _revise_plan,
    ),
]


# Three groups for reading; TOOL_SCOPES decides what a session mounts. The learning pens must
# never reach the main chat agent.

# Read-only tools every dev turn gets. The learning-pool reads live here because they mutate
# nothing.
MAIN_DEV_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "read_dev_log",
        "This repo's dev activity log — the cross-run record of what's happened in its dev work over "
        "time (agent runs, learning-pipeline steps, inbox & work-item changes, constitution/asset "
        "edits), newest first.",
        DevLogArgs, _dev_log,
    ),
    ToolSpec(
        "read_inbox",
        "Read this repo's inbox, open first. TWO kinds share it: `item` — a capture awaiting the "
        "owner's push into real work — and `note`, the owner's own jotting, which is never pushed "
        "and never becomes work. A note is there to be TALKED ABOUT: when the owner refers to one, "
        "read it and pick the conversation up from there.",
        InboxArgs, _list_inbox,
    ),
    ToolSpec(
        "read_candidates",
        "Read the operational-learning candidate pool (what capture has filed), newest first.",
        ReviewCandidatesArgs, _review_candidates,
    ),
    ToolSpec(
        "read_proposals",
        "Read the OPEN operational-learning proposals (consolidated from the candidate pool and "
        "awaiting the owner's gate), newest first.",
        ReviewProposalsArgs, _review_proposals,
    ),
    ToolSpec(
        "read_run",
        "Read one agent run's execution trace — its prompt/reply/tool-call trail + outcome "
        "(pass a run_id) — or omit run_id to list recent runs.",
        ReadRunArgs, _read_run,
    ),
    ToolSpec(
        "create_inbox_item",
        "Create one inbox item (ticket) from a discussion — the sanctioned way to itemize real "
        "work. Also the branch-off front door: pass spawned_from_item + relation "
        "(blocking/parallel auto-push into a child work-item; spawn waits for the owner).",
        CreateInboxItemArgs, _create_inbox_item,
    ),
    ToolSpec(
        "append_inbox_item",
        "Append new discussion content onto an EXISTING inbox item (never edits it) — the dedup path.",
        AppendInboxItemArgs, _append_inbox_item,
    ),
    ToolSpec(
        "push_inbox_item",
        "Push an OPEN inbox item into the workspace: mints its work-item and starts the autonomous "
        "triage→plan→build⟷vet→review flow on it — the same act as the Push button on the inbox "
        "card. Use it when the owner has named a specific item and told you to start it; never on "
        "your own initiative, and never as a follow-on to create_inbox_item (a freshly filed item is "
        "theirs to review first). Takes the item's numeric id from read_inbox and returns the new "
        "work-item id plus whether its first triage run started. Refused inside a work-item session — "
        "there, a branch-off via create_inbox_item is the way to spin work off.",
        PushInboxItemArgs, _push_inbox_item,
    ),
    ToolSpec(
        "itemize_and_launch",
        "Launch a cohort of autopilot work-items from a settled onboarding plan — one call creates "
        "them all, wired by their dependency edges, each born on autopilot and flowing "
        "triage→plan→build⟷vet→review with no human until a review gate. The end-of-onboarding "
        "step; use ONLY after the owner has confirmed the item list.",
        ItemizeAndLaunchArgs, _itemize_and_launch,
    ),
]

# Pipeline-only write pens. These must never reach the main chat agent, which would bypass
# automatic capture.
LEARNING_DEV_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "file_candidate",
        "File one durable operational learning found in a swept conversation slice as a candidate.",
        FileCandidateArgs, _file_candidate,
    ),
    ToolSpec(
        "stage_artifact",
        "Stage the final authored artifact for a proposal (the write phase's pen → drafted).",
        StageArtifactArgs, _stage_artifact,
    ),
    ToolSpec(
        "propose_memory",
        "File one consolidated operational-learning proposal from processed candidates.",
        ProposeMemoryArgs, _propose_memory,
    ),
    ToolSpec(
        "merge_into_proposal",
        "Fold new candidate(s) into an existing open proposal — cross-run consolidation of a recurring learning.",
        MergeProposalArgs, _merge_into_proposal,
    ),
    ToolSpec(
        "drop_candidates",
        "Permanently drop candidates that fail distill's gate (keeps the pool lean — quality over quantity).",
        DropCandidatesArgs, _drop_candidates,
    ),
]

DEV_TOOLS: list[ToolSpec] = MAIN_DEV_TOOLS + ITEM_DEV_TOOLS + LEARNING_DEV_TOOLS   # full set (for reference/tests)

_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in DEV_TOOLS}

# Session scope: the tools it may see. A session that cannot do another phase's job should not
# hold its pen.
TOOL_SCOPES: dict[str, tuple[str, ...]] = {
    # --- chat surfaces (ws.py), by session kind ---
    "general": ("read_dev_log", "read_inbox", "read_run", "read_candidates", "read_proposals",
                "create_inbox_item", "append_inbox_item", "push_inbox_item", "itemize_and_launch"),
    "onboarding": ("read_dev_log", "read_inbox", "create_inbox_item", "append_inbox_item",
                   "itemize_and_launch"),
    "diagnosis": ("read_dev_log", "read_run"),          # strictly read-only, by design
    # --- the item phases: scope name == the skill the run fires ---
    "triage": ("scaffold_artifact", "set_triage_classification", "create_inbox_item",
               "read_decisions", "file_triage_report"),
    "plan": ("scaffold_artifact", "dry_run_checks", "read_verification_library",
             "file_plan_report", "revise_plan", "read_dev_log"),
    "build": ("record_validation", "request_authorization", "sync_from_main", "write_checkpoint",
              "create_inbox_item", "file_build_report"),
    "vet": ("record_verification", "record_diagnosis", "record_lens", "nominate_check",
            "read_verification_library", "file_vet_report"),
    "review": ("scaffold_artifact", "request_authorization", "read_decisions",
               "file_review_report"),
    "close": ("apply_knowledge_delta", "read_verification_library", "create_inbox_item",
              "file_close_report"),
    "investigate": ("scaffold_artifact", "write_checkpoint", "read_decisions",
                    "file_investigate_report"),
    # `itemize_and_launch` belongs to the chat scopes, where the owner approves each call; this
    # run has nobody to ask.
    "itemize": ("read_inbox", "read_dev_log", "read_research_proposals", "create_inbox_item"),
    # --- kernel-fired turns that are not a phase ---
    "deputy": ("read_dev_log", "read_run"),             # it judges a report; it writes a verdict
    "handoff": ("write_checkpoint",),                   # the pre-compaction turn, item-bound branch
    "resolve": ("read_dev_log",),                       # merge-conflict resolution: git work, no pens
    # --- the disposable learning runs (learning.py), one scope each ---
    "capture": ("file_candidate", "read_dev_log"),
    "distill": ("read_candidates", "read_proposals", "propose_memory", "merge_into_proposal",
                "drop_candidates"),
    "write": ("stage_artifact",),
}

# A scope naming a tool that does not exist is a typo, and should never reach a live run.
for _scope, _names in TOOL_SCOPES.items():
    if (_missing := [n for n in _names if n not in _BY_NAME]):
        raise KeyError(f"TOOL_SCOPES[{_scope!r}] names unknown dev tool(s): {', '.join(_missing)}")


def make_dev_mcp_server(store, context_id: str, *, scope: str, **deps):
    """Build the `dev` MCP server for one context, carrying only the tools `scope` may see.

    Unknown scopes raise. Optional deps thread per-turn state to specific tools."""
    return build_mcp_server("dev", dev_tool_specs(scope),
                            store=store, context_id=context_id, **deps)


def dev_tool_specs(scope: str) -> list[ToolSpec]:
    """The EXACT spec list `make_dev_mcp_server` mounts for a scope. One source, so the prompt
    inspector cannot claim a turn carried tools it didn't."""
    try:
        names = TOOL_SCOPES[scope]
    except KeyError:
        raise KeyError(f"unknown dev tool scope {scope!r} — known scopes: "
                       f"{', '.join(sorted(TOOL_SCOPES))}") from None
    return [_BY_NAME[n] for n in names]
