"""Learning surface routes: the capture and distill stats, the ops hooks, and the two-gate proposal
review queue.

The pipeline machinery lives in `services/learning.py`; these routes are the thin HTTP gate.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from ...app_state import DevStore, SystemSpine, get_dev_store, get_spine
from ...deps import (needs_credential, proposal_slug as _proposal_slug,
                     published_ident as _published_ident)
from ....gateway import contexts
from ...services.learning import (
    run_background_distill, run_background_write, run_sweep, sweep_idle_sessions,
    SWEEP_IDLE_SECONDS, WRITE_FORMS, WRITE_SCOPES, has_answer,
)
from ...schemas.dev.learning import (ApproveBody, ApproveResponse, ArtifactEditBody,
                                     DistillResponse, IdleScanResponse, LearningRollupResponse,
                                     MemoryStatsResponse, ProposalActionBody,
                                     ProposalActionResponse, ProposalExecutionResponse,
                                     ProposalsResponse, PublishResponse, SweepResponse)

router = APIRouter()


def _count_by(rows: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key) or "—")
        out[k] = out.get(k, 0) + 1
    return out


def _scope_bucket(scope: str | None) -> str:
    """Collapse a stored scope (repo_dev | universal_dev | core | legacy dev) to the dev/core split
    the UI shows: only `core` is core; everything dev-family is dev."""
    return "core" if (scope or "").strip().lower() == "core" else "dev"


@router.get("/dev/memory/stats", response_model=MemoryStatsResponse)
async def dev_memory_stats(context_id: str = "global",
                           dev_store: DevStore = Depends(get_dev_store),
                           spine: SystemSpine = Depends(get_spine)) -> dict:
    """The two pipeline gauges plus drill-down detail: `candidates` is the un-distilled capture pool,
    `knowledge` what has been published.

    Each carries enough detail to render a popup without a second round-trip."""
    from ....core import operational as ops
    cands = dev_store.list_memory_candidates(context_id, status="candidate")
    all_props = dev_store.list_memory_proposals(context_id)   # every status, one read
    # A 4-slot funnel, each stage its own tile, so nothing vanishes between them.
    pending = [p for p in all_props if p.get("status") in ("proposed", "writing")]
    drafted = [p for p in all_props if p.get("status") == "drafted"]
    # Published learned artifacts.
    pub_items = []
    for prop in [p for p in all_props if p.get("status") == "published"]:
        form, scope, repo_id, slug = _published_ident(prop, context_id)
        try:
            st = ops.published_state(form, scope, repo_id, slug)
        except Exception:
            st = {"present": False, "enabled": False}
        if not st["present"]:
            continue
        pub_items.append({"name": prop["title"], "description": prop.get("summary") or "",
                          "type": form, "enabled": st["enabled"], "source": "agent"})
    enabled = [p for p in pub_items if p["enabled"]]
    return {
        "context_id": context_id,
        # Server-truth (repo × mode × feature) — the FE reads this, no local shadow (defect fix).
        "distilling": spine.is_running(context_id, mode="dev", feature="distill"),
        "candidates": {
            "total": len(cands),
            "pending_proposals": len(pending),
            "drafted_proposals": len(drafted),
            "by_form": _count_by(cands, "form_hint"),
            "items": [
                {"id": c["id"], "signal": c["signal"], "form_hint": c.get("form_hint"),
                 "scope_hint": c.get("scope_hint"),
                 "source": c.get("source"), "captured_at": c.get("captured_at")}
                for c in cands
            ],
        },
        # `facts_*` keys kept for FE-contract stability; they now count PUBLISHED artifacts.
        "knowledge": {
            "facts_total": len(pub_items),
            "facts_enabled": len(enabled),
            "facts_disabled": len(pub_items) - len(enabled),
            "facts_by_type": _count_by(pub_items, "type"),
            "artifacts_total": 0,
            "artifacts_reserved": True,
            "items": pub_items,
        },
    }


@router.get("/dev/memory/rollup", response_model=LearningRollupResponse)
async def dev_memory_rollup(dev_store: DevStore = Depends(get_dev_store),
                            spine: SystemSpine = Depends(get_spine)) -> dict:
    """The Learning tile drill-in: per-repo counts of captured candidates + published/learned
    artifacts, each split dev/core. Learned is counted the SAME way `/dev/memory/stats` does
    (published proposals reconciled with on-disk presence) so the numbers reconcile across surfaces."""
    from ....core import operational as ops

    def _blank() -> dict[str, int]:
        return {"dev": 0, "core": 0, "total": 0}

    repos: list[dict] = []
    tot_cand, tot_pend, tot_draft, tot_learn = _blank(), _blank(), _blank(), _blank()
    for repo_id, rc in spine.repos().items():
        cand, pend, draft, learn = _blank(), _blank(), _blank(), _blank()
        for c in dev_store.list_memory_candidates(repo_id, status="candidate"):
            b = _scope_bucket(c.get("scope_hint"))
            cand[b] += 1; cand["total"] += 1
        # Proposals in flight, bucketed by target_scope: pending (proposed/writing) + drafted (gate-2).
        for prop in dev_store.list_memory_proposals(repo_id):
            b = _scope_bucket(prop.get("target_scope"))
            if prop.get("status") in ("proposed", "writing"):
                pend[b] += 1; pend["total"] += 1
            elif prop.get("status") == "drafted":
                draft[b] += 1; draft["total"] += 1
            elif prop.get("status") == "published":
                form, scope, rid, slug = _published_ident(prop, repo_id)
                try:
                    if not ops.published_state(form, scope, rid, slug)["present"]:
                        continue
                except Exception:
                    continue
                lb = _scope_bucket(scope)
                learn[lb] += 1; learn["total"] += 1
        # Skip repos with nothing anywhere — keep the list to where learning actually happened.
        if cand["total"] or pend["total"] or draft["total"] or learn["total"]:
            repos.append({"repo_id": repo_id, "label": rc.label, "candidates": cand,
                          "pending": pend, "drafted": draft, "learned": learn})
        for k in ("dev", "core", "total"):
            tot_cand[k] += cand[k]; tot_pend[k] += pend[k]
            tot_draft[k] += draft[k]; tot_learn[k] += learn[k]

    repos.sort(key=lambda r: (r["candidates"]["total"] + r["pending"]["total"]
                              + r["drafted"]["total"] + r["learned"]["total"]), reverse=True)
    return {"repos": repos, "candidates": tot_cand, "pending": tot_pend,
            "drafted": tot_draft, "learned": tot_learn}


@router.post("/dev/memory/distill", response_model=DistillResponse,
             response_model_exclude_unset=True,
             dependencies=[Depends(needs_credential)])
async def dev_memory_distill(context_id: str = "global",
                             dev_store: DevStore = Depends(get_dev_store),
                             spine: SystemSpine = Depends(get_spine)) -> dict:
    """Fire a background distill pass over the candidate pool.

    One pass per repo and scope at a time, guarded by a spine query rather than a shadow set. Returns
    immediately; poll the stats for completion."""
    if spine.is_running(context_id, mode="dev", feature="distill"):
        return {"status": "already_running", "context_id": context_id}
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no dev-knowledge root")
    cands = dev_store.list_memory_candidates(context_id, status="candidate")
    if not cands:
        return {"status": "no_candidates", "context_id": context_id, "candidates": 0}
    # Open the run row synchronously so it IS the guard, closing the double-click race with the
    # background task.
    run_id = spine.start_run(context_id, mode="dev", feature="distill")
    asyncio.create_task(run_background_distill(ctx, context_id, run_id))
    return {"status": "started", "context_id": context_id, "candidates": len(cands)}


@router.post("/dev/sweep", response_model=SweepResponse, response_model_exclude_unset=True)
async def dev_sweep(session_id: str, context_id: str = "global", focus: str | None = None) -> dict:
    """Ops hook: force a capture sweep over ONE named session, run to completion so the caller gets the
    result.

    The everyday triggers are automatic; this is the by-id escape hatch, and the only caller that
    supplies `focus`."""
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no dev-knowledge root")
    return await run_sweep(ctx, session_id, focus=focus)


@router.post("/dev/sweep/idle-scan", response_model=IdleScanResponse)
async def dev_sweep_idle_scan(idle_seconds: int = SWEEP_IDLE_SECONDS) -> dict:
    """Ops hook: force an idle-scan pass now — the same pass the heartbeat runs — with an optionally
    lower `idle_seconds`."""
    return await sweep_idle_sessions(idle_seconds=idle_seconds)


# --- the review queue and owner gate --- Apply is deterministic and daemon-side, not an agent
# turn.
@router.get("/dev/memory/proposals", response_model=ProposalsResponse, response_model_exclude_unset=True)
async def memory_proposals(context_id: str = "global", status: str | None = None,
                           dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """The review queue: proposals awaiting or past the owner gate, newest first.

    Each is enriched with its source candidates, so the review popup can show provenance without a
    second round-trip."""
    proposals = dev_store.list_memory_proposals(context_id, status=status)
    # Any status: post-distill they are `processed`, so a status filter would hide them.
    by_id = {c["id"]: c for c in
             dev_store.list_memory_candidates(context_id, status=None, limit=1000)}
    _CAND_KEYS = ("id", "signal", "rationale", "evidence", "form_hint",
                  "scope_hint", "status", "source", "captured_at")
    for p in proposals:
        p["candidates"] = [
            {k: by_id[cid].get(k) for k in _CAND_KEYS}
            for cid in (p.get("candidate_ids") or []) if cid in by_id
        ]
    return {"context_id": context_id, "proposals": proposals}


@router.get("/dev/memory/proposals/{proposal_id}/execution", response_model=ProposalExecutionResponse, response_model_exclude_unset=True)
async def memory_proposal_execution(proposal_id: int, context_id: str = "global",
                                    dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """The proposal's execution trace — its lifecycle timeline, reused from the `events` table
    (`meta.proposal_id`): approve → write.start/end (the forge run) → artifact edits → publish. A
    synthetic head marks where distill filed it. Oldest-first; the gate-2 modal's Execution tab."""
    prop = dev_store.get_memory_proposal(proposal_id)
    if not prop or prop["context_id"] != context_id:
        raise HTTPException(status_code=404, detail="proposal not found")
    steps = dev_store.events_for_proposal(context_id, proposal_id)
    # Synthesize a head when there is none, so the trail always starts at "filed".
    if not any(s.get("kind") == "memory.proposed" for s in steps):
        steps.insert(0, {
            "kind": "memory.proposed", "actor": "distill",
            "summary": f"Filed by distill from {len(prop.get('candidate_ids') or [])} candidate(s)",
            "created_at": prop.get("created_at"), "meta": {"proposal_id": proposal_id},
        })
    return {"context_id": context_id, "proposal_id": proposal_id, "status": prop["status"], "steps": steps}


