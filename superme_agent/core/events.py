"""TurnEvent — the surface-neutral output stream of one agent turn.

`Init` · `TextDelta` · `Status` (a tool is about to run) · `ToolResult` · `Usage` · `Result`.
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
    # A fan-out interleaves its children into one stream; without this the trail is
    # unattributable.
    parent_tool_id: str | None = None


@dataclass
class ToolResult:
    """Output of the tool call named by the PRECEDING Status, matched on tool_use_id. Persisted,
    never streamed."""
    tool_name: str
    content: str
    is_error: bool = False
    tool_id: str | None = None  # the call this result answers
    parent_tool_id: str | None = None  # the sub-agent spawn it came back inside


@dataclass
class Usage:
    """One API call's usage, emitted per step. Steps share a `message_id`, so DEDUPE or over-count.

    Deduped, this is the run's true total: subagent calls land here, `Result.usage` is parent-only."""
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
