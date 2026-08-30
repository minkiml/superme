"""What a run leaves behind: its prompt, its reply, and one row per tool call."""

import re
import json as _json

from ...app_state import agent as _agent, dev as _dev, spine as _spine
from .. import item_stream
from ....core import Status, TextDelta, ToolResult
from ....core.autopilot import PROMPT_EXTRACTION_FEATURE
from ....core import kernel_speech
from .lifecycle import log

# Tool-use Status events map to (kind, head, detail). The UI shows "head - detail", like the CLI's
# call lines.
def _short_path(p, keep: int = 4) -> str:
    """The last `keep` segments of a path, `…/`-prefixed when elided. The UI truncates the tail, so
    keep the meaningful end."""
    parts = [x for x in re.split(r"[\\/]", str(p or "")) if x]
    if not parts:
        return ""
    return "/".join(parts) if len(parts) <= keep else "…/" + "/".join(parts[-keep:])


def _int_or_none(value) -> int | None:
    """An int, or None for anything else a tool's arguments hold — agent-written input is untrusted
    shape."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _artifact_payload(tool_name: str, ti: dict) -> str | None:
    """The row's full text, for rows where the text is audited."""
    if tool_name in ("Task", "Agent"):
        return str(ti.get("prompt") or "") or None
    return None


def _artifact_desc(tool_name: str, ti: dict) -> tuple[str, str, str]:
    """Map a tool-use to (kind, head, detail). kind ∈ tool|subagent|skill|mcp."""
    ti = ti or {}
    base = _short_path
    # A spawn arrives as `Task` or `Agent` depending on SDK build; render which agent ran, not a
    # generic "Agent".
    if tool_name in ("Task", "Agent"):
        # Name the worker when the spawn said which; the bare form is the honest fallback.
        who = (ti.get("subagent_type") or ti.get("subagentType") or ti.get("agent_type")
               or ti.get("agentType") or ti.get("name") or ti.get("description") or "")
        # The model, when the spawn overrode it — otherwise the trail cannot show whether a per-
        # phase instruction was followed.
        model = ti.get("model") or ti.get("modelName")
        # A subagent inherits nothing, so whatever the brief omits is worked without. Size is all
        # a row can hold.
        brief = str(ti.get("prompt") or "")
        parts = [str(who).strip(), str(model).strip() if model else "",
                 f"brief {len(brief)}" if brief else ""]
        inner = " · ".join(x for x in parts if x)
        return "subagent", "Agent", (f"Subagent ({inner[:48]})" if inner else "Subagent")
    if tool_name == "Skill":
        # The skill identity may arrive under any of several keys depending on the SDK build.
        name = (ti.get("command") or ti.get("name") or ti.get("skill")
                or ti.get("skill_name") or ti.get("skillName") or "")
        return "skill", "skill", str(name).lstrip("/")
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        return "mcp", "mcp", parts[-1] if parts else tool_name
    if tool_name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = base(ti.get("file_path") or ti.get("notebook_path"))
        # A read's span, when it named one: the path alone cannot distinguish 20 lines from a
        # whole 46 KB file.
        if tool_name == "Read":
            # `offset`/`limit` are whatever the agent typed, not a validated shape; a label must
            # never crash the run it describes.
            off, lim = _int_or_none(ti.get("offset")), _int_or_none(ti.get("limit"))
            if off is not None or lim is not None:
                start = off or 0
                span = f"{start}-{start + lim}" if lim is not None else f"{start}+"
                return "tool", tool_name, f"{path} [{span}]"
            return "tool", tool_name, f"{path} [whole]"
        return "tool", tool_name, path
    if tool_name == "Bash":
        # 1200 characters, so two commands differing only in a path argument still render
        # differently. The UI truncates the row.
        return "tool", "Bash", (str(ti.get("command") or "") or str(ti.get("description", "")))[:1200]
    if tool_name in ("Grep", "Glob"):
        return "tool", tool_name, str(ti.get("pattern", ""))
    if tool_name in ("WebFetch", "WebSearch"):
        return "tool", tool_name, str(ti.get("url") or ti.get("query") or "")[:60]
    if tool_name == "ToolSearch":
        # what it searched for — the deferred-tool query (e.g. "select:Read,Edit" or keywords).
        return "tool", "ToolSearch", str(ti.get("query", ""))[:60]
    return "tool", tool_name, ""


# --- per-run trail caps ---
_PROMPT_CAP = 4000   # a run's trigger prompt, trimmed
_REPLY_CAP = 8000    # one assistant text block, trimmed
_RESULT_CAP = 1200   # a tool's output, trimmed — enough to show what it returned without bloating the trail


