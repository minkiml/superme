"""The approval seam — surface-neutral human-in-the-loop gating.

Core contains no asking-UI. An `ApproveFn` the SURFACE supplies returns True, False, or a
STRING that denies with a reason — because a bare False stood for three different facts.
"""

import logging
import os
import re
import shlex
from pathlib import Path, PureWindowsPath
from typing import Awaitable, Callable

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from ..harness.policy import MAX_SUBAGENTS, SUBAGENT_TOOLS, is_safe
from .vocab.sandbox import SCRATCH_DIRNAME

log = logging.getLogger("superme-agent")

# (tool_name, tool_input) -> True | False | "why it was denied".  Supplied by each surface.
ApproveFn = Callable[[str, dict], Awaitable[bool | str]]

# Each path states the fact that actually occurred: the deny message is the agent's only account,
# and it reasons onward.
_DENIED_BY_OWNER = (
    "The owner saw this exact call and denied it. Don't retry it, and don't reshape it into a "
    "near-identical call to get around the refusal — if you believe it's needed, say so and ask. "
    "Acknowledge in ONE line and carry on with what you can still do: no list of other things you "
    "could try instead, no theory about what blocked you, and don't re-raise unrelated pending work "
    "in the same breath."
)
APPROVAL_UNANSWERED = (
    "Nothing came back — the approval request went unanswered (the owner is away from it, or the "
    "connection dropped). NOBODY refused this. Don't conclude that a rule, a setting or a hook "
    "blocked you, and don't go looking for one. Say in one line that the request wasn't answered, "
    "then stop and wait — don't try another way in."
)
NO_HUMAN_TO_ASK = (
    "This is a background run with nobody at the keyboard, so every tool that needs approval is "
    "unavailable for the whole run — this is not a judgment on this particular call. Don't retry it "
    "and don't route around it: do what you can with the tools you have, and record what you "
    "couldn't do in your report."
)
# Unlike most refusals, this one has a fix the agent can apply itself. Naming the remedy earns a
# retry.
_BASH_OUTSIDE_BOUNDARY = (
    "That command runs outside this run's write boundary, which is {roots}. This shell did not "
    "start there, and the command does not name it — so nothing about the call says it stays "
    "inside. Scope it in and it runs with no approval at all: `cd {first} && <your command>`, or "
    "pass the directory explicitly (`git -C {first} …`). Keep every path it names inside that "
    "directory. If the work genuinely belongs outside it, that is a wall — say so in your record "
    "rather than looking for another way through."
)
# Only when the boundary actually HAS a scratch dir — telling a builder to create one would litter
# the repo.
_BASH_SCRATCH_HINT = (
    "\n\nIf what you needed was somewhere to put intermediate output, this run has a scratch "
    "directory at `{scratch}/`. It is inside the boundary, so commands writing there need no "
    "approval; nothing in it is read as a result or kept after the item closes. Use it instead of "
    "`$TMPDIR` or `/tmp`, which are outside every boundary."
)
# The number is the point: not the individual wall, but how many there were by report time.
_REFUSAL_TALLY = (
    "(Tool calls refused so far this run: {n}. The kernel keeps this count — quote it in your "
    "record's coverage note rather than recalling it, and say what it left unverified.)"
)
_LEARNING_SCOPE_DENIED = (
    "This run may only use Bash and write inside its own scratch workspace. That target is outside "
    "it, so the call is out of the run's scope rather than refused by anyone — draft into the "
    "workspace instead."
)

# Tools that write to the filesystem (reads are covered by the safe-tool policy).
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# No SuperMe surface handling. `AskUserQuestion` needs a picker this chat lacks — the agent must
# ask in text.
_SURFACE_UNSUPPORTED = {"AskUserQuestion"}
_ASK_IN_TEXT_NUDGE = (
    "The AskUserQuestion tool isn't available on this surface. If a human is in this chat, ask "
    "your question(s) as plain conversational text — one at a time, with your recommended answer. "
    "In a background run, don't ask at all: take your recommended option and record the judgment "
    "call in your final report."
)

