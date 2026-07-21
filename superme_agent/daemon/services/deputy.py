"""The deputy — the agent that judges autopilot gates on the owner's behalf (autopilot slice 4).

Dispatch model (design §2b): a HEADLESS one-shot run minted fresh per gate, a PEER of the phase
sessions (never a `Task`-subagent under them — the judged must not own its judge). It is a PURE
JUDGE: it reads the mandate, its own decision log, and the gate brief (the same one the owner would
see), inspects artifacts read-only, and emits a structured verdict. THIS module executes the verdict
(advance / send-back / escalate) — the agent has no mutation tool, the structural guarantee that a
robot can neither end nor ratify work.

  identity + floor + strictness  →  kernel_speech.deputy_preamble  (run_turn system_append)
  the chosen context to judge    →  kernel_speech.deputy_brief_block (the prompt body)
  the verdict it emits           →  session_contract.parse_deputy_verdict
  its durable memory + trail      →  core.deputy (mandate.md + decisions.md)

The dispatch seam is `gates.maybe_autopilot_advance`: when the deputy is enabled it schedules
`run_deputy_gate` instead of a blind auto-advance, so every autopilot gate is judged by SOMEONE
(the design invariant — never by nobody).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..app_state import agent as _agent, dev as _dev, dev_store as _dev_store, spine as _spine
from ...core import Usage, Result, Status, TextDelta, ToolResult, deny_all
from ...core import kernel_speech, session_contract, gate_briefs, deputy as deputy_core
from .runs import _begin_run, _LiveTokens, capture_prompt, capture_event, _dev_mcp

log = logging.getLogger("superme-agent")

# The gates a human — and so the deputy — judges (design §2b). Build⟷vet is deliberately absent:
# it is fully autonomous, no human and no deputy inside it.
DEPUTY_GATE_PHASES = ("triage", "plan", "review")

_READONLY_NUDGE = ("You are the deputy — a read-only JUDGE. Inspect the artifacts and decide; do not "
                   "edit, run, or write anything. Your only output is the verdict.")


def deputy_gate_for(item: dict) -> str | None:
    """The gate phase this item is resting at, if it is one the deputy judges — else None."""
    phase = str(item.get("phase") or "")
    return phase if phase in DEPUTY_GATE_PHASES else None


def _success_signal(dev_root: Path, item: dict) -> str | None:
    """Best-effort: the item's deliverable success signal from the PRD, verbatim (design §2b — the
    review acceptance test). None when the deliverable/signal can't be resolved; the brief block then
    tells the deputy to escalate if the review turns on a signal it can't confirm."""
    deliverable = item.get("deliverable")
    if not deliverable:
        return None
    try:
        return _dev.deliverable_success_signal(dev_root, str(deliverable))
    except Exception:
        return None


async def run_deputy_gate(context_id: str, item_id: str) -> None:
    """Dispatch the deputy at the item's current gate, then execute its verdict. Best-effort and
    self-contained: any failure leaves the item resting at `awaiting_human` for the owner (the safe
    default — a gate the deputy couldn't judge falls to a human, never to a silent auto-advance)."""
    from ..app_state import get_spine
    from ...gateway import contexts
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id)
        if item is None:
            return
        gate = deputy_gate_for(item)
        if gate is None:
            return
        # Open the deputy run — this takes the item's run-lock, so nothing else advances the item
        # while it judges. None ⇒ something is already running it; yield (it will re-rest at the gate).
        model, effort = _resolve_deputy_params(context_id, item)
        run_id = _begin_run(ctx, context_id, item_id, "deputy", model, phase=gate)
        if run_id is None:
            return
        verdict, final_tokens, final_usage = await _judge(ctx, context_id, item_id, item, gate,
                                                          dev_root, model, effort)
        # Close the deputy's own run row (releases the lock). NOT `_end_run` — that would re-fire the
        # seam and could loop the deputy on itself. The item's resting status is set by the action.
        _spine.finish_item_run(context_id, item_id, fallback_tokens=final_tokens, usage=final_usage,
                               outcome=(verdict or {}).get("decision"))
        _act_on_verdict(ctx, context_id, item_id, gate, verdict)
    except Exception:
        log.exception("deputy gate dispatch failed for %s (item stays for the owner)", item_id)
        try:
            _dev.set_work_item_status(ctx.internal_root / "dev", item_id, "awaiting_human")
        except Exception:
            pass


