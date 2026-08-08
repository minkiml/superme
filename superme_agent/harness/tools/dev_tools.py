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

import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, NotRequired, Required, TypedDict

from .registry import ToolSpec, build_mcp_server


# --------------------------------------------------------------------------- structured id grammar
# Every read_* tool emits NAMESPACE-QUALIFIED ids — `proposal:99`, `candidate:172`, `event:559`,
# `run:228`, `inbox:12`, `item:<slug>` — so a record's ORIGIN table is never ambiguous. A bare `#99`
# is indistinguishable across the proposal / candidate / event / run id spaces (they overlap and are
# renumbered independently), which is exactly what let a recap conflate a dev-log reference with a
# live table row. Each tool also leads with a `#`-prefixed header naming its record shape, so the
# output is self-describing and the agent parses it the same way every time. (read-tool-output)

def _qid(ns: str, ident) -> str:
    """A namespace-qualified id: `proposal:99`, `event:559`, `run:228`, `candidate:172`, `inbox:12`."""
    return f"{ns}:{ident}"


def _artifact_name(path: str | None) -> str | None:
    """The specific artifact a learning event produced, from its staged_path — names WHICH skill /
    constitution / agent, not just the form. `…/skills/add-daemon-endpoint/SKILL.md` → 'add-daemon-
    endpoint'; `…/constitution/commit-subject-line.md` → 'commit-subject-line'."""
    if not path:
        return None
    stem = os.path.basename(path).rsplit(".", 1)[0]
    # A dir-named artifact (skills/<name>/SKILL.md) → the parent dir carries the real name.
    if stem.upper() in ("SKILL", "AGENT", "README", "INDEX"):
        return os.path.basename(os.path.dirname(path)) or stem
    return stem


def _event_refs(meta: dict | None) -> str:
    """The qualified cross-table refs an event carries in its meta, as a compact ` · refs=…` clause:
    proposal_id → proposal:N, candidate_ids → candidate:a+b, staged_path → artifact="<name>". Empty
    when the event references nothing structured. These refs are what make a dev-log line say
    `proposal:99 (skill "add-daemon-endpoint")` instead of an unqualified, unnamed `#99 skill`."""
    if not isinstance(meta, dict):
        return ""
    parts: list[str] = []
    if meta.get("proposal_id") is not None:
        parts.append(_qid("proposal", meta["proposal_id"]))
    cids = meta.get("candidate_ids")
    if isinstance(cids, (list, tuple)) and cids:
        parts.append("+".join(_qid("candidate", i) for i in cids))
    name = _artifact_name(meta.get("staged_path"))
    if name:
        parts.append(f'artifact="{name}"')
    return (" · refs=" + " ".join(parts)) if parts else ""


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
    """Render dev-log events as qualified, scannable records (newest first). Each line:
    `event:<id> · <ts> · <kind> · <actor>@<scope> · [refs=…] · <summary>`. The `refs=` ids point at
    OTHER tables and are a HISTORICAL record — the referenced row may since have been cleaned, so a
    ref here does NOT prove a live row; use read_proposals / read_candidates for what's currently live."""
    if not events:
        return "(no matching activity)"
    lines = [
        "# read_dev_log · dev event log, newest-first",
        "# record: event:<id> · <ts> · <kind> · <actor>@<scope> · [refs=<qualified ids>] · <summary>",
        "# NOTE refs= are HISTORICAL pointers into other tables (a referenced row may be gone); "
        "for LIVE rows use read_proposals / read_candidates.",
    ]
    for e in events:
        scope = f"item:{e['item_id']}" if e.get("item_id") else "dev-level"
        lines.append(
            f"{_qid('event', e.get('id'))} · {e['created_at']} · {e['kind']} · {e['actor']}@{scope}"
            f"{_event_refs(e.get('meta'))} · {e['summary']}")
    return "\n".join(lines)


def _fmt_candidates(rows: list[dict]) -> str:
    """Render operational-learning candidates for distill to judge — one block per row, all the
    fields it needs to classify and draft (id, hints, origin pointers, statement, rationale,
    evidence)."""
    if not rows:
        return "(no candidates in this state)"
    out = ["# read_candidates · LIVE learning-candidate rows · head: candidate:<id> · <ts> · src=… [· form_hint · scope · item:…]"]
    for r in rows:
        head = f"{_qid('candidate', r['id'])} · {r['captured_at']} · src={r['source']}"
        if r.get("form_hint"):
            head += f" · form_hint={r['form_hint']}"
        if r.get("scope_hint"):
            head += f" · scope={r['scope_hint']}"
        if r.get("origin_item_id"):
            head += f" · {_qid('item', r['origin_item_id'])}"
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
    out = ["# read_proposals · LIVE open-proposal rows · head: proposal:<id> · <status> · <form>/<scope> [· cluster]"]
    for r in rows:
        head = f"{_qid('proposal', r['id'])} · {r['status']} · {r.get('output_form')}/{r.get('target_scope')}"
        if r.get("cluster"):
            head += f" · cluster={r['cluster']}"
        cids = r.get("candidate_ids") or []
        block = [head, f"  title: {r['title']}"]
        if r.get("summary"):
            block.append(f"  summary: {r['summary']}")
        block.append(f"  draws on: {' '.join(_qid('candidate', i) for i in cids) if cids else '—'}")
        out.append("\n".join(block))
    return "\n\n".join(out)


def _fmt_run_list(rows: list[dict]) -> str:
    """One line per recent run — enough for the agent to pick the id to inspect."""
    if not rows:
        return "(no runs recorded for this repo yet)"
    out = [
        "# read_run · recent runs, newest-first · record: run:<id> · <feature> · <status> · <model> · <tok> · <ts> [· item:…]",
        "# call read_run with a run_id (the number in run:<id>) to inspect one's full trace.",
    ]
    for r in rows:
        item = f" · {_qid('item', r['item_id'])}" if r.get("item_id") else ""
        out.append(
            f"{_qid('run', r['id'])} · {r.get('feature')} · {r.get('status')} · {r.get('model') or '—'}"
            f" · {r.get('tokens') or 0} tok · {r.get('started_at')}{item}")
    return "\n".join(out)


