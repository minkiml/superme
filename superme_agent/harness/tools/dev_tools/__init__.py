"""In-process tools giving the DEV agent scoped, on-demand access to dev-knowledge.

File-backed knowledge is read with the agent's native Read/Grep; DB-backed, structured-query
knowledge gets a typed tool here.

Each tool is a `ToolSpec` whose `Annotated` schema fields carry per-param docs.
"""

from .render import (_qid, _artifact_name, _event_refs, _day_range, _fmt, _fmt_candidates,
                     _fmt_proposals, _fmt_run_list, _fmt_run_trace, _ids, _s, _err, _ok)
from .learning import (_LOG_LEVELS, DevLogArgs, FileCandidateArgs, StageArtifactArgs,
                       ReviewCandidatesArgs, ReadRunArgs, DropCandidatesArgs, ProposeLearningArgs,
                       ReviewProposalsArgs, MergeProposalArgs, _dev_log, _read_run,
                       _file_candidate, _stage_artifact, _review_candidates, _drop_candidates,
                       _propose_learning, _review_proposals, _merge_into_proposal)
from .inbox import (InboxArgs, _fmt_inbox, _list_inbox, CreateInboxItemArgs, _wk_note,
                    _BRIEF_FIELDS, _brief_nudge, _create_inbox_item, PushInboxItemArgs,
                    _push_inbox_item, ItemizeItemArgs, ItemizeAndLaunchArgs, _itemize_and_launch,
                    AppendInboxItemArgs, _append_inbox_item)
from .items import (_item_dir, _bound_err, ScaffoldArtifactArgs, _scaffold_artifact,
                    SetTriageClassificationArgs, _set_triage_classification, WriteCheckpointArgs,
                    _write_checkpoint, SyncFromAnchorBranchArgs,
                    _sync_from_anchor_branch)
from .verification import (CheckPlanCommandsArgs, _check_plan_commands, RecordValidationArgs,
                           _record_validation, RecordVerificationArgs, _record_verification,
                           RecordDiagnosisArgs, _record_diagnosis, RecordLensArgs, _record_lens,
                           NominateCheckArgs, _nominate_check, ReadVerificationLibraryArgs,
                           _read_verification_library)
from .records import (ReadDecisionsArgs, _read_decisions, ReadResearchProposalsArgs,
                      _read_research_proposals, RequestAuthorizationArgs, _request_authorization)
from .reports import (FilePlanReportArgs, _file_plan_report, FilePhaseReportArgs,
                      _file_phase_report, FileVetReportArgs, _file_vet_report)
from .revisions import (KnowledgeOpArg, ApplyKnowledgeEditsArgs, _apply_knowledge_edits,
                        PlanOpArg, PlanChangeArg, RevisePlanArgs, _revise_plan)
from .scopes import (ITEM_DEV_TOOLS, MAIN_DEV_TOOLS, LEARNING_DEV_TOOLS, DEV_TOOLS, _BY_NAME,
                     TOOL_SCOPES, make_dev_mcp_server, dev_tool_specs)