# Real dev work flows through a work-item. Denied with FEEDBACK and no prompt, so the agent pivots
# to itemizing.
_GENERAL_SESSION_BLOCKED = set(_WRITE_TOOLS)
_GENERAL_SESSION_NUDGE = (
    "Mutating the project's real code (writing/editing files or implementing) is disabled in a "
    "general discussion session — all real dev work must happen inside a work-item and its session. "
    "Authoring the project's `general/` memory docs (onboarding via project-init/retrofit, or "
    "maintaining an anchor doc) is allowed and not what this blocks. If what you're about to do is "
    "real implementation work, don't try it here: propose itemizing it into an inbox item (the "
    "create-inbox-item skill) so it can be picked up and done properly."
)
# Diagnosis carves no `general/` exception, so the general nudge would be false there and invite a
# retry.
_READONLY_SESSION_NUDGE = (
    "This session is fully READ-ONLY — no file writes at all, including the project's memory "
    "docs. Describe the change you'd make instead, and offer to itemize it (the create-inbox-item "
    "skill) so it happens in its own work-item."
)


# FAIL-CLOSED: true only when PROVABLY read-only. A false negative costs a prompt; a false
# positive is a hole.
_READONLY_BASH_CMDS = frozenset({
    "ls", "pwd", "cat", "head", "tail", "wc", "stat", "file", "tree", "du", "df", "echo", "printf",
    "date", "whoami", "id", "hostname", "uname", "env", "which", "type", "basename", "dirname",
    "realpath", "readlink", "grep", "egrep", "fgrep", "rg", "ag", "sort", "uniq", "cut", "comm",
    "diff", "cmp", "nl", "column", "tac", "rev", "fold", "join", "paste", "look", "strings",
    "hexdump", "xxd", "od", "md5", "md5sum", "shasum", "sha1sum", "sha256sum", "true", "test", "[",
    "wc", "tr", "expr", "seq", "yes", "cksum", "sum",
})
# Shell metacharacters that could hide a write / arbitrary execution → refuse outright.
_BASH_UNSAFE_SUBSTR = (">", "<", "`", "$(", "${", ">>", "|&", "&>", "\n")
# find primaries that mutate or execute.
_FIND_MUTATORS = ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls")
# git read-only subcommands (pure inspection; ambiguous/mutating ones fall through to a prompt).
_GIT_READONLY = frozenset({
    "status", "log", "diff", "show", "rev-parse", "ls-files", "ls-tree", "cat-file", "describe",
    "blame", "shortlog", "reflog", "name-rev", "show-ref", "for-each-ref", "whatchanged", "grep",
    "count-objects", "rev-list", "symbolic-ref", "var", "help", "branch", "tag", "remote",
})
# For git branch/tag/remote/stash/config, any of these args flips it to a mutation → refuse.
_GIT_MUTATING_ARGS = ("-d", "-D", "-m", "-M", "--delete", "--move", "--add", "--set", "--unset",
                      "--remove", "-f", "--force", "add", "set", "rename", "prune", "set-url",
                      "set-head", "push", "pop", "apply", "drop", "clear", "create")
# Skipped in PAIRS, so the value is never read as the subcommand. `--opt=…` forms skip singly.
_GIT_GLOBAL_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                                    "--exec-path", "--config-env"})
_BASH_SEPARATORS = frozenset({";", "&&", "||", "|", "&"})


def _segment_read_only(seg: list[str]) -> bool:
    """One pipeline segment (already split on shell operators) — is its command read-only?"""
    if not seg:
        return True
    head = seg[0]
    # An inline env assignment (`FOO=bar cmd`) hides the real command → refuse.
    if "=" in head and not head.startswith("-"):
        return False
    if head == "git":
        # `git -C <dir> diff` is the same read as `git diff`. Reading `seg[1]` saw `-C` and fell
        # through to approval.
        i = 1
        while i < len(seg) and seg[i].startswith("-"):
            i += 2 if seg[i] in _GIT_GLOBAL_WITH_VALUE else 1
        sub = seg[i] if i < len(seg) else ""
        if sub not in _GIT_READONLY:
            return False
        return not any(a in _GIT_MUTATING_ARGS for a in seg[i + 1:])
    if head == "find":
        return not any(tok in _FIND_MUTATORS for tok in seg)
    if head in ("sed", "awk", "gawk"):
        return not any(t == "-i" or t.startswith("-i") for t in seg)
    if head == "sort":
        return "-o" not in seg
    return head in _READONLY_BASH_CMDS


