"""Whether SuperMe can reach Anthropic at all, and the one place that answers it.

A plan token in .env or a signed-in Claude CLI. An API key is never used.
"""

import json
import os
import subprocess
import time

from ..paths import ENV_FILE, log
from .vocab import console

# A credential changes when a person runs a command, so one probe a minute is plenty.
_TTL_S = 60
_CLI_TIMEOUT_S = 10

_cached: dict | None = None
_cached_at = 0.0


def auth_status(*, refresh: bool = False) -> dict:
    """`{ready, method, detail}` — `method` is `token`, `cli`, or None when nothing is set.

    A token is taken at face value: proving it works needs a billable call.
    """
    global _cached, _cached_at
    if not refresh and _cached is not None and time.monotonic() - _cached_at < _TTL_S:
        return dict(_cached)
    _cached, _cached_at = _resolve(), time.monotonic()
    return dict(_cached)


def _resolve() -> dict:
    where = {"env_file": str(ENV_FILE), "cli_installed": console.argv("claude") is not None}
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return {**where, "ready": True, "method": "token",
                "detail": "a plan token is set in .env"}
    if (probe := console.argv("claude", "auth", "status")) is None:
        return {**where, "ready": False, "method": None,
                "detail": "Claude Code is not installed. Install it, then run `claude auth login` "
                          "or add a token to .env"}
    if _cli_signed_in(probe):
        return {**where, "ready": True, "method": "cli",
                "detail": "the Claude CLI on this machine is signed in"}
    return {**where, "ready": False, "method": None,
            "detail": "Claude is not signed in. Run `claude auth login` or add a token to .env"}


def _cli_signed_in(probe: list[str]) -> bool:
    """Ask the CLI itself, so the answer holds on every platform it runs on.

    Only `loggedIn` is read, and nothing from the reply is logged or returned."""
    try:
        p = subprocess.run(probe, capture_output=True, text=True,
                           timeout=_CLI_TIMEOUT_S, encoding="utf-8")
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("could not ask the Claude CLI for auth status: %s", e)
        return False
    try:
        return bool(json.loads(p.stdout or "{}").get("loggedIn"))
    except json.JSONDecodeError:
        # Reading an unparsed reply as a yes restores the silent failure this check exists to end.
        log.warning("`claude auth status` did not answer in JSON — treating auth as unknown")
        return False
