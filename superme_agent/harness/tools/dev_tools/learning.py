"""The learning pipeline's pens: the dev log, capture candidates, and memory proposals."""

import json
from typing import Annotated, Literal, Required, TypedDict

from .render import (_day_range, _err, _fmt, _fmt_candidates, _fmt_proposals, _fmt_run_list,
                     _fmt_run_trace, _ids, _ok, _qid, _s)

# Far-back rows rarely inform a question and cost context, so a no-arg read stays shallow.
_LOG_LEVELS = {"recent": 100, "mid": 300, "max": 500}


# Per-param docs live on the schema and answer "what is this arg", never "when should I call this
# tool".
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
    # The capture sweep saw the moment and later phases did not, so the candidate must be self-
    # sufficient.
    statement: Required[Annotated[str, "what to do — the durable operational learning, stated so it stands alone (1–3 sentences)"]]
    rationale: Annotated[str, "why it matters / what triggered it / the problem it solves"]
    evidence: Annotated[str, "the concrete instance(s) from the slice + a pointer (item id / path / quote)"]
    scope_hint: Annotated[Literal["repo_dev", "universal_dev", "core"],
                          "where the learning applies (default repo_dev). The FORM "
                          "(constitution/skill/agent) is distill's call, not yours — don't classify it."]
    origin_item_id: Annotated[str, "the work-item in scope, if the slice names one"]


class StageArtifactArgs(TypedDict, total=False):
    # Which proposal this belongs to is bound server-side from the write run; the agent supplies
    # only content.
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
    candidate_ids: Required[Annotated[list[int], "the candidate ids to drop, from read_candidates"]]
    reason: Annotated[str, "one short phrase why (e.g. 'self-recitation', 'too-thin') — logged only"]


class ClarificationArg(TypedDict, total=False):
    question: Required[Annotated[str, "the question for the owner, phrased as a question"]]
    suggested: Annotated[str, "the answer you would take if they say nothing"]
    blocking: Annotated[bool, "true when the artifact cannot be written until they answer"]


class ProposeLearningArgs(TypedDict, total=False):
    title: Required[Annotated[str, "short headline"]]
    body: Required[Annotated[str, "the consolidated proposal narrative"]]
    summary: Annotated[str, "purpose · usage · why-raised (one short para; for owner + write phase)"]
    candidate_ids: Annotated[list[int], "the source candidate ids, from read_candidates"]
    output_form: Annotated[Literal["constitution", "skill", "agent"],
                           "the artifact form this proposal targets (default constitution)"]
    target_scope: Annotated[Literal["repo_dev", "universal_dev", "core"],
                            "where the artifact will live (default repo_dev)"]
    fields: Annotated[dict, ("the fields the chosen `output_form` needs — constitution: "
                             "statement, scope, rationale; skill: name, when_to_use, procedure, "
                             "tools, scope; agent: name, role, tools, model, trigger")]
    clarifications: Annotated[list[ClarificationArg],
                              "questions for the owner's gate, one entry each"]
    apply_target: Annotated[str, "drafted destination slug"]
    cluster: Annotated[str, "optional grouping label"]
    confidence: Annotated[Literal["high", "medium", "low"], "how solidly grounded the proposal is"]


class ReviewProposalsArgs(TypedDict, total=False):
    # No args — reads all OPEN proposals in this context (the set distill consolidates against).
    pass


class MergeProposalArgs(TypedDict, total=False):
    proposal_id: Required[Annotated[int, "the existing OPEN proposal to fold the candidate(s) into"]]
    candidate_ids: Required[Annotated[list[int], "the NEW source candidate ids to merge in"]]
    title: Annotated[str, "an enriched headline; omit to keep the existing one"]
    body: Annotated[str, "the re-consolidated narrative, incorporating the new substance"]
    summary: Annotated[str, "refreshed purpose · usage · why-raised"]
    fields: Annotated[dict, "updated form-specific fields, same shape as propose_learning"]
    confidence: Annotated[Literal["high", "medium", "low"],
                          "refreshed confidence — recurrence usually raises it"]


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
            # Three reading depths — `limit` is an exact override.
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
    """Read one run's full trace, or list recent runs — the diagnosis read over the spine's run and
    run_event tables. `spine` is injected by the daemon, which owns it."""
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
    """The capture sweep's pen, called once per learning found in the swept slice.

    Provenance is bound server-side; the agent supplies only substance."""
    async def file_candidate(args: dict) -> dict:
        statement = _s(args, "statement")
        if not statement:
            return _err("Nothing to file — pass `statement` (the operational learning to keep).")
        try:
            row = store.add_memory_candidate(
                context_id, statement,
                source=capture_source,
                # Capture never classifies the form; that needs distill's consolidated, cross-
                # candidate view.
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
    """The write phase's pen, called once with the complete final artifact.

    The proposal and publish path are bound server-side. Staging moves the proposal to `drafted`;
    disk stays untouched until publish."""
    async def stage_artifact(args: dict) -> dict:
        if proposal_id is None:
            return _err("stage_artifact is only available inside a write run.")
        content = _s(args, "content")
        if not content:
            return _err("Nothing to stage — pass `content` (the complete final artifact).")
        # Tolerate a malformed or absent report: the eval is advisory and never blocks staging.
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


def _obj(args: dict, k: str):
    """A structured arg as it arrives, or parsed from JSON text a hand-built call passed."""
    raw = args.get(k)
    if raw in (None, "", [], {}):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (ValueError, TypeError):
        return None


def _propose_learning(*, store, context_id, **_):
    async def propose_learning(args: dict) -> dict:
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
                fields=_obj(args, "fields"),
                clarifications=_obj(args, "clarifications"),
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
    return propose_learning


def _review_proposals(*, store, context_id, **_):
    """Distill's cross-run consolidation lens: the OPEN proposals already standing in this context, so
    a learning captured twice can merge instead of minting a parallel proposal."""
    async def review_proposals(args: dict) -> dict:
        try:
            rows = store.list_memory_proposals(context_id)
        except Exception as e:
            return _err(f"Could not read the proposal pool: {e}")
        open_rows = [r for r in rows if r.get("status") in ("proposed", "writing", "drafted")]
        return _ok(_fmt_proposals(open_rows))
    return review_proposals


def _merge_into_proposal(*, store, context_id, **_):
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
                summary=_s(args, "summary"), fields=_obj(args, "fields"),
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