def is_read_only_bash(command: str) -> bool:
    """PROVE a shell command is read-only (fail-closed). Every segment must be a
    known read-only command with no redirection."""
    if not command or not command.strip():
        return False
    # The two PROVABLY write-free redirects. Strip the exact tokens; any other `>` still refuses.
    command = command.replace("2>/dev/null", " ").replace("2>&1", " ")
    if any(bad in command for bad in _BASH_UNSAFE_SUBSTR):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False                    # unbalanced quotes etc. → not provable
    seg: list[str] = []
    for tok in tokens:
        if tok in _BASH_SEPARATORS:
            if not _segment_read_only(seg):
                return False
            seg = []
        else:
            seg.append(tok)
    return _segment_read_only(seg)


def _write_target_under(input_data: dict, root: Path) -> bool:
    """True if a write tool's target resolves inside `root`. Fail-CLOSED: an
    unclear write is out-of-scope."""
    path = input_data.get("file_path") or input_data.get("path")
    if not path:
        return False
    try:
        target = Path(path).resolve()
        r = root.resolve()
    except (OSError, ValueError):
        return False
    return target == r or r in target.parents

# Bash is deliberately absent: shell READS are the accepted ceiling, and the OS sandbox does not
# scope reads either.
_READ_TOOLS = {"Read", "Grep", "Glob"}


def _read_target(tool_input: dict) -> str | None:
    """The path a Read/Grep/Glob call targets. Grep/Glob's absent `path` means the
    agent's cwd, always in scope."""
    return tool_input.get("file_path") or tool_input.get("path")


def path_in_scope(target: str, cwd: Path, roots: list[Path]) -> bool:
    """True if `target` resolves inside any allowed root. Fail-OPEN: this is
    defense-in-depth, not the primary boundary."""
    try:
        p = Path(target)
        if not p.is_absolute():
            p = cwd / p
        p = p.resolve()
    except (OSError, ValueError):
        return True
    for r in roots:
        try:
            rr = r.resolve()
        except (OSError, ValueError):
            continue
        if p == rr or rr in p.parents:
            return True
    return False


async def deny_all(tool_name: str, tool_input: dict) -> bool | str:
    """An ApproveFn that denies everything, and says so — an agent told only "denied"
    keeps spending turns."""
    return NO_HUMAN_TO_ASK


def scoped_writes_approve(allowed_dir: Path, fallback: ApproveFn) -> ApproveFn:
    """Auto-allow writes INSIDE `allowed_dir`, defer the rest to `fallback`.

    `Bash` gets the same scope: these sessions run at the repo cwd, so only a self-scoping command
    auto-allows."""
    allowed = allowed_dir.resolve()

    async def approve(tool_name: str, tool_input: dict) -> bool | str:
        if tool_name in _WRITE_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("path")
            if path:
                try:
                    target = Path(path).resolve()
                    if target == allowed or allowed in target.parents:
                        return True
                except (OSError, ValueError):
                    pass
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            roots = [allowed]
            if _bash_scoped_into_boundary(command, roots) \
                    and not _bash_escapes_boundary(command, roots):
                return True
        return await fallback(tool_name, tool_input)

    return approve


def learning_write_approve(workspace: Path) -> ApproveFn:
    """Policy for a background learning WRITE run: auto-allow Bash and writes
    inside `workspace`, deny the rest. Hermetic and disposable, so it is safe unattended."""
    ws = workspace.resolve()

    async def approve(tool_name: str, tool_input: dict) -> bool | str:
        if tool_name == "Bash":
            return True
        if tool_name in _WRITE_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("path")
            if path:
                try:
                    target = Path(path).resolve()
                    if target == ws or ws in target.parents:
                        return True
                except (OSError, ValueError):
                    pass
        return _LEARNING_SCOPE_DENIED

    return approve


