"""The approval seam — surface-neutral human-in-the-loop gating.

The Core never contains any asking-UI. When a tool needs human approval, it calls an
`ApproveFn` the *surface* supplied:

    approve(tool_name, tool_input) -> bool      # async; True=allow, False=deny

Each surface plugs in its own implementation (Slack = ✅/❌ reactions; web = an
Allow/Deny button over WebSocket; CLI = a y/n prompt). `build_can_use_tool` bridges
that callback to the SDK's `can_use_tool` interface, applying the safe-tool policy
(which auto-runs read-only tools without ever asking).
"""

import logging
import shlex
from pathlib import Path
from typing import Awaitable, Callable

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from ..harness.policy import is_safe

log = logging.getLogger("superme-agent")

# (tool_name, tool_input) -> allow?  Supplied by each surface.
ApproveFn = Callable[[str, dict], Awaitable[bool]]

# Tools that write to the filesystem (reads are covered by the safe-tool policy).
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Tools with no SuperMe surface handling → denied with feedback so the agent falls back naturally.
# `AskUserQuestion` renders a structured picker in Claude Code, but SuperMe's chat is plain
# conversation (no picker, no way to return a selection) — so the agent must just ASK IN TEXT.
_SURFACE_UNSUPPORTED = {"AskUserQuestion"}
_ASK_IN_TEXT_NUDGE = (
    "The AskUserQuestion tool isn't available on this surface. If a human is in this chat, ask "
    "your question(s) as plain conversational text — one at a time, with your recommended answer. "
    "In a background run, don't ask at all: take your recommended option and record the judgment "
    "call in your final report."
)

# General-session guardrail (work-item-session-recognition-prd): the mutating tools a GENERAL
# (non-work-item) dev session may NOT use — all real dev work must flow through a work-item + its
# session. Denied with FEEDBACK, no user prompt, so the agent re-thinks and pivots to itemizing.
# The sanctioned itemize tool (mcp__*__create_inbox_item) is NOT here, so it stays allowed — the
# one write a general session may make. Bash is deliberately absent (dual-use: read-only inspection
# stays useful; the Guard block soft-restricts mutating shell commands). Named mutating work-item /
# knowledge MCP tools can be added here as they become agent-reachable.
_GENERAL_SESSION_BLOCKED = set(_WRITE_TOOLS)
_GENERAL_SESSION_NUDGE = (
    "Mutating the project's real code (writing/editing files or implementing) is disabled in a "
    "general discussion session — all real dev work must happen inside a work-item and its session. "
    "Authoring the project's `general/` memory docs (onboarding via project-init/retrofit, or "
    "maintaining an anchor doc) is allowed and not what this blocks. If what you're about to do is "
    "real implementation work, don't try it here: propose itemizing it into an inbox item (the "
    "create-inbox-item skill) so it can be picked up and done properly."
)
# The fully read-only variant (diagnosis sessions carve NO general/ write exception): the general
# nudge's "authoring general/ memory is allowed" claim would be false there — the agent would
# retry a blocked write it was just told is fine (M1).
_READONLY_SESSION_NUDGE = (
    "This session is fully READ-ONLY — no file writes at all, including the project's memory "
    "docs. Describe the change you'd make instead, and offer to itemize it (the create-inbox-item "
    "skill) so it happens in its own work-item."
)


# --- read-only shell classifier (session-agent-lifecycle-prd, Bug 1) ------------------------------
# Read-only `Bash` is the same access an agent already has via Read/Grep/Glob (auto-allowed), so it
# shouldn't prompt. But Bash is unsandboxable, so this classifier is FAIL-CLOSED: it returns True only
# for a command it can PROVE is read-only; anything ambiguous → False (falls through to the normal
# prompt/deny, never a silent auto-allow). Conservative by design — a false negative just costs a
# prompt; a false positive would be a hole.
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
        sub = seg[1] if len(seg) > 1 else ""
        if sub not in _GIT_READONLY:
            return False
        return not any(a in _GIT_MUTATING_ARGS for a in seg[2:])
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
    # The two PROVABLY write-free redirects: discarding stderr, and folding it into stdout. Agents
    # reach for both on ordinary reads constantly; without this carve-out every such `ls`/`cat`
    # stalls an interactive turn at an approval card (and hard-fails a background one). Strip the
    # exact tokens, then judge the rest as usual — any other `>`/`<` still refuses.
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

