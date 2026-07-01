"""TurnEvent — the surface-neutral output stream of one agent turn.

`AgentService.run_turn` yields these as the turn progresses. Each surface renders
them however it likes; the Core never formats for a specific surface:

  Init       the turn started — the session's available slash commands (incl. skills)
             and resolved model, so a surface can offer a "/" command palette
  TextDelta  incremental assistant text (web streams it token-by-token; Slack, which
             posts the final reply, can ignore it and use Result.text)
  Status     the agent is about to run a tool — raw tool name + input, so any surface
             can render its own "what it's doing now" indicator (emoji, spinner, …)
  Usage      a live token-usage snapshot as the turn progresses (per assistant step), so a
             surface can show a running token counter; emitted whenever the SDK reports usage
  Result     the turn finished — final text plus run metadata (model, context fill, tokens,
             session id) for the surface to persist / display

No emoji, no markdown, no Slack/web specifics live here.
"""

from dataclasses import dataclass, field


@dataclass
class Init:
    slash_commands: list = field(default_factory=list)
    model: str | None = None


@dataclass
class TextDelta:
    text: str


@dataclass
class Status:
    tool_name: str
    tool_input: dict


@dataclass
class Usage:
    """A live token snapshot for the in-flight turn (cumulative for this run so far)."""
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    context_pct: int | None = None   # context-window fill so far (approx; default window)


@dataclass
class Result:
    text: str
    model: str | None = None
    context_pct: int | None = None
    context_window: int | None = None
    session_id: str | None = None
    tokens: int | None = None   # total tokens for this run (final, authoritative)


TurnEvent = Init | TextDelta | Status | Usage | Result
