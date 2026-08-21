"""The approval seam — surface-neutral human-in-the-loop gating.

Core contains no asking-UI. When a tool needs approval it calls an `ApproveFn` the SURFACE
supplied: True allows, False denies, and a STRING denies with that reason.

The string exists because a bare False had to stand for three different facts — the owner
refused, nobody answered, nobody was there — and the agent reasons from whatever we say.
"""

import logging
import re
import shlex
from pathlib import Path
from typing import Awaitable, Callable

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from ..harness.policy import MAX_SUBAGENTS, SUBAGENT_TOOLS, is_safe
from .sandbox import SCRATCH_DIRNAME

log = logging.getLogger("superme-agent")

# (tool_name, tool_input) -> True | False | "why it was denied".  Supplied by each surface.
ApproveFn = Callable[[str, dict], Awaitable[bool | str]]

# The deny message is the agent's ONLY account of what happened, and it reasons onward from it,
# so each path states the fact that actually occurred. Sharing one string made a timed-out
# request report a refusal by a person who never saw it, and the agent invented a cause.
#
# Each also says how MUCH to say back: a denial is a one-line event. That instruction lives
# here, arriving when it applies, rather than in a preamble every turn would pay for.
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
# Neither provably read-only nor scoped into the write boundary. Unlike most refusals this one
# has a fix the agent can apply itself, and naming the remedy turns a dropped half of a sweep
# into one retry.
_BASH_OUTSIDE_BOUNDARY = (
    "That command runs outside this run's write boundary, which is {roots}. This shell did not "
    "start there, and the command does not name it — so nothing about the call says it stays "
    "inside. Scope it in and it runs with no approval at all: `cd {first} && <your command>`, or "
    "pass the directory explicitly (`git -C {first} …`). Keep every path it names inside that "
    "directory. If the work genuinely belongs outside it, that is a wall — say so in your record "
    "rather than looking for another way through."
)
# Only when the boundary actually HAS a scratch dir — telling a builder to create one would
# litter the repo. "Stay inside the boundary", without saying where a temp file may live, is
# half a rule.
_BASH_SCRATCH_HINT = (
    "\n\nIf what you needed was somewhere to put intermediate output, this run has a scratch "
    "directory at `{scratch}/`. It is inside the boundary, so commands writing there need no "
    "approval; nothing in it is read as a result or kept after the item closes. Use it instead of "
    "`$TMPDIR` or `/tmp`, which are outside every boundary."
)
# The number is the point: what a report gets wrong is never the individual wall, it is how
# many there were by the time the report is written.
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

# No SuperMe surface handling, so denied with feedback. `AskUserQuestion` needs a picker this
# chat does not have — the agent must ask in text.
_SURFACE_UNSUPPORTED = {"AskUserQuestion"}
_ASK_IN_TEXT_NUDGE = (
    "The AskUserQuestion tool isn't available on this surface. If a human is in this chat, ask "
    "your question(s) as plain conversational text — one at a time, with your recommended answer. "
    "In a background run, don't ask at all: take your recommended option and record the judgment "
    "call in your final report."
)

# The mutating tools a GENERAL dev session may not use: real dev work flows through a
# work-item. Denied with FEEDBACK and no prompt, so the agent pivots to itemizing.
# The itemize tool is absent, so it stays allowed — the one write a general session may make.
# Bash is absent too: read-only inspection stays useful.
_GENERAL_SESSION_BLOCKED = set(_WRITE_TOOLS)
_GENERAL_SESSION_NUDGE = (
    "Mutating the project's real code (writing/editing files or implementing) is disabled in a "
    "general discussion session — all real dev work must happen inside a work-item and its session. "
    "Authoring the project's `general/` memory docs (onboarding via project-init/retrofit, or "
    "maintaining an anchor doc) is allowed and not what this blocks. If what you're about to do is "
    "real implementation work, don't try it here: propose itemizing it into an inbox item (the "
    "create-inbox-item skill) so it can be picked up and done properly."
)
# Diagnosis carves no `general/` exception, so the general nudge's claim would be false there
# and the agent would retry a write it was just told is fine.
_READONLY_SESSION_NUDGE = (
    "This session is fully READ-ONLY — no file writes at all, including the project's memory "
    "docs. Describe the change you'd make instead, and offer to itemize it (the create-inbox-item "
    "skill) so it happens in its own work-item."
)


