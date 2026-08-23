"""Typed WebSocket frame models for the agent socket.

Every frame is discriminated on `type`. `protocol.py` serializes every outbound frame through these
models, and the router validates inbound ones.
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
    ctx_pct: int | None = None


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
    ctx_pct: int | None = None
    context_window: int | None = None
    session_id: str | None = None
    tokens: int | None = None


class ErrorFrame(BaseModel):
    """The turn raised — surfaced to the client."""
    type: Literal["error"] = "error"
    message: str


class TimelineFrame(BaseModel):
    """A live event from a work-item run this panel is WATCHING.

    Run-lock means one live run per item, so the FE appends it to the current phase lane."""
    type: Literal["timeline"] = "timeline"
    item_id: str
    run_id: int | None = None
    kind: str
    name: str | None = None
    description: str | None = None
    tool_id: str | None = None
    parent_tool_id: str | None = None   # the sub-agent spawn this row came from (null = the parent)


class InvalidateFrame(BaseModel):
    """Everything under these topics changed; refetch it.

    Carries no values by design, so a pushed frame and a polled read cannot disagree."""
    type: Literal["invalidate"] = "invalidate"
    topics: list[str] = []


class DashboardHelloFrame(BaseModel):
    """Sent once when a dashboard panel connects.

    Its arrival tells the browser push is live, which is when it drops to the slow backstop."""
    type: Literal["dashboard_hello"] = "dashboard_hello"
    coalesce_ms: int = 250


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
    # Meaningful only at a session's BIRTH; on resume the stored kind wins, so a stale payload
    # cannot re-point it.
    kind: str | None = None
    subject_run_id: int | None = None


class ApprovalResponseFrame(BaseModel):
    """The owner's decision on a pending approval_request."""
    type: Literal["approval_response"] = "approval_response"
    id: str | None = None
    approved: bool = False


class WatchFrame(BaseModel):
    """Client to daemon: this panel is now watching `item_id`, so subscribe it to that item's live event
    broker. `null` stops watching.

    Independent of turns: watching is passive observation."""
    type: Literal["watch"] = "watch"
    item_id: str | None = None


# --- discriminated unions + the combined schema container --------------------------

OutboundFrame = Annotated[
    Union[InitFrame, TextDeltaFrame, StatusFrame, UsageFrame,
          ApprovalRequestFrame, ResultFrame, ErrorFrame, TimelineFrame,
          # A separate socket, but the same frame vocabulary and codegen artifact — one protocol
          # file, not two.
          InvalidateFrame, DashboardHelloFrame],
    Field(discriminator="type"),
]

InboundFrame = Annotated[
    Union[TurnFrame, ApprovalResponseFrame, WatchFrame],
    Field(discriminator="type"),
]


class WsFrames(BaseModel):
    """Schema container — its JSON schema carries every frame model in `$defs`, the single artifact
    the FE WS-type codegen consumes (`scripts/ws_schema.py`)."""
    inbound: InboundFrame
    outbound: OutboundFrame