# A build session works ONLY in its worktree, so an outside write is an accident by construction.
_FREEZE_NUDGE = (
    "Edit boundary (build phase): this work-item owns a dedicated git worktree, and all file "
    "changes must happen inside it (or the item's own artifacts folder). The path you tried is "
    "outside that boundary. Work in the worktree — it is your working directory; the merge back "
    "to main happens at the review gate."
)

# Review is read-only on the PLAN: plan.md sits inside the write boundary, so a plain `Edit` would
# auto-allow.
PLAN_READONLY_NUDGE = (
    "`plan.md` is the PLAN phase's to write, and this item is at review — editing it here would "
    "change the contract with nothing downstream re-running against it. If the conversation "
    "concluded the work must change, end your run with "
    "`report_completion(machine.outcome='revise')`, carrying the owner's words verbatim: the item "
    "returns to plan on this same thread and folds them in surgically."
)

VET_READONLY_NUDGE = (
    "This is a VET session — file writes are disabled by design (a verifier that can edit could "
    "grade its own fixes). Record each check's outcome with record_verification and file "
    "the cycle's report with file_vet_report. If the build is wrong, FAIL the check and describe "
    "exactly what you observed — the fix happens in the build session, never here."
)


def _in_any(target: Path, roots: list[Path]) -> bool:
    """True if `target` resolves inside any of `roots` (a root itself counts as inside)."""
    try:
        t = target.resolve()
    except (OSError, ValueError):
        return False
    for root in roots:
        try:
            r = root.resolve()
        except (OSError, ValueError):
            continue
        if t == r or r in t.parents:
            return True
    return False


def _target_in_any(input_data: dict, roots: list[Path]) -> bool:
    """True if a write tool's target resolves inside any of `roots`. Fail-CLOSED:
    an unclear write is out-of-bounds."""
    path = input_data.get("file_path") or input_data.get("path") or input_data.get("notebook_path")
    if not path:
        return False
    try:
        return _in_any(Path(path), roots)
    except (OSError, ValueError):
        return False


# Paths that are not places. Asking whether `/dev/null` sits in a write boundary is a category
# error.
_PSEUDO_DEVICE_ROOTS = ("/dev/null", "/dev/zero", "/dev/tty",
                        "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/fd/")


def _is_pseudo_device(token: str) -> bool:
    """True for `/dev/null` and friends — a kernel device, not a location on disk."""
    return any(token == p or token.startswith(p) for p in _PSEUDO_DEVICE_ROOTS)


def _is_absolute_token(tok: str) -> bool:
    """Absolute under either convention: a Windows command names `C:\\…`, a POSIX one `/…`."""
    return tok.startswith("/") or PureWindowsPath(tok).is_absolute()


def _outside_every(tok: str, roots: list[Path]) -> bool:
    """True when an absolute token lands outside every root.

    A token absolute under the OTHER platform's rules is outside by definition — no native root
    can hold it, and resolving it would silently make it relative to the cwd."""
    p = Path(tok)
    return True if not p.is_absolute() else not _in_any(p, roots)


def _scratch_in(roots: list[Path]) -> Path | None:
    """The first boundary root that already HAS a `scratch/` dir, or None. Read from disk,
    not derived."""
    for root in roots:
        try:
            cand = Path(root) / SCRATCH_DIRNAME
            if cand.is_dir():
                return cand
        except (OSError, ValueError):
            continue
    return None


# Absolute-looking tokens are the only part of a shell command we can honestly reason about.
def _bash_escapes_boundary(command: str, roots: list[Path]) -> bool:
    """True if the command NAMES an absolute path outside every root. A cheap
    accident-catcher — escaping prompts, never denies."""
    for raw in _path_tokens(command):
        tok = raw.strip("'\"")
        if tok.startswith("~"):
            tok = str(Path(tok).expanduser())
        if not _is_absolute_token(tok) or _is_pseudo_device(tok):
            continue
        if _outside_every(tok, roots):
            return True
    return False


