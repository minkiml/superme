"""Which tools exist on each surface, and which of them a session scope may see."""

from ..registry import ToolSpec, build_mcp_server
from .learning import (DevLogArgs, DropCandidatesArgs, FileCandidateArgs, MergeProposalArgs,
                       ProposeLearningArgs, ReadRunArgs, ReviewCandidatesArgs, ReviewProposalsArgs,
                       StageArtifactArgs, _dev_log, _drop_candidates, _file_candidate,
                       _merge_into_proposal, _propose_learning, _read_run, _review_candidates,
                       _review_proposals, _stage_artifact)
from .inbox import (AppendInboxItemArgs, CreateInboxItemArgs, InboxArgs, ItemizeAndLaunchArgs,
                    PushInboxItemArgs, _append_inbox_item, _create_inbox_item,
                    _itemize_and_launch, _list_inbox, _push_inbox_item)
from .items import (ScaffoldArtifactArgs, SetTriageClassificationArgs,
                    SyncFromAnchorBranchArgs, WriteCheckpointArgs, _scaffold_artifact,
                    _set_triage_classification, _sync_from_anchor_branch, _write_checkpoint)
from .verification import (CheckPlanCommandsArgs, NominateCheckArgs, ReadVerificationLibraryArgs,
                           RecordDiagnosisArgs, RecordLensArgs, RecordValidationArgs,
                           RecordVerificationArgs, _check_plan_commands, _nominate_check,
                           _read_verification_library, _record_diagnosis, _record_lens,
                           _record_validation, _record_verification)
from .records import (ReadDecisionsArgs, ReadResearchProposalsArgs, RequestAuthorizationArgs,
                      _read_decisions, _read_research_proposals, _request_authorization)
from .reports import (FilePhaseReportArgs, FilePlanReportArgs, FileVetReportArgs,
                      _file_phase_report, _file_plan_report, _file_vet_report)
from .revisions import (ApplyKnowledgeEditsArgs, RevisePlanArgs, _apply_knowledge_edits,
                        _revise_plan)

