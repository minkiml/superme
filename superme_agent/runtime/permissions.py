"""Human-in-the-loop approvals — the Slack implementation of the Core's ApproveFn.

The Core decides *whether* a tool needs approval (safe tools auto-run; see
core/permissions.py). This module is the Slack *surface* for the ask: when the Core
calls our ApproveFn for a risky tool (Bash, Write, Edit, …), we post an approval card
in the thread and block until the requester responds — or a timeout auto-denies.

You respond with a native Slack reaction on the card: ✅ to allow, ❌ to deny.
The bot pre-adds those two as one-tap hints. Reactions are honored ONLY from the
original requester (so a stray emoji from someone else — or the bot's own seeded
hint reactions — can't decide the action).
"""

import uuid
import asyncio
import logging

from ..core.permissions import ApproveFn

log = logging.getLogger("superme-agent")

# How long to wait for a human to react ✅/❌ before auto-denying (seconds).
APPROVAL_TIMEOUT = 180

# Emoji names that count as approve / deny on an approval card.
APPROVE_REACTIONS = {"white_check_mark", "heavy_check_mark", "+1", "thumbsup"}
DENY_REACTIONS = {"x", "no_entry", "no_entry_sign", "-1", "thumbsdown"}
APPROVE_EMOJI_SEED = "white_check_mark"
DENY_EMOJI_SEED = "x"


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
    """Slack ✅/❌ reactions as the Core's ApproveFn implementation."""

    def __init__(self):
        # approval_id -> {"future": Future, "user": requester_id, "ts": card_ts}
        self._pending: dict[str, dict] = {}
        # card message ts -> approval_id, so a reaction can find its approval.
        self._ts_index: dict[str, str] = {}

    def make_approver(self, say, client, thread_ts, requester: str) -> ApproveFn:
        """Build an ApproveFn bound to this thread and requester.

        Only ever called by the Core for tools that aren't auto-allowed, so it always
        posts a card and waits. Returns True=allow / False=deny (timeout → deny).
        """

        async def approve(tool_name: str, input_data: dict) -> bool:
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
                return await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT)
            except asyncio.TimeoutError:
                return False
            finally:
                rec = self._pending.pop(approval_id, None)
                if rec and rec.get("ts"):
                    self._ts_index.pop(rec["ts"], None)

        return approve

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
