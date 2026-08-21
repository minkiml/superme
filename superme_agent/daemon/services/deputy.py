"""The deputy — the agent that judges autopilot gates on the owner's behalf.

A surfaceless one-shot run, minted fresh per gate and a PEER of the phase sessions: the judged must
not own its judge.
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
from .runs import begin_run, LiveTokens, capture_prompt, capture_event, capture_run_input, \
    dev_mcp, retry_notice, surface_from_turn
from .turns import ResilientTurn

log = logging.getLogger("superme-agent")

# Build⟷vet is deliberately absent: it is fully autonomous, with no human and no deputy inside it.
DEPUTY_GATE_PHASES = ("triage", "plan", "review")

_READONLY_NUDGE = ("You are the deputy — a read-only JUDGE. Inspect the artifacts and decide; do not "
                   "edit, run, or write anything. Your only output is the verdict.")


def deputy_gate_for(item: dict) -> str | None:
    """The gate phase this item is resting at, if it is one the deputy judges — else None."""
    phase = str(item.get("phase") or "")
    return phase if phase in DEPUTY_GATE_PHASES else None


def _success_signal(dev_root: Path, item: dict) -> str | None:
    """The item's deliverable success signal from the PRD, verbatim — the review acceptance test.

    None when it cannot be resolved; the brief then tells the deputy to escalate."""
    deliverable = item.get("deliverable")
    if not deliverable:
        return None
    try:
        return _dev.deliverable_success_signal(dev_root, str(deliverable))
    except Exception:
        return None


def _build_delta(item_dir: Path, gate: str, numbers: dict) -> str | None:
    """The "since your last call at this gate" delta, assembled ONLY on a loop re-entry.

    Lean and structured: what the deputy asked, the agent's latest checkpoint, and the gate brief's
    movement counts. A POINTER, not the source."""
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
    """True once the review deputy has sent this item back at least once, so we are inside the macro-loop
    the single persistent review deputy owns.

    That send-back is the only route back to the plan gate."""
    return deputy_core.count_send_backs(item_dir, "review") > 0


async def run_deputy_gate(context_id: str, item_id: str) -> None:
    """Dispatch the deputy at the item's current gate, then execute its verdict.

    Best-effort: any failure leaves the item resting at `awaiting_human`, because a gate the deputy
    could not judge falls to a human, never to a silent auto-advance."""
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
        # Inside a live review loop the persistent REVIEW deputy owns the item, so a fresh plan
        # deputy must not compete.
        if gate == "plan" and _in_review_loop(dev_root / "work-items" / item_id):
            from . import gates as gate_svc
            _dev_store.log_event(context_id, "deputy.flow_through",
                                 "Plan gate flowed through un-judged — inside a review loop the review "
                                 "deputy owns the decision",
                                 item_id=item_id, actor="deputy",
                                 meta={"gate": gate, "reason": "review_loop"})
            gate_svc.autopilot_advance(ctx, context_id, item_id, actor="deputy")
            return
        # Opening the run takes the item's run-lock, so nothing else advances the item while it
        # judges.
        model, effort = _resolve_deputy_params(context_id, item)
        run_id = begin_run(ctx, context_id, item_id, "deputy", model, phase=gate)
        if run_id is None:
            return
        verdict, final_tokens, final_usage = await _judge(ctx, context_id, item_id, item, gate,
                                                          dev_root, model, effort)
        # Close the run row to release the lock. NOT `end_run`, which would re-fire the seam and
        # loop the deputy.
        rid = _spine.finish_item_run(context_id, item_id, fallback_tokens=final_tokens,
                                     usage=final_usage, outcome=(verdict or {}).get("decision"))
        # The deputy run still ended and still cost, so it writes the same end event every other
        # run kind does.
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
    """The deputy's own tier: this item's deputy pick → the system deputy setting → the floor.

    Configured at system scope, so the judge is not a function of the judged."""
    return _spine.deputy_params(item_model=item.get("deputy_model"),
                                item_effort=item.get("deputy_effort"))


async def _judge(ctx, context_id: str, item_id: str, item: dict, gate: str, dev_root: Path,
                 model: str, effort: str) -> tuple[dict | None, int | None, dict | None]:
    """Run one background deputy turn and return (verdict|None, tokens, usage).

    The verdict is None when the run never made a valid `deputy_verdict` call, which the caller treats
    as "could not judge", never a pass."""
    item_dir = dev_root / "work-items" / item_id
    strictness = _spine.get_deputy_strictness(gate)
    # The context the deputy judges from — assembled from durable state, nothing inherited.
    all_items = _dev.read_all(dev_root)["work_items"]
    events = _dev_store.list_events(context_id, item_id=item_id, limit=100)
    # One computation of this gate's checks, so the deputy can never judge from different numbers
    # than the owner sees.
    from . import drilldown as _drill, git_ops as _git_ops
    counters = _drill.gate_counters(_spine, context_id, item, dev_root,
                                    _git_ops.repo_anchor(ctx, _spine))
    state = gate_briefs.gate_state(item, item_dir, dev_root, ctx.cwd,
                                   all_items=all_items, events=events, **counters)
    dep_root = deputy_core.deputy_root(context_id)  # mandate lives in the harness cell, not knowledge
    mandate = deputy_core.read_mandate(dep_root)
    digest = deputy_core.log_digest(item_dir, gate)  # this item's prior calls AT THIS GATE (continuity)
    # On a loop re-entry, feed a lean delta so the deputy re-judges the DELTA. A POINTER, never
    # ground truth.
    delta = _build_delta(item_dir, gate, state.get("numbers") or {})
    signal = _success_signal(dev_root, item) if gate == "review" else None
    # At review, surface the pending requests and which scopes are delegated, so the deputy can
    # grant or escalate.
    from ...core import artifacts as _arts
    auth_block = None
    verdicts = None
    if gate == "review":
        pending = _arts.pending_authorizations(item_dir)
        if pending:
            auth_block = kernel_speech.render_authorizations_block(
                pending, _spine.get_deputy_delegated_authority())
        # The vet's actual per-check verdicts — the brief alone carries only an entry count.
        verdicts = _arts.verdict_rows(item_dir)
    # The deputy reads the OWNER's document for this phase, not a re-flattening of it.
    prompt = kernel_speech.deputy_brief_block(
        item_id, str(item.get("title") or item_id), gate,
        state=state, report=_arts.report_text(item_dir, str(state.get("phase") or "")),
        mandate=mandate, log_digest=digest, delta=delta, success_signal=signal,
        verdicts=verdicts, authorizations=auth_block)
    system_append = kernel_speech.deputy_preamble(strictness)
    capture_prompt(context_id, f"[deputy] judging the {gate} gate", item_id=item_id)
    # Throwaway probes only. The deputy judges three gates and costs real tokens, so it needs a
    # capture site too.
    final_tokens = None
    final_usage = None
    live = LiveTokens()
    sink: dict = {}   # the deputy_verdict tool (run_tools) lands the verdict here
    turn = ResilientTurn("deputy judge", item_id=item_id,
                         notify=retry_notice(context_id, item_id, gate))
    # Built once, then both SNAPSHOTTED and SENT — see `runs.surface_from_turn`.
    turn_kwargs = dict(
        resume=None,  # fresh per gate — the deputy FORGETS
        model=model, effort=effort,
        approve=deny_all,                  # read-only judge: no writes, no shell side effects
        sandbox_writes=[],                 # …and sandboxed anyway: cwd only, no network
        extra_mcp_servers={**dev_mcp(ctx, ctx.cwd, item_id, scope="deputy"),
                           "deputy": make_deputy_verdict_server(sink)},
        system_append=system_append,
        item_bound=True,                   # judging one item — no board-wide in-progress list
        deny_write_tools=_READONLY_NUDGE,  # Write/Edit die outright — it inspects, never edits
    )
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=ctx, system_append=system_append,
                          prompt=prompt, phase=f"deputy:{gate}",
                          surface=surface_from_turn(turn_kwargs, mcp=["dev", "deputy"]),
                          background=True)
    async for ev in turn.stream(_agent, ctx, prompt, **turn_kwargs):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens = ev.tokens
            # Accumulated per-message usage (parent + subagents), not the parent-only
            # `Result.usage`; falls back when none arrived.
            final_usage = live.usage(ev.usage) or ev.usage
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    # A judge that never ran returns no verdict, which the caller treats as no judgment.
    return sink.get("verdict"), final_tokens, final_usage


# --------------------------------------------------------------------------- verdict → action

def _headline(text: str, cap: int = 240) -> str:
    """First paragraph of `text`, capped — the one line a decision bubble carries. The full rationale
    stays in the event meta."""
    head = (text or "").strip().split("\n\n")[0].replace("\n", " ").strip()
    return head if len(head) <= cap else head[:cap].rstrip() + "…"


_MD_LABEL = re.compile(r"^\*\*[^*]+:\*\*\s*")
_MD_MARK = re.compile(r"[*`_]")


def _plain(text: str, cap: int = 200) -> str:
    """The escalation's first line as PLAIN prose, for the event log.

    Every surface that reads an event summary renders it as text, so markdown emphasis shows through
    as asterisks. The card keeps its markup; the log sentence drops it."""
    return _headline(_MD_MARK.sub("", _MD_LABEL.sub("", (text or "").strip())), cap)


def _act_on_verdict(ctx, context_id: str, item_id: str, gate: str, verdict: dict | None) -> None:
    """Carry out the deputy's decision using the SAME levers the owner's gate buttons pull. A missing or
    invalid verdict falls to the owner, never a silent advance."""
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
    # Record the call first, so the next dispatch sees what it already decided even if the action
    # then fails.
    try:
        deputy_core.append_decision(dev_root / "work-items" / item_id, gate, decision, because,
                                    change=verdict.get("change"), authorize=verdict.get("authorize"))
    except Exception:
        log.exception("deputy decision-log append failed for %s", item_id)
    if decision == "approve":
        _do_approve(ctx, context_id, item_id, gate, verdict)
    elif decision == "send_back":
        # A send_back carrying `authorize` is a GRANT of a delegated authorization, never a new
        # ratify power.
        if (verdict.get("authorize") or "").strip():
            _do_grant(ctx, context_id, item_id, gate, verdict)
        else:
            _do_send_back(ctx, context_id, item_id, gate, verdict)
    else:  # escalate
        _do_escalate(ctx, context_id, item_id, gate, verdict, reason="deputy_escalated")


def _do_grant(ctx, context_id: str, item_id: str, gate: str, verdict: dict) -> None:
    """Grant a DELEGATED authorization the deputy judged ready.

    THE FLOOR IS MECHANICAL: the daemon grants only if the request's scope is in the owner's delegated
    set, and otherwise escalates. A grant records the decision and routes nothing."""
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
    # Re-check the declared scope against the staged ops: they can be restaged after the tool's
    # own check.
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
    """Advance the gate — the deputy pulls the owner's same advance lever. Cap-aware entering build."""
    from . import gates as gate_svc
    because = verdict.get("because", "")
    _dev_store.log_event(context_id, "deputy.approve",
                         f"Deputy approved the {gate} gate: {because[:160]}",
                         item_id=item_id, actor="deputy",
                         meta={"gate": gate, "checked": verdict.get("checked", "")[:400],
                               "because": because[:400],
                               # The headline the chat bubble shows: an approval that kept things
                               # moving still says so, in one line.
                               "speech": f"I approved the **{gate}** gate on your behalf. "
                                         + _headline(because)})
    gate_svc.autopilot_advance(ctx, context_id, item_id, actor="deputy")