ITEM_DEV_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "set_triage_classification",
        "Records this work-item's classification: its confirmed kind, scale, research family and "
        "the deliverable it anchors to. Use it in the triage phase, once you have read the whole "
        "ask. Do not use it after the triage gate, where the kind is fixed, and do not use it to "
        "invent a deliverable the project has not confirmed. Returns what was recorded.",
        SetTriageClassificationArgs, _set_triage_classification,
    ),
    ToolSpec(
        "scaffold_artifact",
        "Creates this work-item's artifact skeleton: the headings and the fill slots you then "
        "complete in place. Use it once, before writing an artifact that does not exist yet. Do "
        "not use it on an artifact already written: an existing plan changes through "
        "`revise_plan`. Returns the path and the sections whose slots you must fill.",
        ScaffoldArtifactArgs, _scaffold_artifact,
    ),
    ToolSpec(
        "check_plan_commands",
        "Runs the commands already written into this item's plan and reports each exit code. Use "
        "it at the end of planning, to catch a command that cannot run at all before a build "
        "cycle discovers it. Do not use it to judge the work: nothing is built yet, and nothing "
        "is recorded. Returns each command with its result.",
        CheckPlanCommandsArgs, _check_plan_commands,
    ),
    ToolSpec(
        "record_validation",
        "Records one validation run you performed while building: the command, the machine "
        "result, pass or fail. Use it for every check you run yourself, so the claim can later be "
        "re-executed rather than believed. Do not use it for a run you only intend to make, or "
        "for the verification plan's checks. Returns the entry and its cycle.",
        RecordValidationArgs, _record_validation,
    ),
    ToolSpec(
        "record_verification",
        "Records one of the verification plan's checks that you executed live. Use it once per "
        "check id, as you go. Do not use it for a check you reasoned through but did not execute, "
        "or for the build's own validation, which is recorded already. Returns the entry, its "
        "cycle and the verdict so far.",
        RecordVerificationArgs, _record_verification,
    ),
    ToolSpec(
        "record_diagnosis",
        "Records where a failed check broke and why, plus what you could not determine. Use it on "
        "every failing check, before filing the vet report. Do not use it to record the fix: the "
        "next build cycle reasons that out from this as its work order. Returns the diagnosis and "
        "its cycle.",
        RecordDiagnosisArgs, _record_diagnosis,
    ),
    ToolSpec(
        "record_lens",
        "Records one standing lens's read of this cycle: what you probed, and what it found. Use "
        "it for all three standing lenses every cycle, since the vet report will not write "
        "without them. Do not use it to manufacture a finding: nothing found is a complete "
        "answer. Returns the entry, its cycle, and whether it gates.",
        RecordLensArgs, _record_lens,
    ),
    ToolSpec(
        "nominate_check",
        "Nominates one of this item's checks for the repo's verification library, the catalogue "
        "later items inherit from. Use it for a check that passed here and whose value you can "
        "state about the repo without naming this item. Do not use it to add the entry yourself: "
        "close writes it and the owner decides. Returns the nomination.",
        NominateCheckArgs, _nominate_check,
    ),
    ToolSpec(
        "read_verification_library",
        "Lists this repo's verification library: the standing entries already attached to every "
        "plan, and the available ones a plan can cite by id. Use it while planning, to reuse a "
        "check instead of authoring one. Do not use it to add an entry: that is `nominate_check`. "
        "Returns the standing and available entries with their ids.",
        ReadVerificationLibraryArgs, _read_verification_library,
    ),
    ToolSpec(
        "read_decisions",
        "Reads this project's decision ledger, the choices the owner has already ruled on. Use it "
        "before raising a question or scoping an item, since a subject settled here is already "
        "answered. Do not use it to record a decision. Returns the index of every decision, or "
        "one full entry when entry_id is given.",
        ReadDecisionsArgs, _read_decisions,
    ),
    ToolSpec(
        "read_research_proposals",
        "Reads an approved research review's proposed work, split already into what you may file "
        "and what you may not. Use it before filing anything from a research report, rather than "
        "judging the report yourself. Do not use it to file a withheld proposal: one whose ruling "
        "was never given only looks startable. Returns both lists and why each was withheld.",
        ReadResearchProposalsArgs, _read_research_proposals,
    ),
    ToolSpec(
        "request_authorization",
        "Requests the owner's authorization for a contract change you cannot make yourself, such "
        "as an edit to an owner-reserved anchor doc. Use it when a vet check is blocked by one, "
        "so that check defers instead of failing. Do not use it to edit the vet plan or force the "
        "change through. Returns the request and where it surfaces.",
        RequestAuthorizationArgs, _request_authorization,
    ),
    ToolSpec(
        "file_plan_report",
        "Files the report the owner reads at the plan gate. You supply the prose; the coverage "
        "matrix comes from the plan itself. Use it once the plan is finished. Do not use it for "
        "another phase's report, and do not hide a task that has no check: a gap shows on "
        "purpose. Returns the path, the counts and the gaps.",
        FilePlanReportArgs, _file_plan_report,
    ),
    ToolSpec(
        "file_phase_report",
        "Files the report the owner reads at this phase's gate. Hand over the whole body, filled "
        "from this phase's template with every slot replaced. Use it once the phase's work is "
        "done. Do not use it for another phase's report: the path is not yours to name, and an "
        "unfilled slot is refused. Returns the path written.",
        FilePhaseReportArgs, _file_phase_report,
    ),
    ToolSpec(
        "file_vet_report",
        "Files the verification report. You supply the observations; the verdict and check table "
        "come from the entries already recorded. Use it after every check, diagnosis and lens is "
        "recorded. Do not use it to propose fixes, or to report a check you never recorded. "
        "Returns the path and the derived verdict.",
        FileVetReportArgs, _file_vet_report,
    ),
    ToolSpec(
        "write_checkpoint",
        "Banks this thread's continuity onto the work-item: what is being worked on, what "
        "remains, what got decided, what to watch for. Use it before a long session ends or is "
        "compacted. Do not use it to copy what the artifacts already hold: point at the plan and "
        "the reports instead. Returns the checkpoint's path.",
        WriteCheckpointArgs, _write_checkpoint,
    ),
    ToolSpec(
        "sync_from_anchor_branch",
        "Merges the repo's anchor branch into this work-item's branch, so the build runs against "
        "current trunk code. Use it partway through a long build and before delivering, from a "
        "clean tree. Do not use it to push, or to merge the item back: a conflict aborts and "
        "leaves your tree untouched. Returns the merge commit, or that you were current.",
        SyncFromAnchorBranchArgs, _sync_from_anchor_branch,
    ),
    ToolSpec(
        "apply_knowledge_edits",
        "Writes this item's changes into the project's dev-knowledge anchor docs and logs the "
        "entry for the week. Use it in the close phase, as the only way those docs ever change. "
        "Do not use it in another phase, and do not edit those docs directly. Returns the ops "
        "applied and the docs touched.",
        ApplyKnowledgeEditsArgs, _apply_knowledge_edits,
    ),
    ToolSpec(
        "revise_plan",
        "Folds review feedback into an existing plan, one entry per concern, each with its own "
        "scope so redesigning one part does not reset another's progress. Use it when a gate "
        "sends the item back to plan. Do not use it to rewrite the file: task-level ops keep the "
        "progress build has earned. Returns the revision id and what changed.",
        RevisePlanArgs, _revise_plan,
        examples=({
            "item_id": "a1b2c3d4e5f6",
            "feedback": "The cache design is wrong — evict on write, not on a timer.",
            "directive": "Rebuild the cache to evict on write; leave the CLI tasks alone.",
            "still_in_force": "nothing",
            "changes": [
                {"area": "caching design", "scope": "redesign",
                 "note": "Timer eviction replaced by write-through.",
                 "superseded": "t4's timer loop is void — remove it in a new commit.",
                 "ops": [{"op": "update", "section": "## Design",
                          "content": "The cache evicts on write..."},
                         {"op": "remove_task", "task": "t4"}]},
                {"area": "cli tasks", "scope": "resume",
                 "note": "Untouched by the feedback; run again as written."},
            ],
        },),
    ),
]