# File-reading tools the L2 read-guard scopes to the host's allowlist (context-model-spec §3).
# Bash is deliberately NOT here — the SDK has no fs-sandbox mode, so shell reads are the accepted
# ceiling (L3, deferred). This guard is defense-in-depth over the by-construction scoping.
_READ_TOOLS = {"Read", "Grep", "Glob"}


def _read_target(tool_input: dict) -> str | None:
    """The path a Read/Grep/Glob call targets. Read → `file_path`; Grep/Glob → `path` (the search
    root; absent means the agent's cwd, which is always in scope)."""
    return tool_input.get("file_path") or tool_input.get("path")


def path_in_scope(target: str, cwd: Path, roots: list[Path]) -> bool:
    """True if `target` resolves inside any allowed root. Relative paths resolve against `cwd`
    (the agent's working root, which differs from the daemon process cwd). Fail-OPEN on an
    unparseable path — the guard is defense-in-depth, not the primary boundary, so a weird path
    is deferred to normal handling rather than hard-denied."""
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


async def deny_all(tool_name: str, tool_input: dict) -> bool:
    """An ApproveFn that denies everything — the fallback for a background run (nothing to ask)."""
    return False


def scoped_writes_approve(allowed_dir: Path, fallback: ApproveFn) -> ApproveFn:
    """An ApproveFn that auto-allows writes *inside* `allowed_dir`, deferring everything
    else to `fallback`. Reads are already auto-allowed upstream (safe-tool policy), so this
    makes an agent autonomous within one folder while keeping a gate on everything outside it
    — e.g. a planning agent can freely write its own work-item but not real source code.

    With `fallback=deny_all` this is a hard sandbox (background: nothing to ask); with
    `fallback=<surface approve>` the outside-writes still prompt the human (interactive)."""
    allowed = allowed_dir.resolve()

    async def approve(tool_name: str, tool_input: dict) -> bool:
        if tool_name in _WRITE_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("path")
            if path:
                try:
                    target = Path(path).resolve()
                    if target == allowed or allowed in target.parents:
                        return True
                except (OSError, ValueError):
                    pass
        return await fallback(tool_name, tool_input)

    return approve


def learning_write_approve(workspace: Path) -> ApproveFn:
    """Policy for a background learning WRITE run (the `forge` sub-agent). Forge needs two non-safe
    tools with no human to ask: `Bash` (to run the forge_kit lint + behavioural eval) and `Write`
    (to draft the artifact into its scratch workspace). Auto-allow Bash, and writes inside
    `workspace`; deny everything else (no edits to real source, no writes outside the scratch dir).

    This is safe to run unattended because the run is hermetic and disposable: it operates on our
    own toolkit, the proposal text is the only input, the transcript is discarded, and the workspace
    is removed afterwards. The actual disk publish stays owner-gated downstream (gate 2)."""
    ws = workspace.resolve()

    async def approve(tool_name: str, tool_input: dict) -> bool:
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
        return False

    return approve


# Freeze-boundary deny message (workspace-workflow S4/D4): a build session works ONLY in its
# item's worktree — outside writes are an accident by construction, so they hard-deny with the
# reason rather than prompting the human.
_FREEZE_NUDGE = (
    "Edit boundary (build phase): this work-item owns a dedicated git worktree, and all file "
    "changes must happen inside it (or the item's own artifacts folder). The path you tried is "
    "outside that boundary. Work in the worktree — it is your working directory; the merge back "
    "to main happens at the review gate."
)

