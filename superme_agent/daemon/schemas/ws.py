"""Typed WebSocket frame models for the agent socket (`/ws/agent`) — R6.

Every frame is a JSON object discriminated on `type`. `protocol.py` serializes EVERY outbound frame
through these models (single source of truth), and the router validates inbound frames against them.
The combined JSON schema (`WsFrames`) is exported for the frontend's WS-type codegen, the same drift-
proof pipeline R8 uses for REST — see `scripts/ws_schema.py` + `npm run gen:ws`.

  /ws/agent      client → daemon : turn · approval_response · watch
                 daemon → client : init · text_delta · status · usage · approval_request ·
                                   result · error · timeline
  /ws/dashboard  daemon → client : dashboard_hello · invalidate   (send-only; the browser speaks
                                   only by closing)
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
    """A live event from a work-item's run that this panel is WATCHING (F2 unified timeline) —
    pushed from the item's broker (`item_stream`), independent of any turn this panel fired. Carries
    which run it belongs to; run-lock means one live run per item, so the FE appends it to the item's
    CURRENT phase lane. Kinds mirror the run trail: `reply` (assistant text) · a tool/skill call
    (status kinds) · `result` (a call's output)."""
    type: Literal["timeline"] = "timeline"
    item_id: str
    run_id: int | None = None
    kind: str
    name: str | None = None
    description: str | None = None
    tool_id: str | None = None


class InvalidateFrame(BaseModel):
    """`/ws/dashboard` → the browser: "everything under these topics changed; refetch it."

    **Carries no values, by design.** The frame names cache topics (`dev:<repo>:`, `sys:`); the
    browser then reads over ordinary HTTP. That keeps ONE source for every number on screen, so a
    pushed frame and a polled read cannot disagree — the failure mode that would otherwise come with
    a push channel. Coalesced: one frame per burst, carrying the union of its topics."""
    type: Literal["invalidate"] = "invalidate"
    topics: list[str] = []


class DashboardHelloFrame(BaseModel):
    """Sent once when a dashboard panel connects. Its arrival is the browser's signal that push is
    live — which is when it raises its polling to the slow backstop. Losing the socket drops it back
    to the ordinary cadence automatically, so a dead channel degrades to the old behaviour rather
    than to silence."""
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
    # Session KIND + subject pointer (session-kinds-diagnose). Only meaningful at a session's BIRTH
    # (no resume row yet); on resume the session's STORED kind wins, so a stale/rogue payload can't
    # re-point it. v1: kind='diagnosis' + subject_run_id=<Activity run> opens a read-only diagnosis
    # session pointed at that run. Absent ⇒ inferred (item_id ⇒ work_item else general).
    kind: str | None = None
    subject_run_id: int | None = None


class ApprovalResponseFrame(BaseModel):
    """The owner's decision on a pending approval_request."""
    type: Literal["approval_response"] = "approval_response"
    id: str | None = None
    approved: bool = False


class WatchFrame(BaseModel):
    """Client → daemon: "this panel is now watching work-item `item_id`" — subscribe it to that
    item's live event broker so build/vet/other-phase runs stream in (F2). `item_id: null` stops
    watching (panel closed / switched away). Independent of turns: watching is passive observation,
    firing a turn is separate."""
    type: Literal["watch"] = "watch"
    item_id: str | None = None


# --- discriminated unions + the combined schema container --------------------------

OutboundFrame = Annotated[
    Union[InitFrame, TextDeltaFrame, StatusFrame, UsageFrame,
          ApprovalRequestFrame, ResultFrame, ErrorFrame, TimelineFrame,
          # /ws/dashboard's two frames. A separate socket, but the same frame vocabulary and the
          # same codegen artifact — one protocol file, not two.
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
