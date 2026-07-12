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


def _fmt_proposals(rows: list[dict]) -> str:
    """Render the OPEN proposals for distill to consolidate against — enough for it to spot a
    standing proposal that already covers a learning (id, status, form/scope, cluster, title,
    summary, the candidates it already draws on) so it can merge into it rather than duplicate."""
    if not rows:
        return "(no open proposals — nothing to consolidate against)"
    out = []
    for r in rows:
        head = f"#{r['id']} · {r['status']} · {r.get('output_form')}/{r.get('target_scope')}"
        if r.get("cluster"):
            head += f" · cluster={r['cluster']}"
        cids = r.get("candidate_ids") or []
        block = [head, f"  title: {r['title']}"]
        if r.get("summary"):
            block.append(f"  summary: {r['summary']}")
        block.append(f"  draws on: {', '.join('#' + str(i) for i in cids) if cids else '—'}")
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


class DropCandidatesArgs(TypedDict, total=False):
    candidate_ids: Required[Annotated[str, "candidate ids to drop, comma-separated"]]
    reason: Annotated[str, "one short phrase why (e.g. 'self-recitation', 'too-thin') — logged only"]


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


class ReviewProposalsArgs(TypedDict, total=False):
    # No args — reads all OPEN proposals in this context (the set distill consolidates against).
    pass


class MergeProposalArgs(TypedDict, total=False):
    proposal_id: Required[Annotated[int, "the existing OPEN proposal to fold the candidate(s) into"]]
    candidate_ids: Required[Annotated[str, "the NEW source candidate ids to merge in, comma-separated"]]
    title: Annotated[str, "optional — enriched headline (omit to keep the existing one)"]
    body: Annotated[str, "optional — the re-consolidated narrative incorporating the new substance"]
    summary: Annotated[str, "optional — refreshed purpose · usage · why-raised"]
    fields: Annotated[str, "optional — updated form-specific fields (JSON), same shape as propose_memory"]
    confidence: Annotated[str, "optional — refreshed high|medium|low (recurrence usually raises it)"]


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


def _drop_candidates(*, store, context_id, **_):
    async def drop_candidates(args: dict) -> dict:
        ids = _ids(args.get("candidate_ids"))
        if not ids:
            return _err("Pass `candidate_ids` (comma-separated) — the candidates to drop.")
        reason = _s(args, "reason")
        try:
            n = store.delete_memory_candidates(context_id, ids)
            store.log_event(
                context_id, "memory.dropped",
                f"Distill dropped {n} candidate(s){f' ({reason})' if reason else ''}: "
                f"{', '.join('#' + str(i) for i in ids)}",
                scope="dev", actor="agent",
                meta={"candidate_ids": ids, "reason": reason, "deleted": n})
        except Exception as e:
            return _err(f"Could not drop the candidates: {e}")
        return _ok(f"Dropped {n} candidate(s) permanently{f' — {reason}' if reason else ''}.")
    return drop_candidates


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


def _review_proposals(*, store, context_id, **_):
    """Distill's cross-run consolidation lens: the OPEN proposals already standing in this context.
    Distill reads this alongside the candidate pool so a learning captured again in a later session
    can MERGE into the proposal that already covers it, instead of minting a parallel one."""
    async def review_proposals(args: dict) -> dict:
        try:
            rows = store.list_memory_proposals(context_id)
        except Exception as e:
            return _err(f"Could not read the proposal pool: {e}")
        open_rows = [r for r in rows if r.get("status") in ("proposed", "writing", "drafted")]
        return _ok(_fmt_proposals(open_rows))
    return review_proposals