def shlex_split_safe(command: str) -> list[str]:
    """`shlex.split` that degrades to a whitespace split on unbalanced quotes.

    POSIX splitting reads a backslash as an escape, which eats every separator in a Windows
    path and leaves a token no boundary check can recognise."""
    try:
        return shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return command.split()


# Commands whose first positional argument is a PATTERN, not a path — a regex is spelled like one.
_PATTERN_FIRST = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "sed", "awk", "gawk"})
_PATTERN_OPTS = frozenset({"-e", "--regexp"})


def _seg_path_tokens(seg: list[str]) -> list[str]:
    """One pipeline segment's tokens, minus the pattern argument if its command takes one."""
    if not seg or Path(seg[0].strip("'\"")).name not in _PATTERN_FIRST:
        return seg
    out: list[str] = []
    i, pattern_taken = 1, False
    while i < len(seg):
        bare = seg[i].strip("'\"")
        if bare in _PATTERN_OPTS:
            pattern_taken = True
            i += 2
            continue
        if bare.startswith("-"):
            pattern_taken = pattern_taken or bare.startswith("--regexp=")
            i += 1
            continue
        if not pattern_taken:
            pattern_taken = True
            i += 1
            continue
        out.append(seg[i])
        i += 1
    return out


def _path_tokens(command: str) -> list[str]:
    """Every token that could name a path. Split per LINE too: `shlex` reads a newline
    as whitespace."""
    out: list[str] = []
    for line in command.splitlines() or [command]:
        seg: list[str] = []
        for tok in [*shlex_split_safe(line), ";"]:
            if tok in _BASH_SEPARATORS:
                out.extend(_seg_path_tokens(seg))
                seg = []
            else:
                seg.append(tok)
    return out


def _bash_scoped_into_boundary(command: str, roots: list[Path]) -> bool:
    """True if the command EXPLICITLY scopes itself into a root.

    A command naming no absolute path stays unmatched: it resolves against a cwd this cannot see."""
    toks = shlex_split_safe(command)
    for i, raw in enumerate(toks[:-1]):
        if raw.strip("'\"") in ("cd", "-C"):
            target = toks[i + 1].strip("'\"")
            if target.startswith("~"):
                target = str(Path(target).expanduser())
            if _is_absolute_token(target):
                try:
                    if not _outside_every(target, roots):
                        return True
                except (OSError, ValueError):
                    pass
    named = 0
    for raw in _path_tokens(command):
        tok = raw.strip("'\"")
        if tok.startswith("~"):
            tok = str(Path(tok).expanduser())
        if not _is_absolute_token(tok) or _is_pseudo_device(tok):
            continue
        named += 1
        try:
            if _outside_every(tok, roots):
                return False
        except (OSError, ValueError):
            return False
    return named > 0


# An agent whose commit is refused WILL find `--no-verify`. Prose lost this argument once; take
# the flag away.
_NO_VERIFY_NUDGE = (
    "Skipping git hooks is not available here. A hook refused your commit for a reason, and "
    "bypassing it converts that reason into a defect someone finds later.\n\n"
    "If the refusal was SuperMe's task-trailer rule, add the trailer and commit again. If it came "
    "from a check this project owns, that is not yours to overrule: leave the work staged and end "
    "your run with report_completion(machine.outcome='needs_user'), quoting the refusal verbatim "
    "and naming what you think the owner should do about it. Do not retry the same commit."
)

# Matched only AFTER the subcommand token: `grep -n foo && git commit` is ordinary, not a bypass.
_HOOK_BYPASS_FLAGS = {"--no-verify", "-n"}
_HOOK_RUNNING_SUBCOMMANDS = {"commit", "merge", "rebase", "cherry-pick", "revert", "am"}
_SHELL_BREAKS = {"&&", "||", ";", "|", "&"}


# Runs are the daemon's children, so a restart kills the agent that issued it and every sibling
# mid-flight.
_KILL_VERBS = {"kill", "pkill", "killall"}
_HOST_MARKERS = ("superme_agent.daemon", "web.bff")
_KILL_HOST_NUDGE = (
    "Stopping or restarting the SuperMe daemon is not available here. Your run is a CHILD of that "
    "process: killing it kills you, every other work-item running right now, and anything you "
    "start to replace it.\n\n"
    "If you need a server to check something against YOUR code, do not reach for the running one — "
    "it serves a different checkout. Boot your own from this worktree with the `vet_env.sh start` "
    "command your trigger names; it takes a free port and leaves the host alone.\n\n"
    "If the work genuinely requires the host to restart, that is the owner's call and not a step "
    "you can take: end your run with report_completion(machine.outcome='needs_user'), saying what "
    "needs the restart and why."
)


