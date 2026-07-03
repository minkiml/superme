"""In-process tools giving the DEV agent scoped, on-demand access to dev-knowledge.

Following the benchmarked read-path rule (PRD §4.9): file-backed knowledge is read with the
agent's native Read/Grep; DB-backed, structured-query knowledge gets a typed tool. The event
LOG lives in SQLite and the key query is date-filtered ("what was done yesterday?"), so it's
exposed here as a tool — the agent calls it instead of us dumping the whole log into context.

Each tool is a `ToolSpec` (see `registry.py`): a lean one-line description (the WHEN/HOW lives in
the owning skill/agent — descriptions sit in every dev turn's context, so they stay one-liners),
a typed TypedDict schema whose `Annotated` fields carry the per-param docs, and a handler factory
bound to the per-context deps. `make_dev_mcp_server` renders the registry into an SDK MCP server.

Dev-only: wired into dev-mode turns; absent in core mode (folder-as-scope, same as skills).
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Required, TypedDict

from .registry import ToolSpec, build_mcp_server


# --------------------------------------------------------------------------- rendering helpers

def _day_range(day: str) -> tuple[str, str] | None:
    """Resolve a relative/calendar day to a [start, end) UTC range, using the OWNER'S LOCAL
    timezone (localhost single-owner → the machine's tz). Events are stored UTC but "today" is
    a local-calendar notion — without this, a UTC+12 owner asking "today" misses events that
    are still "yesterday" in UTC. Accepts 'today' | 'yesterday' | 'YYYY-MM-DD'."""
    local_tz = datetime.now().astimezone().tzinfo
    d = day.strip().lower()
    if d == "today":
        target = datetime.now(local_tz).date()
    elif d == "yesterday":
        target = datetime.now(local_tz).date() - timedelta(days=1)
    else:
        try:
            target = date.fromisoformat(day.strip())
        except ValueError:
            return None
    start = datetime(target.year, target.month, target.day, tzinfo=local_tz)
    end = start + timedelta(days=1)

    def utc(x: datetime) -> str:
        return x.astimezone(timezone.utc).isoformat(timespec="seconds")

    return utc(start), utc(end)


def _fmt(events: list[dict]) -> str:
    """Render events as a compact, scannable digest (newest first)."""
    if not events:
        return "(no matching activity)"
    lines = []
    for e in events:
        item = f" [{e['item_id']}]" if e.get("item_id") else " [dev-level]"
        lines.append(f"- {e['created_at']} · {e['kind']} · {e['actor']}{item}: {e['summary']}")
    return "\n".join(lines)


def _fmt_candidates(rows: list[dict]) -> str:
    """Render operational-learning candidates for distill to judge — one block per row, all the
    fields it needs to classify and draft (id, hints, origin pointers, statement, rationale,
    evidence)."""
    if not rows:
        return "(no candidates in this state)"
    out = []
    for r in rows:
        head = f"#{r['id']} · {r['captured_at']} · src={r['source']}"
        if r.get("form_hint"):
            head += f" · form_hint={r['form_hint']}"
        if r.get("scope_hint"):
            head += f" · scope={r['scope_hint']}"
        if r.get("origin_item_id"):
            head += f" · item={r['origin_item_id']}"
        block = [head, f"  statement: {r['signal']}"]
        if r.get("rationale"):
            block.append(f"  rationale: {r['rationale']}")
        if r.get("evidence"):
            ev = r["evidence"]
            block.append(f"  evidence: {json.dumps(ev) if isinstance(ev, (dict, list)) else ev}")
        out.append("\n".join(block))
    return "\n\n".join(out)


def _ids(raw) -> list[int]:
    """Parse a candidate-id list from a comma/space-separated string or a list (MCP args arrive
    as either). Non-numeric tokens are skipped; order-independent, de-duplicated."""
    if raw in (None, ""):
        return []
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[,\s]+", str(raw))
    seen = []
    for tok in items:
        try:
            n = int(str(tok).strip())
        except (ValueError, TypeError):
            continue
        if n not in seen:
            seen.append(n)
    return seen


def _s(args: dict, k: str) -> str | None:
    """A trimmed string arg, or None when absent/blank."""
    v = args.get(k)
    return str(v).strip() if v not in (None, "") else None


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


# --------------------------------------------------------------------------- typed schemas
# Per-param docs live here (on the schema), NOT in the description. Keep them terse — they answer
# "what is this arg", never "when should I call this tool" (that's the owning skill/agent's job).

class DevLogArgs(TypedDict, total=False):
    day: Annotated[str, "'today' | 'yesterday' | 'YYYY-MM-DD' (owner's local tz)"]
    since: Annotated[str, "ISO timestamp — start of a custom range"]
    until: Annotated[str, "ISO timestamp — end of a custom range"]
    scope: Annotated[str, "'dev' for repo-level housekeeping events only"]
    item_id: Annotated[str, "a single work-item's id — its timeline"]
    limit: Annotated[int, "max rows (default 100, cap 500)"]


class FileCandidateArgs(TypedDict, total=False):
    # The capture sweep saw the moment and later phases (distill, the owner) did not, so make the
    # candidate self-sufficient: state it richly here. This row is written straight to the pool.
    statement: Required[Annotated[str, "what to do — the durable operational learning, stated so it stands alone (1–3 sentences)"]]
    rationale: Annotated[str, "why it matters / what triggered it / the problem it solves"]
    evidence: Annotated[str, "the concrete instance(s) from the slice + a pointer (item id / path / quote)"]
    scope_hint: Annotated[str, "repo_dev | universal_dev | core (optional). The FORM (constitution/skill/agent) is distill's call, not yours — don't classify it."]
    origin_item_id: Annotated[str, "the work-item in scope, if the slice names one"]


class StageArtifactArgs(TypedDict, total=False):
    # The write subagent authors the FINAL artifact and stages it here. Which proposal it belongs to
    # (and where it will publish) is bound server-side from the write run — the agent supplies only
    # the finished content.
    content: Required[Annotated[str, ("the complete final artifact, frontmatter-first: for constitution "
                                      "a `description` (+ optional body); for skill the full SKILL.md; for "
                                      "agent the full agent.md — clean, concise, on-point")]]
    eval_report: Annotated[str, ("the forge_kit eval report as a JSON string (the last line eval.py "
                                 "printed) — the behavioural verdict shown to the gate-2 reviewer")]
    note: Annotated[str, "one optional line on a choice you made, for the gate-2 reviewer"]


class ReviewCandidatesArgs(TypedDict, total=False):
    status: Annotated[str, "'candidate' (default) | 'processed' | any candidate state"]
    limit: Annotated[int, "max rows (default 100, cap 500)"]


class ProposeMemoryArgs(TypedDict, total=False):
    title: Required[Annotated[str, "short headline"]]
    body: Required[Annotated[str, "the consolidated proposal narrative"]]
    summary: Annotated[str, "purpose · usage · why-raised (one short para; for owner + write phase)"]
    candidate_ids: Annotated[str, "source candidate ids, comma-separated"]
    output_form: Annotated[str, "constitution | skill | agent"]
    target_scope: Annotated[str, "repo_dev | universal_dev | core"]
    fields: Annotated[str, ("JSON object of form-specific fields — constitution: "
                            "{statement,scope,rationale}; skill: {name,when_to_use,procedure,tools,scope}; "
                            "agent: {name,role,tools,model,trigger}")]
    clarifications: Annotated[str, "JSON array of batch gate-1 questions, each {question, suggested, blocking}"]
    apply_target: Annotated[str, "drafted destination slug"]
    cluster: Annotated[str, "optional grouping label"]
    confidence: Annotated[str, "high | medium | low"]


# --------------------------------------------------------------------------- handler factories
# Each factory takes the shared deps (**_ absorbs any it doesn't use) and returns the async handler.

def _dev_log(*, store, context_id, **_):
    async def dev_log(args: dict) -> dict:
        since, until = _s(args, "since"), _s(args, "until")
        day = _s(args, "day")
        if day:
            rng = _day_range(day)
            if rng is None:
                return _err(f"Couldn't parse day='{day}' — use 'today', 'yesterday', or 'YYYY-MM-DD'.")
            since, until = rng
        try:
            events = store.list_events(
                context_id, since=since, until=until,
                scope=_s(args, "scope"), item_id=_s(args, "item_id"),
                limit=max(1, min(int(args.get("limit") or 100), 500)),
            )
        except Exception as e:  # malformed args, db error, …
            return _err(f"Could not read the dev log: {e}")
        return _ok(_fmt(events))
    return dev_log


def _file_candidate(*, store, context_id, origin_session_id=None, capture_source="agent", **_):
    """The capture sweep's pen. The `capture` sub-agent calls this once per learning it finds in the
    swept conversation slice; it writes a rich candidate row straight to the pool. Provenance
    (which session, agent vs owner-asked) is bound server-side from the sweep that launched it — the
    agent only supplies the substance. Grounding/consolidation/dedup is `distill`'s job downstream."""
    async def file_candidate(args: dict) -> dict:
        statement = _s(args, "statement")
        if not statement:
            return _err("Nothing to file — pass `statement` (the operational learning to keep).")
        try:
            row = store.add_memory_candidate(
                context_id, statement,
                source=capture_source,
                # No form_hint — capture never classifies the form; that's distill's call (it needs
                # the consolidated, cross-candidate view). Capture supplies substance + scope only.
                rationale=_s(args, "rationale"),
                scope_hint=_s(args, "scope_hint") or "repo_dev",
                origin_item_id=_s(args, "origin_item_id"),
                origin_session_id=origin_session_id,
                evidence=_s(args, "evidence"),
            )
        except Exception as e:
            return _err(f"Could not file the candidate: {e}")
        return _ok(f"Filed candidate #{row['id']} to the learning pool.")
    return file_candidate


def _stage_artifact(*, store, proposal_id=None, staged_path=None, **_):
    """The write phase's pen. The `write` sub-agent calls this once with the complete final artifact
    it authored; the proposal it belongs to and the path it will publish to are bound server-side
    from the write run (the agent only supplies content). Staging moves the proposal to `drafted`
    (gate 2) — disk stays untouched until publish."""
    async def stage_artifact(args: dict) -> dict:
        if proposal_id is None:
            return _err("stage_artifact is only available inside a write run.")
        content = _s(args, "content")
        if not content:
            return _err("Nothing to stage — pass `content` (the complete final artifact).")
        # The behavioural eval report is evidence for gate 2 — accept it as a JSON string and
        # tolerate a malformed/absent one (eval is advisory, never blocks staging).
        report = None
        raw = _s(args, "eval_report")
        if raw:
            try:
                report = json.loads(raw)
            except (ValueError, TypeError):
                report = {"verdict": "unknown", "summary": raw[:500]}
        try:
            store.stage_proposal_artifact(
                proposal_id, staged_artifact=content, eval_report=report,
                staged_path=staged_path, status="drafted",
            )
        except Exception as e:
            return _err(f"Could not stage the artifact: {e}")
        return _ok(f"Staged the artifact for proposal #{proposal_id} → drafted (awaiting gate-2 publish).")
    return stage_artifact


def _review_candidates(*, store, context_id, **_):
    async def review_candidates(args: dict) -> dict:
        status = _s(args, "status") or "candidate"
        try:
            rows = store.list_memory_candidates(
                context_id, status=status,
                limit=max(1, min(int(args.get("limit") or 100), 500)),
            )
        except Exception as e:
            return _err(f"Could not read the candidate pool: {e}")
        return _ok(_fmt_candidates(rows))
    return review_candidates


def _propose_memory(*, store, context_id, **_):
    def j(args: dict, k: str):
        raw = _s(args, k)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None  # tolerate a non-JSON arg rather than failing the whole proposal

    async def propose_memory(args: dict) -> dict:
        title, body = _s(args, "title"), _s(args, "body")
        if not title or not body:
            return _err("A proposal needs both `title` and `body` (the consolidated proposal).")
        ids = _ids(args.get("candidate_ids"))
        try:
            prop = store.add_memory_proposal(
                context_id, title, body,
                candidate_ids=ids,
                output_form=_s(args, "output_form") or "constitution",
                target_scope=_s(args, "target_scope") or "repo_dev",
                summary=_s(args, "summary"),
                fields=j(args, "fields"),
                clarifications=j(args, "clarifications"),
                apply_target=_s(args, "apply_target"),
                cluster=_s(args, "cluster"),
                confidence=_s(args, "confidence"),
            )
            store.log_event(
                context_id, "memory.proposed",
                f"Filed memory proposal #{prop['id']} "
                f"({prop['output_form']}/{prop['target_scope']}): {prop['title']}",
                scope="dev", actor="agent",
                meta={"proposal_id": prop["id"], "candidate_ids": ids},
            )
        except Exception as e:
            return _err(f"Could not file the proposal: {e}")
        src = f" from candidate(s) {', '.join('#' + str(i) for i in ids)}" if ids else ""
        return _ok(
            f"Filed proposal #{prop['id']} ({prop['output_form']} → {prop['target_scope']}){src} "
            f"— status: proposed. Awaiting the owner gate; nothing applied yet.")
    return propose_memory


# --------------------------------------------------------------------------- inbox (read-only)
# The inbox is DB-backed (not files), so it gets a tool rather than native Read (spec §5). Scoped
# to THIS host's context_id server-side, so it can only ever see its own queue. Read-only for now;
# mutation (agent-authored items, behind a human gate) is the deferred write-variant.

class InboxArgs(TypedDict, total=False):
    status: Annotated[str, "filter by status (e.g. 'open'); omit for all"]
    limit: Annotated[int, "max rows (default 50, cap 200)"]


def _fmt_inbox(rows: list[dict]) -> str:
    """Render inbox rows compactly (open first, newest first — the store's order)."""
    if not rows:
        return "(inbox empty)"
    out = []
    for r in rows:
        head = f"#{r['id']} · {r.get('status') or 'open'} · {r.get('kind') or 'note'}"
        if r.get("tag"):
            head += f" · {r['tag']}"
        if r.get("routed_to"):
            head += f" → {r['routed_to']}"
        if r.get("origin"):
            head += f" · from {r['origin']}"
        title = r.get("title") or (r.get("text") or "").strip().replace("\n", " ")
        out.append(f"- {head}: {title[:200]}")
    return "\n".join(out)


def _list_inbox(*, store, context_id, **_):
    async def list_inbox(args: dict) -> dict:
        try:
            rows = store.list_inbox(context_id)
        except Exception as e:
            return _err(f"Could not read the inbox: {e}")
        status = _s(args, "status")
        if status:
            rows = [r for r in rows if (r.get("status") or "open") == status]
        rows = rows[:max(1, min(int(args.get("limit") or 50), 200))]
        return _ok(_fmt_inbox(rows))
    return list_inbox


# --------------------------------------------------------------------------- the registry

DEV_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "dev_log",
        "Read this repo's development activity log (events table), newest first.",
        DevLogArgs, _dev_log,
    ),
    ToolSpec(
        "list_inbox",
        "Read this repo's inbox — captured items awaiting triage/routing, open first.",
        InboxArgs, _list_inbox,
    ),
    ToolSpec(
        "file_candidate",
        "File one durable operational learning found in a swept conversation slice as a candidate.",
        FileCandidateArgs, _file_candidate,
    ),
    ToolSpec(
        "stage_artifact",
        "Stage the final authored artifact for a proposal (the write phase's pen → drafted).",
        StageArtifactArgs, _stage_artifact,
    ),
    ToolSpec(
        "review_candidates",
        "Read the operational learning candidate pool for processing, newest first.",
        ReviewCandidatesArgs, _review_candidates,
    ),
    ToolSpec(
        "propose_memory",
        "File one consolidated operational-learning proposal from processed candidates.",
        ProposeMemoryArgs, _propose_memory,
    ),
]


def make_dev_mcp_server(store, context_id: str, **deps):
    """Build the `dev` MCP server (dev_log + the learning-pipeline tools) bound to one context's
    event store. Optional deps thread per-turn state to specific tools (ignored by the rest):
    `origin_session_id` + `capture_source` (provenance bound onto `file_candidate`'s rows during a
    sweep sub-run), `proposal_id` + `staged_path` (bound onto `stage_artifact` during a write run).
    Capture is fully automatic (idle + phase-advance sweeps) — there is no chat-side capture tool."""
    return build_mcp_server("dev", DEV_TOOLS, store=store, context_id=context_id, **deps)