def _resolve_deputy_params(context_id: str, item: dict) -> tuple[str, str]:
    """The deputy runs on the item/repo/system model + effort — one judge, same tier as the work."""
    model = _spine.effective_model(context_id, item_model=item.get("model"))
    effort = _spine.effective_effort(context_id, item_effort=item.get("effort"))
    return model, effort


async def _judge(ctx, context_id: str, item_id: str, item: dict, gate: str, dev_root: Path,
                 model: str, effort: str) -> tuple[dict | None, int | None, dict | None]:
    """Run one headless deputy turn and return (verdict|None, tokens, usage). The verdict is None
    when the run produced no valid `deputy-verdict` fence — the caller treats that as 'couldn't
    judge → owner', never a pass."""
    item_dir = dev_root / "work-items" / item_id
    strictness = _spine.get_deputy_strictness(gate)
    # The context the deputy judges from — assembled from durable state, nothing inherited.
    all_items = _dev.read_all(dev_root)["work_items"]
    events = _dev_store.list_events(context_id, item_id=item_id, limit=100)
    git_health = None
    brief = gate_briefs.render_gate_brief(item, item_dir, dev_root, ctx.cwd,
                                          all_items=all_items, events=events, git_health=git_health)
    mandate = deputy_core.read_mandate(dev_root)
    digest = deputy_core.log_digest(dev_root, item_id)
    signal = _success_signal(dev_root, item) if gate == "review" else None
    # BV-A2.3: at review, surface the pending authorization requests + which scopes are delegated,
    # so the deputy can grant a delegated one (send_back + authorize) or escalate an owner-reserved one.
    auth_block = None
    if gate == "review":
        from ...core import artifacts as _arts
        pending = _arts.pending_authorizations(item_dir)
        if pending:
            auth_block = kernel_speech.render_authorizations_block(
                pending, _spine.get_deputy_delegated_authority())
    prompt = kernel_speech.deputy_brief_block(
        item_id, str(item.get("title") or item_id), gate, brief.get("brief", ""),
        mandate=mandate, log_digest=digest, success_signal=signal, authorizations=auth_block)
    system_append = kernel_speech.deputy_preamble(strictness)
    capture_prompt(context_id, f"[deputy] judging the {gate} gate", item_id=item_id)
    final_text = None
    final_tokens = None
    final_usage = None
    live = _LiveTokens()
    try:
        async for ev in _agent.run_turn(
            ctx, prompt,
            resume=None,                       # fresh per gate — the deputy FORGETS (design §2b)
            model=model, effort=effort,
            approve=deny_all,                  # read-only judge: no writes, no shell side effects
            extra_mcp_servers=_dev_mcp(ctx, ctx.cwd, item_id),
            system_append=system_append,
            deny_write_tools=_READONLY_NUDGE,  # Write/Edit die outright — it inspects, never edits
        ):
            if isinstance(ev, Usage):
                live.bump(context_id, item_id, ev)
            elif isinstance(ev, Result):
                final_text = ev.text
                final_tokens = ev.tokens
                final_usage = ev.usage
            if isinstance(ev, (Status, TextDelta, ToolResult)):
                capture_event(context_id, ev, item_id=item_id)
    except Exception:
        log.exception("deputy judge turn failed for %s", item_id)
    verdict = session_contract.parse_deputy_verdict(final_text)
    return verdict, final_tokens, final_usage


# --------------------------------------------------------------------------- verdict → action