def _fmt_run_trace(run: dict, events: list[dict]) -> str:
    """One run's full trace: its summary header + the ordered prompt · reply · tool/skill/agent
    call trail — the diagnosis substrate ('what did this run actually do / why did it fail')."""
    head = [
        f"{_qid('run', run['id'])} · {run.get('feature')} · {run.get('mode')} · {run.get('status')}",
        f"  model={run.get('model') or '—'} · tokens={run.get('tokens') or 0}"
        f" · ctx={run.get('ctx_pct') if run.get('ctx_pct') is not None else '—'}%"
        f" · phase={run.get('phase') or '—'}",
        f"  started={run.get('started_at')} · ended={run.get('ended_at') or '(running)'}",
    ]
    if run.get("item_id"):
        head.append(f"  work-item={_qid('item', run['item_id'])}")
    if run.get("session_fate"):
        head.append(f"  origin session: {run['session_fate']} (trace preserved)")
    if not events:
        head.append("\n(no per-event trail recorded for this run)")
        return "\n".join(head)
    trail = ["", "trace:"]
    for e in events:
        desc = (e.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 300:
            desc = desc[:300] + "…"
        label = e.get("name") or e.get("kind")
        # A row from inside a sub-agent is indented under its spawn. Flat, a fan-out reads as one
        # agent thrashing — and a diagnosis of "why did this run fail" turns on exactly that
        # distinction (whose tool call was denied: the parent's, or a delegated reader's).
        indent = "    " if e.get("parent_tool_id") else "  "
        trail.append(f"{indent}{e.get('seq')}. [{e.get('kind')}] {label}" + (f" — {desc}" if desc else ""))
    return "\n".join(head + trail)


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


# read_dev_log reading depths — recent (default) · mid · max. Far-back rows rarely inform a question
# and cost context, so a no-arg read stays at 'recent'; the agent widens deliberately.
_LOG_LEVELS = {"recent": 100, "mid": 300, "max": 500}


# --------------------------------------------------------------------------- typed schemas
# Per-param docs live here (on the schema), NOT in the description. Keep them terse — they answer
# "what is this arg", never "when should I call this tool" (that's the owning skill/agent's job).
# Closed value sets are `Literal[...]` (the registry renders them as JSON-Schema enums, so the
# model sees the allowed values as TYPE, not prose); prose in the doc string is for meaning only.
class DevLogArgs(TypedDict, total=False):
    day: Annotated[str, "'today' | 'yesterday' | 'YYYY-MM-DD' (owner's local tz)"]
    since: Annotated[str, "ISO timestamp — start of a custom range"]
    until: Annotated[str, "ISO timestamp — end of a custom range"]
    scope: Annotated[str, "'dev' for repo-level (non-item-scoped) events only"]
    item_id: Annotated[str, "a single work-item's id — its timeline"]
    level: Annotated[Literal["recent", "mid", "max"],
                     "reading depth: recent=100 rows (default) · mid=300 · max=500"]
    limit: Annotated[int, "exact row cap override (1–500) — use `level` unless you need a precise count"]


class FileCandidateArgs(TypedDict, total=False):
    # The capture sweep saw the moment and later phases (distill, the owner) did not, so make the
    # candidate self-sufficient: state it richly here. This row is written straight to the pool.
    statement: Required[Annotated[str, "what to do — the durable operational learning, stated so it stands alone (1–3 sentences)"]]
    rationale: Annotated[str, "why it matters / what triggered it / the problem it solves"]
    evidence: Annotated[str, "the concrete instance(s) from the slice + a pointer (item id / path / quote)"]
    scope_hint: Annotated[Literal["repo_dev", "universal_dev", "core"],
                          "where the learning applies (default repo_dev). The FORM "
                          "(constitution/skill/agent) is distill's call, not yours — don't classify it."]
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


class ReadRunArgs(TypedDict, total=False):
    run_id: Annotated[int, "the run (Activity item) id to inspect — its full trace. Omit to list recent runs first."]
    limit: Annotated[int, "list mode only: max recent runs (default 20, cap 100)"]


class DropCandidatesArgs(TypedDict, total=False):
    candidate_ids: Required[Annotated[str, "candidate ids to drop, comma-separated"]]
    reason: Annotated[str, "one short phrase why (e.g. 'self-recitation', 'too-thin') — logged only"]


class ProposeMemoryArgs(TypedDict, total=False):
    title: Required[Annotated[str, "short headline"]]
    body: Required[Annotated[str, "the consolidated proposal narrative"]]
    summary: Annotated[str, "purpose · usage · why-raised (one short para; for owner + write phase)"]
    candidate_ids: Annotated[str, "source candidate ids, comma-separated"]
    output_form: Annotated[Literal["constitution", "skill", "agent"],
                           "the artifact form this proposal targets (default constitution)"]
    target_scope: Annotated[Literal["repo_dev", "universal_dev", "core"],
                            "where the artifact will live (default repo_dev)"]
    fields: Annotated[str, ("JSON object of form-specific fields — constitution: "
                            "{statement,scope,rationale}; skill: {name,when_to_use,procedure,tools,scope}; "
                            "agent: {name,role,tools,model,trigger}")]
    clarifications: Annotated[str, "JSON array of batch gate-1 questions, each {question, suggested, blocking}"]
    apply_target: Annotated[str, "drafted destination slug"]
    cluster: Annotated[str, "optional grouping label"]
    confidence: Annotated[Literal["high", "medium", "low"], "how solidly grounded the proposal is"]


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
    confidence: Annotated[Literal["high", "medium", "low"],
                          "optional — refreshed confidence (recurrence usually raises it)"]


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
            # Three discrete reading depths (far-back rows are rarely meaningful + cost context):
            # recent (default) → 100 · mid → 300 · max → 500. `limit` is an exact override (1–500).
            lvl = (_s(args, "level") or "recent").lower()
            depth = int(args.get("limit") or _LOG_LEVELS.get(lvl, 100))
            events = store.list_events(
                context_id, since=since, until=until,
                scope=_s(args, "scope"), item_id=_s(args, "item_id"),
                limit=max(1, min(depth, 500)),
            )
        except Exception as e:  # malformed args, db error, …
            return _err(f"Could not read the dev log: {e}")
        return _ok(_fmt(events))
    return dev_log


def _read_run(*, context_id, spine=None, **_):
    """Read one run's full trace (or list recent runs) — the diagnosis/inspection read over the
    spine's run + run_event tables. `spine` is injected by the daemon (which owns it); learning runs
    build the dev server without it, but they never carry read_run in their allowlist."""
    async def read_run(args: dict) -> dict:
        if spine is None:
            return _err("Run inspection isn't available in this session.")
        rid = args.get("run_id")
        if rid in (None, "", 0):
            limit = max(1, min(int(args.get("limit") or 20), 100))
            try:
                return _ok(_fmt_run_list(spine.run_history(context_id, limit=limit)))
            except Exception as e:
                return _err(f"Could not list runs: {e}")
        try:
            run = spine.get_run(int(rid))
        except (ValueError, TypeError):
            return _err(f"run_id must be a number (got {rid!r}).")
        # Scope to THIS repo's runs — a repo's agent can't read another repo's trace.
        if run is None or run.get("repo_id") != context_id:
            return _err(f"No run #{rid} in this repo.")
        try:
            events = spine.events_for_run(int(rid))
        except Exception as e:
            return _err(f"Could not read run #{rid}'s trace: {e}")
        return _ok(_fmt_run_trace(run, events))
    return read_run


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
                context_id, "candidates.dropped",
                f"Distill dropped {n} candidate(s){f' ({reason})' if reason else ''}: "
                f"{', '.join(_qid('candidate', i) for i in ids)}",
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
    out = ["# read_inbox · triage queue, open-first · record: inbox:<id> · <status> · <kind> [· tag · →routed · from …] · <title>"]
    for r in rows:
        head = f"{_qid('inbox', r['id'])} · {r.get('status') or 'open'} · {r.get('kind') or 'note'}"
        if r.get("tag"):
            head += f" · {r['tag']}"
        if r.get("routed_to"):
            head += f" → {_qid('item', r['routed_to'])}"
        origin = r.get("origin")
        if origin:
            head += f" · from {', '.join(origin) if isinstance(origin, list) else origin}"
        title = r.get("title") or (r.get("text") or "").strip().replace("\n", " ")
        out.append(f"{head} · {title[:200]}")
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
    title: Required[Annotated[str, ("the ticket's headline — a few words naming it, under 60 "
                                    "characters, no closing period. Not the ask; that is `body`")]]
    body: Required[Annotated[str, ("the item content — a crisp synthesis of intent + the on-point "
                                   "context/decisions and any pointers or references (work-item ids, "
                                   "paths, doc names). NOT a raw transcript dump")]]
    kind: Annotated[Literal["note", "idea", "todo", "question"], "item flavor (default note)"]
    spawned_from_item: Annotated[str, ("branch-off ONLY: the parent work-item id this spawns from "
                                       "(requires `relation`)")]
    relation: Annotated[Literal["blocking", "parallel", "spawn"],
                        ("branch-off type: blocking (parent can't proceed without it — auto-pushed, "
                         "pauses the parent) · parallel (child work, auto-pushed, gates the parent's "
                         "completion) · spawn (independent follow-up — waits in the inbox for the "
                         "owner's push)")]
    background: Annotated[str, "handoff brief: the problem/story — why this was raised"]
    discussion: Annotated[str, "handoff brief: what was discussed/concluded so far"]
    direction: Annotated[str, "handoff brief: high-level direction or options, with leanings — NO plans/implementation detail"]
    constraints: Annotated[str, "handoff brief: constraints, tried-but-failed, out-of-scope"]


def _create_inbox_item(*, store, context_id, dev_root=None, fire_triage=None, **_):
    async def create_inbox_item(args: dict) -> dict:
        from pathlib import Path
        from ...core import artifacts as _arts
        from ...core import inbox_flow as _flow
        from ...core.dev_knowledge import DevKnowledgeService
        from ...core.titles import check_title
        title, body = _s(args, "title"), _s(args, "body")
        if not body:
            return _err("An inbox item needs both `title` and `body` (a crisp synthesis, not a dump).")
        if (bad := check_title(title, description=body)):
            return _err(bad)
        parent, relation = _s(args, "spawned_from_item"), _s(args, "relation")
        if bool(parent) != bool(relation):
            return _err("A branch-off needs BOTH `spawned_from_item` and `relation` (or neither).")
        spawned_from = None
        dev = DevKnowledgeService()
        if parent:
            if relation not in ("blocking", "parallel", "spawn"):
                return _err("`relation` must be blocking | parallel | spawn.")
            parent_item = dev.read_work_item(Path(dev_root), parent) if dev_root else None
            if not parent_item:
                return _err(f"Parent work-item {parent!r} not found — a branch-off must name a real item.")
            # blocking/parallel AUTO-PUSH and branch off git: blocking from the parent's own branch,
            # parallel alongside it. A kind with no worktree (research) has no branch to hand over
            # and nothing running to wait on it — and auto-pushing from one would start work the
            # owner never chose. `spawn` is the decoupled relation those items use.
            if relation in ("blocking", "parallel"):
                from ...core.kind_profiles import get_profile
                if not get_profile(parent_item.get("kind")).worktree:
                    return _err(
                        f"A {parent_item.get('kind')!r} item has no worktree, so it cannot carry a "
                        f"{relation!r} branch-off (those auto-push and branch off git). Use "
                        "`relation: \"spawn\"` — it waits in the inbox for the owner's push.")
            spawned_from = {"item": parent, "relation": relation}
        try:
            row = store.add_inbox(
                context_id, body, kind=_s(args, "kind") or "note",
                title=title, origin=["agent"], spawned_from=spawned_from,
            )
        except Exception as e:
            return _err(f"Could not create the inbox item: {e}")
        # Handoff brief (D5): scaffolded + prose slots filled from the args while context is hot.
        # High-level only — the work-item's own phases do the deep work.
        brief = None
        if dev_root:
            brief = _arts.write_handoff_brief(
                _flow.inbox_content_dir(Path(dev_root), row["id"]), title,
                background=_s(args, "background"), discussion=_s(args, "discussion"),
                direction=_s(args, "direction"), constraints=_s(args, "constraints"),
            )
        # Auto-push (D3): blocking/parallel children route through the inbox for the uniform
        # trace but push immediately — only `spawn` waits for the owner.
        if spawned_from and relation in ("blocking", "parallel") and dev_root:
            try:
                wi = _flow.push_inbox_item(store, dev, Path(dev_root), row,
                                           context_id=context_id, actor="agent")
            except Exception as e:
                return _err(f"Inbox item #{row['id']} created but auto-push failed: {e}")
            # First kick — the same one the owner's push gets. Without it the child lands at
            # triage/active with no run behind it and never moves (a BLOCKING child wedges its
            # parent there too). Injected by the daemon; absent in a bare/test server, where the
            # child simply waits for a hand-driven triage.
            triaged = False
            if fire_triage:
                try:
                    triaged = bool(fire_triage(wi["id"]))
                except Exception:
                    triaged = False
            paused = " Parent paused (awaiting_child) until it closes." if relation == "blocking" else ""
            kick = " Triage is running on it." if triaged else " It rests at triage for a triage pass."
            return _ok(f"Branch-off filed and AUTO-PUSHED: inbox #{row['id']} → work-item "
                       f"{wi['id']} ({relation} child of {parent}; brief moved to its "
                       f"preliminary/).{paused}{kick}")
        where = f" Handoff brief at {brief}." if brief else ""
        # An empty brief throws away exactly the context this session holds — flag it back so the
        # agent fills the fields (the future triage session cold-starts from this brief).
        filled = any(_s(args, k) for k in ("background", "discussion", "direction", "constraints"))
        nudge = ("" if filled or not brief else
                 " NOTE: the handoff brief's slots are EMPTY. If this discussion holds real "
                 "context, capture it now with `append_inbox_item` (it mirrors onto the brief) — "
                 "the future triage session cold-starts from this brief and shouldn't start blind.")
        return _ok(f"Created inbox item #{row['id']} — \"{title}\".{where}{nudge} "
                   f"It's in the Inbox for the owner to review and push into a work-item.")
    return create_inbox_item


# --------------------------------------------------------------------------- itemize + launch (4c)
# The onboarding LAUNCH step: turn a settled plan into a cohort of autopilot work-items in one call.
# Unlike create_inbox_item (one ticket, owner triages later), this mints work-items DIRECTLY on
# autopilot with their dependency edges wired — the end-of-onboarding act, after the owner confirms
# the list. `key` is a batch-local handle so the caller can express `after` edges before ids exist.

class ItemizeItemArgs(TypedDict, total=False):
    key: Required[Annotated[str, ("a batch-LOCAL handle for this item, used only to wire `after` "
                                  "edges within this launch (e.g. the deliverable id `d-cli`) — the "
                                  "real work-item id is minted on creation")]]
    title: Required[Annotated[str, ("the card label — a few words naming the change, under 60 "
                                    "characters, no closing period (e.g. \"Add dark mode "
                                    "support\"). Not the ask; that is `description`")]]
    description: Annotated[str, ("the intent — what value this item delivers. Crisp; NOT a plan "
                                 "(the item's own plan phase does that)")]
    after: Annotated[list[str], ("keys of items in THIS batch (or ids of existing work-items) that "
                                 "must finish before this one starts — from the PRD deliverables' "
                                 "`Needs` edges. Omit for items that can start immediately")]
    kind: Annotated[Literal["implementation", "research"], "work-item kind (default implementation)"]


class ItemizeAndLaunchArgs(TypedDict, total=False):
    items: Required[Annotated[list[ItemizeItemArgs], ("the launch cohort — one entry per work-item. "
                                                      "Created together on autopilot, wired by their "
                                                      "`after` edges, stamped with one shared cohort id")]]


def _itemize_and_launch(*, store, context_id, **_):
    async def itemize_and_launch(args: dict) -> dict:
        items = args.get("items")
        if not isinstance(items, list) or not items:
            return _err("itemize_and_launch needs a non-empty `items` list (the cohort to launch).")
        from ...daemon.services import launch   # lazy: harness must not import daemon at load time
        try:
            r = launch.launch_cohort(context_id, items)
        except (ValueError, RuntimeError) as e:
            return _err(f"Could not launch the cohort: {e}")
        head = [f"# launched cohort {r['cohort']} · {len(r['created'])} item(s) on autopilot",
                f"# {r['launched']} started now · {len(r['waiting'])} waiting on upstreams · "
                f"record: <id> · <status> · <title> [· after <ids>]"]
        rows = []
        for c in r["created"]:
            edge = f" · after {', '.join(c['after'])}" if c["after"] else ""
            rows.append(f"{c['id']} · {c['status']} · {c['title']}{edge}")
        return _ok("\n".join(head + rows))
    return itemize_and_launch


class AppendInboxItemArgs(TypedDict, total=False):
    item_id: Required[Annotated[int, "the existing inbox item's id to augment"]]
    addition: Required[Annotated[str, ("the NEW, on-point content from this discussion to append — "
                                       "what the existing item doesn't already cover. Never a rewrite; "
                                       "the existing text is preserved and this is added under it")]]


def _append_inbox_item(*, store, context_id, dev_root=None, **_):
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
        # Mirror the append onto the handoff brief when one exists (D5: append, never rewrite).
        if dev_root:
            from pathlib import Path
            from ...core import artifacts as _arts
            from ...core.inbox_flow import inbox_content_dir
            folder = inbox_content_dir(Path(dev_root), row["id"])
            if (folder / "handoff-brief.md").exists():
                _arts.write_handoff_brief(folder, row.get("title") or "", discussion=addition)
        return _ok(f"Appended to inbox item #{row['id']} — \"{row.get('title') or ''}\" "
                   f"(existing content untouched; origin now {', '.join(row.get('origin') or [])}).")
    return append_inbox_item


# --------------------------------------------------------------------------- work-item artifacts (S2)
# The D5-playbook write tools: code supplies form (scaffold/renderer), the agent supplies content.
# All three need `dev_root` (the item folders) and take `repo_dir` for git-state/freshness; both
# are threaded as deps from the ws turn. S5 scopes these to work-item sessions (worker tool-scoping);
# until then they live in the main dev set — they can only touch a work-item's OWN folder.

def _item_dir(dev_root, item_id: str):
    from pathlib import Path
    if not dev_root:
        return None
    d = Path(dev_root) / "work-items" / str(item_id)
    return d if (d / "item.md").exists() else None


def _bound_err(item_id, bound_item_id) -> str | None:
    """Worker tool-scoping (S5/D9, hermes): a work-item session operates ONLY its own item — no
    cross-item writes; a session with no bound item has no item write-tools at all. Returns the
    refusal text, or None when the call is in scope. (Kernel gate actions — close, merge, push —
    have no tool counterparts at all: enforcement by absence.)"""
    if bound_item_id is None:
        return ("Work-item tools operate only inside a work-item session. This session has no "
                "bound item — if this work is real, itemize it (create_inbox_item) and do it in "
                "the item's own session.")
    if str(item_id) != str(bound_item_id):
        return (f"This session is bound to work-item {bound_item_id!r} and may operate only on "
                f"it — cross-item operations aren't allowed. If item {item_id!r} needs work, that "
                f"happens in ITS session.")
    return None


class ScaffoldArtifactArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id (folder name)"]]
    artifact: Required[Annotated[Literal["brief", "plan", "investigation", "review"],
                                 "which artifact skeleton to scaffold"]]


def _scaffold_artifact(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def scaffold_artifact(args: dict) -> dict:
        from ...core import artifacts as _arts
        from ...core.dev_knowledge import _parse_md
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        from pathlib import Path
        from ...core import verification_library as _vl
        meta, _body = _parse_md((d / "item.md").read_text())
        try:
            r = _arts.scaffold(d, _s(args, "artifact"), title=str(meta.get("title") or item_id),
                               item_kind=meta.get("kind"), item_id=item_id,
                               standing=_vl.standing_blocks(Path(dev_root)))
        except KeyError as e:
            return _err(str(e))
        state = "scaffolded" if r["created"] else "already exists (re-scaffold is a no-op)"
        return _ok(f"{r['path']} {state}. Fill the <fill:…> slots in sections: "
                   f"{', '.join(r['sections']) or '(free-form)'} — the self-check at the consuming "
                   f"gate rejects unfilled slots and empty required sections."
                   + (f" {r['inherited']} standing check(s) from this repo's verification library "
                      "are already in the verification plan — leave them as they are."
                      if r.get("inherited") else ""))
    return scaffold_artifact


class SetTriageClassificationArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    title: Required[Annotated[str, ("the item's name as the board should now read it — a few words, "
                                    "under 60 characters, no closing period (e.g. \"Add dark mode "
                                    "support\"). You have read the whole ask, so this is where a "
                                    "weak name gets fixed: an owner capturing a ticket often pastes "
                                    "the request itself. Pass the existing title back unchanged when "
                                    "it already reads well — that is a no-op, not a cost")]]
    kind: Required[Annotated[Literal["implementation", "research"],
                             ("the confirmed kind — implementation (changes code; worktree + "
                              "vet/review pipeline) or research (answers questions; "
                              "read-only on code, findings instead of merges)")]]
    deliverable: Annotated[str, ("the deliverable this item anchors to: an existing `d-<slug>` "
                                 "from the project PRD, or omit/'none' for a standalone chore. "
                                 "NEVER invent a new slug here — a NEW deliverable is proposed in "
                                 "prose for the owner to confirm first")]


def _set_triage_classification(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def set_triage_classification(args: dict) -> dict:
        """Triage's RECORDING surface (D1/D3): write the item's name + proposed kind + deliverable
        onto the item so the triage-exit gate confirms durable fields, not chat prose. Triage phase
        only — after the gate the kind is fixed (post-triage needs are branch-offs, never re-kinds).

        Naming rides with classifying because they are the same act by the same reader: triage is
        the one phase that has read the whole ask, and the mint points upstream of it cannot always
        name well (an owner's inbox capture may carry no title at all, in which case the item was
        born holding the first sentence of the request)."""
        from pathlib import Path
        from ...core.dev_knowledge import DevKnowledgeService, _parse_deliverables
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        dev = DevKnowledgeService()
        root = Path(dev_root)
        item = dev.read_work_item(root, item_id) or {}
        if str(item.get("phase")) != "triage":
            return _err("Kind/deliverable are fixed after the triage-exit gate — this item is "
                        f"already in `{item.get('phase')}`. A post-triage need is a branch-off "
                        "(create_inbox_item), never a re-kind.")
        title = _s(args, "title")
        try:
            renamed = dev.set_work_item_title(root, item_id, title)
        except ValueError as e:
            return _err(str(e))
        kind = _s(args, "kind")
        try:
            dev.set_work_item_kind(root, item_id, kind)
        except KeyError as e:
            return _err(str(e))
        deliverable = _s(args, "deliverable")
        d_note = ""
        if deliverable and deliverable.lower() != "none":
            known = {x["id"] for x in _parse_deliverables(
                dev.read_general_doc(root, "project-prd") or "")}
            if deliverable not in known:
                return _err(f"Deliverable {deliverable!r} isn't in the project PRD "
                            f"(known: {', '.join(sorted(known)) or 'none'}). Propose a NEW "
                            "deliverable in prose for the owner to confirm — never record an "
                            "unconfirmed slug.")
            dev.set_work_item_scaffold(root, item_id, deliverable=deliverable)
            d_note = f" · deliverable `{deliverable}`"
        # F1: the triage-exit gate's `triage_ran` reads this stamp — recording a classification
        # IS triage having run; a bare inbox push (kind default + body copied) never stamps it.
        dev.set_work_item_triaged(root, item_id)
        t_note = f" · renamed to \"{title}\"" if renamed else ""
        return _ok(f"Recorded triage classification: kind `{kind}`{d_note}{t_note}. The owner "
                   "confirms at the triage-exit gate (Approve → plan); until then it stays a "
                   "proposal on the item's own fields.")
    return set_triage_classification


class DryRunChecksArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]


def _dry_run_checks(*, store, context_id, dev_root=None, repo_dir=None, bound_item_id=None, **_):
    async def dry_run_checks(args: dict) -> dict:
        """Smoke-test the `run:` blocks already written into this item's plan. Records nothing."""
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        if repo_dir is None:
            return _err("no repo to run in from this session.")
        from ...daemon.services import checks as _checks
        try:
            rows = await asyncio.to_thread(_checks.dry_run, d, repo_dir)
        except Exception as e:                       # a dry run must never take the turn down
            return _err(f"dry run could not complete: {e}")
        if not rows:
            return _ok("nothing to dry-run — no check in this plan carries a `run:` block "
                       "(or this host has no sandbox).")
        return _ok("\n".join(f"{r['check']}: {r['result']}" for r in rows)
                   + "\n\nA failing assertion is EXPECTED — the work is not built yet. What you "
                     "are looking for is a command that could not run at all (usage error, import "
                     "error, wrong path): that one will never come back green, whatever build does.")
    return dry_run_checks


class RecordValidationArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    command: Required[Annotated[str, ("the command you ran, verbatim and re-runnable from the "
                                      "worktree root — vet re-executes this exact string to audit "
                                      "the claim, so a paraphrase makes the record uncheckable")]]
    result: Required[Annotated[str, "the MACHINE result — exit code, counts, output tail"]]
    passed: Required[Annotated[bool, "did it pass"]]
    task: Annotated[str, "the plan task id this run defends, when it defends exactly one (`t3`)"]


def _record_validation(*, store, context_id, dev_root=None, repo_dir=None,
                       bound_item_id=None, **_):
    async def record_validation(args: dict) -> dict:
        """Build's own self-check, recorded as data instead of prose. Vet audits these on its pass:
        it re-runs each command and compares, so a green that was never earned goes back into the
        loop as a finding rather than reaching the owner as a sentence."""
        from ...core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            e = _arts.record_validation(d, repo_dir, command=_s(args, "command") or "",
                                        result=_s(args, "result") or "",
                                        passed=bool(args.get("passed")),
                                        task=_s(args, "task") or "")
        except (ValueError, OSError) as ex:
            return _err(str(ex))
        return _ok(f"recorded — `{e['command']}` {'passed' if e['passed'] else 'FAILED'} "
                   f"(cycle {e['cycle']}). Vet re-runs this exact command to audit the claim.")
    return record_validation


class RecordVerificationArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    check: Required[Annotated[str, "the verification-plan check id, verbatim (it keys the ledger)"]]
    how: Required[Annotated[str, "the exact command / procedure that ran"]]
    result: Required[Annotated[str, "the MACHINE result — exit code, counts, output tail"]]
    passed: Required[Annotated[bool, "did the check pass (false when deferred)"]]
    note: Annotated[str, "for a failure: expected vs actual in one line"]
    deferred: Annotated[bool, ("set true for a check the BUILD deferred to the owner (a needs-you "
                               "item pending authorization at review): it is recorded as an "
                               "intentional skip, not a failure — you did NOT run it")]
    met: Annotated[list[str], ("rubric checks only: the plan's criteria this build MEETS, verbatim. "
                               "Every criterion the plan lists must appear in met or missed")]
    missed: Annotated[list[str], ("rubric checks only: the criteria it does NOT meet. Any missed "
                                  "criterion means passed=false — a rubric is the bar, not a score")]


def _record_verification(*, store, context_id, dev_root=None, repo_dir=None,
                         bound_item_id=None, **_):
    async def record_verification(args: dict) -> dict:
        from ...core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            e = _arts.record_verification(d, repo_dir, check=_s(args, "check"),
                                          how=_s(args, "how"), result=_s(args, "result"),
                                          passed=bool(args.get("passed")),
                                          deferred=bool(args.get("deferred")),
                                          note=_s(args, "note"),
                                          met=args.get("met") or [],
                                          missed=args.get("missed") or [])
        except ValueError as err:
            return _err(str(err))
        verdict = _arts.evidence_status(d, repo_dir)
        return _ok(f"Recorded: {e['check']} · passed={e['passed']} · cycle {e['cycle']} · "
                   f"fingerprint={e['fingerprint']}. Derived verdict now: {verdict['status']} "
                   f"({verdict['entries']} entries). Evidence goes STALE on any further repo edit — "
                   f"re-run checks after changes; never claim verified without a fresh green record.")
    return record_verification


class RecordDiagnosisArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    check: Required[Annotated[str, "the FAILING check's plan id, verbatim"]]
    where: Required[Annotated[str, ("the narrowest located source — file:line, the failing frame, "
                                    "the request that errored. Not 'in the parser' when you know "
                                    "which line")]]
    why: Required[Annotated[str, ("the mechanism, as far as the evidence supports it — what "
                                  "actually happens, not what should have")]]
    unknown: Annotated[str, ("what you could NOT determine, if anything. An honest gap beats a "
                             "confident guess: it tells the next build cycle where you did not "
                             "look")]


def _record_diagnosis(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def record_diagnosis(args: dict) -> dict:
        from ...core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            r = _arts.record_diagnosis(d, check=_s(args, "check"), where=_s(args, "where"),
                                       why=_s(args, "why"), unknown=_s(args, "unknown"))
        except ValueError as err:
            return _err(str(err))
        return _ok(f"Diagnosis recorded for {r['check']} (cycle {r['cycle']}). It reaches the next "
                   "build cycle as its work order — do not add the fix; build reasons that out "
                   "inside the current plan.")
    return record_diagnosis


class RecordLensArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    lens: Required[Annotated[str, "intent | safety | robustness | performance"]]
    probed: Required[Annotated[list[str], ("what you actually examined or tried through this lens, "
                                           "ONE PROBE PER ENTRY — an input you tried, a path you "
                                           "read, a command you ran, each with its outcome. Not a "
                                           "paragraph: the owner reads this list to see what was "
                                           "actually checked. A clean pass is a real answer, and "
                                           "this is what makes it one")]]
    findings: Annotated[list[dict], ("what the lens found, each {severity: low|medium|high, text}. "
                                     "Empty is expected and correct when there is nothing — never "
                                     "manufacture one. Name where in the text")]


def _record_lens(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def record_lens(args: dict) -> dict:
        from ...core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            r = _arts.record_lens(d, lens=_s(args, "lens"),
                                  probed=args.get("probed") or [],
                                  findings=args.get("findings") or [])
        except ValueError as err:
            return _err(str(err))
        n = len(r["findings"])
        return _ok(f"{r['lens']} lens recorded (cycle {r['cycle']}): "
                   + (f"{n} finding(s)" if n else "nothing found")
                   + (" — this gates, so the item goes back to build" if r["gates"] else ""))
    return record_lens


class NominateCheckArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    check: Required[Annotated[str, "the verification-plan check id to nominate"]]
    general: Required[Annotated[str, ("what makes it general — the property of THIS REPO it "
                                      "defends, said without mentioning this item. If you cannot "
                                      "say it that way, it isn't a library entry")]]


def _nominate_check(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def nominate_check(args: dict) -> dict:
        from ...core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            r = _arts.record_nomination(d, check=_s(args, "check"), general=_s(args, "general"))
        except ValueError as err:
            return _err(str(err))
        return _ok(f"{r['check']} nominated for this repo's verification library. Close writes it "
                   "in as an AVAILABLE entry — the owner decides whether it becomes standing.")
    return nominate_check


class ReadVerificationLibraryArgs(TypedDict, total=False):
    item_id: Annotated[str, "the work-item id — include it at close to also get this item's "
                            "nominations rendered as ready-to-write entries"]


def _read_verification_library(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def read_verification_library(args: dict) -> dict:
        from pathlib import Path
        from ...core import artifacts as _arts
        from ...core import verification_library as _vl
        lib = _vl.read_library(Path(dev_root))
        out = []
        for tier in _vl.TIERS:
            rows = lib[tier]
            out.append(f"## {tier} ({len(rows)})")
            out.extend(f"- `{e['id']}` — {e['proves'] or e['scenario'] or e['traces']}"
                       for e in rows)
            if not rows:
                out.append("- (none)")
        item_id = _s(args, "item_id")
        d = _item_dir(dev_root, item_id) if item_id else None
        if d is not None and not _bound_err(item_id, bound_item_id):
            noms = _arts.nominations(d)
            checks = {c["id"]: c for c in _arts.parse_vet_plan(
                (d / "artifacts" / _arts.artifact_file("plan")).read_text()).get("checks", [])}
            blocks = [_vl.render_entry(checks[cid]) for cid in noms if cid in checks]
            if blocks:
                out.append("\n## nominated by this item — write each as an `append` op on "
                           f"doc `{_vl.LIBRARY_DOC}`, section `Available`")
                out.extend(f"\n```\n{b.rstrip()}\n```" for b in blocks)
        return _ok("\n".join(out))
    return read_verification_library


class RequestAuthorizationArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    what: Required[Annotated[str, "the contract change you can't self-authorize, one line "
                                  "(e.g. 'retire the legacy spec.md doc')"]]
    why: Required[Annotated[str, "one line: why it's needed and why it's above your pay grade"]]
    doc: Required[Annotated[str, "the anchor doc it touches (project-prd|architecture|capabilities|"
                                 "decisions|roadmap|resources|spec)"]]
    scope: Required[Annotated[
        Literal["doc-sync", "rename-to-shipped", "roadmap-mark-done",
                "prd-identity", "roadmap-scope", "new-decision", "doc-delete"],
        "the authorization scope, matched against the deputy's delegated authority: sync-to-reality "
        "scopes (doc-sync · rename-to-shipped · roadmap-mark-done) are delegable; intent-defining "
        "scopes (prd-identity · roadmap-scope · new-decision · doc-delete) are owner-reserved"]]
    check: Annotated[str, "the vet-plan check id this blocks — so vet DEFERS it instead of failing it"]


def _request_authorization(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def request_authorization(args: dict) -> dict:
        from ...core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        item = {}
        try:
            from ...core.dev_knowledge import DevKnowledgeService as _DK
            item = _DK().read_work_item(dev_root, item_id) or {}
        except Exception:
            pass
        # The declared scope is checked against the STAGED OPS, not taken on trust (§2.1): claiming
        # a delegable `doc-sync` for ops that rewrite what the project IS would route an
        # intent change past the owner to a delegated deputy. Refuse with the scope to use instead.
        try:
            from ...core import knowledge_delta as _kd
            staged = (_kd.read_delta(d) or {}).get("ops") or []
        except Exception:
            staged = []
        if (mismatch := _arts.scope_mismatch(_s(args, "scope"), staged)):
            return _err(f"Authorization refused — {mismatch}")
        try:
            a = _arts.record_authorization(
                d, what=_s(args, "what"), why=_s(args, "why"), doc=_s(args, "doc"),
                scope=_s(args, "scope"), check=_s(args, "check"),
                phase=str(item.get("phase") or ""))
        except ValueError as err:
            return _err(str(err))
        return _ok(f"Authorization requested: {a['what']} (scope {a['scope']}). The blocked vet "
                   f"check now DEFERS — do NOT edit the vet plan and do NOT force the change through. "
                   f"Complete everything else and report. The request rides to REVIEW, where the "
                   f"owner (or a delegated deputy) grants or denies; a grant routes back to you to "
                   f"perform it, a denial accepts the gap on the record.")
    return request_authorization


class FilePlanReportArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    summary: Required[Annotated[str, ("one line, plain words — what is going to happen. The "
                                      "dashboard shows this line on its own, so it must stand "
                                      "without the rest of the report")]]
    approach: Annotated[str, ("the plan in the owner's terms, bullets — implementation: what "
                              "changes and what deliberately does not; research: what we are "
                              "trying to find out. A small ASCII flow in a fence when it carries "
                              "the meaning better than words")]
    confirm: Annotated[str, ("implementation: what the checks will NOT tell you, one short "
                             "paragraph under the confirmation table (the table itself is "
                             "derived); research: how we will look and what we will not, since a "
                             "research item has no table")]
    decisions: Annotated[str, ("one per line, each tagged (Owner) or (Agent), from plan.md "
                               "## Decisions & clarifications. Omit if none were made")]
    assumptions: Annotated[str, ("one per line, each tagged (Owner) or (Agent) — what this plan "
                                 "takes for granted and would have to revisit. Omit if none")]


def _file_plan_report(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def file_plan_report(args: dict) -> dict:
        """The plan gate's user report (`reports/report-plan.md`): the confirmation table is
        DERIVED from the verification plan's checks — each row is a check's own `proves:` line and
        how it will be run — and so are the stats; the agent supplies only the prose. A task
        nothing defends is named under the table."""
        from ...core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        kind = None
        try:
            from ...core.dev_knowledge import _parse_md
            meta, _b = _parse_md((d / "item.md").read_text())
            kind = meta.get("kind")
        except Exception:
            pass
        try:
            r = _arts.write_plan_user_report(
                d, summary=_s(args, "summary"), approach=_s(args, "approach"),
                confirm=_s(args, "confirm"), decisions=_s(args, "decisions"),
                assumptions=_s(args, "assumptions"), item_kind=kind)
        except (ValueError, OSError) as e:
            return _err(str(e))
        gaps = r["uncovered"]
        return _ok(f"{r['path']} written — {r['tasks']} task(s), {r['checks']} check(s)."
                   + (f" {len(gaps)} task(s) have NO check ({', '.join(gaps)}): either add one or "
                      "be ready to say at the gate why that task needs no proof."
                      if gaps else " Every task is defended."))
    return file_plan_report


class FileVetReportArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    summary: Required[Annotated[str, ("one line — what this pass establishes, in the owner's "
                                      "terms. The dashboard shows it alone, so it must stand "
                                      "without the report around it")]]
    confirms: Required[Annotated[str, ("`## What this confirms` — a bullet per thing that is now "
                                       "known to be true, in the product's words, not the check's. "
                                       "Do NOT re-list the checks: the Task tab carries them, and "
                                       "a second list makes your independent pass read like "
                                       "build's self-report")]]
    looked_at: Required[Annotated[str, ("`## What else was looked at` — the lenses in plain "
                                        "language: what question you asked, what you probed, and "
                                        "what came of it. A lens that found nothing still earns "
                                        "its bullet; what you probed is the evidence the question "
                                        "was asked")]]
    unknown: Annotated[str, ("`## What I can't tell you` — one line and a short reason, for what "
                             "this pass could not settle and why. Omit only when there is "
                             "genuinely nothing, which is rare")]


def _file_vet_report(*, store, context_id, dev_root=None, repo_dir=None, bound_item_id=None, **_):
    async def file_vet_report(args: dict) -> dict:
        """The vet cycle's user report (`reports/report-vet.md`) — HYBRID: you write the narrative,
        code writes `## What didn't hold` off the recorded §Verification entries so a failure
        reaches the owner whatever the prose says. Refused while any plan check has no recorded
        entry, a standing lens has no read this cycle, or a failing check has no diagnosis."""
        from ...core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            r = _arts.write_vet_user_report(
                d, repo_dir, summary=_s(args, "summary") or "",
                confirms=_s(args, "confirms") or "", looked_at=_s(args, "looked_at") or "",
                unknown=_s(args, "unknown") or "")
        except ValueError as err:
            return _err(f"Vet report refused — {err}")
        store.log_event(context_id, "vet.report",
                        f"Vet report filed: verdict {r['verdict']}"
                        + (f" · failing: {', '.join(r['failed'])}" if r["failed"] else ""),
                        item_id=item_id, actor="agent",
                        meta={"verdict": r["verdict"], "failed": r["failed"]})
        return _ok(f"Vet report filed: {r['path']} (derived verdict: {r['verdict']}). This "
                   "session's job is done — do not attempt fixes; the loop hands failures back "
                   "to the build session.")
    return file_vet_report


class WriteCheckpointArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    working_on: Required[Annotated[str, "what is being worked on right now"]]
    decisions: Annotated[str, "decisions made + tradeoffs/leans — the reasoning a transcript loses"]
    remaining: Required[Annotated[str, "what remains, concretely — next session starts here"]]
    notes: Annotated[str, "tried-but-failed, gotchas, anything else worth carrying"]


def _write_checkpoint(*, store, context_id, dev_root=None, repo_dir=None, bound_item_id=None, **_):
    async def write_checkpoint(args: dict) -> dict:
        from ...core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        # The banking THREAD, derived from the item's phase — never asked of the agent, which has
        # no reason to know its own session role. Continuity reads filter on it.
        role = None
        try:
            from ...core import kind_profiles as _kp, dev_knowledge as _dk  # noqa: F401
            from ...daemon import app_state as _app
            it = _app.dev.read_work_item(dev_root, item_id) or {}
            role = _kp.session_role(str(it.get("phase") or "triage"))
        except Exception:
            pass
        try:
            p = _arts.write_checkpoint(d, repo_dir, working_on=_s(args, "working_on"),
                                       decisions=_s(args, "decisions"),
                                       remaining=_s(args, "remaining"), notes=_s(args, "notes"),
                                       role=role)
        except ValueError as err:
            return _err(str(err))
        return _ok(f"Checkpoint banked: {p} (append-only; the next session's cold start reads the "
                   f"newest one). Reference artifacts by path — never duplicate their content here.")
    return write_checkpoint


class SyncFromMainArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id (must have a live worktree — build phase)"]]


def _sync_from_main(*, store, context_id, dev_root=None, main_repo_dir=None, bound_item_id=None,
                    spine=None, **_):
    async def sync_from_main(args: dict) -> dict:
        from pathlib import Path
        from ...core import git_layer as _gl
        from ...core.dev_knowledge import _parse_md
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        meta, _body = _parse_md((d / "item.md").read_text())
        wt = meta.get("git_worktree")
        if not wt or not Path(str(wt)).is_dir():
            return _err("This item has no live worktree — sync applies only during build "
                        "(the worktree is created at build entry).")
        try:
            # Sync from the repo's ANCHOR, not whatever git calls the default branch — a repo
            # anchored on `develop` must not pull `main` into an item branch mid-build.
            rc = spine.repo(context_id) if spine else None
            res = _gl.sync_from_main(main_repo_dir or Path(str(wt)), Path(str(wt)),
                                     target=rc.anchor_branch if rc else None)
        except (_gl.GitError, _gl.GitBusy) as e:
            return _err(str(e))
        if res.get("up_to_date"):
            return _ok("Already up to date with the trunk — nothing to merge.")
        if res.get("conflicts"):
            return _err("Sync hit conflicts (merge aborted, your tree is unchanged): "
                        + ", ".join(res["conflicts"]) +
                        ". Commit your work, then resolve by re-running the merge yourself in the "
                        "worktree (`git merge <trunk>`, fix markers, commit) — or report the "
                        "conflict for the owner's Resolve-with-Agent action.")
        return _ok(f"Trunk merged into the item branch at {res['commit'][:10]}. Re-run your "
                   f"validation checks — evidence recorded before this merge is now stale.")
    return sync_from_main


class KnowledgeOpArg(TypedDict):
    doc: Annotated[Literal["project-prd", "architecture", "capabilities", "decisions",
                           "roadmap", "resources", "verification"],
                   "which anchor doc this op edits (the retired `spec` is read-only — its "
                   "content lives in architecture/decisions now). `verification` is close's "
                   "library write and nothing else's: append a vet nomination under `Available`, "
                   "never under `Standing` (promoting is the owner's call alone)"]
    section: Annotated[str, "the exact `## heading` text the op targets (must exist in the doc)"]
    op: Annotated[Literal["update", "append", "supersede", "rename_section"],
                  "update/supersede replace the section body; append extends it; rename_section "
                  "rewrites the `## heading` LINE itself (content = the new heading text)"]
    content: Annotated[str, ("for body ops: the section BODY markdown only — do NOT repeat the "
                             "`## <section>` heading (the writer keeps it; a repeated heading is "
                             "stripped). For rename_section: the new heading TEXT, one line, no `##`")]


class ApplyKnowledgeDeltaArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    ops: Required[Annotated[list[KnowledgeOpArg],
                            ("the edit ops — validated, then written to the anchor docs and "
                             "recorded in this week's change log. A rejection writes nothing")]]


def _apply_knowledge_delta(*, store, context_id, dev_root=None, repo_dir=None,
                           bound_item_id=None, **_):
    async def apply_knowledge_delta(args: dict) -> dict:
        from pathlib import Path
        from ...core import knowledge_delta as _kd
        from ...core import verification_library as _vl
        from ...core.dev_knowledge import DevKnowledgeService as _DK
        from ...core.kind_profiles import get_profile, is_final_phase
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        item = _DK().read_work_item(Path(dev_root), item_id) or {}
        if not get_profile(item.get("kind")).knowledge_writes:
            return _err("This item's kind never writes general dev-knowledge (D7). The anchor "
                        "docs describe what is IN the main tree, so they change only when code "
                        "does. Nothing this item concludes has been implemented yet — its "
                        "conclusions belong in its own report, and reach the docs later via the "
                        "work that acts on them.")
        # Close is the ONLY writing moment (renovation §2.3): before it, the owner has not yet
        # locked the code, so a doc written earlier could describe something that never lands.
        if not is_final_phase(item.get("kind"), item.get("phase") or "triage"):
            return _err(f"The anchor docs are written at CLOSE — this item is at "
                        f"`{item.get('phase')}`. Until the merge locks the code there is nothing "
                        f"true to write about it yet.")
        # A blocking child merged into its PARENT's branch, so its content is not on main until
        # the parent lands. The parent's close speaks for the family.
        sf = item.get("spawned_from") or {}
        if isinstance(sf, dict) and sf.get("relation") == "blocking":
            return _err(f"This is a blocking child of `{sf.get('item')}` — its work landed on the "
                        f"parent's branch, not on main, so the anchor docs cannot describe it yet. "
                        f"The parent's close writes for the family; note what it should say in "
                        f"your close report.")
        ops = args.get("ops")
        if isinstance(ops, str):
            try:
                ops = json.loads(ops) if ops.strip() else None
            except (ValueError, TypeError) as e:
                return _err(f"`ops` must be a JSON array of edit ops: {e}")
        # The verification library is created the first time a close has something to put in it —
        # a repo that predates the library, or one that has never proven a general check, simply
        # doesn't have the doc yet, and validation would otherwise reject the op for that.
        if any(isinstance(o, dict) and o.get("doc") == _vl.LIBRARY_DOC for o in (ops or [])):
            _vl.seed(Path(dev_root))
        issues = _kd.validate_ops(ops, Path(dev_root), repo_dir)
        if issues:
            return _err("Delta rejected — NOTHING was written. Fix and re-apply:\n- "
                        + "\n- ".join(issues))
        try:
            res = _kd.apply_ops(Path(dev_root), ops)
        except (ValueError, OSError) as e:
            return _err(f"Write failed, docs unchanged: {e}")
        log_path = _kd.append_change_log(Path(dev_root), item_id,
                                         str(item.get("title") or ""), ops)
        store.log_event(context_id, "knowledge.applied",
                        f"Anchor docs updated at close ({res['applied']} op(s) → "
                        f"{', '.join(res['docs'])})",
                        item_id=item_id, actor="agent",
                        meta={**res, "change_log": log_path})
        return _ok(f"Written: {res['applied']} op(s) → {', '.join(res['docs'])}, and this week's "
                   f"change log has the entry. Say in your close report what the docs now claim.")
    return apply_knowledge_delta


class PlanOpArg(TypedDict, total=False):
    op: Required[Annotated[Literal["update", "append", "add_task", "edit_task", "remove_task"],
                           "update/append act on a section BODY; the *_task ops act on ONE task "
                           "line in `## Tasks` (its `- [x]` state survives an edit)"]]
    section: Annotated[str, "section ops only: the exact `## heading` text (must already exist)"]
    task: Annotated[str, "edit_task/remove_task only: the task id, e.g. `t3`"]
    content: Annotated[str, ("the new text — a section BODY (no `## heading`) for section ops, the "
                             "task text (no `- [ ] t<n> —` prefix) for add_task/edit_task; omit "
                             "for remove_task")]


class PlanChangeArg(TypedDict, total=False):
    area: Required[Annotated[str, ("what this change answers, in a few words (`caching design`, "
                                   "`cli tasks`) — a concern with no change is a dropped concern, "
                                   "and this is what makes that visible")]]
    scope: Required[Annotated[Literal["resume", "targeted", "redesign"],
                              "resume = the plan was right; run another generation against it "
                              "unchanged (NO ops — an edit here is refused) · targeted = right in "
                              "approach, wrong in places · redesign = the approach itself was "
                              "wrong; rewrite it and remove the void tasks explicitly"]]
    note: Required[Annotated[str, "one line: what changed here, or why nothing needed to"]]
    ops: Annotated[list[PlanOpArg], ("this change's edits — required except at `resume`, where "
                                     "they are refused")]
    superseded: Annotated[str, ("redesign only: what prior work is void and what build must undo "
                                "(forward, with new commits — never a reset)")]


class RevisePlanArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    feedback: Required[Annotated[str, ("the feedback driving this revision, VERBATIM — the "
                                       "owner's (or deputy's) words, never your paraphrase")]]
    directive: Required[Annotated[str, ("what the next build does DIFFERENTLY because of this "
                                        "revision — the one line it acts on")]]
    still_in_force: Required[Annotated[str, ("what earlier revisions still bind (`nothing` on the "
                                             "first). Build reads the newest block; this is what "
                                             "makes that honest")]]
    changes: Required[Annotated[list[PlanChangeArg],
                                "one entry per concern, each with its OWN scope — validated "
                                "first; a refusal writes nothing"]]


def _revise_plan(*, store, context_id, dev_root=None, bound_item_id=None, spine=None, **_):
    async def revise_plan(args: dict) -> dict:
        from ...core import plan_revision as _pr
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        path = _pr.plan_path(d)
        if not path.is_file():
            return _err("This item has no plan.md yet — scaffold and author it first "
                        "(`scaffold_artifact`); a revision edits a plan that already exists.")
        # PLAN PHASE ONLY (§2.1). plan.md is the plan phase's to write, and the review agent is
        # read-only on it — which is exactly why review has `revise`. Caught live on 2026-07-27:
        # the review agent folded owner feedback straight into the plan and stayed at review, so
        # the plan changed with nothing downstream re-running against it. The bound-item check
        # can't catch this — review rides the SAME intake session, so it holds the same binding.
        from ...core.dev_knowledge import _parse_md
        phase = str((_parse_md((d / "item.md").read_text())[0] or {}).get("phase") or "")
        if phase != "plan":
            return _err(
                f"plan.md is the PLAN phase's to write — this item is at `{phase}`. "
                f"If the conversation concluded the plan must change, end your run with "
                f"`report_completion(machine.outcome='revise')` and carry the owner's words in "
                f"`machine.summary`: the item flips to plan in this same thread and the plan turn "
                f"makes the edit, so build and vet re-run against what changed. Editing it here "
                f"would change the contract with nothing downstream re-running against it.")
        feedback = (_s(args, "feedback") or "").strip()
        if not feedback:
            return _err("`feedback` is required — the words that drove this revision are what the "
                        "next build reads first.")
        directive = (_s(args, "directive") or "").strip()
        if not directive:
            return _err("`directive` is required — one line on what the next build does "
                        "DIFFERENTLY. Without it the block records a complaint, not an instruction.")
        still = (_s(args, "still_in_force") or "").strip()
        if not still:
            return _err("`still_in_force` is required — what earlier revisions still bind (say "
                        "`nothing` on the first). Build reads the newest block; this is what keeps "
                        "that honest.")
        changes = args.get("changes")
        if isinstance(changes, str):   # tolerate a JSON string (older skill text / tests)
            try:
                changes = json.loads(changes) if changes.strip() else None
            except (ValueError, TypeError) as e:
                return _err(f"`changes` must be a JSON array of changes: {e}")
        issues = _pr.validate(path.read_text(), changes)
        if issues:
            return _err("Revision rejected — plan.md is unchanged. Fix and re-send:\n- "
                        + "\n- ".join(issues))
        # `concerns` and the spend boundary come from the RECORD, never from the agent (§3-bis.3/.4):
        # the loop's typed exit + the authorization ledger say what drove this, and the meter reading
        # at this instant is what makes the next generation's budget start at zero.
        concerns = _pr.derive_concerns(d)
        spend = spine.item_phase_tokens(context_id, item_id) if spine else 0
        res = _pr.revise(d, changes=changes, feedback=feedback, directive=directive,
                         still_in_force=still, concerns=concerns, spend=spend)
        scopes = sorted({str(c.get("scope") or "") for c in changes})
        store.log_event(context_id, "plan.revised",
                        f"plan.md revised ({res['revision']}, {'/'.join(scopes)}): {feedback[:160]}",
                        item_id=item_id, actor="agent",
                        meta={"revision": res["revision"], "scopes": scopes,
                              "concerns": concerns, "changed": res["changed"]})
        return _ok(f"plan.md revised as {res['revision']} ({res['ops']} op(s)): "
                   + "; ".join(res["changed"])
                   + f". Concerns on record: {', '.join(concerns)} (read off the loop's exit and "
                     f"the authorization ledger — not yours to assert). Every section you didn't "
                     f"name is untouched, `## Tasks` and `## Verification plan` stay last as the "
                     f"live truth, and the block sits above them. This opens a new generation: the "
                     f"loop's budget and recurrence guards restart from here.")
    return revise_plan


_ITEM_DEV_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "set_triage_classification",
        "Record a triage session's classification onto its work-item: the confirmed kind "
        "(implementation | research) and, optionally, an EXISTING PRD deliverable it anchors to. "
        "Triage phase only — after the triage-exit gate the kind is fixed.",
        SetTriageClassificationArgs, _set_triage_classification,
    ),
    ToolSpec(
        "scaffold_artifact",
        "Scaffold a work-item artifact skeleton (brief/plan/investigation) — "
        "code owns the structure, you fill the <fill:…> prose slots.",
        ScaffoldArtifactArgs, _scaffold_artifact,
    ),
    ToolSpec(
        "dry_run_checks",
        "Run the `run:` blocks already written into this item's plan and report each exit code. "
        "Records nothing: at plan time the work does not exist, so a failing assertion is the "
        "expected answer — this catches the command that cannot run AT ALL, before a build⟷vet "
        "cycle is spent discovering it.",
        DryRunChecksArgs, _dry_run_checks,
    ),
    ToolSpec(
        "record_validation",
        "Record one of BUILD's own validation runs — the command, the machine result, pass/fail. "
        "Validation stays yours to run; recording it as data is what lets vet audit the claim "
        "instead of taking a sentence's word for it.",
        RecordValidationArgs, _record_validation,
    ),
    ToolSpec(
        "record_verification",
        "Record one machine-checked verification entry into the current cycle report "
        "(check + how + result + pass/fail; freshness-tracked against the repo state).",
        RecordVerificationArgs, _record_verification,
    ),
    ToolSpec(
        "record_diagnosis",
        "Record WHERE a failed check broke and WHY, plus what you could not determine. "
        "Never the fix: build reasons that out inside the current plan. Required before the vet "
        "report on every failing check, and it becomes the next build cycle's work order.",
        RecordDiagnosisArgs, _record_diagnosis,
    ),
    ToolSpec(
        "record_lens",
        "Record one standing lens's read of this cycle — what you probed, and what it found "
        "(nothing is a fine answer; never manufacture a finding). intent and safety gate on any "
        "finding, robustness on a high one, performance never. Every cycle owes all three standing "
        "lenses before the vet report will write.",
        RecordLensArgs, _record_lens,
    ),
    ToolSpec(
        "nominate_check",
        "Nominate one of this item's checks for the repo's VERIFICATION LIBRARY — the catalogue "
        "later items inherit from. Only a check that has actually passed here, and only when you "
        "can say what it defends about the REPO without mentioning this item. You nominate; close "
        "writes it in; the owner decides whether it becomes standing.",
        NominateCheckArgs, _nominate_check,
    ),
    ToolSpec(
        "read_verification_library",
        "Read this repo's verification library: the standing entries (already attached to every "
        "plan) and the available ones a plan can cite by id instead of re-authoring. At close, "
        "pass item_id to also get this item's nominations rendered as ready-to-write entries.",
        ReadVerificationLibraryArgs, _read_verification_library,
    ),
    ToolSpec(
        "request_authorization",
        "Request authorization for a contract change you can't self-authorize (an owner-reserved "
        "anchor-doc edit). The blocked vet check DEFERS instead of failing — never edit the vet "
        "plan or force the change. It surfaces at review, where the owner or a delegated deputy "
        "grants (routes back to you) or denies (accepts the gap).",
        RequestAuthorizationArgs, _request_authorization,
    ),
    ToolSpec(
        "file_plan_report",
        "File the plan gate's user report (plan phase, once the plan is finished): the coverage "
        "matrix — every task and the checks that will prove it — plus the depth call and the "
        "stats are derived from plan.md; you supply only the prose. A task with no check shows "
        "as a gap, which is the point.",
        FilePlanReportArgs, _file_plan_report,
    ),
    ToolSpec(
        "file_vet_report",
        "File the verification report (vet phase, after every check is recorded): the verdict "
        "and check table are derived from the recorded entries; you supply only the "
        "observations (real concerns, never fixes).",
        FileVetReportArgs, _file_vet_report,
    ),
    ToolSpec(
        "write_checkpoint",
        "Bank a session-continuity checkpoint onto a work-item (working-on / decisions / "
        "remaining / notes) — what a fresh session cold-starts from.",
        WriteCheckpointArgs, _write_checkpoint,
    ),
    ToolSpec(
        "sync_from_main",
        "Freshness merge for a work-item's git worktree: merge the trunk INTO the item branch "
        "(run during long builds and before delivering; requires a clean tree — commit first).",
        SyncFromMainArgs, _sync_from_main,
    ),
    ToolSpec(
        "apply_knowledge_delta",
        "Write this item's changes into the general dev-knowledge anchor docs (structured edit "
        "ops), and record the entry in this week's change log. Close-phase only, and the only way "
        "those docs ever change — never edit them directly.",
        ApplyKnowledgeDeltaArgs, _apply_knowledge_delta,
    ),
    ToolSpec(
        "revise_plan",
        "Fold review feedback into an EXISTING plan.md — the only way a re-plan changes it. One "
        "entry per concern, each with its own scope (resume | targeted | redesign), so redesigning "
        "one part never resets the progress another part earned. Never rewrite the file: `## Tasks` "
        "takes task-level ops so the `- [x]` build earned survives. Appends the revision block the "
        "next build reads first, and opens a fresh build⟷vet generation.",
        RevisePlanArgs, _revise_plan,
    ),
]


# --------------------------------------------------------------------------- the registry
# Two tiers. The MAIN set is what a normal dev chat turn gets: read activity, read the inbox, and the
# two SANCTIONED itemize writes. The LEARNING set is the pipeline sub-agents' pens (capture / distill
# / forge) — they must NOT reach the main chat agent, or it will short-circuit the automatic
# capture→distill→forge flow by filing candidates/proposals itself (there is no chat-side learning
# surface by design). Those tools are added only for the disposable background runs (`learning=True`).

# Read-only tools available to EVERY dev turn (main chat + learning runs). Naming: every read is
# `read_*` so the tool surface is scannable. The learning-pool reads (`read_candidates`/`read_proposals`)
# live here — not in the learning-only set — because they mutate nothing: a general session should be
# able to answer "what learning is pending?" (the UI already shows it). Only the learning WRITE pens
# stay gated (below), so the main agent can't file learnings by hand and bypass automatic sweep-capture.
_MAIN_DEV_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "read_dev_log",
        "This repo's dev activity log — the cross-run record of what's happened in its dev work over "
        "time (agent runs, learning-pipeline steps, inbox & work-item changes, constitution/asset "
        "edits), newest first.",
        DevLogArgs, _dev_log,
    ),
    ToolSpec(
        "read_inbox",
        "Read this repo's inbox — captured items awaiting triage/routing into real work, open first.",
        InboxArgs, _list_inbox,
    ),
    ToolSpec(
        "read_candidates",
        "Read the operational-learning candidate pool (what capture has filed), newest first.",
        ReviewCandidatesArgs, _review_candidates,
    ),
    ToolSpec(
        "read_proposals",
        "Read the OPEN operational-learning proposals (consolidated from the candidate pool and "
        "awaiting the owner's gate), newest first.",
        ReviewProposalsArgs, _review_proposals,
    ),
    ToolSpec(
        "read_run",
        "Read one agent run's execution trace — its prompt/reply/tool-call trail + outcome "
        "(pass a run_id) — or omit run_id to list recent runs.",
        ReadRunArgs, _read_run,
    ),
    ToolSpec(
        "create_inbox_item",
        "Create one inbox item (ticket) from a discussion — the sanctioned way to itemize real "
        "work. Also the branch-off front door: pass spawned_from_item + relation "
        "(blocking/parallel auto-push into a child work-item; spawn waits for the owner).",
        CreateInboxItemArgs, _create_inbox_item,
    ),
    ToolSpec(
        "append_inbox_item",
        "Append new discussion content onto an EXISTING inbox item (never edits it) — the dedup path.",
        AppendInboxItemArgs, _append_inbox_item,
    ),
    ToolSpec(
        "itemize_and_launch",
        "Launch a cohort of autopilot work-items from a settled onboarding plan — one call creates "
        "them all, wired by their dependency edges, each born on autopilot and flowing "
        "triage→plan→build⟷vet→review with no human until a review gate. The end-of-onboarding "
        "step; use ONLY after the owner has confirmed the item list.",
        ItemizeAndLaunchArgs, _itemize_and_launch,
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

DEV_TOOLS: list[ToolSpec] = _MAIN_DEV_TOOLS + _ITEM_DEV_TOOLS + _LEARNING_DEV_TOOLS   # full set (for reference/tests)


def make_dev_mcp_server(store, context_id: str, *, learning: bool = False, **deps):
    """Build the `dev` MCP server bound to one context's event store. By default it exposes only the
    MAIN dev tools (the `read_*` reads over the event log · inbox · learning pool, plus the sanctioned
    inbox itemize writes) — a normal chat turn. The disposable learning runs (capture/distill/forge)
    pass `learning=True` to also get the WRITE pens; those must never reach the main chat agent (it
    would bypass automatic capture).

    Optional deps thread per-turn state to specific learning tools (ignored by the rest):
    `origin_session_id` + `capture_source` (provenance bound onto `file_candidate` during a sweep),
    `proposal_id` + `staged_path` (bound onto `stage_artifact` during a write run)."""
    tools = _MAIN_DEV_TOOLS + _ITEM_DEV_TOOLS + (_LEARNING_DEV_TOOLS if learning else [])
    return build_mcp_server("dev", tools, store=store, context_id=context_id, **deps)
