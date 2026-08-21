"""AgentService — run one conversational turn, surface-agnostically.

Composes ClaudeAgentOptions from the harness plus the per-run Context, drives the SDK loop, and
yields surface-neutral TurnEvents. It knows nothing about sessions, surfaces or model policy —
those arrive as plain parameters.
"""

import logging
from collections import deque
from pathlib import Path
from typing import AsyncIterator

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    UserMessage,
)

from ..paths import (
    SELF_FILE, CHARTER_FILES, HARNESS_DIR, LOCAL_HARNESS_DIR, CONSTITUTION_DIR, plugins_for,
)
from .models import normalize_model
from .operational import (constitution_catalog, list_repo_assets, silent_skill_names,
                          skills_in_category)
from .dev_knowledge import DevKnowledgeService

_DEV = DevKnowledgeService()  # stateless — reused to build the dev Orient digest
from ..harness.tools.base_tools import make_base_mcp_server
from .context import Context
from .events import Init, TextDelta, Status, ToolResult, Usage, Result, TurnEvent
from .permissions import ApproveFn, build_can_use_tool, deny_all
from .sandbox import sandbox_options

log = logging.getLogger("superme-agent")

# The CLI's last stderr lines: ProcessError says to check a stderr the SDK never piped.
_CLI_STDERR: deque[str] = deque(maxlen=40)


def _cli_stderr(line: str) -> None:
    line = (line or "").rstrip()
    if line:
        _CLI_STDERR.append(line)


def cli_stderr_tail(n: int = 12) -> str:
    """The CLI's last stderr lines, newest last — for a fault report to quote."""
    return "\n".join(list(_CLI_STDERR)[-n:])