@router.post("/dev/memory/proposals/{proposal_id}/approve", response_model=ApproveResponse)
async def memory_proposal_approve(proposal_id: int, body: ApproveBody,
                                  dev_store: DevStore = Depends(get_dev_store),
                                  spine: SystemSpine = Depends(get_spine)) -> dict:
    """GATE 1 — the owner approves the proposal's INTENT and answers distill's questions.

    Validates the form and scope, records the answers, and fires a background WRITE run that authors
    the artifact and stages it for gate 2."""
    prop = dev_store.get_memory_proposal(proposal_id)
    if not prop or prop["context_id"] != body.context_id:
        raise HTTPException(status_code=404, detail="proposal not found")
    if prop["status"] != "proposed":
        raise HTTPException(status_code=409, detail=f"proposal already {prop['status']}")
    if prop["output_form"] not in WRITE_FORMS:
        raise HTTPException(status_code=400, detail=f"unknown output_form `{prop['output_form']}`")
    if prop["target_scope"] not in WRITE_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"`{prop['target_scope']}` apply is reserved — core internals aren't wired yet. "
                   f"Reject or drop this proposal.")
    # Blocking clarifications must be answered first (the write phase needs the decision).
    answers = body.answers
    unanswered = [
        q.get("question") for q in (prop.get("clarifications") or [])
        if isinstance(q, dict) and q.get("blocking") and not has_answer(answers, q.get("question"))
    ]
    if unanswered:
        raise HTTPException(
            status_code=400,
            detail="answer the blocking clarifying question(s) first: " + "; ".join(unanswered))
    if answers is not None:
        dev_store.set_proposal_clarification_answers(proposal_id, answers)
    dev_store.set_proposal_status(proposal_id, "writing")
    ctx = contexts.resolve(body.context_id, "dev")
    run_id = spine.start_run(body.context_id, mode="dev", feature="write")
    dev_store.log_event(
        body.context_id, "memory.approved",
        f"Approved proposal #{proposal_id} ({prop['output_form']}/{prop['target_scope']}) → writing",
        scope="dev", actor="owner", meta={"proposal_id": proposal_id})
    asyncio.create_task(run_background_write(ctx, body.context_id, proposal_id, run_id))
    return {"status": "writing", "proposal_id": proposal_id}