def _merge_into_proposal(*, store, context_id, **_):
    def j(args: dict, k: str):
        raw = _s(args, k)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    async def merge_into_proposal(args: dict) -> dict:
        try:
            pid = int(args.get("proposal_id"))
        except (TypeError, ValueError):
            return _err("Pass a numeric `proposal_id` (the open proposal to merge into).")
        ids = _ids(args.get("candidate_ids"))
        if not ids:
            return _err("Pass `candidate_ids` — the new candidates to fold into the proposal.")
        try:
            prop = store.merge_memory_proposal(
                pid, context_id, add_candidate_ids=ids,
                title=_s(args, "title"), body=_s(args, "body"),
                summary=_s(args, "summary"), fields=j(args, "fields"),
                confidence=_s(args, "confidence"))
        except ValueError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"Could not merge into the proposal: {e}")
        if prop is None:
            return _err(f"No proposal #{pid} in this context to merge into.")
        store.log_event(
            context_id, "memory.merged",
            f"Merged candidate(s) {', '.join('#' + str(i) for i in ids)} into proposal #{pid} "
            f"({prop['output_form']}/{prop['target_scope']}): {prop['title']}",
            scope="dev", actor="agent",
            meta={"proposal_id": pid, "candidate_ids": ids, "reforged": prop.get("reforged")})
        note = (" It was already forged, so it's reset to `proposed` for re-forge with the fuller "
                "candidate set.") if prop.get("reforged") else ""
        return _ok(
            f"Merged {len(ids)} candidate(s) into proposal #{pid} — it now draws on "
            f"{len(prop['candidate_ids'])} candidate(s), status: {prop['status']}.{note}")
    return merge_into_proposal


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
        origin = r.get("origin")
        if origin:
            head += f" · from {', '.join(origin) if isinstance(origin, list) else origin}"
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


# --------------------------------------------------------------------------- inbox (create — sanctioned)
# The one WRITE a general (non-work-item) session may make (work-item-session-recognition-prd): the
# sanctioned front door for turning a discussion into a proper ticket. Caller-agnostic — the skill
# that drives it synthesizes the content; this tool just performs the controlled inbox write (it can
# touch nothing else, so it can't be abused to smuggle implementation past the general-session
# guardrail). Origin is stamped `agent`; the owner triages/pushes it into a work-item downstream.

class CreateInboxItemArgs(TypedDict, total=False):
    title: Required[Annotated[str, "short, on-point headline for the ticket"]]
    body: Required[Annotated[str, ("the item content — a crisp synthesis of intent + the on-point "
                                   "context/decisions and any pointers or references (work-item ids, "
                                   "paths, doc names). NOT a raw transcript dump")]]
    kind: Annotated[str, "note | idea | todo | question (default note)"]


def _create_inbox_item(*, store, context_id, **_):
    async def create_inbox_item(args: dict) -> dict:
        title, body = _s(args, "title"), _s(args, "body")
        if not title or not body:
            return _err("An inbox item needs both `title` and `body` (a crisp synthesis, not a dump).")
        try:
            row = store.add_inbox(
                context_id, body, kind=_s(args, "kind") or "note",
                title=title, origin=["agent"], source="agent",
            )
        except Exception as e:
            return _err(f"Could not create the inbox item: {e}")
        return _ok(f"Created inbox item #{row['id']} — \"{title}\". "
                   f"It's in the Inbox for the owner to review and push into a work-item.")
    return create_inbox_item


class AppendInboxItemArgs(TypedDict, total=False):
    item_id: Required[Annotated[int, "the existing inbox item's id to augment"]]
    addition: Required[Annotated[str, ("the NEW, on-point content from this discussion to append — "
                                       "what the existing item doesn't already cover. Never a rewrite; "
                                       "the existing text is preserved and this is added under it")]]


def _append_inbox_item(*, store, context_id, **_):
    async def append_inbox_item(args: dict) -> dict:
        addition = _s(args, "addition")
        try:
            item_id = int(args.get("item_id"))
        except (TypeError, ValueError):
            return _err("Pass a numeric `item_id` (the existing inbox item to append to).")
        if not addition:
            return _err("Nothing to append — pass `addition` (the new content this discussion adds).")
        try:
            row = store.append_inbox(item_id, addition, origin_add="agent")
        except Exception as e:
            return _err(f"Could not append to the inbox item: {e}")
        if row is None:
            return _err(f"No inbox item #{item_id} to append to.")
        return _ok(f"Appended to inbox item #{row['id']} — \"{row.get('title') or ''}\" "
                   f"(existing content untouched; origin now {', '.join(row.get('origin') or [])}).")
    return append_inbox_item