# Read-only Bash is access the agent already has via Read/Grep/Glob, so it should not prompt.
# FAIL-CLOSED: True only for a command it can PROVE read-only. A false negative costs a
# prompt; a false positive is a hole.
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
# Skipped in PAIRS, so the value is never read as the subcommand. `--opt=…` forms carry their
# own value and skip singly.
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
        # Skip git's GLOBAL options before reading the subcommand: `git -C <dir> diff` is the same
        # read as `git diff`, and it is the idiom review and close are told to use. Reading `seg[1]`
        # saw `-C` and fell through to an approval no background run can answer.
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
        return not any(t == "-i" or t.startswith("-i") for t in seg)   # in-place edit
    if head == "sort":
        return "-o" not in seg          # `sort -o` writes a file
    return head in _READONLY_BASH_CMDS


def is_read_only_bash(command: str) -> bool:
    """PROVE a shell command is read-only (fail-closed). True only when every pipeline segment is a
    known read-only command with no redirection, command-substitution, or in-place/mutating flags."""
    if not command or not command.strip():
        return False
    # The two PROVABLY write-free redirects. Without this carve-out every ordinary `ls` using one
    # stalls at an approval card. Strip the exact tokens; any other `>` still refuses.
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
    """True if a write tool's target path resolves inside `root`. Fail-CLOSED (no/unparseable path
    → False): the general-session gate treats an unclear write as out-of-scope, so only a clearly
    in-`root` write (the project's `general/` memory) escapes the deny."""
    path = input_data.get("file_path") or input_data.get("path")
    if not path:
        return False
    try:
        target = Path(path).resolve()
        r = root.resolve()
    except (OSError, ValueError):
        return False
    return target == r or r in target.parents

# Bash is deliberately absent: shell READS are the accepted ceiling, and the OS sandbox does
# not scope reads either. Defense-in-depth over the by-construction scoping.
_READ_TOOLS = {"Read", "Grep", "Glob"}


def _read_target(tool_input: dict) -> str | None:
    """The path a Read/Grep/Glob call targets. Read → `file_path`; Grep/Glob → `path` (the search
    root; absent means the agent's cwd, which is always in scope)."""
    return tool_input.get("file_path") or tool_input.get("path")


def path_in_scope(target: str, cwd: Path, roots: list[Path]) -> bool:
    """True if `target` resolves inside any allowed root; relatives resolve against `cwd`.

    Fail-OPEN on an unparseable path: this is defense-in-depth, not the primary boundary."""
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
    """An ApproveFn that denies everything — the fallback for a background run. Says so, because
    an agent told only "denied" will keep spending turns on a gate that will never open."""
    return NO_HUMAN_TO_ASK


def scoped_writes_approve(allowed_dir: Path, fallback: ApproveFn) -> ApproveFn:
    """An ApproveFn that auto-allows writes INSIDE `allowed_dir` and defers the rest to
    `fallback`. With `deny_all` that is a hard sandbox; with a surface approve, outside writes still
    prompt.

    `Bash` gets the same scope and needs it: these sessions run at the REPO cwd, so a mutating
    command has no in-boundary cwd to be allowed by — which silently costs a research run its whole
    measurement half. A command that EXPLICITLY scopes itself in (`cd <item-dir> && …`) auto-allows;
    a bare mutating command at the repo cwd still defers."""
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
    """Policy for a background learning WRITE run: auto-allow Bash and writes inside `workspace`,
    deny everything else.

    Safe unattended because the run is hermetic and disposable — the proposal text is the only
    input, the transcript is discarded, and the workspace is removed. The disk publish stays
    owner-gated downstream."""
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


