"""
SuperMe — a Slack-hosted Claude agent (host-app vs workspace architecture).

Run:  python -m superme_agent   (from the repo root, in the `my-agent` conda env)

This is the composition root: it wires the runtime modules together and starts the
Socket Mode listener. Layers:
  runtime/   the application (Slack I/O, query loop, sessions, approvals, workspaces)
  harness/   the portable agent brain (persona, policy, in-proc tools, plugin)
  config/    registry.yaml — channel → workspace links
"""

import os
import asyncio

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from .runtime.config import log, warn_on_conflicting_auth
from .runtime.sessions import SessionStore
from .runtime.permissions import PermissionManager
from .runtime.agent import Assistant
from .runtime.slack_app import create_app, register_mention_handler


def build():
    """Compose the app and all of its collaborators."""
    warn_on_conflicting_auth()

    sessions = SessionStore()
    permissions = PermissionManager()
    assistant = Assistant(sessions, permissions)

    app = create_app()
    permissions.register(app)                 # ✅/❌ reaction approvals
    register_mention_handler(app, assistant)  # @mention -> agent
    return app


async def main():
    app = build()
    handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    log.info("Starting SuperMe agent…")
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
