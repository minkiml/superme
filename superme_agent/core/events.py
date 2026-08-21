"""TurnEvent — the surface-neutral output stream of one agent turn.

  Init       the turn started: available slash commands and resolved model
  TextDelta  incremental assistant text
  Status     a tool is about to run — raw name and input
  ToolResult that call's output, persisted to the run trail, never streamed to chat
  Usage      a per-step token snapshot
  Result     the turn finished: final text plus run metadata

No emoji, no markdown, no surface specifics.
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
    tool_id: str | None = None   # pairs this call with its result
    # The sub-agent spawn this call happened inside, or None. A fan-out interleaves its children
    # into one stream, so without this the trail is unattributable.
    parent_tool_id: str | None = None


@dataclass
class ToolResult:
    """The output of the tool call named by the PRECEDING Status, correlated by tool_use_id.
    Persisted (truncated) to the run trail, never streamed to the chat UI."""
    tool_name: str
    content: str
    is_error: bool = False
    tool_id: str | None = None  # the call this result answers
    parent_tool_id: str | None = None  # the sub-agent spawn it came back inside


@dataclass
class Usage:
    """The usage of ONE API call, emitted per assistant step. Steps of one call share a
    `message_id` and repeat its usage, so a reader must DEDUPE by it or over-count.

    Deduped, this — not `Result.usage` — is the run's total: subagent calls arrive on this same
    stream, while `Result.usage` counts only the parent conversation."""
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    ctx_pct: int | None = None  # context-window fill so far (approx)
    usage: dict | None = None  # raw per-step SDK usage (all four token types)
    # The API call this step belongs to. Steps share it, so a live counter must dedupe by it.
    message_id: str | None = None


@dataclass
class Result:
    text: str
    model: str | None = None
    ctx_pct: int | None = None
    context_window: int | None = None
    session_id: str | None = None
    tokens: int | None = None  # final total for this run
    usage: dict | None = None  # whole-turn SDK usage (finish-time fallback)


TurnEvent = Init | TextDelta | Status | ToolResult | Usage | Result