# A build session works ONLY in its worktree, so an outside write is an accident by
# construction and hard-denies rather than prompting.
_FREEZE_NUDGE = (
    "Edit boundary (build phase): this work-item owns a dedicated git worktree, and all file "
    "changes must happen inside it (or the item's own artifacts folder). The path you tried is "
    "outside that boundary. Work in the worktree — it is your working directory; the merge back "
    "to main happens at the review gate."
)

# A verifier that physically cannot edit cannot report a fix it did not make. The shell stays,
# for RUNNING checks.
# Review is read-only on the PLAN. `revise_plan` refuses outside the plan phase, but that is
# not the boundary that matters: at review the write boundary is directory-level, so plan.md
# sits inside it and a plain `Edit` would auto-allow. Denied outright — a permission card here
# would grant exactly the wholesale rewrite the surgical revise exists to prevent.
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
    """True if a write tool's target path resolves inside any of `roots`. Fail-CLOSED (no path /
    unparseable → False): during a build freeze an unclear write is treated as out-of-bounds."""
    path = input_data.get("file_path") or input_data.get("path") or input_data.get("notebook_path")
    if not path:
        return False
    try:
        return _in_any(Path(path), roots)
    except (OSError, ValueError):
        return False


# Paths that are not places. `/dev/null` and the standard streams name a kernel device, not a
# directory anyone can be inside, so asking whether they sit in a write boundary is a category
# error — and answering "no" costs real work: measured 2026-08-16, an investigation's capability
# probe (`… | sed … > /dev/null; echo $?`) was refused as an out-of-boundary write, and so were
# several inventory commands whose only sin was discarding stderr. They are transparent to both
# boundary checks: they never mark a command as escaping, and they never count as the in-boundary
# path that scopes one.
_PSEUDO_DEVICE_ROOTS = ("/dev/null", "/dev/zero", "/dev/tty",
                        "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/fd/")


def _is_pseudo_device(token: str) -> bool:
    """True for `/dev/null` and friends — a kernel device, not a location on disk."""
    return any(token == p or token.startswith(p) for p in _PSEUDO_DEVICE_ROOTS)


def _scratch_in(roots: list[Path]) -> Path | None:
    """The first boundary root that already HAS a `scratch/` directory, or None. Read from disk
    rather than derived: an item folder is scaffolded with one, a build worktree is not, and that is
    the difference between a remedy the agent can follow and an instruction to litter the repo."""
    for root in roots:
        try:
            cand = Path(root) / SCRATCH_DIRNAME
            if cand.is_dir():
                return cand
        except (OSError, ValueError):
            continue
    return None


# Tokens that look like an absolute path (`/x`, `~/x`) — the only part of a shell command we can
# honestly reason about. Everything else (relative paths, `cd`, substitution) resolves against the
# session cwd, which the boundary check already pins inside the worktree.
def _bash_escapes_boundary(command: str, roots: list[Path]) -> bool:
    """True if the command NAMES an absolute path outside every boundary root. A cheap
    accident-catcher, not a sandbox — it cannot see through `cd`, variables or substitution.

    Escaping does not deny; it falls through to the normal prompt."""
    for raw in _path_tokens(command):
        tok = raw.strip("'\"")
        if tok.startswith("~"):
            tok = str(Path(tok).expanduser())
        if not tok.startswith("/") or _is_pseudo_device(tok):
            continue
        if not _in_any(Path(tok), roots):
            return True
    return False


def shlex_split_safe(command: str) -> list[str]:
    """`shlex.split` that degrades to a whitespace split on unbalanced quotes."""
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


# Commands whose first positional argument is a PATTERN, not a path. Both boundary checks below
# reason about a command by reading its `/…` tokens as paths, and a regex is spelled like one:
# `grep -v '/generated/' <in> > <out>` names two files, both in bounds, and was refused for the
# third "path" — which was the pattern. `-e`/`--regexp` take the pattern as their VALUE, so they
# are skipped in pairs, the same shape as git's global options.
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
            pattern_taken = True                       # the pattern is this option's value
            i += 2
            continue
        if bare.startswith("-"):
            pattern_taken = pattern_taken or bare.startswith("--regexp=")
            i += 1
            continue
        if not pattern_taken:
            pattern_taken = True                       # the first bare word is the pattern
            i += 1
            continue
        out.append(seg[i])
        i += 1
    return out


