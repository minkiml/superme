"""OS-level sandbox (macOS Seatbelt) around an agent's shell commands.

The permission layer reads command strings, which cannot say where a command writes. This
closes that gap at the kernel. CPU and memory caps are a known gap.
"""

import logging
import shutil
import sys
from pathlib import Path

log = logging.getLogger("superme-agent")

# `allowUnsandboxedCommands: False` is load-bearing — a boundary the occupant can lift is none.
# `autoAllowBashIfSandboxed` stays False so `can_use_tool` still sees every command.
# Network is default-deny by omission: no `allowedDomains`.
# Interactive turns are not sandboxed: a person approves each command.
_POLICY: dict = {
    "enabled": True,
    "autoAllowBashIfSandboxed": False,
    "allowUnsandboxedCommands": False,
    "network": {"allowLocalBinding": True},
}


def sandbox_options(writes: list[Path] | None) -> dict:
    """The `ClaudeAgentOptions` fragment that sandboxes a run, writable in `writes`.

    `None` = not sandboxed. An empty LIST is different: sandboxed, writable in its cwd only.
    """
    if writes is None:
        return {}
    return {"sandbox": _POLICY, "add_dirs": _roots(writes)}


# Agents need a temp file, and `$TMPDIR` is outside every boundary we grant. Each item folder
# carries its own — transient, removed when the item goes terminal.
SCRATCH_DIRNAME = "scratch"


def ensure_scratch(item_dir: Path) -> Path:
    """Create and return `<item_dir>/scratch/`. Returns the path even if mkdir failed — an agent
    inside the boundary can create it itself."""
    scratch = Path(item_dir) / SCRATCH_DIRNAME
    try:
        scratch.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("could not create scratch dir %s: %s", scratch, exc)
    return scratch


def prune_scratch(item_dir: Path, *, only_if_empty: bool = True) -> bool:
    """Remove `<item_dir>/scratch/`, reporting whether it went.

    `only_if_empty` is the per-run sweep; False is the terminal one. A run that left files keeps
    them. Never raises — an item that cannot be tidied must still close."""
    scratch = Path(item_dir) / SCRATCH_DIRNAME
    try:
        if not scratch.is_dir():
            return False
        if only_if_empty and any(scratch.iterdir()):
            return False
        shutil.rmtree(scratch)
        return True
    except OSError as exc:
        log.warning("could not remove scratch dir %s: %s", scratch, exc)
        return False


def _roots(paths: list[Path]) -> list[str]:
    """Resolved, deduped, order-preserving. An unresolvable path grants nothing, silently."""
    seen: dict[str, None] = {}
    for p in paths:
        try:
            seen[str(Path(p).resolve())] = None
        except (OSError, ValueError):
            continue
    return list(seen)


# The daemon runs verification commands where the CLI's sandbox does not reach, so it wraps its
# own. ALLOW-default: deny-default would enumerate every dylib a test runner touches.
_SEATBELT = """(version 1)
(allow default)
(deny network*)
(allow network-bind (local ip "localhost:*"))
(allow network-inbound (local ip "localhost:*"))
(allow network-outbound (remote ip "localhost:*"))
(deny file-write*)
(allow file-write*{writable}
  (subpath "/private/var/folders")
  (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr")
  (literal "/dev/dtracehelper") (regex #"^/dev/tty"))
"""
# Per-user TMPDIR is allowed: test runners scribble there constantly. Shared `/private/tmp` is
# not — world-visible, so a write there is a channel out of the boundary.


def kernel_command(command: str, writable: list[Path]) -> list[str] | None:
    """Wrap a shell `command` so the kernel runs it isolated. `None` = no supported sandbox here,
    and the caller must NOT fall back to running it bare. macOS only; Linux would use bubblewrap.
    """
    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        return None
    roots = "".join(f'\n  (subpath "{r}")' for r in _roots(writable))
    return ["sandbox-exec", "-p", _SEATBELT.format(writable=roots), "/bin/sh", "-c", command]
