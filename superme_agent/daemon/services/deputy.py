"""The deputy — the agent that judges autopilot gates on the owner's behalf (autopilot slice 4).

Dispatch model (design §2b): a surfaceless one-shot background run minted fresh per gate, a PEER of the phase
sessions (never a `Task`-subagent under them — the judged must not own its judge). It is a PURE
JUDGE: it reads the mandate, its own decision log, and the gate brief (the same one the owner would
see), inspects artifacts read-only, and emits a structured verdict. THIS module executes the verdict
(advance / send-back / escalate) — the agent has no mutation tool, the structural guarantee that a
robot can neither end nor ratify work.

  identity + floor + strictness  →  kernel_speech.deputy_preamble  (run_turn system_append)
  the chosen context to judge    →  kernel_speech.deputy_brief_block (the prompt body)
  the verdict it emits           →  the deputy_verdict TOOL (run_tools) — validated at call time,
                                    delivered through this dispatch's sink
  its mandate + per-item log      →  core.deputy (mandate.md in the harness cell + deputy-log.jsonl per item)

The dispatch seam is `gates.maybe_autopilot_advance`: when the deputy is enabled it schedules
`run_deputy_gate` instead of a blind auto-advance, so every autopilot gate is judged by SOMEONE
(the design invariant — never by nobody).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..app_state import agent as _agent, dev as _dev, dev_store as _dev_store, spine as _spine
from ...core import Usage, Result, Status, TextDelta, ToolResult, deny_all
from ...core import kernel_speech, gate_briefs, deputy as deputy_core
from ...core import autopilot as _autopilot
from ...harness.tools.run_tools import make_deputy_verdict_server
from .runs import _begin_run, _LiveTokens, capture_prompt, capture_event, capture_run_input, \
    _dev_mcp, retry_notice, turn_surface
from .turns import ResilientTurn

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


def _build_delta(item_dir: Path, gate: str, numbers: dict) -> str | None:
    """The 'since your last call at this gate' delta (design §5) — assembled ONLY on a loop re-entry
    (a prior send-back at this gate exists). Lean and structured: what the deputy asked, the agent's
    latest checkpoint, and the movement counts the gate brief already computed. A POINTER, not the
    source — the deputy still drills into the full artifacts on demand. None on a first judgment."""
    prior = [r for r in deputy_core.gate_decisions(item_dir, gate) if r.get("decision") == "send_back"]
    if not prior:
        return None
    asked = prior[-1].get("change") or prior[-1].get("because") or "(the change you asked for)"
    lines = ["### Since your last call at this gate (a pointer — verify against the artifacts)",
             f"- You asked (send_back): \"{asked}\""]
    try:
        from ...core import artifacts as _arts
        cp = _arts.latest_checkpoint(item_dir, char_cap=400)
        if cp:
            head = next((ln.strip() for ln in (cp.get("text") or "").splitlines()
                         if ln.strip() and not ln.startswith("#") and not ln.startswith("---")), None)
            if head:
                lines.append(f"- The agent's latest checkpoint: \"{head[:160]}\"")
    except Exception:
        pass
    prog = []
    if numbers.get("tasks_total"):
        prog.append(f"tasks {numbers.get('tasks_done', 0)}/{numbers['tasks_total']}")
    if numbers.get("cycle"):
        prog.append(f"vet {numbers['cycle']} cycle(s)")
    if numbers.get("checks_total"):
        prog.append(f"{numbers.get('checks_pass', 0)}/{numbers['checks_total']} checks pass")
    if prog:
        lines.append("- Movement: " + " · ".join(prog))
    lines.append("→ Judge whether the asked-for change is now met — re-inspect the delta, not the whole "
                 "item from scratch. Open the full artifacts (brief below) if anything is unclear.")
    return "\n".join(lines)


def _in_review_loop(item_dir: Path) -> bool:
    """True once the review deputy has sent this item back at least once — meaning we are inside the
    review↔(plan-build-vet) macro-loop that the (single, persistent) review deputy OWNS (Fork A,
    forward-only lifetime). The only route back to the plan gate after a review send-back is that
    send-back, so a prior review send-back is a sufficient signal."""
    return deputy_core.count_send_backs(item_dir, "review") > 0


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
        # FLOW-THROUGH (Fork A, forward-only lifetime): when the item is back at the PLAN gate inside a
        # live review loop, the persistent REVIEW deputy owns this loop — a fresh plan deputy must not
        # compete. Skip the plan judgment and auto-advance to build; the review deputy re-judges the
        # end result when the work climbs back. (Only plan can be re-entered backward; triage never is.)
        if gate == "plan" and _in_review_loop(dev_root / "work-items" / item_id):
            from . import gates as gate_svc
            _dev_store.log_event(context_id, "deputy.flow_through",
                                 "Plan gate flowed through un-judged — inside a review loop the review "
                                 "deputy owns the decision",
                                 item_id=item_id, actor="deputy",
                                 meta={"gate": gate, "reason": "review_loop"})
            gate_svc.autopilot_advance(ctx, context_id, item_id, actor="deputy")
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
        rid = _spine.finish_item_run(context_id, item_id, fallback_tokens=final_tokens,
                                     usage=final_usage, outcome=(verdict or {}).get("decision"))
        # …but it still ENDED, and it still cost. Skipping `_end_run` silently dropped the one event
        # every other run kind writes, so the deputy was the only run whose tokens never reached the
        # timeline — and the phase chip (which sums the whole phase, gate run included) then read
        # 116.5k beside a trace that showed 85.6k with nothing to account for the difference. Same
        # authoritative 3-type total off the finished row, same shape as `{kind}.end`.
        _dev_store.log_event(context_id, "deputy.end",
                             f"Finished deputy run · Σ {_spine.run_tokens(rid) if rid else 0} tok",
                             item_id=item_id, actor="daemon",
                             meta={"tokens": _spine.run_tokens(rid) if rid else 0, "gate": gate})
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
    """Run one background deputy turn and return (verdict|None, tokens, usage). The verdict is None
    when the run never made a valid `deputy_verdict` tool call — the caller treats that as
    'couldn't judge → owner', never a pass."""
    item_dir = dev_root / "work-items" / item_id
    strictness = _spine.get_deputy_strictness(gate)
    # The context the deputy judges from — assembled from durable state, nothing inherited.
    all_items = _dev.read_all(dev_root)["work_items"]
    events = _dev_store.list_events(context_id, item_id=item_id, limit=100)
    # ONE computation of this gate's checks (core/gate_briefs) — the owner's drilldown reads the same
    # call, so the deputy can never judge from different numbers than the owner is shown (§2.1).
    state = gate_briefs.gate_state(item, item_dir, dev_root, ctx.cwd,
                                   all_items=all_items, events=events)
    dep_root = deputy_core.deputy_root(context_id)  # mandate lives in the harness cell, not knowledge
    mandate = deputy_core.read_mandate(dep_root)
    digest = deputy_core.log_digest(item_dir, gate)  # this item's prior calls AT THIS GATE (continuity)
    # On a loop RE-ENTRY (a prior send-back at this gate exists), feed a lean "since your last call"
    # delta so the deputy re-judges the DELTA, not the whole item from scratch (design §5). It is a
    # POINTER — the full artifacts stay on demand behind the contract path; never a substitute for
    # ground truth.
    delta = _build_delta(item_dir, gate, state.get("numbers") or {})
    signal = _success_signal(dev_root, item) if gate == "review" else None
    # BV-A2.3: at review, surface the pending authorization requests + which scopes are delegated,
    # so the deputy can grant a delegated one (send_back + authorize) or escalate an owner-reserved one.
    from ...core import artifacts as _arts
    auth_block = None
    verdicts = None
    if gate == "review":
        pending = _arts.pending_authorizations(item_dir)
        if pending:
            auth_block = kernel_speech.render_authorizations_block(
                pending, _spine.get_deputy_delegated_authority())
        # The vet's actual per-check verdicts. Before slice 6b the review deputy got none of these:
        # its "vet results" section had a `vet_note` parameter no caller ever filled, so it fell back
        # to "read the ledger embedded in the brief" — and the brief carried only an entry count.
        verdicts = _arts.verdict_rows(item_dir)
    # The deputy reads the OWNER's document for this phase, not a re-flattening of it (§2.1).
    prompt = kernel_speech.deputy_brief_block(
        item_id, str(item.get("title") or item_id), gate,
        state=state, report=_arts.report_text(item_dir, str(state.get("phase") or "")),
        mandate=mandate, log_digest=digest, delta=delta, success_signal=signal,
        verdicts=verdicts, authorizations=auth_block)
    system_append = kernel_speech.deputy_preamble(strictness)
    capture_prompt(context_id, f"[deputy] judging the {gate} gate", item_id=item_id)
    # Prompt inspector "A" — throwaway probes ONLY. The deputy was the one run the X-ray could not
    # see: it judges three gates, costs ~19% of an item, and had no capture site at all, so the
    # inspector's "actual input over a lifecycle" was silently missing a whole speaker.
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=ctx, system_append=system_append,
                          prompt=prompt, phase=f"deputy:{gate}",
                          surface=turn_surface(model=model, effort=effort, mcp=["dev", "deputy"],
                                               write_boundary=[], sandbox_writes=[],
                                               read_only=True, resumes=False),
                          background=True)
    final_tokens = None
    final_usage = None
    live = _LiveTokens()
    sink: dict = {}   # the deputy_verdict tool (run_tools) lands the verdict here
    turn = ResilientTurn("deputy judge", item_id=item_id,
                         notify=retry_notice(context_id, item_id, gate))
    async for ev in turn.stream(
        _agent, ctx, prompt,
        resume=None,                       # fresh per gate — the deputy FORGETS (design §2b)
        model=model, effort=effort,
        approve=deny_all,                  # read-only judge: no writes, no shell side effects
        sandbox_writes=[],                 # …and sandboxed anyway: cwd only, no network
        extra_mcp_servers={**_dev_mcp(ctx, ctx.cwd, item_id),
                           "deputy": make_deputy_verdict_server(sink)},
        system_append=system_append,
        deny_write_tools=_READONLY_NUDGE,  # Write/Edit die outright — it inspects, never edits
    ):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens = ev.tokens
            final_usage = ev.usage
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    # A judge that never ran returns no verdict — which the caller already treats as "no judgment",
    # leaving the gate to the owner. The ladder above means an outage has to outlast it first.
    return sink.get("verdict"), final_tokens, final_usage


