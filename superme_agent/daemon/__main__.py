"""Run the SuperMe Core daemon.

    python -m superme_agent.daemon      (from the repo root, in your Python environment)

Localhost only. Surfaces connect to ws://HOST:PORT/ws/agent.
"""

import uvicorn

from ..core.auth import auth_status
from ..paths import DAEMON_HOST, DAEMON_PORT, log


def main() -> None:
    if not (auth := auth_status())["ready"]:
        log.warning("no Anthropic credential — every agent turn will fail: %s", auth["detail"])
    log.info("Starting SuperMe Core daemon on http://%s:%s", DAEMON_HOST, DAEMON_PORT)
    uvicorn.run(
        "superme_agent.daemon.server:app",
        host=DAEMON_HOST,
        port=DAEMON_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
