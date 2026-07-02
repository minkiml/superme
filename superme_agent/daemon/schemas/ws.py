"""Typed WebSocket frame models for the agent socket (`/ws/agent`) — R6.

Every frame is a JSON object discriminated on `type`. `protocol.py` serializes EVERY outbound frame
through these models (single source of truth), and the router validates inbound frames against them.
The combined JSON schema (`WsFrames`) is exported for the frontend's WS-type codegen, the same drift-
proof pipeline R8 uses for REST — see `scripts/ws_schema.py` + `npm run gen:ws`.

  client → daemon : turn · approval_response
  daemon → client : init · text_delta · status · usage · approval_request · result · error
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


# --- daemon → client (outbound) ----------------------------------------------------

class InitFrame(BaseModel):
    """Sent on connect + whenever the SDK reveals the live "/" command list."""
    type: Literal["init"] = "init"
    slash_commands: list[str] = []
    model: str | None = None


class TextDeltaFrame(BaseModel):
    """One streamed chunk of the assistant's reply."""
    type: Literal["text_delta"] = "text_delta"
    text: str


class StatusFrame(BaseModel):
    """A tool-use the agent is performing (drives the live call-trail)."""
    type: Literal["status"] = "status"
    tool_name: str
    tool_input: dict = {}


class UsageFrame(BaseModel):
    """A per-step token/context snapshot (accumulated into the run's live telemetry)."""
    type: Literal["usage"] = "usage"
    total_tokens: int
    input_tokens: int
    output_tokens: int
    context_pct: int | None = None


class ApprovalRequestFrame(BaseModel):
    """A tool needs the owner's approval — the client answers with an approval_response."""
    type: Literal["approval_request"] = "approval_request"
    id: str
    tool_name: str
    tool_input: dict = {}


class ResultFrame(BaseModel):
    """The turn finished (or a direct command reply): final text + run metadata. `tokens` is the
    Result aggregate (null for command/busy replies that never ran an agent)."""
    type: Literal["result"] = "result"
    text: str
    model: str | None = None
    context_pct: int | None = None
    context_window: int | None = None
    session_id: str | None = None
    tokens: int | None = None


class ErrorFrame(BaseModel):
    """The turn raised — surfaced to the client."""
    type: Literal["error"] = "error"
    message: str


# --- client → daemon (inbound) -----------------------------------------------------

class TurnFrame(BaseModel):
    """A turn request. Lenient (every field defaulted) so a sparse client frame still validates."""
    type: Literal["turn"] = "turn"
    prompt: str = ""
    context_id: str | None = None
    resume: str | None = None
    model: str | None = None
    effort: str | None = None  # per-turn reasoning-effort override (low|medium|high)
    mode: str | None = None
    work_item_id: str | None = None


class ApprovalResponseFrame(BaseModel):
    """The owner's decision on a pending approval_request."""
    type: Literal["approval_response"] = "approval_response"
    id: str | None = None
    approved: bool = False


# --- discriminated unions + the combined schema container --------------------------

OutboundFrame = Annotated[
    Union[InitFrame, TextDeltaFrame, StatusFrame, UsageFrame,
          ApprovalRequestFrame, ResultFrame, ErrorFrame],
    Field(discriminator="type"),
]

InboundFrame = Annotated[
    Union[TurnFrame, ApprovalResponseFrame],
    Field(discriminator="type"),
]


class WsFrames(BaseModel):
    """Schema container — its JSON schema carries every frame model in `$defs`, the single artifact
    the FE WS-type codegen consumes (`scripts/ws_schema.py`)."""
    inbound: InboundFrame
    outbound: OutboundFrame