def _path_tokens(command: str) -> list[str]:
    """Every token that could name a path. Split per LINE as well as on shell operators: `shlex`
    treats a newline as whitespace, so a multi-line script's second command reads as the first's
    argument."""
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
    """True if the command EXPLICITLY scopes itself into a boundary root — `cd <root>…`,
    `git -C <root>…`, or a command whose every absolute path sits inside one.

    The second form matters: `mkdir -p <item-dir>/scratch` is scoped by its ARGUMENTS, and refusing
    it while allowing the identical work behind `cd` teaches a rule nobody can follow. A command
    naming no absolute path stays unmatched — it resolves against a cwd this cannot see."""
    toks = shlex_split_safe(command)
    for i, raw in enumerate(toks[:-1]):
        if raw.strip("'\"") in ("cd", "-C"):
            target = toks[i + 1].strip("'\"")
            if target.startswith("~"):
                target = str(Path(target).expanduser())
            if target.startswith("/"):
                try:
                    if _in_any(Path(target), roots):
                        return True
                except (OSError, ValueError):
                    pass
    named = 0
    for raw in _path_tokens(command):
        tok = raw.strip("'\"")
        if tok.startswith("~"):
            tok = str(Path(tok).expanduser())
        if not tok.startswith("/") or _is_pseudo_device(tok):
            continue
        named += 1
        try:
            if not _in_any(Path(tok), roots):
                return False
        except (OSError, ValueError):
            return False
    return named > 0


# Bypassing the commit gate is denied for every session, in every phase — the one shell rule that
# is not about the write boundary. An agent whose commit is refused WILL find `--no-verify`; it is
# the first answer anywhere. Prose already lost this argument once (the trailer rule), so the flag
# is taken away rather than discouraged, and the deny message carries the actual instruction.
_NO_VERIFY_NUDGE = (
    "Skipping git hooks is not available here. A hook refused your commit for a reason, and "
    "bypassing it converts that reason into a defect someone finds later.\n\n"
    "If the refusal was SuperMe's task-trailer rule, add the trailer and commit again. If it came "
    "from a check this project owns, that is not yours to overrule: leave the work staged and end "
    "your run with report_completion(machine.outcome='needs_user'), quoting the refusal verbatim "
    "and naming what you think the owner should do about it. Do not retry the same commit."
)

# Flags that turn hooks off, and the subcommands they'd turn them off for. Matched only AFTER the
# subcommand token: `grep -n foo && git commit -m x` is an ordinary command, and reading `-n` there
# as a bypass would deny it.
_HOOK_BYPASS_FLAGS = {"--no-verify", "-n"}
_HOOK_RUNNING_SUBCOMMANDS = {"commit", "merge", "rebase", "cherry-pick", "revert", "am"}
_SHELL_BREAKS = {"&&", "||", ";", "|", "&"}


# Stopping the host is denied for every session, in every phase. An agent working ON SuperMe reads
# this project's own restart procedure and follows it — which is correct advice for a human at a
# keyboard and fatal from inside a run: runs are the daemon's children, so the command kills the
# agent that issued it, every sibling item mid-flight, and the replacement daemon it just started
# (observed live, 2026-08-10: one hub build agent took down three items across two repos). Prose
# cannot hold this line — the procedure is written down in the very repo being worked on — so the
# command is taken away.
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