# Three groups for reading; TOOL_SCOPES decides what a session mounts. The learning pens must
# never reach the main chat agent.

# Read-only tools every dev turn gets. The learning-pool reads live here because they mutate
# nothing.
MAIN_DEV_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "read_dev_log",
        "Lists this repo's dev activity: agent runs, learning steps, inbox and work-item changes, "
        "newest first. Use it when you need to know when something happened, or whether work on "
        "it already exists. Do not use it to read the work itself: an artifact is read at its "
        "path, one run's trail with `read_run`. Returns the matching log rows.",
        DevLogArgs, _dev_log,
    ),
    ToolSpec(
        "read_inbox",
        "Lists this repo's inbox, open items first. An item is a capture awaiting the owner's "
        "push into real work; a note is the owner's own jotting and never becomes work. Use it "
        "before filing anything, and whenever the owner refers to a note. Do not use it to push "
        "an item onward. Returns the matching rows and their ids.",
        InboxArgs, _list_inbox,
    ),
    ToolSpec(
        "read_candidates",
        "Lists the candidate pool: single learnings filed from one conversation each, newest "
        "first, before anyone has judged them. Use it at the start of a distill pass, to see what "
        "is waiting. Do not use it to read proposals: a candidate already consolidated into one "
        "is read with `read_proposals`. Returns the matching candidates and their ids.",
        ReviewCandidatesArgs, _review_candidates,
    ),
    ToolSpec(
        "read_proposals",
        "Lists the open learning proposals standing at the owner's gate, newest first. Use it "
        "before filing a new one, so a learning that recurs strengthens an existing proposal. Do "
        "not use it to read the raw candidates behind them: that is `read_candidates`. Returns "
        "each open proposal with its id and status.",
        ReviewProposalsArgs, _review_proposals,
    ),
    ToolSpec(
        "read_run",
        "Reads one run's execution trace: its prompt, replies, tool calls and outcome. Use it "
        "when you need to know what a particular run actually did. Do not use it to survey "
        "activity across many runs: that is `read_dev_log`. Returns the full trace for a run_id, "
        "or a list of recent runs when run_id is omitted.",
        ReadRunArgs, _read_run,
    ),
    ToolSpec(
        "create_inbox_item",
        "Files one inbox ticket for work that came up in this conversation, and branches work off "
        "the current item. Use it once the discussion settles on something to be done later. Do "
        "not use it to begin that work, to duplicate an item that already covers it, or to record "
        "a decision already made. Returns the new item's id.",
        CreateInboxItemArgs, _create_inbox_item,
    ),
    ToolSpec(
        "append_inbox_item",
        "Adds what this discussion newly says to an inbox item that already covers the work. Use "
        "it when `read_inbox` matched an item missing something you have just settled. Do not use "
        "it to correct or replace the existing text: the addition lands underneath it untouched. "
        "Returns the item's id and title.",
        AppendInboxItemArgs, _append_inbox_item,
    ),
    ToolSpec(
        "push_inbox_item",
        "Turns an open inbox item into a work-item and starts its unattended triage, plan, build, "
        "vet and review flow. Use it when the owner names an item and tells you to start it. Do "
        "not use it on your own initiative, straight after filing an item, or inside a work-item "
        "session. Returns the new work-item id and whether triage started.",
        PushInboxItemArgs, _push_inbox_item,
    ),
    ToolSpec(
        "itemize_and_launch",
        "Creates a whole cohort of work-items from a settled onboarding plan, wired by their "
        "dependency edges and each running unattended until its review gate. Use it once, after "
        "the owner has confirmed the item list. Do not use it to file a single ticket: that is "
        "`create_inbox_item`. Returns every item created with its id.",
        ItemizeAndLaunchArgs, _itemize_and_launch,
    ),
]

