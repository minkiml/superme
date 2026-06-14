"""Run the web BFF.

    python -m web.bff            (from the repo root, in the my-agent env)

Serves /api on http://127.0.0.1:8000, forwarding to the Core daemon. The frontend
(Vite dev server) proxies /api here.
"""

import os

from . import _env  # noqa: F401  (loads the repo-root .env before reading os.environ)

import uvicorn

HOST = os.environ.get("SUPERME_BFF_HOST", "127.0.0.1")
PORT = int(os.environ.get("SUPERME_BFF_PORT", "8000"))


def main() -> None:
    uvicorn.run("web.bff.server:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
