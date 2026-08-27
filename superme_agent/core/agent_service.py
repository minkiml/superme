"""AgentService — run one conversational turn, surface-agnostically.

Composes ClaudeAgentOptions from the harness plus the per-run Context, drives the SDK loop, and
yields surface-neutral TurnEvents. It knows nothing about sessions, surfaces or model policy —
those arrive as plain parameters.
"""

import logging
import sys
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
    SELF_FILE, CHARTER_FILES, HARNESS_DIR, LOCAL_HARNESS_DIR, CONSTITUTION_DIR,
    fill_charter_paths, plugins_for,
)
from .vocab.models import normalize_model
from .operational import (constitution_catalog, list_repo_assets, silent_skill_names,
                          skills_in_category)
from .dev_knowledge import DevKnowledgeService

_DEV = DevKnowledgeService()  # stateless — reused to build the dev Orient digest
from ..harness.tools.base_tools import make_base_mcp_server
from .vocab.context import Context
from .vocab.events import Init, TextDelta, Status, ToolResult, Usage, Result, TurnEvent
from .permissions import ApproveFn, build_can_use_tool, deny_all
from .vocab.sandbox import sandbox_options

log = logging.getLogger("superme-agent")

# The two channels a turn's authored text can ride in. The system one is the CACHED prefix, so
# anything phase-varying placed there re-writes the whole cache at every phase boundary; the turn
# one is the user message, which is appended to a thread rather than rewritten in it.
SYSTEM_CHANNEL, TURN_CHANNEL = "system", "turn"
# The same boundary the birth block uses, so ONE rule peels every kernel-injected prefix back off
# a message before a person is shown what they typed (`sessions._strip_kernel_prefix`).
TURN_SEP = "\n\n---\n\n"

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
    """The "critical" token usage: fresh input + output + cache writes. EXCLUDES
    cache_read, which would dwarf real work."""
    if not usage:
        return 0
    return sum(usage.get(k, 0) for k in (
        "input_tokens", "output_tokens", "cache_creation_input_tokens",
    ))


def _window_from_model_usage(model_usage: dict | None, model: str | None) -> int | None:
    """The model's true contextWindow, if the SDK reported it. Only the
    ResultMessage carries it, so callers cache it."""
    if not model_usage:
        return None
    entry = model_usage.get(model) if model else None
    entry = entry or next((v for v in model_usage.values() if isinstance(v, dict)), None)
    if entry and entry.get("contextWindow"):
        return entry["contextWindow"]
    return None


def _context_usage(usage: dict | None, model_usage: dict | None, model: str | None,
                   window_hint: int | None = None):
    """Approximate context-window fill, or None. `usage` MUST be a single call's.

    No fallback window: a percentage against the wrong one is worse than none."""
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
    """The host's allowed-read roots: cwd, knowledge tree, universal harness, own local
    harness. The hub's cwd contains all four."""
    roots = [ctx.cwd, HARNESS_DIR]
    if ctx.internal_root:
        roots.append(ctx.internal_root)
    if ctx.id:
        roots.append(LOCAL_HARNESS_DIR / ctx.id)
    return roots