# --------------------------------------------------------------------------- verdict → action

def _headline(text: str, cap: int = 240) -> str:
    """First paragraph of `text`, capped — the one line a decision bubble carries (§2.1). The full
    rationale never rides the bubble; it stays in the event meta for the Deputy tab."""
    head = (text or "").strip().split("\n\n")[0].replace("\n", " ").strip()
    return head if len(head) <= cap else head[:cap].rstrip() + "…"


_MD_LABEL = re.compile(r"^\*\*[^*]+:\*\*\s*")
_MD_MARK = re.compile(r"[*`_]")


def _plain(text: str, cap: int = 200) -> str:
    """The escalation's first line as PLAIN prose, for the event LOG (owner, 2026-08-08).

    The escalation is markdown now — `**Issue summary:** …` over bulleted concerns — and the event
    summary was carrying the first 200 characters of it verbatim. Every surface that reads an event
    summary renders it as text: the Activity row, and the Now card's one-line live strip. So the
    owner read `**Issue summary:**` with the asterisks showing, in a line too short to have wanted
    the markup anyway. The CARD keeps its markup (meta `escalation`, rendered); the log sentence
    drops the label and the emphasis marks and says the thing."""
    return _headline(_MD_MARK.sub("", _MD_LABEL.sub("", (text or "").strip())), cap)


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
    # Record the call first into THIS item's continuity log (its per-item scratch memory). The durable
    # accountability trail is the run row + dev events below; this must still land even if the
    # downstream action then fails, so the next dispatch sees what it already decided.
    try:
        deputy_core.append_decision(dev_root / "work-items" / item_id, gate, decision, because,
                                    change=verdict.get("change"), authorize=verdict.get("authorize"))
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
    deputy conviction changes that. A grant RECORDS the decision and routes nothing (§2.1) — the
    item stays at its gate; the deputy's own approve (below) is what advances it."""
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
    # Re-check the DECLARED scope against the STAGED OPS at the floor too (§2.1). The tool refuses
    # a mislabel at request time, but the ops can be restaged afterwards — and a delegated grant is
    # exactly where a wrong label would do its damage, by routing an intent change past the owner.
    try:
        from ...core import knowledge_delta as _kd
        staged = (_kd.read_delta(item_dir) or {}).get("ops") or []
    except Exception:
        staged = []
    if (mismatch := _arts.scope_mismatch(str(auth.get("scope") or ""), staged)):
        _do_escalate(ctx, context_id, item_id, gate, verdict, reason="grant_scope_mismatch",
                     override=(f"The deputy tried to grant '{auth.get('what')}' as a delegated "
                               f"sync, but the staged ops change what the project IS — {mismatch} "
                               f"This is yours to grant or deny."))
        return
    recorded, why = _loop.grant_authorization(ctx, context_id, item_id, auth_id, by="deputy")
    if not recorded:
        _do_escalate(ctx, context_id, item_id, gate, verdict, reason="grant_undeliverable",
                     override=(f"The deputy granted authorization for '{auth.get('what')}' but the "
                               f"grant could not be recorded: {why}"))
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
    """Advance the gate — the deputy pulls the owner's same advance lever. Cap-aware entering
    build (slice 3)."""
    from . import gates as gate_svc
    because = verdict.get("because", "")
    _dev_store.log_event(context_id, "deputy.approve",
                         f"Deputy approved the {gate} gate: {because[:160]}",
                         item_id=item_id, actor="deputy",
                         meta={"gate": gate, "checked": verdict.get("checked", "")[:400],
                               "because": because[:400],
                               # The headline the chat bubble shows (§2.1) — an approval that kept
                               # things moving still says so, in one line, where the owner looks.
                               "speech": f"I approved the **{gate}** gate on your behalf. "
                                         + _headline(because)})
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
    # Cap is per-GATE (per-episode, forward-only model): a review loop gets its own 3 tries, not
    # capped by earlier plan send-backs. Includes the call just logged.
    prior = deputy_core.count_send_backs(dev_root / "work-items" / item_id, gate)
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