# Must stop the spawn AND make the CAP visible: an agent told only "denied" reports as though it
# covered everything.
_SUBAGENT_CAP_NUDGE = (
    "Subagent limit reached — this run has already spawned {cap} of them, which is the ceiling for "
    "ONE run. Nothing is wrong with the brief; there are simply no more readers to hand it to.\n\n"
    "Read the remaining surface yourself, and SAY SO in your report: name what you were about to "
    "delegate and that the limit is why you read it directly. A reader that was cut off and did not "
    "say so is indistinguishable from one that found nothing — and the gate can only judge the "
    "difference if you write it down."
)


def kills_the_host(command: str) -> bool:
    """True if this command would stop the daemon or BFF. The port test is narrow:
    only the host's own ports."""
    from ..paths import DAEMON_PORT
    toks = [t.strip("'\"") for t in shlex_split_safe(command)]
    if not any(Path(t).name in _KILL_VERBS for t in toks):
        return False
    ports = {str(DAEMON_PORT), "8000"}   # the BFF's port is fixed in web/bff/__main__.py
    for t in toks:
        if any(m in t for m in _HOST_MARKERS):
            return True
        # `:8787`, `8787`, `-ti:8787` — a port token anywhere in the pipeline, digits-delimited so
        # `18787` and `87870` do not match.
        if any(re.search(rf"(?<!\d){p}(?!\d)", t) for p in ports):
            return True
    return False


_PUBLISH_NUDGE = (
    "Publishing to a remote is not available to any session here. Every boundary this run has is "
    "about the local disk, and a push leaves it — the owner's gate is the merge, and nothing "
    "reaches a remote without passing it. If your work genuinely needs to be somewhere else, say "
    "so in your record and let the owner move it."
)


def publishes_outward(command: str) -> bool:
    """True if this command would send local work to a remote — the one mutation
    that escapes every filesystem wall."""
    toks = [t.strip("'\"") for t in shlex_split_safe(command)]
    for i, tok in enumerate(toks[:-1]):
        if Path(tok).name in ("git", "gh") and "push" in toks[i + 1:]:
            # `git stash push` / `git stash push -u` — a local stash, not a remote.
            return "stash" not in toks[i + 1: toks.index("push", i + 1)]
    return False


def bypasses_commit_hooks(command: str) -> bool:
    """True if this shell command runs a hook-firing git subcommand with hooks turned off."""
    toks = [t.strip("'\"") for t in shlex_split_safe(command)]
    for i, tok in enumerate(toks):
        if tok not in _HOOK_RUNNING_SUBCOMMANDS:
            continue
        for nxt in toks[i + 1:]:
            if nxt in _SHELL_BREAKS:
                break
            if nxt in _HOOK_BYPASS_FLAGS:
                return True
    return False


# Multiplexers whose SUBCOMMAND is the meaningful unit for approval memory; everything else keys
# on the program name.
_BASH_MULTIPLEXERS = {"git", "npm", "yarn", "pnpm", "npx", "cargo", "go", "make", "python",
                      "python3", "pip", "pip3", "uv", "poetry", "conda", "docker", "node", "bun"}


def approval_signature(tool_name: str, tool_input: dict) -> str:
    """A stable key for "the owner already OK'd this KIND of call". Coarse ON
    PURPOSE: agents vary args every call."""
    if tool_name == "Bash":
        words = [t.strip("'\"") for t in shlex_split_safe(str(tool_input.get("command", "")))
                 if not t.startswith("-")]
        prog = Path(words[0]).name if words else ""
        if prog in _BASH_MULTIPLEXERS and len(words) > 1:
            return f"Bash:{prog} {words[1]}"
        return f"Bash:{prog}"
    return f"tool:{tool_name}"