# Pipeline-only write pens. These must never reach the main chat agent, which would bypass
# automatic capture.
LEARNING_DEV_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "file_candidate",
        "Files one durable operational learning found in the conversation slice being swept. Use "
        "it for a lesson that would still change what an agent does in a future run. Do not use "
        "it to record what happened, or to decide what form the learning should take: "
        "consolidation and form come later. Returns the candidate's id.",
        FileCandidateArgs, _file_candidate,
    ),
    ToolSpec(
        "stage_artifact",
        "Stages the finished artifact you authored for this proposal, with the eval report that "
        "is the reviewer's evidence. Use it once, as this run's last action. Do not use it before "
        "the artifact passes lint, and do not expect it to publish: nothing reaches disk until "
        "the owner's gate. Returns the proposal id and its drafted status.",
        StageArtifactArgs, _stage_artifact,
    ),
    ToolSpec(
        "propose_learning",
        "Files one consolidated learning proposal from candidates you have read. Use it only for "
        "a learning no open proposal already covers. Do not use it when one does: fold into that "
        "with `merge_into_proposal` instead of filing a parallel proposal. Returns the proposal's "
        "id, its target form and scope, and that nothing is applied yet.",
        ProposeLearningArgs, _propose_learning,
        examples=({
            "title": "Name the branch before editing shared config",
            "body": "Three runs edited the shared config on the trunk and had to be reverted...",
            "summary": "Stops a shared-config edit landing on the trunk · used at build entry",
            "candidate_ids": [41, 47],
            "output_form": "constitution",
            "target_scope": "repo_dev",
            "fields": {"statement": "Cut a branch before editing shared config.",
                       "scope": "any run that writes outside its own module",
                       "rationale": "A trunk edit is reverted, not merged."},
            "clarifications": [{"question": "Does this bind research runs too?",
                                "suggested": "yes", "blocking": False}],
            "confidence": "high",
        },),
    ),
    ToolSpec(
        "merge_into_proposal",
        "Folds new candidates into an existing open proposal, so a learning seen again across "
        "runs strengthens one instead of spawning near-duplicates. Use it when `read_proposals` "
        "shows a proposal already covering the same learning. Do not use it to create a proposal, "
        "or to touch one the owner has settled. Returns the proposal's id, candidate count and "
        "status.",
        MergeProposalArgs, _merge_into_proposal,
    ),
    ToolSpec(
        "drop_candidates",
        "Permanently removes candidates that fail the distill gate: self-recitation, restatements "
        "of the obvious, anything too thin to become an artifact. Use it on candidates you have "
        "just read. Do not use it on one you are unsure about, since the drop cannot be undone. "
        "Returns how many were dropped.",
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
               "read_decisions", "file_phase_report"),
    "plan": ("scaffold_artifact", "check_plan_commands", "read_verification_library",
             "file_plan_report", "revise_plan", "read_dev_log"),
    "build": ("record_validation", "request_authorization", "sync_from_anchor_branch",
              "write_checkpoint",
              "create_inbox_item", "file_phase_report"),
    "vet": ("record_verification", "record_diagnosis", "record_lens", "nominate_check",
            "read_verification_library", "file_vet_report"),
    "review": ("scaffold_artifact", "request_authorization", "read_decisions",
               "file_phase_report"),
    "close": ("apply_knowledge_edits", "read_verification_library", "create_inbox_item",
              "file_phase_report"),
    "investigate": ("scaffold_artifact", "write_checkpoint", "read_decisions",
                    "file_phase_report"),
    # `itemize_and_launch` belongs to the chat scopes, where the owner approves each call; this
    # run has nobody to ask.
    "itemize": ("read_inbox", "read_dev_log", "read_research_proposals", "create_inbox_item"),
    # --- kernel-fired turns that are not a phase ---
    "deputy": ("read_dev_log", "read_run"),             # it judges a report; it writes a verdict
    "handoff": ("write_checkpoint",),                   # the pre-compaction turn, item-bound branch
    "resolve": ("read_dev_log",),                       # merge-conflict resolution: git work, no pens
    # --- the disposable learning runs (learning.py), one scope each ---
    "capture": ("file_candidate", "read_dev_log"),
    "distill": ("read_candidates", "read_proposals", "propose_learning", "merge_into_proposal",
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
                            store=store, context_id=context_id, scope=scope, **deps)


def dev_tool_specs(scope: str) -> list[ToolSpec]:
    """The EXACT spec list `make_dev_mcp_server` mounts for a scope. One source, so the prompt
    inspector cannot claim a turn carried tools it didn't."""
    try:
        names = TOOL_SCOPES[scope]
    except KeyError:
        raise KeyError(f"unknown dev tool scope {scope!r} — known scopes: "
                       f"{', '.join(sorted(TOOL_SCOPES))}") from None
    return [_BY_NAME[n] for n in names]