# The fan-out backstop's deny message (harness.policy.MAX_SUBAGENTS). It has to do two jobs: stop
# the spawn, and make sure the CAP — not a quietly narrowed surface — is what the gate reads. An
# agent told only "denied" carries on and reports as though it covered everything.
_SUBAGENT_CAP_NUDGE = (
    "Subagent limit reached — this run has already spawned {cap} of them, which is the ceiling for "
    "ONE run. Nothing is wrong with the brief; there are simply no more readers to hand it to.\n\n"
    "Read the remaining surface yourself, and SAY SO in your report: name what you were about to "
    "delegate and that the limit is why you read it directly. A reader that was cut off and did not "
    "say so is indistinguishable from one that found nothing — and the gate can only judge the "
    "difference if you write it down."
)


def kills_the_host(command: str) -> bool:
    """True if this command would stop the daemon or BFF serving the dashboard.

    A kill verb plus a HOST reference. The port test is narrow on purpose: a vet-env teardown kills
    by port too, so only the host's own ports count."""
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
    """True if this command would send local work to a remote. The write boundary and the OS
    sandbox both reason about the filesystem, so a push is the one mutation that escapes every wall
    by not touching disk. `git stash push` is local and is not this."""
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


# Shell multiplexers whose SUBCOMMAND is the meaningful unit for approval memory (`git commit` vs
# `git push`); everything else is keyed by its program name alone.
_BASH_MULTIPLEXERS = {"git", "npm", "yarn", "pnpm", "npx", "cargo", "go", "make", "python",
                      "python3", "pip", "pip3", "uv", "poetry", "conda", "docker", "node", "bun"}