def _result_text(content) -> str:
    """Flatten a ToolResultBlock's content to plain text for the trail, dropping non-text blocks.
    Capping is the caller's job."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text" and b.get("text"):
                    parts.append(str(b["text"]))
            elif hasattr(b, "text") and getattr(b, "text"):
                parts.append(str(b.text))
        return "\n".join(parts)
    return str(content)


def _sum_tokens(usage: dict | None) -> int:
    """The "critical" token usage: fresh input + output + cache writes.

    EXCLUDES cache_read, which is cheap re-reads that otherwise dwarf the tokens representing real
    work. Context-fill still counts them, because they do fill the window."""
    if not usage:
        return 0
    return sum(usage.get(k, 0) for k in (
        "input_tokens", "output_tokens", "cache_creation_input_tokens",
    ))


def _window_from_model_usage(model_usage: dict | None, model: str | None) -> int | None:
    """The model's true contextWindow from the SDK's model_usage, if present.

    Only the ResultMessage carries it, so the caller caches this per model and passes it back as
    `window_hint` — otherwise streaming and the final result divide by different windows."""
    if not model_usage:
        return None
    entry = model_usage.get(model) if model else None
    entry = entry or next((v for v in model_usage.values() if isinstance(v, dict)), None)
    if entry and entry.get("contextWindow"):
        return entry["contextWindow"]
    return None


def _context_usage(usage: dict | None, model_usage: dict | None, model: str | None,
                   window_hint: int | None = None):
    """Approximate context-window fill. Returns (percent, window_tokens) or None.

    `usage` MUST be a SINGLE call's usage, never a turn aggregate: an aggregate double-counts cache
    reads across round-trips and inflates the fill 2–3×.

    The denominator is the model's real contextWindow, read from `model_usage` or the cached hint.
    There is deliberately NO fallback guess — a percentage against the wrong window is a false
    reading, worse than none, so an unknown window returns None and the surface shows nothing."""
    if not usage:
        return None
    used = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("output_tokens", 0)
    )
    window = _window_from_model_usage(model_usage, model) or window_hint
    if not used or not window:
        return None
    return round(used / window * 100), window


def _read_roots(ctx: Context) -> list[Path]:
    """The host's allowed-read roots for the L2 guard: its cwd, its knowledge tree, the universal
    harness and its own local harness. Everything else is out of scope.

    The hub's cwd already contains all of those, so the hub reads broadly and a repo host narrowly,
    from the same rule."""
    roots = [ctx.cwd, HARNESS_DIR]
    if ctx.internal_root:
        roots.append(ctx.internal_root)          # this host's <id>-knowledge tree
    if ctx.id:
        roots.append(LOCAL_HARNESS_DIR / ctx.id)  # this host's local harness (both modes)
    return roots


class AgentService:
    """Runs SDK turns for any surface, emitting TurnEvents."""

    def __init__(self, persona: str | None = None):
        # The portable SELF (WHO), loaded once from the in-code harness.
        self._persona = persona if persona is not None else SELF_FILE.read_text()
        # Per-mode charters (WHAT MODE), loaded once. Selected by Context.mode per turn.
        self._charters = {
            mode: path.read_text() for mode, path in CHARTER_FILES.items() if path.exists()
        }
        # Real contextWindow per model, so per-step frames divide by the same window the Result
        # does. Warms after the first turn.
        self._window_by_model: dict[str, int] = {}
        # Measured context FLOOR per (context, model) — see measure_context_floor.
        self._floor_by_key: dict[str, tuple[int, int]] = {}

    async def measure_context_floor(self, ctx: Context, model: str | None = None
                                    ) -> tuple[int, int] | None:
        """The session's incompressible FLOOR — (tokens, window) — measured, not guessed.

        `get_context_usage()` read on a session built with the real `_build_options` but BEFORE
        any turn reports exactly what every turn re-sends and no summary can ever remove: system
        prompt + tool schemas + skills + agent defs. That is the honest denominator for
        "did this compaction shed most of what was SHEDDABLE".

        The old proxy — the session's FIRST observed fill — was wrong in both directions: a first
        turn already carries a prompt and the item's context (it read ~16–20% against a ~10.6%
        floor), and a daemon restart re-measured it mid-conversation, so the floor could land
        anywhere. This depends only on the options, so it is stable and cached per
        (context, model); the cost is one subprocess spawn the first time.

        Returns None if the read fails — callers fall back to their flat threshold.
        """
        key = f"{ctx.id or '-'}::{ctx.mode}::{model or '-'}"
        if key in self._floor_by_key:
            return self._floor_by_key[key]
        try:
            options = self._build_options(ctx, resume=None, model=model, approve=deny_all,
                                          extra_mcp_servers=None, scope_reads=True)
            async with ClaudeSDKClient(options=options) as client:
                usage = await client.get_context_usage()
            cats = usage.get("categories") or []
            tokens = int(usage.get("totalTokens")
                         or sum(int(c.get("tokens") or 0) for c in cats))
            window = int(usage.get("rawMaxTokens") or usage.get("maxTokens") or 0)
            if not tokens or not window:
                return None
        except Exception:
            log.exception("context-floor probe failed for %s", key)
            return None
        self._floor_by_key[key] = (tokens, window)
        log.info("context floor %s = %d tok / %d window (%.1f%%)",
                 key, tokens, window, tokens / window * 100)
        return self._floor_by_key[key]

    def _context_preamble(self, ctx: Context, *, item_bound: bool = False) -> str:
        """Where this turn is operating: host, cwd, the knowledge roots, and (unbound sessions only)
        the project orientation digest.

        Written as a lookup table, not prose — every line here rides EVERY turn, and the reader
        needs to find a path, not read a paragraph. Each root is stated ONCE at full length and its
        children hang off it relatively; spelling the 60-char prefix again per child was three
        copies of one fact (00-superme-context-practice §4d/§4f)."""
        where = ("the **SuperMe hub** (the owner's home host: their cross-domain self, and "
                 "SuperMe's own codebase)" if ctx.layer == "global" else f"project host `{ctx.id}`")
        text = (
            f"\n\n## Operating context\n"
            f"Host: {where} · context `{ctx.label}` · cwd `{ctx.cwd}`."
        )
        # ABSOLUTE paths: the knowledge trees do not live under the cwd, so relative ones silently
        # miss. The charter refers to them relatively; these lines bind those references.
        if ctx.internal_root:
            core_root = ctx.knowledge_root or (ctx.internal_root / "core")
            if ctx.mode == "dev":
                dev_root = ctx.internal_root / "dev"
                text += (
                    f"\nDev-knowledge root — NOT under the cwd — `{dev_root}`\n"
                    f"  · anchor docs `general/` · work-items `work-items/`\n"
                    f"Core knowledge `{core_root}` (read-only in dev)."
                )
                # A digest so a cold session is oriented without reading. An item-bound turn drops the
                # in-progress list: it already has a subject.
                try:
                    digest = _DEV.orient_digest(dev_root, in_progress=not item_bound)
                except Exception:  # noqa: BLE001 — orientation is best-effort, never fatal
                    digest = None
                if digest:
                    text += f"\n\n## This project\n{digest}"
            else:
                text += f"\nCore-knowledge home — NOT under the cwd — `{core_root}`."
        return text

    # The agent's ONLY feedback about a block, so each must be true of the skills it covers.
    _SILENT_SKILL_DENY = ("This is an internal SuperMe skill — it runs only inside the learning "
                          "pipeline, not from chat.")
    _CATEGORY_DENY = {
        "onboarding": (
            "Onboarding is already done for this project — it has established project's single source of truth (anchor docs), "
            "so the onboarding skills are closed and would overwrite that memory rather than build "
            "on it. To CHANGE an anchor doc, edit the specific section that's wrong (or raise a "
            "work-item for it); to record something new, add it. Don't re-derive the docs."
        ),
    }
    _CATEGORY_DENY_DEFAULT = "The `{category}` skills aren't available in this session."

    def _resolve_scope(self, ctx: Context):
        """The per-repo scope a turn resolves against: (op_home, const_universal, const_repo,
        activated_assets). Deterministic from the Context — `_build_options` reuses it for the MCP
        server + turn plugins, and `assemble_system_append` builds the catalog from it. Factored so
        the input-preview endpoint resolves scope through the exact same code the live turn does."""
        op_home = LOCAL_HARNESS_DIR / ctx.id / ctx.mode if ctx.id else None
        const_universal = CONSTITUTION_DIR / ctx.mode
        const_repo = (op_home / "constitution") if op_home is not None else None
        activated_assets = list_repo_assets(const_repo)
        return op_home, const_universal, const_repo, activated_assets

    def _fragment_parts(self, ctx: Context, *, op_home, const_universal, const_repo,
                        activated_assets, system_append: str | None = None,
                        item_bound: bool = False) -> list[dict]:
        """The layer-2 system append as ORDERED provenance fragments: persona (WHO) · mode charter
        (WHAT MODE) · per-repo local charter · constitution CATALOG · operating-context preamble
        (WHERE) · per-project persona_append · the per-turn session-kind block (which, on
        kernel-fired work-item runs, carries the run protocol — the retired background-contract
        block has no successor fragment). THE single source of truth — `_assemble_append` joins these into the exact
        string a turn sends, and the prompt inspector captures them for per-fragment display. Each
        fragment carries `sep` = the exact string preceding it in the joined append, so
        `''.join(sep+text)` reproduces the append byte-for-byte."""
        frags: list[dict] = []

        def add(name: str, location: str, text: str, *, sep: str) -> None:
            frags.append({"name": name, "location": location, "text": text, "sep": sep})

        # The leading joined group: "\n\n" between members, nothing before the first.
        def add_joined(name: str, location: str, text: str) -> None:
            add(name, location, text, sep="" if not frags else "\n\n")

        add_joined("Persona — SELF (who)", "harness/SELF.md", self._persona)
        charter = self._charters.get(ctx.mode) or self._charters.get("core", "")
        if charter:
            add_joined(f"Charter — {ctx.mode} mode (what)", f"harness/{ctx.mode}-charter.md", charter)
        # Per-repo overlay, appended after the universal charter. Additive; most repos have none.
        if op_home is not None:
            local_charter = op_home / "charter.local.md"
            if local_charter.is_file():
                add_joined("Local charter — repo overlay",
                           f"local-harness/{ctx.id}/{ctx.mode}/charter.local.md",
                           local_charter.read_text())
        # Frontmatter only, for ENABLED in-scope items. Bodies are pulled on demand.
        catalog = constitution_catalog(ctx.mode, const_universal, const_repo,
                                       activated=activated_assets)
        if catalog:
            add_joined("Constitution catalog — enabled, frontmatter only",
                       f"harness/constitution/{ctx.mode}/ + repo asset pool", catalog)
        # Operating context: appended with NO added separator — its own text opens with "\n\n".
        add("Operating context (where) + orientation", "agent_service._context_preamble()",
            self._context_preamble(ctx, item_bound=item_bound), sep="")
        if ctx.persona_append:
            add("Project persona append", "Context.persona_append (per-project)",
                ctx.persona_append, sep="\n\n")
        # Core stays session-agnostic: it just appends what the daemon hands in.
        if system_append:
            add("Session-kind block — focus/guard/phase",
                "core/kernel_speech.py · work_item_preamble (Current focus/Guard/phase)", system_append,
                sep="\n\n")
        return frags

    @staticmethod
    def _join_fragments(frags: list[dict]) -> str:
        """Reconstruct the append string from its fragments — the byte-for-byte inverse of the split."""
        return "".join(f["sep"] + f["text"] for f in frags)

    def _assemble_append(self, ctx: Context, *, op_home, const_universal, const_repo,
                         activated_assets, system_append: str | None = None,
                         item_bound: bool = False) -> str:
        """Assemble the layer-2 system append (system_prompt.append) by joining `_fragment_parts`.
        THE single assembler — `_build_options` and the input-preview endpoint both call it, so what
        a preview shows is byte-for-byte what a real turn sends."""
        return self._join_fragments(self._fragment_parts(
            ctx, op_home=op_home, const_universal=const_universal, const_repo=const_repo,
            activated_assets=activated_assets, system_append=system_append, item_bound=item_bound))

    def assemble_system_append(self, ctx: Context, *, system_append: str | None = None,
                               item_bound: bool = False) -> str:
        """Public seam for the prompt inspector: resolve scope + assemble the exact system append a
        turn with this (ctx, session_append) would send. No side effects. `item_bound` must match
        what the real turn passes, or a preview drifts from the run it claims to show."""
        op_home, const_universal, const_repo, activated_assets = self._resolve_scope(ctx)
        return self._assemble_append(
            ctx, op_home=op_home, const_universal=const_universal, const_repo=const_repo,
            activated_assets=activated_assets, system_append=system_append, item_bound=item_bound)

    def assemble_system_fragments(self, ctx: Context, *, system_append: str | None = None,
                                  item_bound: bool = False) -> list[dict]:
        """Public seam for the prompt inspector's per-fragment view: the SAME append as
        `assemble_system_append`, but as ordered provenance fragments [{name, location, text}] (the
        internal `sep` is dropped — it's for reconstruction, not display). Same builder → the
        fragments always sum to the captured system prompt."""
        op_home, const_universal, const_repo, activated_assets = self._resolve_scope(ctx)
        frags = self._fragment_parts(
            ctx, op_home=op_home, const_universal=const_universal, const_repo=const_repo,
            activated_assets=activated_assets, system_append=system_append, item_bound=item_bound)
        return [{"name": f["name"], "location": f["location"], "text": f["text"]} for f in frags]

    def _build_options(
        self, ctx: Context, *, resume, model, approve: ApproveFn, extra_mcp_servers,
        enforce_silent: bool = False, effort: str | None = None, scope_reads: bool = False,
        system_append: str | None = None, gate_general_mutations: bool = False,
        general_write_root: Path | None = None, write_boundary: list[Path] | None = None,
        shell_roots: list[Path] | None = None,
        hooks: dict | None = None, block_categories: set[str] | None = None,
        deny_write_tools: str | None = None,
        protected_paths: list[Path] | None = None,
        protected_nudge: str | None = None,
        sandbox_writes: list[Path] | None = None,
        item_bound: bool = False,
    ) -> ClaudeAgentOptions:
        # Assembled through the SAME helper the input-preview endpoint calls, so a preview is
        # byte-for-byte what this turn sends.
        op_home, const_universal, const_repo, activated_assets = self._resolve_scope(ctx)
        append = self._assemble_append(
            ctx, op_home=op_home, const_universal=const_universal, const_repo=const_repo,
            activated_assets=activated_assets, system_append=system_append, item_bound=item_bound)
        # User-facing turns may not invoke `access: silent` skills; the owning sub-run still can.
        turn_plugins = [Path(p) for p in plugins_for(ctx.mode, op_home)]
        # name → the deny message for THAT block, the agent's only feedback.
        blocked: dict[str, str] = {}
        if enforce_silent:
            blocked.update({n: self._SILENT_SKILL_DENY for n in silent_skill_names(turn_plugins)})
        # The caller decides the block, Core resolves it: the daemon knows a repo's phase of life,
        # Core knows where the skills live.
        for cat in (block_categories or ()):
            msg = self._CATEGORY_DENY.get(cat, self._CATEGORY_DENY_DEFAULT.format(category=cat))
            blocked.update({n: msg for n in skills_in_category(turn_plugins, cat)})
        return ClaudeAgentOptions(
            cwd=str(ctx.cwd),                       # the Context (cwd / workspace)
            # Without this callback the SDK pipes no stderr, so a launch failure raises an error
            # telling you to read output that was never captured.
            stderr=_cli_stderr,
            resume=resume,                          # continuous session (surface-owned)
            # The ONE execution choke, so a tier alias never silently runs a lagging version.
            model=normalize_model(model),           # surface-resolved override (None = default)
            effort=effort,                          # surface-resolved reasoning effort (None = SDK default)
            system_prompt={"type": "preset", "preset": "claude_code", "append": append},
            # Layers the owner's own ~/.claude and project settings under SuperMe's carried harness.
            setting_sources=ctx.setting_sources,
            # Per-mode plugins: shared + (core|dev). The other mode's plugin isn't loaded,
            # so its skills are simply absent — folder-as-scope (see config.plugins_for).
            plugins=[{"type": "local", "path": p} for p in plugins_for(ctx.mode, op_home)],
            # SuperMe's own skills are mode-scoped by the plugins above; "all" keeps the owner's
            # native commands available alongside them.
            skills="all",

            # `superme` is pull_constitution, bound to THIS host's homes so it serves only in-scope
            # items.
            mcp_servers={
                "superme": make_base_mcp_server(ctx.mode, const_universal, const_repo,
                                                activated=activated_assets),
                **(extra_mcp_servers or {}),
            },
            permission_mode="default",
            # Surface-supplied SDK hooks (S8: the PreCompact checkpoint-first safety net on
            # work-item sessions). None on every other turn — no behavior change.
            hooks=hooks,
            # Keeps reads inside the host's scope. Background runs opt out: they are write-sandboxed
            # and read their own scratch, which the guard would deny.
            can_use_tool=build_can_use_tool(
                approve, blocked_skills=blocked,
                # `cwd` does two INDEPENDENT jobs: resolving relative reads, and pinning the shell inside
                # the write boundary. Gating it on scope_reads would disarm the second.
                cwd=ctx.cwd,
                read_roots=_read_roots(ctx) if scope_reads else None,
                gate_general_mutations=gate_general_mutations,
                general_write_root=general_write_root,
                write_boundary=write_boundary,
                shell_roots=shell_roots,   # nameable by the shell, never by the write tools
                deny_write_tools=deny_write_tools,   # vet read-only (build-vet-loop §4/§8·O4)
                protected_paths=protected_paths,      # review read-only on plan.md (§2.1)
                protected_nudge=protected_nudge,
            ),
            # SuperMe owns its own memory subsystem and must never touch the CLI's native auto-memory,
            # which is on by default and not gated by setting_sources. Targeted: skills and commands
            # stay intact.
            env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
            # Empty on an unsandboxed run. `core.sandbox` owns the policy.
            **sandbox_options(sandbox_writes),
        )

    async def run_turn(
        self,
        ctx: Context,
        prompt: str,
        *,
        resume: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        approve: ApproveFn,
        extra_mcp_servers: dict | None = None,
        enforce_silent: bool = False,
        scope_reads: bool = False,
        system_append: str | None = None,
        gate_general_mutations: bool = False,
        general_write_root: Path | None = None,
        write_boundary: list[Path] | None = None,
        shell_roots: list[Path] | None = None,
        hooks: dict | None = None,
        block_categories: set[str] | None = None,
        deny_write_tools: str | None = None,
        protected_paths: list[Path] | None = None,
        protected_nudge: str | None = None,
        sandbox_writes: list[Path] | None = None,
        item_bound: bool = False,
    ) -> AsyncIterator[TurnEvent]:
        """Run one turn against `ctx`, yielding TurnEvents.

        Emits TextDelta as assistant text arrives, Status before each tool call, and a
        final Result with the reply + run metadata (model, context fill, session id).
        Raises on a hard SDK failure (the surface decides whether to retry, e.g. after a
        stale resume).

        Uses ClaudeSDKClient (persistent connection) rather than the one-shot query():
        interactive permission callbacks (can_use_tool) need the control channel held
        open for the whole turn, which query() closes once its input stream ends.
        """
        options = self._build_options(
            ctx, resume=resume, model=model, approve=approve,
            extra_mcp_servers=extra_mcp_servers, enforce_silent=enforce_silent, effort=effort,
            scope_reads=scope_reads, system_append=system_append,
            gate_general_mutations=gate_general_mutations,
            general_write_root=general_write_root, write_boundary=write_boundary,
            shell_roots=shell_roots, hooks=hooks, block_categories=block_categories,
            deny_write_tools=deny_write_tools,
            protected_paths=protected_paths, protected_nudge=protected_nudge,
            sandbox_writes=sandbox_writes, item_bound=item_bound,
        )
        resolved_model = None
        # Fill is measured from a SINGLE call, not the turn aggregate: `Result.usage` sums every
        # round-trip, so a multi-tool turn balloons to N× real occupancy. The last AssistantMessage
        # is the fullest single prompt, so its usage ≈ true fill.
        last_step_usage: dict | None = None
        # A tool result arrives with a tool_use_id and no name, so correlate it back to its call.
        tool_names: dict[str, str] = {}
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, SystemMessage):
                    # The init system message reports the resolved model + the slash
                    # commands available this session (built-ins + custom + skills).
                    if getattr(message, "subtype", "") == "init":
                        data = getattr(message, "data", None) or {}
                        resolved_model = data.get("model")
                        yield Init(
                            slash_commands=data.get("slash_commands") or [],
                            model=resolved_model,
                        )
                elif isinstance(message, AssistantMessage):
                    # Non-None when the message came from INSIDE a sub-agent. A fan-out interleaves children
                    # into one stream, so the trail needs it to say who did what.
                    parent_tuid = getattr(message, "parent_tool_use_id", None)
                    # ONE TextDelta per assistant MESSAGE, joining its blocks. Per-block would make the live
                    # view and the reloaded transcript disagree about where a message ends.
                    said = "\n\n".join(b.text for b in message.content if hasattr(b, "text"))
                    if said.strip():
                        log.info("assistant: %s", said[:200])
                        yield TextDelta(said)
                    for block in message.content:
                        if hasattr(block, "text"):
                            continue
                        if hasattr(block, "name") and hasattr(block, "input"):
                            # A tool-use block — surface its own "what it's doing" indicator.
                            tuid = getattr(block, "id", None)
                            if tuid:
                                tool_names[tuid] = block.name
                            yield Status(block.name, block.input or {}, tool_id=tuid,
                                         parent_tool_id=parent_tuid)
                    # A live token snapshot for this step, so a surface can show a running
                    # counter while the turn is still in flight.
                    step_usage = getattr(message, "usage", None)
                    if step_usage:
                        last_step_usage = dict(step_usage)  # fullest single prompt so far
                        cu = _context_usage(step_usage, None, resolved_model,
                                            window_hint=self._window_by_model.get(resolved_model))
                        yield Usage(
                            total_tokens=_sum_tokens(step_usage),
                            input_tokens=step_usage.get("input_tokens", 0),
                            output_tokens=step_usage.get("output_tokens", 0),
                            ctx_pct=cu[0] if cu else None,
                            usage=dict(step_usage),
                            # Dedupe key for the live counter: messages of one API call share it.
                            message_id=getattr(message, "message_id", None),
                        )
                elif isinstance(message, UserMessage):
                    # Never rendered in the live chat, but persisted to the run trail so a full execution
                    # trace is available to Activity and to diagnosis.
                    content = getattr(message, "content", None)
                    if isinstance(content, list):
                        for block in content:
                            if not hasattr(block, "content"):  # not a tool_result block
                                continue
                            tuid = getattr(block, "tool_use_id", None)
                            name = tool_names.get(tuid or "", "tool")
                            yield ToolResult(
                                tool_name=name,
                                content=_result_text(block.content),
                                is_error=bool(getattr(block, "is_error", False)),
                                tool_id=tuid,
                                # Same attribution as the call (see AssistantMessage above): a result
                                # that came back inside a sub-agent belongs to that sub-agent's trail.
                                parent_tool_id=getattr(message, "parent_tool_use_id", None),
                            )
                elif isinstance(message, ResultMessage):
                    # Fill % from the last single call; window size from model_usage. The tokens below stay
                    # the turn aggregate — that IS the billing total.
                    usage = _context_usage(
                        last_step_usage or message.usage, message.model_usage, resolved_model,
                        window_hint=self._window_by_model.get(resolved_model),
                    )
                    pct, window = usage if usage else (None, None)
                    # Cache the real window so this model's next per-step frames match (not 200k).
                    if window and resolved_model:
                        self._window_by_model[resolved_model] = window
                    text = (
                        message.result
                        if message.subtype == "success"
                        else "Sorry — I ran into an error handling that request."
                    )
                    yield Result(
                        text=text or "I didn't produce a response.",
                        model=resolved_model,
                        ctx_pct=pct,
                        context_window=window,
                        session_id=getattr(message, "session_id", None),
                        tokens=_sum_tokens(message.usage) or None,
                        usage=dict(message.usage) if message.usage else None,
                    )
