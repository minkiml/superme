"""The OS-level sandbox around an agent's shell commands (verification-model design §7).

`permissions.py` has always carried an honest admission: the freeze boundary is
"accident-prevention, not security (Bash is not path-checkable)". Reading a command string can
never tell you where it will write — `cd /elsewhere && rm -rf` is a legal sentence. This module
closes that gap with the kernel instead of a classifier: Claude Code ships an OS-level sandbox
(macOS Seatbelt / Linux bubblewrap) that SuperMe was simply never turning on.

**What it actually enforces** (probed on SDK 0.2.87 / macOS, 2026-08-02 — not inferred):

- a shell write inside the run's cwd succeeds
- a shell write outside it fails at the kernel: `operation not permitted`, before the syscall
- outbound network is refused by the CLI's egress proxy (`CONNECT tunnel failed, 403`)
- `add_dirs` widens the writable set — this is how a run's declared write boundary is handed to
  the kernel rather than only to the permission callback
- binding a localhost port, serving on it, and fetching it back all work — so a check that starts
  a dev server is not killed by the boundary (this is what `allowLocalBinding` buys, and without
  it the most valuable class of verification would silently die)
- `git` inside a linked worktree commits fine, even though the real `.git` lives in the parent
  repo, outside cwd — so no `excludedCommands` escape hatch is needed, and none is granted

Not covered, and a deliberate known gap: CPU and memory caps. A wrapper is the cheap fix and a
container the proper one; both slot in behind this same seam.

**Which runs are sandboxed.** Runs that touch a repository's code — the work-item phases (build,
vet, plan/triage/review/close/investigate), the deputy's passes over an item, and conflict
resolution. Runs that only reason over SuperMe's own knowledge (distill, write, the capture sweep,
compaction) are not: they shell out into no repo, so there is nothing for a boundary to hold.
Interactive turns are not sandboxed either — a person is approving each command there, and a
sandbox that silently refuses the owner's own `pip install` trades a real capability for a
guarantee the human already provides.
"""

import shutil
import sys
from pathlib import Path

# `allowUnsandboxedCommands: False` is the load-bearing line: without it a command can opt out via
# `dangerouslyDisableSandbox`, and a boundary the occupant can lift is not a boundary.
#
# `autoAllowBashIfSandboxed` stays FALSE on purpose. It would let the CLI approve shell commands
# without consulting `can_use_tool`, which is where the freeze boundary and the read-only phases
# live — so turning it on would quietly hand the sandbox veto power over rules it knows nothing
# about. The two layers answer different questions (*may* this run vs. *can* it reach outside), and
# both should keep answering.
#
# Network is default-deny by omission: no `allowedDomains`, so nothing resolves. Per-check declared
# exceptions are the next stage's business, not a blanket hole here.
_POLICY: dict = {
    "enabled": True,
    "autoAllowBashIfSandboxed": False,
    "allowUnsandboxedCommands": False,
    "network": {"allowLocalBinding": True},
}


def sandbox_options(writes: list[Path] | None) -> dict:
    """The `ClaudeAgentOptions` fragment that puts a run in the sandbox, writable in `writes`.

    `None` means the run is not sandboxed and yields an empty fragment — the caller spreads it
    either way, so "sandboxed" is one decision made at one place per runner. An empty LIST is a
    real answer, distinct from `None`: sandboxed, writable in its cwd and nowhere else.

    `writes` is the same list the run already declares to the permission layer (its freeze
    boundary, or the item folder a read-only phase may scope a command into). One declaration,
    now enforced twice — the callback denies the attempt, the kernel denies the syscall.
    """
    if writes is None:
        return {}
    return {"sandbox": _POLICY, "add_dirs": _roots(writes)}


def _roots(paths: list[Path]) -> list[str]:
    """Resolved, deduped, order-preserving. An unresolvable path grants nothing, silently."""
    seen: dict[str, None] = {}
    for p in paths:
        try:
            seen[str(Path(p).resolve())] = None
        except (OSError, ValueError):
            continue
    return list(seen)


# --------------------------------------------------------------- the kernel's own sandbox
# When the DAEMON runs a command itself (a verification check carrying a literal `run:` block —
# design §4), the CLI's sandbox is not in the picture: that one wraps the agent's Bash tool, and
# there is no agent here. Running it bare would mean the design's most trusted class of evidence is
# also its least isolated, so the kernel wraps its own subprocess in the same two guarantees the
# agent gets — writes confined to declared roots, no outbound network, localhost still reachable.
#
# The profile is ALLOW-default with two denials rather than deny-default with an allowlist. A
# deny-default profile has to enumerate every dylib, temp path and mach service a real test runner
# touches, and the failure mode is a legitimate check dying for reasons that have nothing to do with
# the code under test. Reads are not the threat being managed here; escape and exfiltration are.
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
# The per-user TMPDIR (`/private/var/folders`) is allowed deliberately: compilers, test runners and
# package managers scribble there constantly, and denying it would fail checks for reasons that have
# nothing to do with the code under test. Shared `/private/tmp` is NOT allowed — it is world-visible,
# so a write there is a channel out of the boundary rather than private scratch. The line being
# drawn is "ephemeral scratch, yes; anywhere a person or another project would look, no".


def kernel_command(command: str, writable: list[Path]) -> list[str] | None:
    """Wrap a shell `command` so the kernel can run it isolated. `None` = this host has no
    supported sandbox, and the caller must NOT fall back to running it bare — an unisolated
    kernel execution would silently downgrade the guarantee the record claims.

    macOS only today (Seatbelt, via `sandbox-exec`). Linux would go through bubblewrap here; until
    that is written and tested on a real Linux host, saying "unsupported" is the honest answer and
    the check simply stays agent-attested.
    """
    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        return None
    roots = "".join(f'\n  (subpath "{r}")' for r in _roots(writable))
    return ["sandbox-exec", "-p", _SEATBELT.format(writable=roots), "/bin/sh", "-c", command]
