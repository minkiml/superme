"""AgentService — run one conversational turn, surface-agnostically.

This is the extracted brain. It composes ClaudeAgentOptions from the in-code harness
(persona + local plugin) plus the per-run Context (cwd, persona_append, workspace
harness via setting_sources), runs the SDK query loop, and yields surface-neutral
TurnEvents. It knows nothing about Slack, web, sessions storage, or model policy —
those are the surface's job and arrive as plain parameters (resume, model, approve,
extra_mcp_servers).
"""

import logging
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

from ..runtime.config import (
    SELF_FILE, CHARTER_FILES, HARNESS_DIR, LOCAL_HARNESS_DIR, CONSTITUTION_DIR, plugins_for,
)
from . import kernel_speech
from .models import normalize_model
from .operational import (constitution_catalog, list_repo_assets, silent_skill_names,
                          skills_in_category)
from .dev_knowledge import DevKnowledgeService

_DEV = DevKnowledgeService()  # stateless — reused to build the dev Orient digest
from ..harness.tools.base_tools import make_base_mcp_server
from .context import Context
from .events import Init, TextDelta, Status, ToolResult, Usage, Result, TurnEvent
from .permissions import ApproveFn, build_can_use_tool

log = logging.getLogger("superme-agent")


def _result_text(content) -> str:
    """Flatten a ToolResultBlock's content to plain text for the trail. The SDK gives either a
    string or a list of content blocks (text / image / …); we keep the text blocks and join them,
    dropping non-text (images) — the run trail is a text record. Trimming/capping is the caller's
    job (the trail applies its own cap)."""
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
    """The "critical" token usage for a turn/step: fresh input + output + cache writes.

    Deliberately EXCLUDES cache_read_input_tokens — cheap re-reads of already-cached context,
    which otherwise dwarf (often 10x+) the tokens that represent real work/cost. (Context-fill
    %, computed separately in _context_usage, still counts cache reads since they fill the window.)
    """
    if not usage:
        return 0
    return sum(usage.get(k, 0) for k in (
        "input_tokens", "output_tokens", "cache_creation_input_tokens",
    ))