def _do_send_back(ctx, context_id: str, item_id: str, gate: str, verdict: dict) -> None:
    """Negotiate the deputy's change by firing a REAL turn at the work-item agent: the phase that owns
    the fix re-runs, ends at its gate, and the deputy re-judges.

    Past the cap a send-back becomes an escalation."""
    dev_root = ctx.internal_root / "dev"
    change = verdict.get("change") or verdict.get("because") or ""
    # The cap is per-gate: a review loop gets its own tries, uncapped by earlier plan send-backs.
    prior = deputy_core.count_send_backs(dev_root / "work-items" / item_id, gate)
    if prior >= deputy_core.SEND_BACK_CAP:
        _do_escalate(ctx, context_id, item_id, gate, verdict, reason="send_back_cap",
                     override=(f"The deputy has sent this item back {prior}× and it still isn't "
                               f"meeting the bar. Latest change asked for: {change}"))
        return
    # The phase that owns the fix re-runs and rests at its gate, re-firing the seam so the deputy
    # re-judges.
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

    A byte slice lands mid-bullet, so cut on a line boundary, keep whole bullets, and say when
    anything was dropped."""
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
    """Page the owner with the deputy's instructions. The escalation text is the card the deputy owed —
    summary, concerns, what to do — never a bare "please review"."""
    dev_root = ctx.internal_root / "dev"
    escalation = override or verdict.get("escalation") or verdict.get("because") or "(no detail)"
    _dev.set_work_item_status(dev_root, item_id, "awaiting_human")
    _dev_store.log_event(context_id, "deputy.escalate",
                         f"Deputy escalated the {gate} gate to you: {_plain(escalation)}",
                         item_id=item_id, actor="deputy",
                         meta={"gate": gate, "reason": reason,
                               "escalation": _clip_card(escalation, 1600),
                               "checked": verdict.get("checked", "")[:400],
                               # `speech` is the headline: one line tells the owner they are
                               # needed, and the runbook rides `escalation`.
                               "speech": f"This one needs you at the **{gate}** gate. "
                                         + _headline(verdict.get("because") or escalation)})