def approval_signature(tool_name: str, tool_input: dict) -> str:
    """A stable key for "the owner already OK'd this KIND of call". Bash → program plus
    subcommand for a known multiplexer, program alone otherwise, so approving one `pytest` clears
    later ones while `git commit` and `git push` stay distinct.

    Coarse ON PURPOSE: exact-command memory barely helps, since agents vary args every call."""
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
    """Best-effort: the skill identifiers a Skill/SlashCommand call targets, with the leading
    `/` and any args dropped, and the bare name after a `<ns>:` also yielded."""
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
    """Wrap a surface's ApproveFn into the SDK's `can_use_tool`. Safe tools auto-allow;
    everything else defers to `approve`. The switches:

      blocked_skills       skill → its own deny message. Checked before the safe-tool allow, since
                           `Skill` is itself safe. Per-skill because the two blocks are unrelated.
      read_roots + cwd     the L2 read-guard: a read outside the host's allowlist is denied.
      gate_general_mutations   in a general dev session, file-WRITE tools deny WITH FEEDBACK and no
                           prompt. `Bash` is not covered — a command is not path-checkable, and
                           refusing what cannot be proven read-only refused reading too.
      general_write_root   the one exception: writes into the project's `general/` memory home.
      write_boundary       the build freeze boundary. Inside, writes auto-allow; outside they hard-
                           deny. Governs `Bash` too, or the loop parks on a human every command.
                           Accident-prevention, not security — but a run passing `sandbox_writes`
                           hands the same roots to the OS, where an escape fails as a syscall.
      deny_write_tools     removes file-write tools OUTRIGHT — vet's anti-self-report guarantee.
                           `Bash` deliberately stays: vet must RUN checks.
      shell_roots          directories the SHELL may name, and nothing else. Research is the one run
                           whose source and destination are different trees, so every honest command
                           names both. File writes stay confined by `write_boundary`.
      subagent_cap         how many subagents ONE turn may spawn. The count lives in this closure,
                           which is built per turn, and nested spawns come back through here too."""
    spawned = 0   # this turn's subagent tally — see `subagent_cap` above

    async def can_use_tool(
        tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        nonlocal spawned
        # Set only by the Bash branch, and only when a command missed the write boundary. Bound
        # here so every path below can read it without caring whether that branch ran.
        bash_boundary_miss: str | None = None
        if subagent_cap is not None and tool_name in SUBAGENT_TOOLS:
            if spawned >= subagent_cap:
                log.warning("subagent cap reached (%d) — refused a further %s spawn",
                            subagent_cap, tool_name)
                return PermissionResultDeny(
                    message=_SUBAGENT_CAP_NUDGE.format(cap=subagent_cap))
            # Counted on the ALLOW, not on the attempt: a spawn the SDK never runs must not spend
            # a slot, and a refused one is already reported by its deny message.
            spawned += 1
        if blocked_skills and tool_name in _SKILL_TOOLS:
            for n in _invoked_skill_names(tool_name, input_data):
                if n in blocked_skills:
                    log.info("blocked skill via %s: %s", tool_name, input_data)
                    # The deny message is the agent's ONLY feedback — it must be TRUE for THIS
                    # block, or the agent self-corrects toward a wrong conclusion (an onboarding
                    # skill told "this is learning-pipeline machinery" learns nothing useful).
                    return PermissionResultDeny(message=blocked_skills[n])
        # Surface-unsupported tools (e.g. AskUserQuestion) → deny with a nudge to ask in plain text.
        if tool_name in _SURFACE_UNSUPPORTED:
            log.info("surface-unsupported tool denied: %s (ask in text)", tool_name)
            return PermissionResultDeny(message=_ASK_IN_TEXT_NUDGE)
        # Vet read-only (§4 anti-self-report): file-write tools are denied OUTRIGHT — before the
        # freeze boundary's in-boundary auto-allow and before any human prompt.
        if deny_write_tools and tool_name in _WRITE_TOOLS:
            log.info("vet read-only denied %s", tool_name)
            return PermissionResultDeny(message=deny_write_tools)
        # Per-FILE carve-out inside the boundary. Checked BEFORE the in-boundary auto-allow (the
        # protected file lives inside it) and before `approve`, so it can never become a permission
        # card. Today's only user is review's `plan.md` — and it is deliberately a caller-supplied
        # path list, not a phase rule baked in here: the caller knows the phase, this layer only
        # knows paths, exactly as `write_boundary` is passed in.
        if protected_paths and tool_name in _WRITE_TOOLS \
                and _target_in_any(input_data, protected_paths):
            log.info("protected path denied %s", tool_name)
            return PermissionResultDeny(message=protected_nudge or _FREEZE_NUDGE)
        # Build-phase freeze boundary (S4): writes live-or-die on the worktree/item roots.
        if write_boundary and tool_name in _WRITE_TOOLS:
            if _target_in_any(input_data, write_boundary):
                return PermissionResultAllow()
            log.info("freeze boundary denied %s outside the item worktree", tool_name)
            return PermissionResultDeny(message=_FREEZE_NUDGE)
        # General-session guardrail: hard-deny mutating tools with a nudge, no user prompt. Placed
        # before the safe-tool check and before `approve`, so the human is never interrupted — the
        # agent self-corrects toward itemizing (the deny message is its feedback).
        if gate_general_mutations and tool_name in _GENERAL_SESSION_BLOCKED:
            # The one allowed write: authoring/maintaining the project's `general/` memory (onboarding
            # + anchor-doc upkeep). Auto-allow it (autonomous, no prompt); deny everything else.
            # No general_write_root at all = a fully read-only session (diagnosis) — its deny
            # message must not claim general/ writes are allowed.
            if general_write_root is not None and _write_target_under(input_data, general_write_root):
                return PermissionResultAllow()
            log.info("general-session guardrail denied %s (nudge to itemize)", tool_name)
            return PermissionResultDeny(message=_GENERAL_SESSION_NUDGE if general_write_root
                                        is not None else _READONLY_SESSION_NUDGE)
        # Shell: read-only commands are the same access as Read/Grep/Glob → auto-allow (no prompt).
        # Anything the classifier cannot PROVE read-only defers to approval — in every session,
        # general ones included.
        if tool_name == "Bash":
            command = input_data.get("command", "")
            # Before everything else, including the read-only fast path: no session, in any phase,
            # gets to run git with its hooks off.
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
            # A general session's shell ASKS rather than refuses. The classifier proves read-only or
            # it proves nothing — `sqlite3 db "SELECT …"` is unprovable for the same reason
            # `sqlite3 db "DROP …"` is, so refusing the unprovable refused reading the project's own
            # data while `cat` on the very same file sailed through. Denying also broke the premise
            # the OS sandbox is skipped on: `core.sandbox` leaves interactive turns unsandboxed
            # BECAUSE "a person is approving each command there" — which was false here, so neither
            # the kernel nor the owner was in the loop and the command just died. Falling through to
            # `approve` makes that premise true. The write TOOLS above still hard-deny: those are
            # provably mutations, and the itemize nudge belongs to them.
            # Freeze boundary (S4): a phase agent working inside its own worktree owns its shell.
            # Running tests, installing deps and committing are the agent's job — the same autonomy
            # the boundary already grants its writes, and the precondition for the build⟷vet
            # loop (a loop that stops for a click on every test run is not a loop). The owner's gate
            # is the merge, not each command.
            # Auto-allow when the command lives in the boundary: either the session cwd is inside it
            # (build/vet — worktree cwd), OR the command explicitly scopes itself into it via
            # `cd <worktree>` / `git -C <worktree>` (review/close — repo cwd for transcript
            # continuity, P4). Both require the command to name no path OUTSIDE the boundary.
            # `reachable` is the shell's territory: the write boundary plus any `shell_roots` this
            # run may also name. The write TOOLS above were already decided against `write_boundary`
            # alone, so a shell root never becomes a place the agent can `Write` to.
            reachable = [*(write_boundary or []), *(shell_roots or [])]
            if (reachable and not _bash_escapes_boundary(command, reachable)
                    and ((cwd is not None and _in_any(cwd, reachable))
                         or _bash_scoped_into_boundary(command, reachable))):
                return PermissionResultAllow()
            # Not read-only, and not scoped into the boundary. It still goes to `approve` — an owner
            # at the keyboard may well want to allow it — but the REFUSAL now carries the remedy
            # instead of the generic "a background run cannot ask anyone" sentence, which is true
            # and useless here: the command was refusable on its own terms and fixable in one edit.
            # Only when scoping it in WOULD work — the command names no path outside the boundary,
            # it simply isn't anchored inside one. A command that does reach outside gets the plain
            # refusal: telling it to `cd` in would be advice that cannot be followed, and for a
            # destructive one it would be advice worth not giving.
            if reachable and not _bash_escapes_boundary(command, reachable):
                bash_boundary_miss = _BASH_OUTSIDE_BOUNDARY.format(
                    roots=", ".join(f"`{r}`" for r in reachable), first=reachable[0])
                if scratch := _scratch_in(write_boundary):
                    bash_boundary_miss += _BASH_SCRATCH_HINT.format(scratch=scratch)
        # L2 read-guard: keep reads inside the host's scope (before the safe-tool auto-allow).
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
        # A shell command that missed its own write boundary is refused ABOUT ITSELF, and the agent
        # can fix it — so that sentence wins over whatever generic wording the approver returned.
        if bash_boundary_miss:
            return PermissionResultDeny(message=bash_boundary_miss)
        # A surface that KNOWS why hands back the sentence; one that only knows "no" gets the
        # owner-refusal wording, which is what a plain False has always meant at this seam.
        return PermissionResultDeny(
            message=verdict if isinstance(verdict, str) and verdict.strip() else _DENIED_BY_OWNER)

    refused = 0

    async def counted(tool_name: str, input_data: dict, context: ToolPermissionContext
                      ) -> PermissionResultAllow | PermissionResultDeny:
        """Every refusal carries a running tally. Each deny message is read once, hundreds of calls
        before the report is written — a run refused fourteen times reported "no tool was unavailable".
        A count is the one thing the agent cannot reconstruct and the kernel cannot get wrong."""
        nonlocal refused
        result = await can_use_tool(tool_name, input_data, context)
        if isinstance(result, PermissionResultDeny):
            refused += 1
            return PermissionResultDeny(
                message=f"{result.message}\n\n{_REFUSAL_TALLY.format(n=refused)}")
        return result

    return counted
