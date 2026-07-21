"""WebSocket frame protocol for the agent socket — the single (de)serialization seam (R6).

Every outbound frame is built through a typed model in `schemas/ws.py` and dumped to a plain dict
here, so the wire shape has ONE source of truth (the models), which is also what the FE generates its
WS types from. The router calls these constructors instead of hand-writing dicts.

  client → daemon : turn · approval_response          (parsed via `parse_inbound`)
  daemon → client : init · text_delta · status · usage · approval_request · result · error
"""

from ..core.events import Init, TextDelta, Status, Usage, Result, TurnEvent
from .schemas.ws import (
    InitFrame, TextDeltaFrame, StatusFrame, UsageFrame, ApprovalRequestFrame,
    ResultFrame, ErrorFrame, TimelineFrame, TurnFrame, ApprovalResponseFrame, WatchFrame,
)


def event_to_frame(ev: TurnEvent) -> dict:
    """Serialize a Core TurnEvent into a daemon→client frame (through its typed model)."""
    if isinstance(ev, Init):
        return InitFrame(slash_commands=ev.slash_commands, model=ev.model).model_dump()
    if isinstance(ev, TextDelta):
        return TextDeltaFrame(text=ev.text).model_dump()
    if isinstance(ev, Status):
        return StatusFrame(tool_name=ev.tool_name, tool_input=ev.tool_input or {}).model_dump()
    if isinstance(ev, Usage):
        return UsageFrame(total_tokens=ev.total_tokens, input_tokens=ev.input_tokens,
                          output_tokens=ev.output_tokens, ctx_pct=ev.ctx_pct).model_dump()
    if isinstance(ev, Result):
        return ResultFrame(text=ev.text, model=ev.model, ctx_pct=ev.ctx_pct,
                           context_window=ev.context_window, session_id=ev.session_id,
                           tokens=ev.tokens).model_dump()
    raise TypeError(f"unknown TurnEvent: {ev!r}")


# --- non-event outbound frames (the router builds these directly) ------------------

def init_frame(slash_commands: list[str], model: str | None = None) -> dict:
    return InitFrame(slash_commands=slash_commands, model=model).model_dump()


def result_frame(text: str, *, model: str | None = None, ctx_pct: int | None = None,
                 context_window: int | None = None, session_id: str | None = None,
                 tokens: int | None = None) -> dict:
    """A direct result frame — the command-reply and run-lock-busy replies (no agent ran)."""
    return ResultFrame(text=text, model=model, ctx_pct=ctx_pct,
                       context_window=context_window, session_id=session_id,
                       tokens=tokens).model_dump()


def approval_request_frame(approval_id: str, tool_name: str, tool_input: dict) -> dict:
    return ApprovalRequestFrame(id=approval_id, tool_name=tool_name,
                                tool_input=tool_input or {}).model_dump()


def error_frame(message: str) -> dict:
    return ErrorFrame(message=message).model_dump()


# --- inbound (client → daemon) -----------------------------------------------------

def parse_inbound(msg: dict) -> TurnFrame | ApprovalResponseFrame | WatchFrame | None:
    """Validate a client frame into its typed model (None for an unknown/!dict type), so the router
    works against typed fields instead of `msg.get(...)`."""
    if not isinstance(msg, dict):
        return None
    kind = msg.get("type")
    if kind == "turn":
        return TurnFrame.model_validate(msg)
    if kind == "approval_response":
        return ApprovalResponseFrame.model_validate(msg)
    if kind == "watch":
        return WatchFrame.model_validate(msg)
    return None
