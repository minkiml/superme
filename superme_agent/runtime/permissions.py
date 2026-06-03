"""Human-in-the-loop approvals.

Mirrors the Claude Code CLI's interactive permission prompt: safe tools run
immediately; risky tools (Bash, Write, Edit, …) post an approval card in the
Slack thread and block the agent loop until the requester responds — or a timeout
auto-denies.

You respond with a native Slack reaction on the card: ✅ to allow, ❌ to deny.
The bot pre-adds those two as one-tap hints. Reactions are honored ONLY from the
original requester (so a stray emoji from someone else — or the bot's own seeded
hint reactions — can't decide the action).
"""

import uuid
import asyncio
import logging

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from .config import (
    APPROVAL_TIMEOUT,
    APPROVE_REACTIONS,
    DENY_REACTIONS,
    APPROVE_EMOJI_SEED,
    DENY_EMOJI_SEED,
)
from ..harness.policy import is_safe

log = logging.getLogger("superme-agent")


def _describe(tool_name: str, input_data: dict) -> str:
    """One-line summary of what the tool is about to do, for the approval card."""
    if tool_name == "Bash":
        return input_data.get("command", "")[:300]
    if tool_name in ("Write", "Edit", "NotebookEdit"):
        return input_data.get("file_path", "") or str(input_data)[:300]
    return str(input_data)[:300]


def _approval_blocks(tool_name: str, input_data: dict) -> list:
    """The Slack Block Kit payload for one approval card (reaction-driven)."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Claude wants to use *{tool_name}*:\n"
                f"```{_describe(tool_name, input_data)}```",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "React :white_check_mark: to allow / :x: to deny "
                    "(tap the ones I added below).",
                }
            ],
        },
    ]


class PermissionManager:
    """Bridges the SDK's `can_use_tool` callback to Slack ✅/❌ reactions."""

    def __init__(self):
        # approval_id -> {"future": Future, "user": requester_id, "ts": card_ts}
        self._pending: dict[str, dict] = {}
        # card message ts -> approval_id, so a reaction can find its approval.
        self._ts_index: dict[str, str] = {}

    def make_callback(self, say, client, thread_ts, requester: str):
        """Build a `can_use_tool` callback bound to this thread and requester."""

        async def can_use_tool(
            tool_name: str, input_data: dict, context: ToolPermissionContext
        ) -> PermissionResultAllow | PermissionResultDeny:
            if is_safe(tool_name, input_data):
                return PermissionResultAllow()

            approval_id = uuid.uuid4().hex
            fut: asyncio.Future = asyncio.get_running_loop().create_future()

            resp = await say(
                thread_ts=thread_ts,
                text=f"Approve {tool_name}?",
                blocks=_approval_blocks(tool_name, input_data),
            )
            card_ts = resp.get("ts")
            card_channel = resp.get("channel")

            self._pending[approval_id] = {
                "future": fut,
                "user": requester,
                "ts": card_ts,
            }
            log.info(
                "approval posted: tool=%s card_ts=%s requester=%s",
                tool_name, card_ts, requester,
            )
            if card_ts:
                self._ts_index[card_ts] = approval_id
                # Pre-add tappable ✅/❌ hints (best-effort; needs reactions:write).
                for emoji in (APPROVE_EMOJI_SEED, DENY_EMOJI_SEED):
                    try:
                        await client.reactions_add(
                            channel=card_channel, timestamp=card_ts, name=emoji
                        )
                    except Exception:
                        pass

            try:
                approved = await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT)
            except asyncio.TimeoutError:
                return PermissionResultDeny(message="Approval timed out (no response).")
            finally:
                rec = self._pending.pop(approval_id, None)
                if rec and rec.get("ts"):
                    self._ts_index.pop(rec["ts"], None)

            return (
                PermissionResultAllow()
                if approved
                else PermissionResultDeny(message="User denied this action in Slack.")
            )

        return can_use_tool

    def _resolve(self, approval_id: str, approved: bool) -> None:
        """Resolve a pending approval's future (idempotent)."""
        rec = self._pending.get(approval_id)
        if rec and not rec["future"].done():
            rec["future"].set_result(approved)

    def register(self, app) -> None:
        """Wire ✅/❌ reactions to resolve pending approvals."""

        @app.event("reaction_added")
        async def _reaction(event):
            approval_id = self._ts_index.get(event.get("item", {}).get("ts"))
            rec = self._pending.get(approval_id) if approval_id else None
            # Reaction on a non-approval message, or from someone other than the
            # requester (incl. the bot's own seeded ✅/❌) — ignore silently.
            if not rec or event.get("user") != rec["user"]:
                return
            reaction = event.get("reaction", "")
            if reaction in APPROVE_REACTIONS:
                log.info("approval %s ALLOWED via :%s:", approval_id[:8], reaction)
                self._resolve(approval_id, True)
            elif reaction in DENY_REACTIONS:
                log.info("approval %s DENIED via :%s:", approval_id[:8], reaction)
                self._resolve(approval_id, False)
