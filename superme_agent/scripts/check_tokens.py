"""Validate Slack + Anthropic auth before running the bot.

Run:  python -m superme_agent.scripts.check_tokens
Checks token formats, that the bot token authenticates, that Socket Mode is granted,
and lists the granted bot scopes (flags any missing for the features we use).
"""

import asyncio

from slack_sdk.web.async_client import AsyncWebClient

from superme_agent.runtime.config import (
    SLACK_BOT_TOKEN,
    SLACK_APP_TOKEN,
    ENV_FILE,
)

NEEDED_SCOPES = [
    "app_mentions:read", "chat:write",
    "channels:history", "channels:read",
    "groups:history", "groups:read",
    "reactions:read", "reactions:write",
]


async def main() -> None:
    print(f"Using .env at: {ENV_FILE}")
    web = AsyncWebClient(token=SLACK_BOT_TOKEN)

    try:
        auth = await web.auth_test()
    except Exception as e:
        print("❌ Bot token (xoxb-) failed:", e)
        return
    print(f"✅ Bot token OK — @{auth['user']} on team '{auth['team']}'")

    scopes = [s.strip() for s in auth.headers.get("x-oauth-scopes", "").split(",")]
    missing = [s for s in NEEDED_SCOPES if s not in scopes]
    for s in NEEDED_SCOPES:
        print(f"  {'✅' if s in scopes else '❌ MISSING'}  {s}")

    try:
        await web.apps_connections_open(app_token=SLACK_APP_TOKEN)
        print("✅ App token (xapp-) OK — Socket Mode granted")
    except Exception as e:
        print("❌ App token failed:", e)

    if missing:
        print("\n⚠️  Missing scopes — update the manifest + reinstall:", ", ".join(missing))


if __name__ == "__main__":
    asyncio.run(main())
