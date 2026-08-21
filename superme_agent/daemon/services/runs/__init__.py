"""Run lifecycle — the helpers that drive a work-item's background runs.

Open and close spine run rows, accumulate telemetry, map tool-use to trail rows, and snapshot
the execution trace.

Imports singletons from `app_state`, never server.py, to avoid an import cycle.
"""

from .lifecycle import (log, DEFAULT_RUN_MODEL, stop_item_work, mark_item_error, retry_notice,
                        _set_status, _begin_run, _LiveTokens, _end_run, _dev_mcp)
from .capture import (_short_path, _int_or_none, _artifact_payload, _artifact_desc, _PROMPT_CAP,
                      _REPLY_CAP, _RESULT_CAP, _result_row, capture_prompt, turn_surface,
                      surface_from_turn, _authored_extras, capture_run_input, capture_event,
                      _capture_event, _publish_timeline)
from .checkpoints import (bank_auto_checkpoint, compacted_checkpoint, compacted_session_memory,
                          reset_vet_thread)
from .completion import read_completion, UNREPORTED, ensure_completion
from .close import fire_close_run, _run_background_close, _clear_or_retry
from .background import (_run_background_plan, _run_background_item_skill, _run_background_triage,
                         _background_intake_run, _run_background_resolve)
from .phases import (fire_review_entry, fire_first_investigate, fire_auto_triage,
                     _PHASE_FEEDBACK_SKILL, _ROUTES_BACK, _SEND_BACK_TARGET, _send_back_phase,
                     fire_phase_feedback, fire_deputy_feedback, _run_deputy_feedback_turn)
from .timeline import build_item_timeline, _render_execution_md