@router.patch("/dev/memory/proposals/{proposal_id}/artifact", response_model=ProposalActionResponse, response_model_exclude_unset=True)
async def memory_proposal_edit_artifact(proposal_id: int, body: ArtifactEditBody,
                                         dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """GATE 2 (refine) — owner edits the staged artifact before publishing. Only a `drafted` proposal
    can be edited; publish then writes whatever is saved here. Status stays `drafted`."""
    prop = dev_store.get_memory_proposal(proposal_id)
    if not prop or prop["context_id"] != body.context_id:
        raise HTTPException(status_code=404, detail="proposal not found")
    if prop["status"] != "drafted":
        raise HTTPException(status_code=409,
                            detail=f"proposal is `{prop['status']}` — only `drafted` can be edited")
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="artifact content is empty")
    updated = dev_store.update_staged_artifact(proposal_id, content)
    dev_store.log_event(
        body.context_id, "memory.artifact_edited",
        f"Edited staged artifact for proposal #{proposal_id} ('{prop['title']}')",
        scope="dev", actor="owner", meta={"proposal_id": proposal_id})
    return {"ok": True, "proposal": updated}


@router.post("/dev/memory/proposals/{proposal_id}/publish", response_model=PublishResponse, response_model_exclude_unset=True)
async def memory_proposal_publish(proposal_id: int, body: ProposalActionBody,
                                  dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """GATE 2 — the owner publishes the staged artifact: the single disk write into its live operational
    home.

    The proposal must be `drafted`. Core is reserved."""
    from ....core import operational as ops
    from datetime import datetime, timezone
    prop = dev_store.get_memory_proposal(proposal_id)
    if not prop or prop["context_id"] != body.context_id:
        raise HTTPException(status_code=404, detail="proposal not found")
    if prop["status"] != "drafted":
        raise HTTPException(status_code=409,
                            detail=f"proposal is `{prop['status']}` — only `drafted` can be published")
    content = prop.get("staged_artifact")
    if not content:
        raise HTTPException(status_code=409, detail="no staged artifact to publish")
    repo_id = body.context_id if prop["target_scope"] == "repo_dev" else None
    slug = _proposal_slug(prop)
    try:
        path = ops.publish_artifact(
            prop["output_form"], prop["target_scope"], repo_id,
            slug=slug, content=content, source="agent",
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    except ops.ReservedScope as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"publish failed: {e}")
    dev_store.set_proposal_status(proposal_id, "published")
    for cid in (prop.get("candidate_ids") or []):
        dev_store.set_candidate_status(cid, "promoted")
    dev_store.log_event(
        body.context_id, "memory.published",
        f"Published {prop['output_form']} '{prop['title']}' → {path}",
        scope="dev", actor="owner",
        meta={"proposal_id": proposal_id, "staged_path": path, "form": prop["output_form"],
              "scope": prop["target_scope"]})
    return {"ok": True, "path": path, "proposal": dev_store.get_memory_proposal(proposal_id)}


@router.post("/dev/memory/proposals/{proposal_id}/reject", response_model=ProposalActionResponse, response_model_exclude_unset=True)
async def memory_proposal_reject(proposal_id: int, body: ProposalActionBody,
                                 dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """The owner REJECTS a proposal: this framing is wrong, but the observation may still matter.

    Marks it `rejected` and re-queues its source candidates, so a later distill pass can reframe them.
    To stop re-suggestion, use DROP."""
    prop = dev_store.get_memory_proposal(proposal_id)
    if not prop or prop["context_id"] != body.context_id:
        raise HTTPException(status_code=404, detail="proposal not found")
    if prop["status"] not in ("proposed", "drafted"):
        raise HTTPException(status_code=409, detail=f"proposal already {prop['status']}")
    dev_store.set_proposal_status(proposal_id, "rejected")
    for cid in (prop.get("candidate_ids") or []):
        dev_store.set_candidate_status(cid, "candidate")  # re-queue for reconsideration
    dev_store.log_event(
        body.context_id, "memory.rejected",
        f"Rejected proposal '{prop['title']}' (candidates re-queued)",
        scope="dev", actor="owner", meta={"proposal_id": proposal_id},
    )
    return {"ok": True, "proposal": dev_store.get_memory_proposal(proposal_id)}


@router.post("/dev/memory/proposals/{proposal_id}/drop", response_model=ProposalActionResponse, response_model_exclude_unset=True)
async def memory_proposal_drop(proposal_id: int, body: ProposalActionBody,
                               dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Owner DROPS a proposal — this is noise, stop suggesting it.

    Marks the proposal and its source candidates `dropped`, the negative signal the learning
    loop reads. Unlike reject, dropped candidates are never re-queued."""
    prop = dev_store.get_memory_proposal(proposal_id)
    if not prop or prop["context_id"] != body.context_id:
        raise HTTPException(status_code=404, detail="proposal not found")
    if prop["status"] not in ("proposed", "drafted"):
        raise HTTPException(status_code=409, detail=f"proposal already {prop['status']}")
    dev_store.set_proposal_status(proposal_id, "dropped")
    for cid in (prop.get("candidate_ids") or []):
        dev_store.set_candidate_status(cid, "dropped")
    dev_store.log_event(
        body.context_id, "proposal.dropped",
        f"Dropped proposal '{prop['title']}' (noise — won't re-suggest)",
        scope="dev", actor="owner", meta={"proposal_id": proposal_id},
    )
    return {"ok": True, "proposal": dev_store.get_memory_proposal(proposal_id)}
