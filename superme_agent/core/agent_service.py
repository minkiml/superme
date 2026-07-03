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
)

from ..runtime.config import (
    SELF_FILE, CHARTER_FILES, HARNESS_DIR, LOCAL_HARNESS_DIR, CONSTITUTION_DIR, plugins_for,
)
from .models import normalize_model
from .operational import constitution_catalog, silent_skill_names
from ..harness.tools.base_tools import make_base_mcp_server
from .context import Context
from .events import Init, TextDelta, Status, Usage, Result, TurnEvent
from .permissions import ApproveFn, build_can_use_tool

log = logging.getLogger("superme-agent")


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
            where = ("the owner's top-level **global SuperMe** — their cross-domain "
                     "identity and knowledge, not any single project")
        else:
            where = f"a **local / project sub-SuperMe** (`{ctx.id}`)"
        text = (
            f"\n\n## Operating context\n"
            f"You are operating in {where}. "
            f"Context: `{ctx.label}` · working directory: `{ctx.cwd}`."
        )
        # In dev mode, anchor the agent to its dev-knowledge tree with ABSOLUTE paths. It does
        # NOT live under the cwd (it's under `superme-knowledge/<id>-knowledge/dev/`), so relative
        # paths silently miss. (The old `memory/` fact store is retired — learned operational content
        # now lives as constitution/skill/agent in the harness; see WI-8.)
        if ctx.mode == "dev" and ctx.internal_root:
            dev_root = ctx.internal_root / "dev"
            text += (
                f"\n\nYour **dev-knowledge root** is `{dev_root}` (it is NOT under the working "
                f"directory above); work-items live at `{dev_root}/work-items/`. Use this absolute "
                f"path when you read or write dev-knowledge."
            )
        return text

    def _build_options(
        self, ctx: Context, *, resume, model, approve: ApproveFn, extra_mcp_servers,
        enforce_silent: bool = False, effort: str | None = None, scope_reads: bool = False,
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
        catalog = constitution_catalog(ctx.mode, const_universal, const_repo)
        if catalog:
            parts.append(catalog)
        append = "\n\n".join(parts) + self._context_preamble(ctx)
        if ctx.persona_append:
            append += f"\n\n{ctx.persona_append}"
        # User-facing turns may not invoke `access: silent` skills (forge-* — internal pipeline
        # machinery); the owning sub-run leaves enforce_silent False so it still can. Computed from
        # the same plugin set the turn loads.
        blocked = (silent_skill_names([Path(p) for p in plugins_for(ctx.mode, op_home)])
                   if enforce_silent else None)
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
                "superme": make_base_mcp_server(ctx.mode, const_universal, const_repo),
                **(extra_mcp_servers or {}),
            },
            permission_mode="default",
            # L2 read-guard on user-facing turns (chat / work-item): keep Read/Grep/Glob inside the
            # host's scope. Headless runs pass scope_reads=False — they are hermetic + write-sandboxed
            # and read their own /tmp scratch, which the guard would otherwise deny.
            can_use_tool=build_can_use_tool(
                approve, blocked_skills=blocked,
                cwd=ctx.cwd if scope_reads else None,
                read_roots=_read_roots(ctx) if scope_reads else None,
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
            scope_reads=scope_reads,
        )
        resolved_model = None
        # Context-window fill is measured from a SINGLE API call, not the turn aggregate.
        # ResultMessage.usage SUMS input/cache across every internal round-trip in the turn,
        # so a multi-tool turn re-reads the same context N times and the sum balloons to N×
        # the real occupancy (a simple 2-call turn then reads *lower* than a 3-call one — the
        # "% dropped after a simple query" bug). The last AssistantMessage is the fullest
        # single prompt (history + all tool exchanges), so its usage ≈ true window fill.
        last_step_usage: dict | None = None
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
                    for block in message.content:
                        if hasattr(block, "text"):
                            log.info("assistant: %s", block.text[:200])
                            yield TextDelta(block.text)
                        elif hasattr(block, "name") and hasattr(block, "input"):
                            # A tool-use block — surface its own "what it's doing" indicator.
                            yield Status(block.name, block.input or {})
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
                            context_pct=cu[0] if cu else None,
                            usage=dict(step_usage),
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
                        context_pct=pct,
                        context_window=window,
                        session_id=getattr(message, "session_id", None),
                        tokens=_sum_tokens(message.usage) or None,
                        usage=dict(message.usage) if message.usage else None,
                    )
