"""The work-item drilldown's ONE server-computed payload.

Every control carries a server-computed `active` and `reason`, so the greying rule has one writer.
Everything here is a PROJECTION of state computed elsewhere: this module adds no facts.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from ...core import artifacts as _arts
from ...core import decision_ledger as _ledger
from ...core import gate_briefs
from ...core.auth import auth_status
from ...core.vocab import kind_profiles, status_router
from . import attention as _attention
from .rerun import rerun_reason as _rerun_reason
from .resume import resume_reason as _resume_reason, RESUMABLE_PHASES

# Phases with a background run of their own — the same list Resume re-fires from, since it is one
# question.
RUNNABLE_PHASES = set(RESUMABLE_PHASES)

log = logging.getLogger("superme-agent")

# EVERY control is always rendered — greyed with its reason when unworkable, never hidden. `home`
# says where each lives.
ACTION_HOMES = {"approve": "actions", "run": "actions", "resume": "actions",
                "drop": "actions", "rerun": "actions",
                "pr": "git"}


def _label(phase: str | None) -> str:
    """A phase slug as the owner reads it. Title-case IS the FE's label for every phase, so a second
    mapping here would be a second thing to keep in sync."""
    return (phase or "").title()


def _act(aid: str, label: str, *, active: bool, reason: str) -> dict:
    """One control. `reason` is required whether or not it is active: greyed it says what would make it
    live, live it says what clicking does."""
    return {"id": aid, "label": label, "home": ACTION_HOMES.get(aid, "actions"),
            "active": active, "reason": reason}


def _actions(item: dict, state: dict, *, running: bool, git_health: dict | None,
             paged: dict | None, next_phase: str | None,
             review_mode: str | None) -> list[dict]:
    terminal = bool(state.get("terminal"))
    phase = str(state.get("phase") or "")
    at_gate = bool(state.get("at_gate"))
    blocked = list(state.get("blocked_by") or [])
    merged = bool(item.get("git_merge_commit"))
    pr_open = bool(item.get("git_pr_opened_at")) and not merged
    # Passed in on its own, not read off `git_health`, which is None until a branch exists.
    mode = review_mode
    trunk = (git_health or {}).get("trunk") or "the anchor"
    out: list[dict] = []

    # The label must name the ACT. A research item has no branch, so its review-approve fires
    # `itemize` instead of merging.
    if phase == "review" and str(item.get("kind")) == "research":
        approve_label, approve_does = "Approve", (
            "accepts the findings and files `## Proposed work` as inbox items you can triage. "
            "Nothing merges — a research item changes what we know, not what ships.")
    elif phase == "review":
        approve_label, approve_does = "Approve & merge", (
            f"the review decision IS the merge: lands the branch on {trunk} (applies the staged "
            "knowledge delta, backup ref first), then advances to close. On conflicts it holds here "
            "so you can sync + resolve, then approve again."
            + (" This repo is `strict`, which means the DEPUTY cannot land it — its approval only "
               "opens the PR for you. Yours merges either way." if mode == "strict" else ""))
    else:
        approve_label = "Approve"
        approve_does = (f"advances to {_label(next_phase)}"
                        if next_phase else "advances to the next phase")
    # `not at_gate` is tested first, because between gates `blocked_by` previews the NEXT gate and
    # is not actionable now.
    out.append(_act("approve", approve_label,
                    active=at_gate and not terminal and not running and not blocked,
                    reason="nothing to decide: this item is terminal" if terminal else
                           f"the item is mid-`{phase}`; this gate opens when the phase ends"
                           if not at_gate else
                           "; ".join(blocked) if blocked else
                           "an agent is working — the gate waits for it" if running else
                           approve_does))
    # Drop — always available while the item lives.
    out.append(_act("drop", "Drop", active=not terminal,
                    reason="already terminal" if terminal else
                           "dispose the work-item — terminal, worktree removed, branch kept, "
                           "zero knowledge writes"))
    # One slot, one meaning. At a gate it belongs to Approve, on a stopped item to Resume.
    runnable = bool(RUNNABLE_PHASES & {phase}) and not at_gate and not terminal and not running \
        and str(item.get("status")) not in ("error", "done")
    out.append(_act("run", f"Run {_label(phase)}", active=runnable,
                    reason=f"fires the {phase} run — the manual driver when the item is not on "
                           f"autopilot" if runnable else
                           "this item is finished" if terminal else
                           "an agent is working on it right now" if running else
                           "the run stopped — Resume re-fires it"
                           if str(item.get("status")) == "error" else
                           f"`{phase}` ends at a gate — the decision is Approve" if at_gate else
                           f"`{phase}` has no run of its own to fire"))
    # Always rendered, greyed with its reason. The activation rule is the same function the route
    # calls, so it cannot 409.
    can_resume, resume_why = _resume_reason(item, running=running)
    out.append(_act("resume", "Resume", active=can_resume and not terminal, reason=resume_why))
    # Resume rewinds nothing; this rewinds everything but the item's identity. Deliberately not
    # conditioned on `error`.
    can_rerun, rerun_why = _rerun_reason(item, running=running)
    out.append(_act("rerun", "Re-run", active=can_rerun, reason=rerun_why))
    # Rendered in both modes: a `fast` repo with no PR button anywhere read as a missing feature.
    out.append(_act("pr", "Open PR page", active=bool((git_health or {}).get("branch_exists")),
                    reason="the review report beside the branch's diff, grouped by the plan's tasks "
                           "— readable in any mode, before or after the merge"
                           if (git_health or {}).get("branch_exists") else
                           "no branch yet — it is created when build starts"))
    # There is no second merge control: it was a second door onto review's Approve, with a weaker
    # lock.
    return _credential_gate(out)


# Drop disposes and the PR page only reads, so both stay live without a credential.
_NEEDS_CREDENTIAL = {"approve", "run", "resume", "rerun"}


def _credential_gate(actions: list[dict]) -> list[dict]:
    """Grey every control that would start agent work when nothing can authenticate.

    Overrides `active` last, so no earlier rule hands back a button the daemon will reject."""
    status = auth_status()
    if status["ready"]:
        return actions
    return [{**a, "active": False, "reason": status["detail"]}
            if a["id"] in _NEEDS_CREDENTIAL and a["active"] else a
            for a in actions]


def _blocking_children(item: dict, all_items: list[dict]) -> list[dict]:
    """The open children this item is waiting on, as ROWS the owner can act on.

    `close_readiness` reports them as joined ids, which is right for a mechanical check and useless as
    an answer."""
    _ok, open_ids = status_router.children_terminal(all_items, item.get("id"))
    by_id = {str(i.get("id")): i for i in all_items}
    rows = []
    for cid in open_ids:
        child = by_id.get(str(cid)) or {}
        rows.append({"id": str(cid), "title": str(child.get("title") or ""),
                     "phase": str(child.get("phase") or ""),
                     "status": str(child.get("status") or "")})
    return rows


def _attention_card(item: dict, state: dict, hold: dict | None, paged: dict | None,
                    actions: list[dict], proof: list[dict],
                    all_items: list[dict] | None = None) -> dict | None:
    """The what-you-need-to-do card: WHY, DO (the exact act and its one click), and BASIS.

    None when nothing needs the owner, which is the common case — the card is hidden rather than
    rendered empty. Composed from `classify_hold` and `_page_reason`."""
    status = str(item.get("status"))
    if state.get("terminal"):
        return None
    # Waiting on a child is also worth saying. It asks nothing, so it carries a route, not a
    # click.
    if status == "awaiting_child":
        kids = _blocking_children(item, all_items or [])
        n = len(kids)
        return {"kind": "awaiting_child",
                "why": (f"Waiting on {n} open sub-item{'' if n == 1 else 's'}. This item resumes on "
                        "its own as soon as the last one finishes." if n else
                        "Waiting on a sub-item, but none is open — nothing will release this "
                        "automatically, so it needs you."),
                "detail": "", "do": ("Nothing to do here — open a sub-item below to follow it"
                                     if n else "Clear it at this gate or drop it"),
                "click": "", "basis": [], "questions": [], "children": kids}
    if status != "awaiting_human":
        return None
    kind = str((hold or {}).get("kind") or "gate")
    why = str((paged or {}).get("headline") or (hold or {}).get("reason") or "")
    detail = str((paged or {}).get("detail") or "")
    questions = list((hold or {}).get("questions") or [])
    live = {a["id"] for a in actions if a["active"]}
    phase = str(state.get("phase") or "")

    # The act, and the one control that performs it. A card saying "decide" without naming where
    # is what this replaces.
    if questions or kind == "question":
        do, click = "Answer the questions in this item's chat", "chat"
    elif "approve" in live:
        # At review the diff is the thing to read first, so the card routes through the PR page.
        do = ("Read the diff on the PR page, then Approve" if phase == "review" and "pr" in live
              else "Approve the gate or give me your feedback")
        click = "approve"
    elif not state.get("at_gate"):
        # Between gates nothing in `blocked_by` is the owner's to clear, so do not send them at
        # that list.
        do, click = (f"Nothing to clear here — `{phase}` is still the item's phase. Run it, or "
                     "tell me in chat what should change", "chat")
    elif state.get("blocked_by"):
        do, click = ("Resolve what's open below — Approve activates when the must-resolve set is "
                     "empty", "")
    else:
        do, click = "Read the reports, then decide in chat or with the buttons below", "chat"

    # Pointers, never pasted content, each written as a route: "Go to <surface> → <where in it>".
    basis = [f"Go to Reports tab → see {phase.title()}"]
    # The report is the argument; the tasks and their checks are what was committed to. Named only
    # when non-empty.
    if proof:
        basis.append("Go to Task tab → see the tasks and the checks that prove them")
    if str((paged or {}).get("next") or ""):
        basis.append(str(paged["next"]))
    # An authorization is a DECISION rather than a state to wait out, so it gets its own route.
    if (n_auth := len(state.get("authorizations") or [])):
        basis.append(f"Go to Quick View → Authorization → grant or deny "
                     f"{n_auth} request{'' if n_auth == 1 else 's'}")
    if state.get("blocked_by") and state.get("at_gate"):
        basis.append(f"See Must resolve below - {len(state['blocked_by'])} to clear")
    return {"kind": kind, "why": why, "detail": detail, "do": do, "click": click,
            "basis": basis, "questions": questions, "children": []}


# One list, because "when did this item last enter this phase" is one question.
_PHASE_ENTRY = ("phase.advance", "review.route", "revise.route")


def _live_summary(item_dir: Path, phase: str, events: list[dict]) -> str:
    """This phase's summary line, but ONLY when it describes the pass you are looking at.

    Several reports are overwritten in place, so the test is whether one was written after the item
    last entered this phase."""
    path = Path(item_dir) / "reports" / f"report-{phase}.md"
    if not phase or not path.is_file():
        return ""
    try:
        written = path.stat().st_mtime
    except OSError:
        return ""
    # Three kinds move an item into a phase; the routes back to plan set it directly and log no
    # advance.
    entered = next((e.get("created_at") for e in events
                    if e.get("kind") in _PHASE_ENTRY), None)
    if entered:
        try:
            at = datetime.fromisoformat(str(entered)).timestamp()
        except ValueError:
            at = 0.0
        if written < at:
            return ""
    return _arts.report_summary(item_dir, phase)


def _standing_summary(item: dict, item_dir: Path, phase: str,
                      events: list[dict]) -> tuple[str, str]:
    """The summary the card shows, and WHICH phase concluded it.

    Prefer this phase's own; while it is still working, hold the last completed phase's conclusion
    rather than going blank. Handing back the phase is what makes holding it honest."""
    if (own := _live_summary(item_dir, phase, events)):
        return own, phase
    from ...core.vocab.kind_profiles import get_profile
    try:
        order = list(get_profile(str(item.get("kind") or "implementation")).phases)
    except KeyError:
        return "", ""
    if phase not in order:
        return "", ""
    for earlier in reversed(order[:order.index(phase)]):
        if (text := _arts.report_summary(item_dir, earlier)):
            return text, earlier
    return "", ""


def _about(item: dict, item_dir: Path, inbox_origin: str = "") -> list[dict]:
    """What this item IS, in the owner's own framing, as ordered rows.

    A LIST, not a dict: order is meaning here. Empty rows are dropped rather than rendered blank."""
    facts = _arts.triage_facts(item_dir)
    origin = ""
    spawned = item.get("spawned_from") or {}
    if isinstance(spawned, dict) and spawned.get("item"):
        # The note is the human sentence; the relation and parent id are the machine's version of
        # the same edge.
        origin = str(spawned.get("note") or "").strip() or f"spawned from {spawned['item']}"
    elif item.get("inbox_id"):
        # WHO filed it, not the mechanism: whether a person raised this tells the reader how much
        # intent to read in.
        who = "agent" if inbox_origin == "agent" else "owner"
        origin = f"Inbox ({who})"
    # This IS the veto surface. Reason and label ride together: a bare "small" is a label nobody
    # can argue with.
    scale = kind_profiles.item_scale(item)
    scale_why = str(item.get("scale_reason") or "").strip()
    # Same terms as scale. An empty value drops the row rather than printing a fabricated default.
    fam = kind_profiles.research_kind(item)
    fam_why = str(item.get("research_kind_reason") or "").strip()
    rows = [("Workflow", str(item.get("kind") or "implementation")),
            ("Scale", f"{scale} — {scale_why}" if scale_why else scale),
            ("Research kind", (f"{fam} — {fam_why}" if fam_why else fam) if fam else ""),
            ("Category", facts["category"]),
            ("Origin", origin),
            ("Background", facts["background"]),
            ("Problem", facts["problem"])]
    return [{"label": k, "value": v} for k, v in rows if v]


def gate_counters(spine, context_id: str, item: dict, dev_root: Path,
                  anchor: str | None) -> dict:
    """Every gate check the item folder cannot answer, read once → the kwargs `gate_state` wants.

    ONE READER, TWO CALLERS: the drilldown and the deputy. `None` means nobody could look, never zero."""
    from ...core import git_layer as _git
    is_research = str(item.get("kind")) == "research"
    fam = kind_profiles.research_kind(item) if is_research else None

    debug_tags = None
    if item.get("git_worktree") and anchor:
        try:
            debug_tags = _git.debug_tags(Path(item["git_worktree"]), anchor)
        except (_git.GitError, _git.GitBusy, OSError):
            debug_tags = None

    # Counted only where the project HAS standards: a young repo cannot fail to have read a doc it
    # lacks.
    standards_reads = None
    if not is_research:
        docs = [d for d in ("decisions", "architecture")
                if (Path(dev_root) / "general" / f"{d}.md").is_file()]
        if docs:
            standards_reads = sum(spine.read_hits(context_id, str(item.get("id")), phase="review",
                                                  needle=f"general/{d}.md") for d in docs)

    return {
        "subagents": (spine.subagent_count(context_id, str(item.get("id")), phase="investigate")
                      if is_research else None),
        "guide_reads": (spine.read_hits(context_id, str(item.get("id")), phase="investigate",
                                        needle=f"investigate/{kind_profiles.family_guide(fam)}")
                        if fam else None),
        "brief_sizes": (spine.brief_sizes(context_id, str(item.get("id")), phase="investigate")
                        if is_research else None),
        "debug_tags": debug_tags,
        "standards_reads": standards_reads,
    }


def build_payload(item: dict, item_dir: Path, dev_root: Path, main_repo_dir: Path | None, *,
                  all_items: list[dict], events: list[dict], git_health: dict | None,
                  review_mode: str | None = None, inbox_origin: str = "",
                  subagents: int | None = None, guide_reads: int | None = None,
                  brief_sizes: list[int] | None = None,
                  debug_tags: list[dict] | None = None,
                  standards_reads: int | None = None) -> dict:
    """The whole drilldown, server-side. One read of the item folder feeds every tab, so the surface
    polls one route instead of four.

    The spine-counted values arrive as parameters, so this function stays pure and testable."""
    item_dir = Path(item_dir)
    state = gate_briefs.gate_state(item, item_dir, dev_root, main_repo_dir,
                                   all_items=all_items, events=events, subagents=subagents,
                                   guide_reads=guide_reads, brief_sizes=brief_sizes,
                                   debug_tags=debug_tags, standards_reads=standards_reads)
    paged = state.get("paged")
    hold = (_attention.classify_hold(item, events)
            if str(item.get("status")) == "awaiting_human" else None)
    proof = _arts.proof_rows(item_dir)
    running = bool(item.get("running"))
    phase = str(state.get("phase") or "")
    try:
        next_phase = kind_profiles.next_phase(item.get("kind"), phase)
    except KeyError:
        next_phase = None
    actions = _actions(item, state, running=running, git_health=git_health, paged=paged,
                       next_phase=next_phase, review_mode=review_mode)
    summary_text, summary_phase = _standing_summary(item, item_dir, phase, events)
    return {
        "id": item["id"], "phase": phase, "gate": state.get("gate"),
        "gate_label": state.get("gate_label"), "at_gate": bool(state.get("at_gate")),
        "terminal": bool(state.get("terminal")),
        "now": {"phase": phase, "cycle": (state.get("numbers") or {}).get("cycle") or 0,
                "running": running,
                # This phase's own summary line, and only when it describes the pass on screen.
                "summary": summary_text,
                # Which phase concluded it: equal to `phase` for its own, an earlier one while
                # this phase still works.
                "summary_phase": summary_phase},
        "attention": _attention_card(item, state, hold, paged, actions, proof, all_items),
        "about": _about(item, item_dir, inbox_origin),
        "checks": state.get("checks") or [], "blocked_by": state.get("blocked_by") or [],
        "numbers": state.get("numbers") or {},
        "authorizations": state.get("authorizations") or [],
        "paged": paged, "actions": actions, "proof": proof,
        # Item-wide by nature — the lenses answer questions no single task owns — so they sit
        # beside the proof rows.
        "lenses": [{"lens": ln, **r} for ln, r in _arts.lens_reads(item_dir).items()],
        # Shown here because this is where the owner answered; a ruling that silently becomes
        # durable teaches nothing.
        "decisions": _ledger.entries_for_item(dev_root, str(item.get("id"))),
        # Which phases have a report; the Reports tab greys the rest rather than opening an empty
        # tab.
        "reports": [p for p in kind_profiles.get_profile(item.get("kind")).phases
                    if (item_dir / "reports" / f"report-{p}.md").is_file()],
    }
