"""TurnEvent — the surface-neutral output stream of one agent turn.

`AgentService.run_turn` yields these as the turn progresses. Each surface renders
them however it likes; the Core never formats for a specific surface:

  Init       the turn started — the session's available slash commands (incl. skills)
             and resolved model, so a surface can offer a "/" command palette
  TextDelta  incremental assistant text (web streams it token-by-token; Slack, which
             posts the final reply, can ignore it and use Result.text)
  Status     the agent is about to run a tool — raw tool name + input, so any surface
             can render its own "what it's doing now" indicator (emoji, spinner, …). Carries
             `parent_tool_id` when the call came from inside a sub-agent, so a trail can nest a
             fan-out under the spawn that made it instead of flattening it into the parent's calls.
  ToolResult a tool call returned — the (truncated) output of the PRECEDING Status call, so a
             surface can persist a full execution trace (call + result). Not streamed to the live
             chat UI; consumed only by the per-run trail (Activity / diagnosis observability).
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
    tool_id: str | None = None   # the SDK tool_use id — lets the trail pair this call with its result
    # The tool_use id of the SUB-AGENT SPAWN this call happened inside (SDK `parent_tool_use_id`),
    # or None for the parent's own calls. A fan-out interleaves its children's calls into one
    # stream, so without this the trail flattens three parallel readers into unattributable soup.
    parent_tool_id: str | None = None


@dataclass
class ToolResult:
    """The output of the tool call named by the PRECEDING Status. `tool_name` is the raw SDK tool
    name (correlated back from the result block's tool_use_id) so the trail can pair result→call;
    `content` is the tool's text output (already joined from the SDK content blocks); `is_error`
    flags a failed call. Persisted (truncated) to the run trail, never streamed to the chat UI."""
    tool_name: str
    content: str
    is_error: bool = False
    tool_id: str | None = None   # the SDK tool_use id this result answers — pairs it back to its call
    parent_tool_id: str | None = None   # the sub-agent spawn this result came back inside (see Status)


@dataclass
class Usage:
    """The usage of ONE API call, emitted per assistant step. Several steps of one call share a
    `message_id` and repeat that call's usage, so a reader must DEDUPE by it — summing every step
    over-counts. Deduped, summing across message_ids gives the turn's real spend, because that is
    how the calls are billed, and it includes SUBAGENT calls: their messages arrive on this same
    stream (tagged `parent_tool_use_id`) and are counted here.

    That last point is why this — not `Result.usage` — is the run's total (measured 2026-08-14:
    `Result.usage` is the parent conversation's own calls only, 8.3x smaller on a fully-delegated
    turn). See `services/runs._LiveTokens`, which does the dedupe and the accumulation.

    `usage` is the raw SDK usage dict for this call, carrying all four token types (input /
    cache_creation / cache_read / output) so the ledger keeps them separate and never drops
    cache_read; `total_tokens` is the legacy critical-sum (excludes cache_read)."""
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    ctx_pct: int | None = None   # context-window fill so far (approx; default window)
    usage: dict | None = None        # raw per-step SDK usage dict (all four token types + extras)
    # The SDK's message id for the API call this step belongs to. Several steps of ONE call share it
    # (one per content block), so a live counter must dedupe by this — summing every step over-counts.
    message_id: str | None = None


@dataclass
class Result:
    text: str
    model: str | None = None
    ctx_pct: int | None = None
    context_window: int | None = None
    session_id: str | None = None
    tokens: int | None = None   # total tokens for this run (final, authoritative; legacy critical-sum)
    usage: dict | None = None   # raw WHOLE-TURN SDK usage dict (finish-time fallback; not re-accumulated)


TurnEvent = Init | TextDelta | Status | ToolResult | Usage | Result
