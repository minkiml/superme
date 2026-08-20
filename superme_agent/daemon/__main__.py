"""Run the SuperMe Core daemon.

    python -m superme_agent.daemon      (from the repo root, in the my-agent env)

Localhost only. Surfaces (web BFF, Slack in B2) connect to ws://HOST:PORT/ws/agent.
"""

import uvicorn

from ..paths import DAEMON_HOST, DAEMON_PORT, log, warn_on_conflicting_auth


def main() -> None:
    warn_on_conflicting_auth()
    log.info("Starting SuperMe Core daemon on http://%s:%s", DAEMON_HOST, DAEMON_PORT)
    uvicorn.run(
        "superme_agent.daemon.server:app",
        host=DAEMON_HOST,
        port=DAEMON_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