_SKILL_TOOLS = {"Skill", "SlashCommand"}


def _invoked_skill_names(tool_name: str, input_data: dict) -> list[str]:
    """Best-effort: the skill identifiers a Skill/SlashCommand call targets,
    leading `/` and args dropped, bare name too."""
    if tool_name not in _SKILL_TOOLS:
        return []
    out: list[str] = []
    for v in input_data.values():
        if isinstance(v, str) and v.strip():
            tok = v.strip().lstrip("/").split()[0] if v.strip().lstrip("/").split() else ""
            if tok:
                out.append(tok)
                if ":" in tok:
                    out.append(tok.split(":", 1)[1])
    return out


def build_can_use_tool(approve: ApproveFn, *, blocked_skills: dict[str, str] | None = None,
                       cwd: Path | None = None, read_roots: list[Path] | None = None,
                       gate_general_mutations: bool = False,
                       general_write_root: Path | None = None,
                       write_boundary: list[Path] | None = None,
                       shell_roots: list[Path] | None = None,
                       deny_write_tools: str | None = None,
                       protected_paths: list[Path] | None = None,
                       protected_nudge: str | None = None,
                       subagent_cap: int | None = MAX_SUBAGENTS):
    """Wrap a surface's ApproveFn into the SDK's `can_use_tool`.

    `write_boundary` prevents accidents. `sandbox_writes` hands the same roots to
    `core.vocab.sandbox`, where an escape fails as a syscall."""
    spawned = 0

    async def can_use_tool(
        tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        nonlocal spawned
        # Set only by the Bash branch, and only on a boundary miss. Bound here so every path can
        # read it.
        bash_boundary_miss: str | None = None
        if subagent_cap is not None and tool_name in SUBAGENT_TOOLS:
            if spawned >= subagent_cap:
                log.warning("subagent cap reached (%d) — refused a further %s spawn",
                            subagent_cap, tool_name)
                return PermissionResultDeny(
                    message=_SUBAGENT_CAP_NUDGE.format(cap=subagent_cap))
            # Counted on the ALLOW: a spawn the SDK never runs must not spend a slot.
            spawned += 1
        if blocked_skills and tool_name in _SKILL_TOOLS:
            for n in _invoked_skill_names(tool_name, input_data):
                if n in blocked_skills:
                    log.info("blocked skill via %s: %s", tool_name, input_data)
                    # The deny message is the agent's ONLY feedback — it must be TRUE for THIS
                    # block, or it self-corrects wrongly.
                    return PermissionResultDeny(message=blocked_skills[n])
        # Surface-unsupported tools (e.g. AskUserQuestion) → deny with a nudge to ask in plain text.
        if tool_name in _SURFACE_UNSUPPORTED:
            log.info("surface-unsupported tool denied: %s (ask in text)", tool_name)
            return PermissionResultDeny(message=_ASK_IN_TEXT_NUDGE)
        # Vet is read-only: file-write tools are denied OUTRIGHT, before the in-boundary auto-
        # allow and any prompt.
        if deny_write_tools and tool_name in _WRITE_TOOLS:
            log.info("vet read-only denied %s", tool_name)
            return PermissionResultDeny(message=deny_write_tools)
        # Per-FILE carve-out INSIDE the boundary, checked before the auto-allow so it can never
        # become a permission card.
        if protected_paths and tool_name in _WRITE_TOOLS \
                and _target_in_any(input_data, protected_paths):
            log.info("protected path denied %s", tool_name)
            return PermissionResultDeny(message=protected_nudge or _FREEZE_NUDGE)
        # Build-phase freeze boundary: writes live or die on the worktree and item roots.
        if write_boundary and tool_name in _WRITE_TOOLS:
            if _target_in_any(input_data, write_boundary):
                return PermissionResultAllow()
            log.info("freeze boundary denied %s outside the item worktree", tool_name)
            return PermissionResultDeny(message=_FREEZE_NUDGE)
        # Hard-deny with a nudge, before the safe-tool check and `approve`: the human is never
        # interrupted.
        if gate_general_mutations and tool_name in _GENERAL_SESSION_BLOCKED:
            # The one allowed write: the project's `general/` memory. No root at all means a fully
            # read-only session.
            if general_write_root is not None and _write_target_under(input_data, general_write_root):
                return PermissionResultAllow()
            log.info("general-session guardrail denied %s (nudge to itemize)", tool_name)
            return PermissionResultDeny(message=_GENERAL_SESSION_NUDGE if general_write_root
                                        is not None else _READONLY_SESSION_NUDGE)
        # Read-only commands are the same access as Read/Grep/Glob. Anything unprovable defers to
        # approval.
        if tool_name == "Bash":
            command = input_data.get("command", "")
            # Before everything, including the read-only fast path: no session runs git with its
            # hooks off.
            if bypasses_commit_hooks(command):
                log.info("hook bypass denied: %s", command)
                return PermissionResultDeny(message=_NO_VERIFY_NUDGE)
            # Same standing: no session, in any phase, gets to stop the process it is running in.
            if kills_the_host(command):
                log.warning("host-kill denied: %s", command)
                return PermissionResultDeny(message=_KILL_HOST_NUDGE)
            # …or send local work to a remote. Nothing in SuperMe pushes and no skill asks for it.
            if publishes_outward(command):
                log.warning("outward publish denied: %s", command)
                return PermissionResultDeny(message=_PUBLISH_NUDGE)
            if is_read_only_bash(command):
                return PermissionResultAllow()
            # `reachable` is the shell's territory: the write boundary plus this run's
            # `shell_roots`. Write TOOLS ignore it.
            reachable = [*(write_boundary or []), *(shell_roots or [])]
            if (reachable and not _bash_escapes_boundary(command, reachable)
                    and ((cwd is not None and _in_any(cwd, reachable))
                         or _bash_scoped_into_boundary(command, reachable))):
                return PermissionResultAllow()
            # Only when scoping it in WOULD work: a command that truly reaches outside gets the
            # plain refusal instead.
            if reachable and not _bash_escapes_boundary(command, reachable):
                bash_boundary_miss = _BASH_OUTSIDE_BOUNDARY.format(
                    roots=", ".join(f"`{r}`" for r in reachable), first=reachable[0])
                if scratch := _scratch_in(write_boundary):
                    bash_boundary_miss += _BASH_SCRATCH_HINT.format(scratch=scratch)
        # Keep reads inside the host's scope, before the safe-tool auto-allow.
        if read_roots and cwd is not None and tool_name in _READ_TOOLS:
            target = _read_target(input_data)
            if target and not path_in_scope(target, cwd, read_roots):
                log.info("out-of-scope read blocked: %s %s", tool_name, target)
                return PermissionResultDeny(
                    message="Out of scope — this host may read only its own project directory, its "
                            "knowledge tree, and the SuperMe harness. That path is outside its scope.")
        if is_safe(tool_name, input_data):
            return PermissionResultAllow()
        verdict = await approve(tool_name, input_data)
        log.info("approval: %s -> %s", tool_name, "ALLOW" if verdict is True else "DENY")
        if verdict is True:
            return PermissionResultAllow()
        # A command refused ABOUT ITSELF, and fixable — that sentence wins over the approver's
        # generic wording.
        if bash_boundary_miss:
            return PermissionResultDeny(message=bash_boundary_miss)
        # A surface that KNOWS why hands back its sentence; a plain False means the owner refused.
        return PermissionResultDeny(
            message=verdict if isinstance(verdict, str) and verdict.strip() else _DENIED_BY_OWNER)

    refused = 0

    async def counted(tool_name: str, input_data: dict, context: ToolPermissionContext
                      ) -> PermissionResultAllow | PermissionResultDeny:
        """Every refusal carries a tally. A count is the one thing the agent cannot reconstruct
        and the kernel cannot get wrong."""
        nonlocal refused
        result = await can_use_tool(tool_name, input_data, context)
        if isinstance(result, PermissionResultDeny):
            refused += 1
            return PermissionResultDeny(
                message=f"{result.message}\n\n{_REFUSAL_TALLY.format(n=refused)}")
        return result

    return counted
