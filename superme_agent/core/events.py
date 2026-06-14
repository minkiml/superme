"""TurnEvent — the surface-neutral output stream of one agent turn.

`AgentService.run_turn` yields these as the turn progresses. Each surface renders
them however it likes; the Core never formats for a specific surface:

  TextDelta  incremental assistant text (web streams it token-by-token; Slack, which
             posts the final reply, can ignore it and use Result.text)
  Status     the agent is about to run a tool — raw tool name + input, so any surface
             can render its own "what it's doing now" indicator (emoji, spinner, …)
  Result     the turn finished — final text plus run metadata (model, context fill,
             session id) for the surface to persist / display

No emoji, no markdown, no Slack/web specifics live here.
"""

from dataclasses import dataclass


@dataclass
class TextDelta:
    text: str


@dataclass
class Status:
    tool_name: str
    tool_input: dict


@dataclass
class Result:
    text: str
    model: str | None = None
    context_pct: int | None = None
    context_window: int | None = None
    session_id: str | None = None


TurnEvent = TextDelta | Status | Result