def _result_row(ev: ToolResult) -> tuple[str, str]:
    """Map a ToolResult to name and description."""
    _, head, detail = _artifact_desc(ev.tool_name, {})
    name = detail or head
    body = (ev.content or "").strip()
    if ev.is_error:
        body = "[error] " + body
    return name, body[:_RESULT_CAP]


# `run_event` is the one trail; `run_artifact` is frozen history with no writer.


def capture_prompt(repo_id: str, prompt: str, *, run_id: int | None = None,
                   item_id: str | None = None) -> None:
    """Record the prompt that opened a run as the first entry of its trail."""
    _spine.log_run_event(repo_id=repo_id, kind="prompt", name="prompt",
                         description=(prompt or "").strip()[:_PROMPT_CAP], run_id=run_id, item_id=item_id)


def turn_surface(*, model: str | None = None, effort: str | None = None,
                 mcp: list[str] | None = None, write_boundary: list | None = None,
                 sandbox_writes: list | None = None, read_only: bool = False,
                 approve: str = "denied", resumes: bool = False) -> dict:
    """What the turn is allowed to do: a run's input that is not prose."""
    return {"model": model or "", "effort": effort or "",
            "mcp": sorted(mcp or []),
            "write_boundary": [str(p) for p in (write_boundary or [])],
            "sandbox_writes": [str(p) for p in (sandbox_writes or [])],
            "read_only": bool(read_only), "approve": approve, "resumes": bool(resumes)}


def surface_from_turn(turn_kwargs: dict, *, mcp: list[str] | None = None) -> dict:
    """`turn_surface` read off the kwargs a turn is actually sent with.

    A restatement could drift from the send."""
    return turn_surface(
        model=turn_kwargs.get("model"),
        effort=turn_kwargs.get("effort"),
        mcp=mcp if mcp is not None else sorted(turn_kwargs.get("extra_mcp_servers") or {}),
        write_boundary=turn_kwargs.get("write_boundary"),
        sandbox_writes=turn_kwargs.get("sandbox_writes"),
        read_only=bool(turn_kwargs.get("deny_write_tools")),
        resumes=bool(turn_kwargs.get("resume")),
    )


def _authored_extras(ctx, item: dict, phase: str | None, mcp: list[str]) -> dict:
    """The prompt text SuperMe authors outside the system append.

    The phase skill and the tool schemas, which no message carries."""
    from ....harness.tools.base_tools import BASE_TOOLS
    from ....harness.tools.dev_tools import dev_tool_specs
    from ....harness.tools.registry import describe_specs
    from ....harness.tools.run_tools import SUBMIT_GATE_VERDICT_TOOL, REPORT_COMPLETION_TOOL
    from ....paths import DEV_PLUGIN_DIR

    skills: list[dict] = []
    contract = kernel_speech.phase_contract(item.get("kind"), str(phase or ""))
    name = contract.get("skill")
    if name:
        path = DEV_PLUGIN_DIR / "skills" / name / "SKILL.md"
        if path.is_file():
            skills.append({"name": f"superme-dev:{name} — SKILL.md",
                           "location": f"plugins/superme-dev/skills/{name}/SKILL.md",
                           "text": path.read_text(encoding="utf-8")})
    # `superme` is always mounted; the rest come from the run's own `extra_mcp_servers`. An
    # unrecognised one drops the fragment.
    try:
        dev_specs = dev_tool_specs(name or str(phase or ""))
    except KeyError:
        dev_specs = []
    by_server = {"superme": BASE_TOOLS, "dev": dev_specs,
                 "run": [REPORT_COMPLETION_TOOL], "deputy": [SUBMIT_GATE_VERDICT_TOOL]}
    tools: list[dict] = []
    deferred: list[dict] = []
    for server in sorted(set(mcp) | {"superme"}):
        specs = by_server.get(server)
        if not specs:
            continue
        tools.append({"name": f"mcp__{server}__* — {len(specs)} names",
                      "location": f"harness/tools · the `{server}` MCP server",
                      "text": "\n".join(f"mcp__{server}__{s.name}" for s in specs)})
        deferred.append({"name": f"mcp__{server}__* — {len(specs)} schemas",
                         "location": f"harness/tools · the `{server}` MCP server",
                         "text": describe_specs(specs)})
    return {"skills": skills, "tools": tools, "deferred_tools": deferred}


