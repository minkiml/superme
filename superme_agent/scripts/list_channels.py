"""List Slack channels the bot can see (id + name), to fill registry.yaml.

Run:  python -m superme_agent.scripts.list_channels
(Private channels appear only after the bot is invited to them.)
"""

import os
import asyncio

from slack_sdk.web.async_client import AsyncWebClient

from superme_agent.runtime import config  # noqa: F401 — loads .env (SLACK_BOT_TOKEN)


async def main() -> None:
    web = AsyncWebClient(token=os.environ["SLACK_BOT_TOKEN"])
    rows, cursor = [], None
    while True:
        resp = await web.conversations_list(
            types="public_channel,private_channel", limit=200, cursor=cursor
        )
        for c in resp["channels"]:
            rows.append((c["id"], "✓" if c.get("is_member") else " ", c["name"]))
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    print(f"{'CHANNEL ID':12}  bot?  name")
    for cid, member, name in sorted(rows, key=lambda r: r[2]):
        print(f"{cid:12}   {member}    #{name}")


if __name__ == "__main__":
    asyncio.run(main())
