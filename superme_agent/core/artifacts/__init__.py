"""Artifact machinery — one template plus a deterministic scaffolder per kind.

THE STANDARD: agent supplies content, code supplies form. Code owns frontmatter, section order,
ids and timestamps; the agent fills `<fill:…>` slots. A self-check runs at the CONSUMING gate.
"""

from .text import (log, FILL, atomic_write, split_sections, _section_filled, clip, _FENCE,
                   _fenced_blocks, _LABEL_LINE, _HEADING, _one_line)
from .templates import _TEMPLATE_HOMES, _template_cache, skill_template, template_section_spec
from .spec import (_FM_BLOCK, _FM_RESEARCH_KIND, _SPECS, _PLAN_REQUIRED_LEGACY,
                   _PLAN_FEED_SECTIONS, _PLAN_REQUIRED_V1, _PLAN_REQUIRED_V2,
                   _PLAN_REQUIRED_RESEARCH_V1, ARTIFACT_KINDS, _template_name, section_spec,
                   required_sections, artifact_file, ARTIFACT_READERS)
from .scaffold import (_HANDOFF, _template, _inject_checks, scaffold, _BRIEF_SECTIONS,
                       write_handoff_brief)
from .vet_plan import (VET_DEPTHS, VET_MODES, VET_CHECK_ID, _VET_HEADER_KEY, _VET_FIELD,
                       _RUBRIC_ITEM, _VET_CHECK_HEAD, _VET_VAGUE, _VET_EXPECT_MIN,
                       _PROVES_MACHINE, _PROVES_MIN, vet_value, parse_check_blocks,
                       parse_vet_plan, _SUITE_RUNNERS, _SUITE_NARROWERS, _SUITE_RUN,
                       is_whole_suite_run, vet_plan_hard_issues, _RETIRED_DOC_REF,
                       vet_plan_soft_flags, _VET_DEPTH_RANK, parse_inner_checks, _is_legacy_plan,
                       _plan_check_ids, plan_vet_depth)
from .touches import TOUCH_ACTIONS, parse_touches, touches_hard_issues
from .tasks import _TASK_LINE, parse_tasks, _DECISION_HEAD, parse_decisions
from .cycles import (_CYCLE_FILE, _VET_REPORT_FILE, _CYCLE_REVISION, cycle_reports,
                     latest_cycle_report, _cycle_closed, scaffold_cycle, _append_to_section,
                     _OUTCOME_HEAD, append_cycle_outcome, read_cycle_outcomes)
from .proposals import (_PROPOSAL_FIELDS, _PROPOSAL_FIELD, RESERVED_REASONS, BECOMES_WORK,
                        research_proposals, proposal_is_withheld, filed_and_withheld,
                        proposal_promotable, proposal_becomes_work, filed_and_settled,
                        research_proposal_issues)
from .authorization import (AUTH_SCOPES, _AUTHORIZATION_FILE, _AUTHORIZATION_HEAD,
                            record_authorization, authorization_entries, pending_authorizations,
                            resolve_authorization)
from .ledger import (BY_MACHINE, BY_AGENT, KIND_VERDICT, KIND_DIAGNOSIS, KIND_LENS,
                     KIND_NOMINATION, KIND_AUDIT, VALIDATION_FENCE, VERIFICATION_FENCE,
                     STANDING_LENSES, LENSES, SEVERITIES, _LENS_GATES_AT, repo_fingerprint,
                     _EVIDENCE_HEAD, _resolve_evidence_check, record_validation, validation_runs,
                     record_validation_audit, validation_audit, validation_discrepancies,
                     _parse_ledger_entries, record_verification, record_diagnosis, record_lens,
                     record_nomination, nominations, lens_reads, _gating, lens_gaps,
                     missing_lenses, undiagnosed_failures, evidence_entries, _ledger, diagnoses,
                     _TAGGED_BULLET, _tagged_bullets, proof_rows, verdict_rows, _NO_VET_LINE,
                     note_no_verification, evidence_status)
from .reports import (_space_labels, _DEAD_VALUES, _dead_label, _live_body, _drop_dead_blocks,
                      changed_since, report_text, _LABEL_VALUE, label_values, report_summary,
                      triage_facts, report_issues, report_body_issues, _OWNER_DECISION, owner_decision, proposed_work,
                      delivered_line)
from .owner_input import (FROM_YOU, _OWNER_BLOCKS, _OWNER_BULLET, _owner_blocks, _owner_slots,
                          owner_input, _CARRY_CAP, _DECISIONS, carry_owner_input,
                          _render_from_you, write_owner_input)
from .self_check import (self_check, OWNER_EDITABLE, _EDITED_LINE, owner_edited_at, owner_edit,
                         artifact_status)
from .user_reports import (_how_checked, _slot, _LENS_LINE, _bold_lenses, write_plan_user_report,
                           write_vet_user_report)
from .pr_notes import _NOTE, _NONE, _bullets, _note_fields, pr_task_notes, pr_task_guide
from .checkpoints import (_SIG_HEX, _SIG_NUM, _SIG_JUNK, _normalize_signature,
                          convergence_fingerprint, write_checkpoint, checkpoint_feed,
                          latest_checkpoint, session_memory_path, read_session_memory)