def _window_from_model_usage(model_usage: dict | None, model: str | None) -> int | None:
    """Pull the model's true contextWindow out of the SDK's model_usage dict, if present.

    Only the ResultMessage carries model_usage, so per-step frames can't read it — the
    caller caches the returned value per model and passes it back as `window_hint` so
    streaming and the final result divide by the SAME window (else they diverge 5×, e.g.
    a 1M-window Sonnet session read as 200k mid-stream but 1M at turn end).
    """
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

    `usage` MUST be a SINGLE API call's usage (e.g. one AssistantMessage), not a turn
    aggregate: input + both cache buckets ≈ the prompt that was in the window, plus its
    output. A turn-aggregate double-counts cache reads across tool round-trips and inflates
    the fill 2–3×.

    The window (denominator) is the model's real contextWindow — a per-model, per-session
    property (Sonnet 5 negotiates 1M here, Opus 1M, others differ): from `model_usage` when
    present (ResultMessage), else the cached `window_hint` (so per-step frames match the
    result). We DELIBERATELY do NOT fall back to a fixed guess — a %'d against the wrong
    window is a false reading, worse than none — so when the real window is unknown we
    return None and the surface simply shows no fill.
    """
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
    """The host's allowed-read roots for the L2 guard (context-model-spec §3): its working root
    (cwd) + its whole working-knowledge tree + the universal harness + its own local harness.
    Everything else (other repos, the rest of SuperMe's system code) is out of scope. For the hub,
    cwd is the SuperMe repo root, which already contains the harness/local-harness/knowledge — so
    the hub reads broadly, a repo host narrowly, from the same rule."""
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
        # Real contextWindow per model, learned from ResultMessage.model_usage (the only
        # place the SDK reports it). Lets per-step Usage frames divide by the same window
        # the Result does, instead of the 200k default. Warms after the first turn/model.
        self._window_by_model: dict[str, int] = {}

    def _context_preamble(self, ctx: Context) -> str:
        """A short note telling the agent which context it's operating in."""
        if ctx.layer == "global":
            where = ("the **SuperMe hub** — the owner's home host: their cross-domain "
                     "self, and SuperMe's own codebase")
        else:
            where = f"a **project host** (`{ctx.id}`)"
        text = (
            f"\n\n## Operating context\n"
            f"You are operating in {where}. "
            f"Context: `{ctx.label}` · working directory: `{ctx.cwd}`."
        )
        # Anchor the host's knowledge trees with ABSOLUTE paths — they live under
        # `superme-knowledge/<id>-knowledge/`, NOT under the cwd, so relative paths silently miss.
        # The charter references these *relatively* ("this context's dev-knowledge", "core
        # knowledge"); these lines are what bind those references to THIS host's concrete trees.
        # (The old `memory/` fact store is retired — learned operational content now lives as
        # constitution/skill/agent in the harness; see WI-8.)
        if ctx.internal_root:
            core_root = ctx.knowledge_root or (ctx.internal_root / "core")
            if ctx.mode == "dev":
                dev_root = ctx.internal_root / "dev"
                text += (
                    f"\n\nYour **dev-knowledge root** is `{dev_root}` (NOT under the working "
                    f"directory above): the `general/` anchor docs are at `{dev_root}/general/` and "
                    f"work-items at `{dev_root}/work-items/`. Use these absolute paths to read or write "
                    f"dev-knowledge. This host's **core knowledge** is at `{core_root}` (read-only in "
                    f"dev — see the charter)."
                )
                # ORIENT (S3): a thin always-on digest of THIS project — what it is + active waves +
                # in-progress items — regenerated from the anchor docs each turn so a cold session is
                # oriented without reading. Guarded: a parse hiccup must never break a turn.
                try:
                    digest = _DEV.orient_digest(dev_root)
                except Exception:  # noqa: BLE001 — orientation is best-effort, never fatal
                    digest = None
                if digest:
                    text += f"\n\n## This project (orientation)\n{digest}"
            else:
                text += (
                    f"\n\nYour **core-knowledge home** is `{core_root}` (NOT under the working "
                    f"directory above). Use this absolute path to read or grow core knowledge."
                )
        return text

    # Deny messages for a blocked Skill call. Each is the agent's ONLY feedback about the block, so
    # each states what is true of the skills it covers — and what to do instead.
    _SILENT_SKILL_DENY = ("This is an internal SuperMe skill — it runs only inside the learning "
                          "pipeline, not from chat.")
    _CATEGORY_DENY = {
        "onboarding": (
            "Onboarding is already done for this project — it has established memory (anchor docs), "
            "so the onboarding skills are closed and would overwrite that memory rather than build "
            "on it. To CHANGE an anchor doc, edit the specific section that's wrong (or raise a "
            "work-item for it); to record something new, add it. Don't re-derive the docs."
        ),
    }
    _CATEGORY_DENY_DEFAULT = "The `{category}` skills aren't available in this session."

    def _build_options(
        self, ctx: Context, *, resume, model, approve: ApproveFn, extra_mcp_servers,
        enforce_silent: bool = False, effort: str | None = None, scope_reads: bool = False,
        system_append: str | None = None, gate_general_mutations: bool = False,
        general_write_root: Path | None = None, write_boundary: list[Path] | None = None,
        hooks: dict | None = None, block_categories: set[str] | None = None,
        deny_write_tools: str | None = None, background: bool = False,
    ) -> ClaudeAgentOptions:
        # Assemble layer-2 append: persona (WHO) + mode charter (WHAT MODE) + preamble
        # (WHERE) + persona_append (per-project extra). Mode falls back to core.
        charter = self._charters.get(ctx.mode) or self._charters.get("core", "")
        parts = [self._persona]
        if charter:
            parts.append(charter)
        # Per-repo operational overlay (renovation §4.11.1): the per-repo operational home is
        # `local-harness/<id>/<mode>` (under the code, NOT the knowledge tree). Its
        # `charter.local.md` is appended AFTER the universal mode charter when present, and its
        # plugin (if any) loads via plugins_for below. Additive — most repos have none.
        op_home = LOCAL_HARNESS_DIR / ctx.id / ctx.mode if ctx.id else None
        if op_home is not None:
            local_charter = op_home / "charter.local.md"
            if local_charter.is_file():
                parts.append(local_charter.read_text())
        # Constitution CATALOG (context-model-spec §1/§2): frontmatter-first. The always-on context
        # carries only the catalog (name + description) of ENABLED in-scope items — universal
        # (harness/constitution/<mode>) + this repo's (op_home/constitution); bodies are pulled on
        # demand via the `pull_constitution` tool (mounted below). Empty string when there are none.
        const_universal = CONSTITUTION_DIR / ctx.mode
        const_repo = (op_home / "constitution") if op_home is not None else None
        # Asset pool (opt-in constitutional knowledge): the shared `local-harness/asset/` pool; this
        # repo activates slugs via its `.assets` list. A new repo activates none — so its catalog
        # carries only the true universals + anything it has explicitly turned on.
        activated_assets = list_repo_assets(const_repo)
        catalog = constitution_catalog(ctx.mode, const_universal, const_repo, activated=activated_assets)
        if catalog:
            parts.append(catalog)
        append = "\n\n".join(parts) + self._context_preamble(ctx)
        if ctx.persona_append:
            append += f"\n\n{ctx.persona_append}"
        # Per-turn, session-aware append (work-item-session-recognition-prd): the Focus block (a
        # work-item session, centering the agent on its item) or the Guard block (a general session,
        # discussion-only). Assembled by the daemon, which knows the session's durable item stamp;
        # Core stays session-agnostic (it has no spine), so it just appends what it's handed.
        if system_append:
            append += f"\n\n{system_append}"
        # Background run (Thread 3 §3): a PER-TURN fact, not a session property — the same session
        # is later resumed for interactive chat, and this block simply doesn't appear on those
        # turns. One factual sentence (kernel-fired, kernel-processed) + the completion-report
        # fence the kernel parses; the per-phase behaviour lives in each skill's background section.
        if background:
            append += f"\n\n{kernel_speech.BACKGROUND_RUN_CONTRACT}"
        # User-facing turns may not invoke `access: silent` skills (forge-* — internal pipeline
        # machinery); the owning sub-run leaves enforce_silent False so it still can. Computed from
        # the same plugin set the turn loads.
        turn_plugins = [Path(p) for p in plugins_for(ctx.mode, op_home)]
        # name → the deny message for THAT block (the agent's only feedback, so it must be true of
        # the skill it lands on — see build_can_use_tool).
        blocked: dict[str, str] = {}
        if enforce_silent:
            blocked.update({n: self._SILENT_SKILL_DENY for n in silent_skill_names(turn_plugins)})
        # `block_categories` = a block the CALLER decides on but Core resolves, off the same plugin
        # set (the daemon knows a repo's phase of life; Core knows where the skills live). Today:
        # `onboarding`, once a project's memory is established.
        for cat in (block_categories or ()):
            msg = self._CATEGORY_DENY.get(cat, self._CATEGORY_DENY_DEFAULT.format(category=cat))
            blocked.update({n: msg for n in skills_in_category(turn_plugins, cat)})
        return ClaudeAgentOptions(
            cwd=str(ctx.cwd),                       # the Context (cwd / workspace)
            resume=resume,                          # continuous session (surface-owned)
            # Normalize the model to a CONCRETE id here — the ONE execution choke every turn passes
            # through — so a tier alias (`sonnet`) never silently runs a lagging concrete version.
            model=normalize_model(model),           # surface-resolved override (None = default)
            effort=effort,                          # surface-resolved reasoning effort (None = SDK default)
            system_prompt={"type": "preset", "preset": "claude_code", "append": append},
            # Which Claude Code setting layers to load (per-Context). Default is
            # ["user","project","local"] so SuperMe layers the owner's global ~/.claude
            # artifacts + the hosting dir's project .claude + its own carried harness
            # (the plugin below, cwd-independent). See Context.setting_sources.
            setting_sources=ctx.setting_sources,
            # Per-mode plugins: shared + (core|dev). The other mode's plugin isn't loaded,
            # so its skills are simply absent — folder-as-scope (see config.plugins_for).
            plugins=[{"type": "local", "path": p} for p in plugins_for(ctx.mode, op_home)],
            # SuperMe's OWN skills are mode-scoped by the per-mode plugins above (dev sees
            # superme-dev/shared, core sees superme-core/shared). "all" additionally keeps the
            # owner's NATIVE Claude commands/skills available (global + project + local) — the
            # slash palette = SuperMe's mode-scoped skills + the native environment.
            skills="all",

            # Base tools (every mode) + surface-specific (e.g. Slack readers, the dev server).
            # `superme` = pull_constitution, bound to THIS host's constitution homes so it only
            # ever serves in-scope items (context-model-spec §2).
            mcp_servers={
                "superme": make_base_mcp_server(ctx.mode, const_universal, const_repo,
                                                activated=activated_assets),
                **(extra_mcp_servers or {}),
            },
            permission_mode="default",
            # Surface-supplied SDK hooks (S8: the PreCompact checkpoint-first safety net on
            # work-item sessions). None on every other turn — no behavior change.
            hooks=hooks,
            # L2 read-guard on user-facing turns (chat / work-item): keep Read/Grep/Glob inside the
            # host's scope. Background runs pass scope_reads=False — they are hermetic + write-sandboxed
            # and read their own /tmp scratch, which the guard would otherwise deny.
            can_use_tool=build_can_use_tool(
                approve, blocked_skills=blocked,
                # `cwd` is the agent's working root, used for two INDEPENDENT things: resolving a
                # relative read target against `read_roots`, and pinning the shell inside
                # `write_boundary`. Only the former is a read-guard concern, so pass cwd
                # unconditionally — gating it on scope_reads would silently disarm the boundary's
                # shell allow on every background run.
                cwd=ctx.cwd,
                read_roots=_read_roots(ctx) if scope_reads else None,
                gate_general_mutations=gate_general_mutations,
                general_write_root=general_write_root,
                write_boundary=write_boundary,
                deny_write_tools=deny_write_tools,   # vet read-only (build-vet-loop §4/§8·O4)
            ),
            # SuperMe owns its OWN log+memory subsystem — it must never read from or write to
            # Claude Code's native auto-memory store (~/.claude/projects/<hash>/memory/). That
            # feature is ON by default and is NOT gated by setting_sources, so we disable it
            # explicitly here. This is targeted: it kills auto-memory only, leaving the owner's
            # native skills/commands (loaded via setting_sources) intact. Verified flag in CLI
            # 2.1.159; the toggle covers both read and write ("will not write or read new memories").
            env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
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
        hooks: dict | None = None,
        block_categories: set[str] | None = None,
        deny_write_tools: str | None = None,
        background: bool = False,
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
            hooks=hooks, block_categories=block_categories, deny_write_tools=deny_write_tools,
            background=background,
        )
        resolved_model = None
        # Context-window fill is measured from a SINGLE API call, not the turn aggregate.
        # ResultMessage.usage SUMS input/cache across every internal round-trip in the turn,
        # so a multi-tool turn re-reads the same context N times and the sum balloons to N×
        # the real occupancy (a simple 2-call turn then reads *lower* than a 3-call one — the
        # "% dropped after a simple query" bug). The last AssistantMessage is the fullest
        # single prompt (history + all tool exchanges), so its usage ≈ true window fill.
        last_step_usage: dict | None = None
        # Correlate each tool RESULT (which arrives as a UserMessage carrying a tool_use_id, no
        # name) back to the tool_use that spawned it, so the trail can label the result with the
        # tool that produced it. Populated when a tool-use block is seen, read when its result lands.
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
                    # ONE TextDelta per assistant MESSAGE, joining its text blocks — deliberately
                    # not one per block. A surface renders each TextDelta as a chat bubble, and the
                    # session transcript replays a message's blocks joined (sessions._blocks_text),
                    # so emitting per-block would make the live view and the reloaded view disagree
                    # about where one message ends and the next begins.
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
                            yield Status(block.name, block.input or {}, tool_id=tuid)
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
                            # Dedupe key for the live counter: many AssistantMessages of one API call
                            # share this id (see Usage.message_id). None on older SDK builds → callers
                            # fall back to summing.
                            message_id=getattr(message, "message_id", None),
                        )
                elif isinstance(message, UserMessage):
                    # Tool RESULTS come back as a UserMessage of tool_result blocks. The live chat
                    # UI never renders these (they're not streamed downstream — see event_to_frame),
                    # but the per-run trail persists them so a full execution trace (call + output)
                    # is available to the Activity view and the diagnosis agent (read-tool-output).
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
                            )
                elif isinstance(message, ResultMessage):
                    # Fill % from the last single call (true occupancy); window size from
                    # model_usage (only present on the ResultMessage). tokens/usage below stay
                    # the turn aggregate — that IS the correct cost/billing total.
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