def _act_on_verdict(ctx, context_id: str, item_id: str, gate: str, verdict: dict | None) -> None:
    """Carry out the deputy's decision using the SAME levers the owner's gate buttons pull. A
    missing/invalid verdict falls to the owner (page) — never a silent advance."""
    dev_root = ctx.internal_root / "dev"
    if not verdict:
        _dev.set_work_item_status(dev_root, item_id, "awaiting_human")
        _dev_store.log_event(context_id, "deputy.escalate",
                             "Deputy returned no valid verdict — paged the owner",
                             item_id=item_id, actor="deputy",
                             meta={"gate": gate, "reason": "no_verdict",
                                   "speech": f"I couldn't reach a clean decision at the **{gate}** "
                                             f"gate, so I've left it for you rather than guess."})
        return
    decision = verdict["decision"]
    because = verdict.get("because") or verdict.get("checked") or ""
    # Record the call first — the ledger is the deputy's accountability trail and the next dispatch's
    # only continuity, so it must land even if the downstream action then fails.
    try:
        deputy_core.append_decision(dev_root, item_id, gate, decision, because)
    except Exception:
        log.exception("deputy decision-log append failed for %s", item_id)
    if decision == "approve":
        _do_approve(ctx, context_id, item_id, gate, verdict)
    elif decision == "send_back":
        # A send_back carrying `authorize` is a GRANT of a delegated authorization (BV-A2.3) — a
        # send_back variant, never a new ratify power. The floor is enforced mechanically in _do_grant.
        # Otherwise it's a real fix request: the deputy found a thing it can handle → auto-loop the
        # owning phase (triage/plan/review→plan). That loop is DELIBERATE (optimistic autopilot: no
        # user in the loop for a fixable thing); the send-back CAP in _do_send_back escalates only when
        # it stops converging. (Careful mode, later, adds a human PR gate on approve — not here.)
        if (verdict.get("authorize") or "").strip():
            _do_grant(ctx, context_id, item_id, gate, verdict)
        else:
            _do_send_back(ctx, context_id, item_id, gate, verdict)
    else:  # escalate
        _do_escalate(ctx, context_id, item_id, gate, verdict, reason="deputy_escalated")


def _do_grant(ctx, context_id: str, item_id: str, gate: str, verdict: dict) -> None:
    """Grant a DELEGATED authorization the deputy judged ready (BV-A2.3). THE FLOOR IS MECHANICAL:
    the daemon grants only if the request's scope is in the owner's delegated set — otherwise it
    ESCALATES, because an intent-defining contract change is the owner's alone and no amount of
    deputy conviction changes that. A grant records the decision and routes the item back to build
    to perform the now-allowed change (grant-as-send_back)."""
    from ...core import artifacts as _arts
    from . import loop as _loop
    dev_root = ctx.internal_root / "dev"
    item_dir = dev_root / "work-items" / item_id
    auth_id = (verdict.get("authorize") or "").strip()
    auth = next((a for a in _arts.pending_authorizations(item_dir) if a["id"] == auth_id), None)
    if auth is None:
        _do_escalate(ctx, context_id, item_id, gate, verdict, reason="grant_unknown",
                     override=(f"The deputy tried to grant authorization {auth_id!r}, but no such "
                               f"pending request exists on this item — over to you."))
        return
    if auth.get("scope") not in _spine.get_deputy_delegated_authority():
        _do_escalate(ctx, context_id, item_id, gate, verdict, reason="grant_reserved",
                     override=(f"The deputy judged this ready, but the change needs an authorization "
                               f"it cannot give — scope `{auth.get('scope')}` is owner-reserved. "
                               f"Request: {auth.get('what')}. Grant or deny it yourself."))
        return
    started, why = _loop.grant_authorization(ctx, context_id, item_id, auth_id, by="deputy")
    if not started:
        _do_escalate(ctx, context_id, item_id, gate, verdict, reason="grant_undeliverable",
                     override=(f"The deputy granted authorization for '{auth.get('what')}' but the "
                               f"build re-entry couldn't start: {why}"))
        return
    _dev_store.log_event(context_id, "deputy.approve",
                         f"Deputy granted a delegated authorization at {gate}: "
                         f"{(auth.get('what') or '')[:140]}",
                         item_id=item_id, actor="deputy",
                         meta={"gate": gate, "auth_id": auth_id, "scope": auth.get("scope"),
                               "speech": f"I granted a delegated authorization "
                                         f"('{auth.get('what')}') and sent it back to build to apply "
                                         f"— this is a scope you delegated to me."})