def _clip_card(text: str, cap: int) -> str:
    """Bound the page card WITHOUT cutting a word in half.

    A flat `[:800]` ended the owner's card on "…non-goal — s" (owner, 2026-08-08): the escalation is
    markdown now, so a byte slice lands mid-bullet and the last thing the owner reads is a fragment.
    Cut on a line boundary, keep whole bullets, and say so when anything was dropped. The cap is
    generous because the shape already bounds this — one summary line and short bullets."""
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    kept: list[str] = []
    used = 0
    for line in text.splitlines():
        if used + len(line) + 1 > cap:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept).rstrip() + "\n\n(trimmed — the full card is in the deputy log)"


def _do_escalate(ctx, context_id: str, item_id: str, gate: str, verdict: dict, *,
                 reason: str, override: str | None = None) -> None:
    """Page the owner (`awaiting_human`) with the deputy's instructions. The escalation text is the
    card the deputy owed (summary · concerns · what to do) — never a bare 'please review'."""
    dev_root = ctx.internal_root / "dev"
    escalation = override or verdict.get("escalation") or verdict.get("because") or "(no detail)"
    _dev.set_work_item_status(dev_root, item_id, "awaiting_human")
    _dev_store.log_event(context_id, "deputy.escalate",
                         f"Deputy escalated the {gate} gate to you: {_plain(escalation)}",
                         item_id=item_id, actor="deputy",
                         meta={"gate": gate, "reason": reason,
                               "escalation": _clip_card(escalation, 1600),
                               "checked": verdict.get("checked", "")[:400],
                               # `speech` is the HEADLINE (§2.1): the chat bubble and the paged
                               # notice's lead line. The runbook rides `escalation` — one line
                               # tells the owner they're needed; the detail is one surface deeper.
                               "speech": f"This one needs you at the **{gate}** gate. "
                                         + _headline(verdict.get("because") or escalation)})