def capture_run_input(context_id: str, item_id: str, *, ctx, preamble: str | None,
                      prompt: str, background: bool, phase: str | None,
                      surface: dict | None = None) -> None:
    """Persist the actual input a run is about to send, keyed to the live run."""
    try:
        info = _spine.live_run(context_id, item_id)
        rid = (info or {}).get("id")
        if rid is None:
            return
        _spine.set_run_feature(rid, PROMPT_EXTRACTION_FEATURE)
        # `item_bound=True` changes what the operating-context fragment renders; without it the
        # preview shows a prompt no run sent.
        system_prompt = _agent.assemble_system_append(ctx, item_bound=True)
        # Both channels come from one builder, so the fragments sum to what was sent.
        try:
            frags = _agent.assemble_system_fragments(ctx, preamble=preamble, item_bound=True)
            fragments_json = _json.dumps(frags, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            fragments_json = None
        # Same guard: losing the skill body must never cost us the prose.
        try:
            item = (_dev.read_work_item(ctx.internal_root / "dev", item_id) or {}) \
                if getattr(ctx, "internal_root", None) else {}
            extras_json = _json.dumps(
                _authored_extras(ctx, item, phase, list((surface or {}).get("mcp") or [])),
                ensure_ascii=False)
        except Exception:  # noqa: BLE001
            extras_json = None
        _spine.record_run_input(rid, repo_id=context_id, item_id=item_id, phase=phase,
                                feature=PROMPT_EXTRACTION_FEATURE, background=background,
                                system_prompt=system_prompt,
                                # The preamble rides the turn, so the body recorded is the
                                # composed message.
                                prompt_body=_agent.compose_prompt(preamble, prompt),
                                system_fragments=fragments_json, authored_extras=extras_json,
                                turn_surface=(_json.dumps(surface, ensure_ascii=False)
                                              if surface else None))
    except Exception:
        log.exception("capture_run_input failed for %s", item_id)


def capture_event(repo_id: str, ev, *, run_id: int | None = None, item_id: str | None = None,
                  publish_live: bool = True) -> None:
    """Record one turn event onto a run's trail and publish it to any watching panel."""
    try:
        _capture_event(repo_id, ev, run_id=run_id, item_id=item_id, publish_live=publish_live)
    except Exception:
        log.exception("trail capture failed for %s — the run continues, this event unrecorded",
                      item_id or repo_id)


def _capture_event(repo_id: str, ev, *, run_id: int | None = None, item_id: str | None = None,
                   publish_live: bool = True) -> None:
    # Resolve once here — a live frame with `run_id=None` cannot be matched to the history the
    # browser already holds.
    if run_id is None and item_id is not None:
        run_id = _spine.running_run_id(repo_id, item_id)
    if isinstance(ev, Status):
        kind, head, detail = _artifact_desc(ev.tool_name, ev.tool_input or {})
        _spine.log_run_event(repo_id=repo_id, kind=kind, name=head, description=detail,
                             run_id=run_id, item_id=item_id, tool_id=ev.tool_id,
                             parent_tool_id=ev.parent_tool_id,
                             payload=_artifact_payload(ev.tool_name, ev.tool_input or {}))
        if publish_live:
            _publish_timeline(item_id, run_id, kind, head, detail, ev.tool_id, ev.parent_tool_id)
    elif isinstance(ev, ToolResult):
        name, desc = _result_row(ev)
        # The tool_use id pairs result to call exactly; concurrent tools return out of order.
        _spine.log_run_event(repo_id=repo_id, kind="result", name=name, description=desc,
                             run_id=run_id, item_id=item_id, tool_id=ev.tool_id,
                             parent_tool_id=ev.parent_tool_id)
        if publish_live:
            _publish_timeline(item_id, run_id, "result", name, desc, ev.tool_id, ev.parent_tool_id)
    elif isinstance(ev, TextDelta):
        txt = (ev.text or "").strip()
        if txt:
            _spine.log_run_event(repo_id=repo_id, kind="reply", name="reply", description=txt[:_REPLY_CAP],
                                 run_id=run_id, item_id=item_id)
            if publish_live:
                _publish_timeline(item_id, run_id, "reply", "reply", txt[:_REPLY_CAP], None, None)


def _publish_timeline(item_id: str | None, run_id: int | None, kind: str, name: str,
                      description: str, tool_id: str | None,
                      parent_tool_id: str | None) -> None:
    """Fan a captured event out to any panel watching this item. No-op when nobody is watching.
    Never raises."""
    if not item_id or not item_stream.has_subscribers(item_id):
        return
    try:
        item_stream.publish(item_id, {
            "type": "timeline", "item_id": str(item_id), "run_id": run_id,
            "kind": kind, "name": name, "description": description, "tool_id": tool_id,
            "parent_tool_id": parent_tool_id,
        })
    except Exception:
        log.debug("item_stream publish failed for %s", item_id, exc_info=True)