def _do_approve(ctx, context_id: str, item_id: str, gate: str, verdict: dict) -> None:
    """Advance the gate — the deputy pulls the owner's advance lever, with `ratify=False` (the human
    floor: a robot cannot ratify an assumption). Cap-aware entering build (slice 3)."""
    from . import gates as gate_svc
    because = verdict.get("because", "")
    _dev_store.log_event(context_id, "deputy.approve",
                         f"Deputy approved the {gate} gate: {because[:160]}",
                         item_id=item_id, actor="deputy",
                         meta={"gate": gate, "checked": verdict.get("checked", "")[:400],
                               "because": because[:400],
                               "speech": f"I approved the **{gate}** gate on your behalf. {because}"})
    gate_svc.autopilot_advance(ctx, context_id, item_id, actor="deputy")


def _do_send_back(ctx, context_id: str, item_id: str, gate: str, verdict: dict) -> None:
    """NEGOTIATE the deputy's change by firing a REAL turn at the work-item agent (deputy-live-turns
    Q1): the change is delivered into the item's own session and the phase that owns the fix re-runs
    (triage → re-triage; plan → re-plan). The re-run ends at `awaiting_human`, which re-fires the
    gate seam so the deputy RE-JUDGES the result — chaining IS the negotiation loop. At REVIEW the
    fall-back is to the PLAN phase (Q1-B); until that lands, review still uses the vet-check router.

    The send-back CAP (max 3): once the deputy has already bounced this item 3×, a further send-back
    becomes an escalation — 'I've sent this back 3× and it still isn't there' is information the owner
    needs, not a 4th silent loop."""
    dev_root = ctx.internal_root / "dev"
    change = verdict.get("change") or verdict.get("because") or ""
    prior = deputy_core.count_send_backs(dev_root, item_id)  # includes the one just logged
    if prior >= deputy_core.SEND_BACK_CAP:
        _do_escalate(ctx, context_id, item_id, gate, verdict, reason="send_back_cap",
                     override=(f"The deputy has sent this item back {prior}× and it still isn't "
                               f"meeting the bar. Latest change asked for: {change}"))
        return
    # Every gate's send-back is delivered as a LIVE TURN at the agent (deputy-live-turns Q1). The
    # phase that owns the fix re-runs: triage → re-triage, plan → re-plan, and REVIEW → the plan
    # phase (fire_deputy_feedback flips review→plan and carries a downstream digest so the re-plan
    # knows what build/vet/review found). The re-run rests at its gate, re-firing the seam → the
    # deputy re-judges (the negotiation loop). The `deputy.query` marker is logged inside
    # fire_deputy_feedback (for the trail + FE re-attribution).
    from .runs import fire_deputy_feedback
    from . import git_ops
    digest = None
    if gate == "review":
        digest = git_ops.build_downstream_digest(dev_root / "work-items" / item_id)
    if fire_deputy_feedback(context_id, item_id, phase=gate, feedback=change, digest=digest):
        return
    # Couldn't deliver (no session yet, or a run raced in) — hand it to the owner honestly.
    _do_escalate(ctx, context_id, item_id, gate, verdict, reason="send_back_undeliverable",
                 override=(f"The deputy wants a change at {gate} but it couldn't be delivered to the "
                           f"agent automatically: {change}"))


def _do_escalate(ctx, context_id: str, item_id: str, gate: str, verdict: dict, *,
                 reason: str, override: str | None = None) -> None:
    """Page the owner (`awaiting_human`) with the deputy's instructions. The escalation text is the
    runbook the deputy owed (situation · concern · what-to-do) — never a bare 'please review'."""
    dev_root = ctx.internal_root / "dev"
    escalation = override or verdict.get("escalation") or verdict.get("because") or "(no detail)"
    _dev.set_work_item_status(dev_root, item_id, "awaiting_human")
    _dev_store.log_event(context_id, "deputy.escalate",
                         f"Deputy escalated the {gate} gate to you: {escalation[:200]}",
                         item_id=item_id, actor="deputy",
                         meta={"gate": gate, "reason": reason, "escalation": escalation[:800],
                               "checked": verdict.get("checked", "")[:400],
                               "speech": f"This one needs you at the **{gate}** gate.\n\n{escalation}"})