# Vet read-only denial (build-vet-loop §4/§8·O4, nimbalyst anti-self-report): a vet session has NO
# file-write tools — a verifier that physically cannot edit cannot report a fix it didn't make.
# Denied outright with the reason (never a human prompt); the shell stays available for RUNNING
# checks (tests, env) under the freeze-boundary rule.
VET_READONLY_NUDGE = (
    "This is a VET session — file writes are disabled by design (a verifier that can edit could "
    "grade its own fixes). Record each check's outcome with record_validation_evidence and file "
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


# Tokens that look like an absolute path (`/x`, `~/x`) — the only part of a shell command we can
# honestly reason about. Everything else (relative paths, `cd`, substitution) resolves against the
# session cwd, which the boundary check already pins inside the worktree.
def _bash_escapes_boundary(command: str, roots: list[Path]) -> bool:
    """True if the command NAMES an absolute path outside every boundary root. A cheap
    accident-catcher, not a sandbox: it can't see through `cd`, variables, or substitution (the
    read-only classifier already refuses those constructs, and Bash is not path-checkable in
    general — see build_can_use_tool). Escaping doesn't deny; it falls through to the normal
    prompt, so a deliberate outside command still works with the owner's click."""
    for raw in shlex_split_safe(command):
        tok = raw.strip("'\"")
        if tok.startswith("~"):
            tok = str(Path(tok).expanduser())
        if not tok.startswith("/"):
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


def _bash_scoped_into_boundary(command: str, roots: list[Path]) -> bool:
    """True if the command EXPLICITLY scopes itself into a boundary root — `cd <root>…` or
    `git -C <root>…` where the target is an absolute path inside a boundary root. This is the
    review/close analogue of the build case's cwd-in-boundary auto-allow (P4): those sessions keep
    the REPO cwd for transcript continuity, but their legitimate worktree work NAMES the worktree,
    so a command that enters the boundary explicitly is as safe to auto-allow as one run from inside
    it. A bare mutating command (no scoping — a `rm` at the repo cwd) does NOT match, so it still
    defers to the owner."""
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
    return False


# Shell multiplexers whose SUBCOMMAND is the meaningful unit for approval memory (`git commit` vs
# `git push`); everything else is keyed by its program name alone.
_BASH_MULTIPLEXERS = {"git", "npm", "yarn", "pnpm", "npx", "cargo", "go", "make", "python",
                      "python3", "pip", "pip3", "uv", "poetry", "conda", "docker", "node", "bun"}


def approval_signature(tool_name: str, tool_input: dict) -> str:
    """A stable key for 'the owner already OK'd this KIND of call' (session approval memory, P4 —
    'remember approval within a session'). Bash → program + subcommand for a known multiplexer
    (`git commit`), program alone otherwise (`pytest`), so approving one `pytest …` clears later
    `pytest …` runs but `git commit` and `git push` stay distinct. Every other tool → its name (its
    target is already governed by the write boundary). Coarse ON PURPOSE: exact-command memory
    barely helps, since agents vary args on every call."""
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
    """Best-effort: the skill/command identifiers a Skill/SlashCommand call targets. We look at every
    string value, drop a leading `/` and any args, and also yield the bare name after a `<ns>:` —
    so both `superme-dev:forge-skill` and `forge-skill` can be matched against the blocked set."""
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
                       deny_write_tools: str | None = None):
    """Wrap a surface's ApproveFn into the SDK `can_use_tool` callback.

    Safe (read-only) tools auto-allow; everything else defers to `approve`. `blocked_skills` maps a
    skill name → the DENY MESSAGE explaining that specific block, and is checked OUTRIGHT before the
    safe-tool check (even though `Skill` is a safe tool). Two blocks use it, and they are unrelated
    — which is why the message is per-skill rather than one constant: `access: silent` machinery
    (forge-*, invocable only by its owning sub-run) and, once a project's memory is established, the
    one-shot `onboarding` skills. A caller that passes nothing blocks nothing.

    `read_roots` (with `cwd` to resolve relatives) enables the **L2 read-guard**: a Read/Grep/Glob
    whose target falls outside the host's allowlist is denied before the safe-tool auto-allow, so a
    host cannot read another repo's files or the bulk of SuperMe's own source. Omit both to disable
    the guard (e.g. a hermetic background run that governs paths its own way).

    `gate_general_mutations` enables the **general-session guardrail** (work-item-session-recognition-
    prd): in a GENERAL (non-work-item) dev session, mutating tools are denied WITH FEEDBACK and NO
    user prompt — the agent gets a nudge to itemize instead and re-thinks. The sanctioned itemize tool
    is not in the blocked set, so it stays allowed. `general_write_root` carves the one exception:
    writes whose target is inside it (the project's `general/` memory home) are AUTO-allowed, so
    onboarding (project-init/retrofit authoring the anchor docs) and routine anchor-doc maintenance
    work in a general session while real-code writes stay denied.

    `write_boundary` enables the **build-phase freeze boundary** (workspace-workflow S4/D4): a
    work-item build session's writes are confined to its git worktree + item folder — inside any
    boundary root a write AUTO-allows (the agent works autonomously in its own tree, "commits
    freely"); outside it hard-denies with the boundary explained (no human prompt — an outside
    write during build is an accident by construction). Cleared at terminal by simply not passing
    it. Honest limit: accident-prevention, not security (Bash is not path-checkable).

    The boundary governs `Bash` too, and must: an agent that has to ask before running its own
    tests isn't autonomous, and the build⟷vet loop can't run at all if every command parks
    on a human. A mutating command AUTO-allows when the session `cwd` is inside the boundary and
    the command names no absolute path outside it. That is weaker than the write-tool check by
    necessity — `cd /elsewhere && rm -rf` is not detectable by reading a string — so the guarantee
    here is the worktree's, not the classifier's: the agent's cwd IS a throwaway tree, its commits
    are local to a branch nobody has merged, and the owner's real gate is the merge. A command that
    does name an outside path doesn't deny — it falls through to the normal prompt, so deliberate
    outside work still happens with a click.

    `deny_write_tools` (a deny MESSAGE) removes the file-write tools OUTRIGHT — the vet session's
    anti-self-report guarantee (build-vet-loop §4): checked before the freeze boundary's
    auto-allow, so not even in-boundary writes pass, and before `approve`, so no human prompt can
    grant one either. `Bash` is deliberately NOT covered — vet must RUN checks (tests, env), and
    the freeze-boundary shell rule still applies; the guarantee is "no write capability via file
    tools", the same honest limit as the boundary itself.
    """

    async def can_use_tool(
        tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
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
        # A mutating shell command in a general session is denied+nudged (same rule as the write
        # tools — "no code mutation, including via shell"); elsewhere it defers to approval as before.
        if tool_name == "Bash":
            command = input_data.get("command", "")
            if is_read_only_bash(command):
                return PermissionResultAllow()
            if gate_general_mutations:
                log.info("general-session guardrail denied mutating Bash (nudge to itemize)")
                return PermissionResultDeny(message=_GENERAL_SESSION_NUDGE if general_write_root
                                            is not None else _READONLY_SESSION_NUDGE)
            # Freeze boundary (S4): a phase agent working inside its own worktree owns its shell.
            # Running tests, installing deps and committing are the agent's job — the same autonomy
            # the boundary already grants its writes, and the precondition for the build⟷vet
            # loop (a loop that stops for a click on every test run is not a loop). The owner's gate
            # is the merge, not each command.
            # Auto-allow when the command lives in the boundary: either the session cwd is inside it
            # (build/vet — worktree cwd), OR the command explicitly scopes itself into it via
            # `cd <worktree>` / `git -C <worktree>` (review/close — repo cwd for transcript
            # continuity, P4). Both require the command to name no path OUTSIDE the boundary.
            if (write_boundary and not _bash_escapes_boundary(command, write_boundary)
                    and ((cwd is not None and _in_any(cwd, write_boundary))
                         or _bash_scoped_into_boundary(command, write_boundary))):
                return PermissionResultAllow()
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
        approved = await approve(tool_name, input_data)
        log.info("approval: %s -> %s", tool_name, "ALLOW" if approved else "DENY")
        return (
            PermissionResultAllow()
            if approved
            else PermissionResultDeny(message="Denied by the owner.")
        )

    return can_use_tool