class AgentService:
    """Runs SDK turns for any surface, emitting TurnEvents."""

    def __init__(self, persona: str | None = None):
        # The portable SELF (WHO), loaded once from the in-code harness.
        self._persona = persona if persona is not None else SELF_FILE.read_text(encoding="utf-8")
        # Per-mode charters (WHAT MODE), loaded once. Selected by Context.mode per turn.
        self._charters = {
            mode: fill_charter_paths(path.read_text(encoding="utf-8"))
            for mode, path in CHARTER_FILES.items() if path.exists()
        }
        # Real contextWindow per model, so per-step frames divide by the same window the Result
        # does. Warms after the first turn.
        self._window_by_model: dict[str, int] = {}
        # Measured context FLOOR per (context, model) — see measure_context_floor.
        self._floor_by_key: dict[str, tuple[int, int]] = {}

    async def measure_context_floor(self, ctx: Context, model: str | None = None
                                    ) -> tuple[int, int] | None:
        """The session's incompressible FLOOR: what every turn re-sends and no summary can shed.

        System prompt, tools, skills and agent defs. Cached per (context, model)."""
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
        """Where this turn is operating: host, cwd, knowledge roots, and the orientation digest.

        A lookup table, not prose: every line rides every turn and the reader needs a path."""
        where = ("the **SuperMe hub** (the owner's home host: their cross-domain self, and "
                 "SuperMe's own codebase)" if ctx.layer == "global" else f"project host `{ctx.id}`")
        text = (
            f"\n\n## Operating context\n"
            f"Host: {where} · context `{ctx.label}` · cwd `{ctx.cwd}`.\n"
            # Skills name a tool bare. Looking one up under that name misses, and the fallback
            # keyword search costs several more round-trips.
            f"SuperMe's own tools are namespaced `mcp__<server>__<tool>`. Prompts and skills name "
            f"them bare, so add the namespace when you look one up rather than searching the bare "
            f"name."
        )
        # ABSOLUTE paths: the knowledge trees do not live under the cwd, so relative ones silently
        # miss.
        if ctx.internal_root:
            core_root = ctx.knowledge_root or (ctx.internal_root / "core")
            if ctx.mode == "dev":
                dev_root = ctx.internal_root / "dev"
                text += (
                    f"\nDev-knowledge root — NOT under the cwd — `{dev_root}`\n"
                    f"  · anchor docs `general/` · work-items `work-items/`\n"
                    f"Core knowledge `{core_root}` (read-only in dev)."
                )
                # A digest so a cold session is oriented without reading. An item-bound turn
                # already has a subject.
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
        "workspace": (
            "The work-item phase skills only run inside a work-item, and this session is not tied "
            "to one — the item's artifacts and this phase's pens are both absent here, so the "
            "procedure would run against nothing. Open the item on the board to work it, or file "
            "an inbox item for work that has none yet."
        ),
        "onboarding": (
            "Onboarding is already done for this project — it has established project's single source of truth (anchor docs), "
            "so the onboarding skills are closed and would overwrite that memory rather than build "
            "on it. To CHANGE an anchor doc, edit the specific section that's wrong (or raise a "
            "work-item for it); to record something new, add it. Don't re-derive the docs."
        ),
    }
    # A category block means something different to each frame, and one message cannot be true of
    # both. Keyed (frame, category); `_CATEGORY_DENY` answers for every frame without an entry.
    _CATEGORY_DENY_BY_FRAME = {
        ("deputy", "workspace"): (
            "You judge this gate; you do not work the item. The phase skills belong to the run "
            "that produces the artifacts, and this dispatch is read-only — running one would "
            "redo work you were sent to assess. Judge what the artifacts show, and say so in "
            "your verdict."
        ),
    }
    _CATEGORY_DENY_DEFAULT = "The `{category}` skills aren't available in this session."

    def _resolve_scope(self, ctx: Context):
        """The per-repo scope a turn resolves against, deterministic from the Context.
        Factored so the preview endpoint runs the same code."""
        op_home = LOCAL_HARNESS_DIR / ctx.id / ctx.mode if ctx.id else None
        const_universal = CONSTITUTION_DIR / ctx.mode
        const_repo = (op_home / "constitution") if op_home is not None else None
        activated_assets = list_repo_assets(const_repo)
        return op_home, const_universal, const_repo, activated_assets

    def _fragment_parts(self, ctx: Context, *, op_home, const_universal, const_repo,
                        activated_assets, preamble: str | None = None,
                        item_bound: bool = False, charter_key: str | None = None) -> list[dict]:
        """Everything the turn is given, as ORDERED fragments.

        The single source of truth. Each carries `channel` — the cached system prefix, or the user
        message — and `sep`, so joining a channel reproduces it byte-for-byte."""
        frags: list[dict] = []

        def add(name: str, location: str, text: str, *, sep: str,
                channel: str = SYSTEM_CHANNEL) -> None:
            frags.append({"name": name, "location": location, "text": text, "sep": sep,
                          "channel": channel})

        # The leading joined group: "\n\n" between members, nothing before the first.
        def add_joined(name: str, location: str, text: str) -> None:
            add(name, location, text, sep="" if not frags else "\n\n")

        add_joined("Persona — SELF (who)", "harness/SELF.md", self._persona)
        key = charter_key or ctx.mode
        charter = self._charters.get(key) or self._charters.get("core", "")
        if charter:
            add_joined(f"Charter — {key} mode (what)", f"harness/{key}-charter.md", charter)
        # Per-repo overlay, appended after the universal charter. Additive; most repos have none.
        if op_home is not None:
            local_charter = op_home / "charter.local.md"
            if local_charter.is_file():
                add_joined("Local charter — repo overlay",
                           f"local-harness/{ctx.id}/{ctx.mode}/charter.local.md",
                           local_charter.read_text(encoding="utf-8"))
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
        # The one fragment that varies by PHASE, so it rides the turn: in the append it would
        # re-write the whole cached prefix at every phase boundary. `compose_prompt` places it.
        if preamble:
            add("Session-kind block — focus/guard/phase",
                "core/kernel_speech.py · work_item_preamble (Current focus/Guard/phase)", preamble,
                sep="", channel=TURN_CHANNEL)
        return frags

    @staticmethod
    def _join_fragments(frags: list[dict]) -> str:
        """Reconstruct the system append — the byte-for-byte inverse of the split. Turn-channel
        fragments are not part of it; `compose_prompt` places those."""
        return "".join(f["sep"] + f["text"] for f in frags
                       if f.get("channel", SYSTEM_CHANNEL) == SYSTEM_CHANNEL)

    @staticmethod
    def compose_prompt(preamble: str | None, prompt: str) -> str:
        """The message a turn actually sends.

        The session-kind block rides HERE rather than in the system append, because it is the only
        part that varies by phase and the append is the CACHED prefix: ~650 tokens of pointer
        sitting there invalidated ~74,000 tokens of it, measured. Placed after the messages it
        costs 1,433. Every turn restates it, which is also what carries it through a compaction —
        a summary can drop a message, but it cannot drop the one being sent."""
        return f"{preamble}{TURN_SEP}{prompt}" if preamble else prompt

    def _assemble_append(self, ctx: Context, *, op_home, const_universal, const_repo,
                         activated_assets, item_bound: bool = False,
                         charter_key: str | None = None) -> str:
        """Join `_fragment_parts` into the layer-2 system append. THE single assembler,
        so a preview is byte-for-byte the real turn."""
        return self._join_fragments(self._fragment_parts(
            ctx, op_home=op_home, const_universal=const_universal, const_repo=const_repo,
            activated_assets=activated_assets, item_bound=item_bound, charter_key=charter_key))

    def assemble_system_append(self, ctx: Context, *, item_bound: bool = False,
                               charter_key: str | None = None) -> str:
        """Public seam for the prompt inspector: the exact append this ctx would send. It takes no
        preamble because it no longer carries one — that block rides the turn (`compose_prompt`),
        which is what keeps this string identical across a work item's phases. `item_bound` must
        match the real turn."""
        op_home, const_universal, const_repo, activated_assets = self._resolve_scope(ctx)
        return self._assemble_append(
            ctx, op_home=op_home, const_universal=const_universal, const_repo=const_repo,
            activated_assets=activated_assets, item_bound=item_bound, charter_key=charter_key)

    def assemble_system_fragments(self, ctx: Context, *, preamble: str | None = None,
                                  item_bound: bool = False,
                                  charter_key: str | None = None) -> list[dict]:
        """Everything the turn is given, as ordered fragments
        [{name, location, text, channel}]. Same builder as the append and the composed prompt, so
        all three always agree."""
        op_home, const_universal, const_repo, activated_assets = self._resolve_scope(ctx)
        frags = self._fragment_parts(
            ctx, op_home=op_home, const_universal=const_universal, const_repo=const_repo,
            activated_assets=activated_assets, preamble=preamble, item_bound=item_bound,
            charter_key=charter_key)
        return [{"name": f["name"], "location": f["location"], "text": f["text"],
                 "channel": f["channel"]} for f in frags]

    def _build_options(
        self, ctx: Context, *, resume, model, approve: ApproveFn, extra_mcp_servers,
        enforce_silent: bool = True, effort: str | None = None, scope_reads: bool = False,
        gate_general_mutations: bool = False,
        general_write_root: Path | None = None, write_boundary: list[Path] | None = None,
        shell_roots: list[Path] | None = None,
        hooks: dict | None = None, block_categories: set[str] | None = None,
        deny_write_tools: str | None = None,
        protected_paths: list[Path] | None = None,
        protected_nudge: str | None = None,
        sandbox_writes: list[Path] | None = None,
        item_bound: bool = False,
        charter_key: str | None = None,
    ) -> ClaudeAgentOptions:
        op_home, const_universal, const_repo, activated_assets = self._resolve_scope(ctx)
        append = self._assemble_append(
            ctx, op_home=op_home, const_universal=const_universal, const_repo=const_repo,
            activated_assets=activated_assets, item_bound=item_bound, charter_key=charter_key)
        turn_plugins = [Path(p) for p in plugins_for(ctx.mode, op_home)]
        # skill name → the deny message for that block, which is the agent's only feedback.
        blocked: dict[str, str] = {}
        if enforce_silent:
            blocked.update({n: self._SILENT_SKILL_DENY for n in silent_skill_names(turn_plugins)})
        frame = charter_key or ctx.mode
        for cat in (block_categories or ()):
            msg = (self._CATEGORY_DENY_BY_FRAME.get((frame, cat))
                   or self._CATEGORY_DENY.get(cat)
                   or self._CATEGORY_DENY_DEFAULT.format(category=cat))
            blocked.update({n: msg for n in skills_in_category(turn_plugins, cat)})
        return ClaudeAgentOptions(
            cwd=str(ctx.cwd),
            # Without this the SDK pipes no stderr, so a launch failure points at output nobody kept.
            stderr=_cli_stderr,
            resume=resume,
            model=normalize_model(model),
            effort=effort,
            system_prompt={"type": "preset", "preset": "claude_code", "append": append},
            setting_sources=ctx.setting_sources,
            plugins=[{"type": "local", "path": p} for p in plugins_for(ctx.mode, op_home)],
            # The plugins above already mode-scope SuperMe's skills; "all" keeps the owner's own.
            skills="all",
            mcp_servers={
                "superme": make_base_mcp_server(ctx.mode, const_universal, const_repo,
                                                activated=activated_assets),
                **(extra_mcp_servers or {}),
            },
            permission_mode="default",
            hooks=hooks,
            can_use_tool=build_can_use_tool(
                approve, blocked_skills=blocked,
                # `cwd` does two INDEPENDENT jobs: resolving relative reads, and pinning the shell
                # inside the write boundary.
                cwd=ctx.cwd,
                read_roots=_read_roots(ctx) if scope_reads else None,
                gate_general_mutations=gate_general_mutations,
                general_write_root=general_write_root,
                write_boundary=write_boundary,
                shell_roots=shell_roots,
                deny_write_tools=deny_write_tools,
                protected_paths=protected_paths,
                protected_nudge=protected_nudge,
            ),
            env={
                # On by default in the CLI, and not gated by setting_sources.
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                # The interpreter SuperMe runs under, so a shelled-out script uses it, not PATH's.
                "SUPERME_PY": sys.executable,
            },
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
        enforce_silent: bool = True,
        scope_reads: bool = False,
        preamble: str | None = None,
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
        charter_key: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Run one turn against `ctx`, yielding TurnEvents. Raises on a hard SDK failure.

        ClaudeSDKClient rather than query(), because `can_use_tool` needs the control channel held open."""
        options = self._build_options(
            ctx, resume=resume, model=model, approve=approve,
            extra_mcp_servers=extra_mcp_servers, enforce_silent=enforce_silent, effort=effort,
            scope_reads=scope_reads, gate_general_mutations=gate_general_mutations,
            general_write_root=general_write_root, write_boundary=write_boundary,
            shell_roots=shell_roots, hooks=hooks, block_categories=block_categories,
            deny_write_tools=deny_write_tools,
            protected_paths=protected_paths, protected_nudge=protected_nudge,
            sandbox_writes=sandbox_writes, item_bound=item_bound, charter_key=charter_key,
        )
        resolved_model = None
        # Fill comes from a SINGLE call, not the turn aggregate: `Result.usage` sums round-trips
        # and balloons N×.
        last_step_usage: dict | None = None
        # A tool result arrives with a tool_use_id and no name, so correlate it back to its call.
        tool_names: dict[str, str] = {}
        async with ClaudeSDKClient(options=options) as client:
            await client.query(self.compose_prompt(preamble, prompt))
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
                    # Non-None when the message came from INSIDE a sub-agent — the trail needs it
                    # to say who did what.
                    parent_tuid = getattr(message, "parent_tool_use_id", None)
                    # ONE TextDelta per assistant MESSAGE. Per-block would make the live view and
                    # the reloaded transcript disagree.
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
                    # A live token snapshot, so a surface can show a running counter mid-turn.
                    step_usage = getattr(message, "usage", None)
                    if step_usage:
                        last_step_usage = dict(step_usage)
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
                    # Never rendered in chat, but persisted to the run trail for Activity and
                    # diagnosis.
                    content = getattr(message, "content", None)
                    if isinstance(content, list):
                        for block in content:
                            if not hasattr(block, "content"):
                                continue
                            tuid = getattr(block, "tool_use_id", None)
                            name = tool_names.get(tuid or "", "tool")
                            yield ToolResult(
                                tool_name=name,
                                content=_result_text(block.content),
                                is_error=bool(getattr(block, "is_error", False)),
                                tool_id=tuid,
                                # Same attribution as the call: a result from inside a sub-agent
                                # belongs to that sub-agent's trail.
                                parent_tool_id=getattr(message, "parent_tool_use_id", None),
                            )
                elif isinstance(message, ResultMessage):
                    # Fill % from the last single call; window size from model_usage. Tokens below
                    # stay the billing total.
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