# --------------------------------------------------------------------------- the registry
# Two tiers. The MAIN set is what a normal dev chat turn gets: read activity, read the inbox, and the
# two SANCTIONED itemize writes. The LEARNING set is the pipeline sub-agents' pens (capture / distill
# / forge) — they must NOT reach the main chat agent, or it will short-circuit the automatic
# capture→distill→forge flow by filing candidates/proposals itself (there is no chat-side learning
# surface by design). Those tools are added only for the disposable headless runs (`learning=True`).

# Read-only tools available to EVERY dev turn (main chat + learning runs). Naming: every read is
# `read_*` so the tool surface is scannable. The learning-pool reads (`read_candidates`/`read_proposals`)
# live here — not in the learning-only set — because they mutate nothing: a general session should be
# able to answer "what learning is pending?" (the UI already shows it). Only the learning WRITE pens
# stay gated (below), so the main agent can't file learnings by hand and bypass automatic sweep-capture.
_MAIN_DEV_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "read_dev_log",
        "Read this repo's dev event log (harness · work-item · learning events), newest first.",
        DevLogArgs, _dev_log,
    ),
    ToolSpec(
        "read_inbox",
        "Read this repo's inbox — captured items awaiting triage/routing, open first.",
        InboxArgs, _list_inbox,
    ),
    ToolSpec(
        "read_candidates",
        "Read the operational-learning candidate pool (what capture has filed), newest first.",
        ReviewCandidatesArgs, _review_candidates,
    ),
    ToolSpec(
        "read_proposals",
        "Read the OPEN operational-learning proposals already standing, newest first.",
        ReviewProposalsArgs, _review_proposals,
    ),
    ToolSpec(
        "create_inbox_item",
        "Create one inbox item (ticket) from a discussion — the sanctioned way to itemize real work.",
        CreateInboxItemArgs, _create_inbox_item,
    ),
    ToolSpec(
        "append_inbox_item",
        "Append new discussion content onto an EXISTING inbox item (never edits it) — the dedup path.",
        AppendInboxItemArgs, _append_inbox_item,
    ),
]

# Pipeline-only WRITE pens — capture files candidates, distill proposes/merges/drops, forge stages.
# These must never reach the main chat agent (it would bypass automatic capture). The learning-pool
# READS moved to the main set above; only the mutating pens remain gated here.
_LEARNING_DEV_TOOLS: list[ToolSpec] = [
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
        "propose_memory",
        "File one consolidated operational-learning proposal from processed candidates.",
        ProposeMemoryArgs, _propose_memory,
    ),
    ToolSpec(
        "merge_into_proposal",
        "Fold new candidate(s) into an existing open proposal — cross-run consolidation of a recurring learning.",
        MergeProposalArgs, _merge_into_proposal,
    ),
    ToolSpec(
        "drop_candidates",
        "Permanently drop candidates that fail distill's gate (keeps the pool lean — quality over quantity).",
        DropCandidatesArgs, _drop_candidates,
    ),
]

DEV_TOOLS: list[ToolSpec] = _MAIN_DEV_TOOLS + _LEARNING_DEV_TOOLS   # full set (for reference/tests)


def make_dev_mcp_server(store, context_id: str, *, learning: bool = False, **deps):
    """Build the `dev` MCP server bound to one context's event store. By default it exposes only the
    MAIN dev tools (the `read_*` reads over the event log · inbox · learning pool, plus the sanctioned
    inbox itemize writes) — a normal chat turn. The disposable learning runs (capture/distill/forge)
    pass `learning=True` to also get the WRITE pens; those must never reach the main chat agent (it
    would bypass automatic capture).

    Optional deps thread per-turn state to specific learning tools (ignored by the rest):
    `origin_session_id` + `capture_source` (provenance bound onto `file_candidate` during a sweep),
    `proposal_id` + `staged_path` (bound onto `stage_artifact` during a write run)."""
    tools = _MAIN_DEV_TOOLS + (_LEARNING_DEV_TOOLS if learning else [])
    return build_mcp_server("dev", tools, store=store, context_id=context_id, **deps)
