"""OS-level sandbox (macOS Seatbelt) around an agent's shell commands.

The permission layer reads command strings, which cannot say where a command writes. This
closes that gap at the kernel. CPU and memory caps are a known gap.
"""

import logging
import shutil
import sys
from pathlib import Path

log = logging.getLogger("superme-agent")

# Interactive turns are not sandboxed: a person approves each command.
_POLICY: dict = {
    "enabled": True,
    # False, so `can_use_tool` still sees every command.
    "autoAllowBashIfSandboxed": False,
    # Load-bearing: a boundary the occupant can lift is no boundary.
    "allowUnsandboxedCommands": False,
    # Network is default-deny by omission — no `allowedDomains` key.
    "network": {"allowLocalBinding": True},
}


def sandbox_options(writes: list[Path] | None) -> dict:
    """The `ClaudeAgentOptions` fragment that sandboxes a run. `None` is unsandboxed; an empty LIST
    is cwd-only."""
    if writes is None:
        return {}
    return {"sandbox": _POLICY, "add_dirs": _roots(writes)}


# `$TMPDIR` is outside every boundary we grant, so each item folder carries its own.
SCRATCH_DIRNAME = "scratch"


def ensure_scratch(item_dir: Path) -> Path:
    """Create and return `<item_dir>/scratch/`. Returns the path even if mkdir failed — the agent can
    retry inside."""
    scratch = Path(item_dir) / SCRATCH_DIRNAME
    try:
        scratch.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("could not create scratch dir %s: %s", scratch, exc)
    return scratch


def prune_scratch(item_dir: Path, *, only_if_empty: bool = True) -> bool:
    """Remove `<item_dir>/scratch/`, reporting whether it went. `only_if_empty` is the per-run sweep.
    Never raises."""
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


# The daemon verifies where the CLI's sandbox does not reach. ALLOW-default: deny-default would
# enumerate every dylib.
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
# Per-user TMPDIR only. Shared `/private/tmp` is world-visible — a write there leaves the
# boundary.


def kernel_available() -> bool:
    """Whether this host can isolate a shell command. macOS seatbelt only, today."""
    return sys.platform == "darwin" and bool(shutil.which("sandbox-exec"))


def kernel_command(command: str, writable: list[Path]) -> list[str] | None:
    """Wrap `command` so the kernel isolates it. `None` means no supported sandbox — never fall back
    to bare."""
    if not kernel_available():
        return None
    roots = "".join(f'\n  (subpath "{r}")' for r in _roots(writable))
    return ["sandbox-exec", "-p", _SEATBELT.format(writable=roots), "/bin/sh", "-c", command]
