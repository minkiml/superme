"""The work-item drilldown's ONE server-computed payload (renovation v2 §4).

★ Why this exists. The drilldown used to assemble itself from a gate BRIEF plus a pile of FE-side
derivations: which button is live, why it's greyed, what the owner is actually being asked for. Two
consequences, both real:

- **Activation was decided in the component.** `approve_blocked_by` shipped on the brief and no
  component ever read it, so the greying rule lived in TypeScript beside the rule the backend
  enforces — two writers for one question (see `core/attention.py`'s note on the same class of bug).
  Every control here carries a server-computed `active` + `reason`.
- **A failing check was a coloured dot with its reason in a `title` attribute.** Owner rule: checks
  render as NAMED ROWS with the reason inline, visible; only the narrow must-resolve set
  (`gate_state.blocked_by`) actually greys Approve.

Everything here is a PROJECTION of state computed elsewhere — `gate_briefs.gate_state` for the
checks, `artifacts.proof_rows` for the connected proof view, `attention.classify_hold` for why the
item is parked. This module adds no facts; it decides what the surface shows.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from ...core import artifacts as _arts
from ...core import gate_briefs, kind_profiles
from . import attention as _attention
from .rerun import rerun_reason as _rerun_reason
from .resume import resume_reason as _resume_reason, RESUMABLE_PHASES

# Phases that HAVE a background run of their own to fire. Same list Resume re-fires from — one
# definition, because "can this phase be started" and "can this phase be restarted" are the same
# question asked at two moments. The gate phases in it (review/close) are excluded at the call site
# by `not at_gate`: at a gate the slot belongs to Approve.
RUNNABLE_PHASES = set(RESUMABLE_PHASES)

log = logging.getLogger("superme-agent")

# The controls the drilldown renders, in one place. `home` says where each lives: the frame's action
# bar (§4.1's closed gate set), or the Git tab (git work belongs to git). EVERY one is always
# rendered — greyed with its reason when it isn't workable, never hidden (owner rule: a greyed
# control that explains itself teaches the model; an absent one hides it).
# THREE slots in the bar, always (owner, 2026-07-31): the PRIMARY (Approve at a gate · Resume on a
# stopped item · Run <Phase> otherwise — resolved in that order by the FE), then Drop, then Re-run.
ACTION_HOMES = {"approve": "actions", "run": "actions", "resume": "actions",
                "drop": "actions", "rerun": "actions",
                "pr": "git"}


def _label(phase: str | None) -> str:
    """A phase slug as the owner reads it. Title-case IS the FE's label for every phase in every
    profile (`common.tsx` PHASES) — a second mapping here would be a second thing to keep in sync."""
    return (phase or "").title()


def _act(aid: str, label: str, *, active: bool, reason: str) -> dict:
    """One control. `reason` is required whether or not it's active: when greyed it says what would
    make it live, and when live it says what clicking does. A tooltip that only exists in the
    disabled case is how a button ends up meaning something different than the owner thinks."""
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
    # The repo's landing rule, passed in on its own — NOT read off `git_health`, which is None until
    # a branch exists and would silently make every pre-build item read as `fast`.
    mode = review_mode
    trunk = (git_health or {}).get("trunk") or "the anchor"
    out: list[dict] = []

    # Approve — the ONE gate decision. Its label at review names the act, and the act is the same in
    # both modes: the OWNER's approve merges. `strict` keys the PR-opening branch off
    # `actor != "owner"` (gates.py) because the owner IS the second pair of eyes that mode buys.
    # Do not condition this on `review_mode`: that was done on 2026-07-29 and a live approve merged
    # the branch under a button reading "Approve & open PR".
    if phase == "review":
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
    out.append(_act("approve", approve_label,
                    active=at_gate and not terminal and not running and not blocked,
                    reason="; ".join(blocked) if blocked else
                           "an agent is working — the gate waits for it" if running else
                           "nothing to decide: this item is terminal" if terminal else
                           f"the item is mid-`{phase}`; this gate opens when the phase ends"
                           if not at_gate else approve_does))
    # Drop — always available while the item lives (§2.1's table).
    out.append(_act("drop", "Drop", active=not terminal,
                    reason="already terminal" if terminal else
                           "dispose the work-item — terminal, worktree removed, branch kept, "
                           "zero knowledge writes"))
    # RUN — the ONE launch control (owner, 2026-07-31). It fires the current phase's own background
    # run: the manual driver for a repo that is not on autopilot, where triage/plan/build/vet each
    # need a hand. It replaced three buttons that split one job three ways —
    #   `Plan it`   gated on `status == "queued"`, a status NOTHING in the codebase ever writes,
    #               so it could never light up;
    #   `Run vet`   fired the vet run the loop already fires for itself;
    #   `Force <n>` skipped a non-gate phase, an override for a stalled loop — the state R1–R5 now
    #               own (a stall becomes `error`, and the way out is Resume, not a nudge).
    # One slot, one meaning: "start what this phase is for". At a gate the slot belongs to Approve;
    # on a stopped item it belongs to Resume (see the resolution order in the FE action bar).
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
    # Resume — the escape hatch for a STOPPED item (R4). Always rendered, greyed with its reason
    # when nothing has stopped: the activation rule is `resume_reason`, the SAME function the route
    # calls, so a button that looks live can never 409.
    can_resume, resume_why = _resume_reason(item, running=running)
    out.append(_act("resume", "Resume", active=can_resume and not terminal, reason=resume_why))
    # Re-run — the destructive restart, and a REAL button at all times while the item lives (owner,
    # 2026-07-31). Resume rewinds nothing; this rewinds everything except the item's identity. It is
    # not conditioned on `error` on purpose: an item wedged where Resume refuses and an item whose
    # work the owner simply wants done again both reach for the same control.
    can_rerun, rerun_why = _rerun_reason(item, running=running)
    out.append(_act("rerun", "Re-run", active=can_rerun, reason=rerun_why))
    # `continue` (BV-A1) was RETIRED 2026-07-31 with its trigger. It needed a build parked at
    # `awaiting_human` with a `paged` reason, and since "the loop never parks" (loop.py, owner
    # 2026-07-30) no such state exists: build's only resting klass is `needs_user`, whose report
    # outcome `_page_reason` does not match, and an infra fault now stops at `error` (R2) where
    # the way out is Resume. A button whose reason is always "nothing is parked right now" is a
    # button that has never been clickable.
    # Git tab. Rendered in both modes: a `fast` repo with no PR button anywhere read as a missing
    # feature, with nothing on screen saying why.
    out.append(_act("pr", "Open PR page", active=bool((git_health or {}).get("branch_exists")),
                    reason="the review report beside the branch's diff, grouped by the plan's tasks "
                           "— readable in any mode, before or after the merge"
                           if (git_health or {}).get("branch_exists") else
                           "no branch yet — it is created when build starts"))
    # There is NO second `merge` control (owner, 2026-08-03 — action, button and its FE branch all
    # deleted). It was a second door onto the review gate's own Approve, and the two doors did not
    # have the same lock: Approve is refused while an agent is running and while the mechanical
    # must-resolve set is non-empty, and `merge` was gated only on `pr_open and strict and not
    # merged` — so the redundant button was also the way around the gate it duplicated. One act,
    # one control. What the strict mode actually asks of the owner — read the diff first — is said
    # on the Approve card below, and the PR page is one line up.
    return out


def _attention_card(item: dict, state: dict, hold: dict | None, paged: dict | None,
                    actions: list[dict], proof: list[dict]) -> dict | None:
    """§4.2's WHAT-YOU-NEED-TO-DO card: WHY (the back story) · DO (the exact act + the one click) ·
    BASIS (what to check to decide) — plus the grill's questions when that's the hold.

    None when nothing needs the owner, and that is the common case: the card is hidden entirely
    rather than rendering an empty shell. Composed from the two existing readers — `classify_hold`
    (kind · reason · actor · questions) and `_page_reason` (headline · detail · next). No third
    walker over the event log."""
    if str(item.get("status")) != "awaiting_human" or state.get("terminal"):
        return None
    kind = str((hold or {}).get("kind") or "gate")
    why = str((paged or {}).get("headline") or (hold or {}).get("reason") or "")
    detail = str((paged or {}).get("detail") or "")
    questions = list((hold or {}).get("questions") or [])
    live = {a["id"] for a in actions if a["active"]}
    phase = str(state.get("phase") or "")

    # DO — the act, and the one control that performs it. A card that says "decide" without naming
    # where is the thing this card exists to replace.
    if questions or kind == "question":
        do, click = "Answer the questions in this item's chat", "chat"
    elif "approve" in live:
        # At the review gate the diff is the thing to read before deciding, so the card routes
        # through the PR page and lands on the SAME control every other gate lands on.
        do = ("Read the diff on the PR page, then Approve" if phase == "review" and "pr" in live
              else "Approve the gate or give me your feedback")
        click = "approve"
    elif state.get("blocked_by"):
        do, click = ("Resolve what's open below — Approve activates when the must-resolve set is "
                     "empty", "")
    else:
        do, click = "Read the reports, then decide in chat or with the buttons below", "chat"

    # REFERENCE — pointers, never pasted content, each written as a ROUTE the owner can follow:
    # "Go to <surface> → <where in it>". A bare noun ("Review") told them a thing exists without
    # saying where it lives, which is the one job this field has.
    basis = [f"Go to Reports tab → see {phase.title()}"]
    # The Task tab is the other half of the read: the report is the argument, the tasks and their
    # checks are what was actually committed to. Named only when there is something in it — a route
    # to an empty tab is worse than no route.
    if proof:
        basis.append("Go to Task tab → see the tasks and the checks that prove them")
    if str((paged or {}).get("next") or ""):
        basis.append(str(paged["next"]))
    # An authorization is the one blocker that is a DECISION rather than a state to wait out, and it
    # now has its own sub — so it gets its own route. Named before the generic must-resolve line,
    # which would otherwise send the owner to a list whose single row just points here.
    if (n_auth := len(state.get("authorizations") or [])):
        basis.append(f"Go to Quick View → Authorization → grant or deny "
                     f"{n_auth} request{'' if n_auth == 1 else 's'}")
    if state.get("blocked_by"):
        basis.append(f"See Must resolve below - {len(state['blocked_by'])} to clear")
    return {"kind": kind, "why": why, "detail": detail, "do": do, "click": click,
            "basis": basis, "questions": questions}


# Every event kind that puts an item INTO a phase. Kept as one list because "when did this item
# last enter this phase" is one question, and a reader that knows only some of the answers is
# reading a timestamp from a phase the item has already left.
_PHASE_ENTRY = ("phase.advance", "review.route", "revise.route")


def _live_summary(item_dir: Path, phase: str, events: list[dict]) -> str:
    """This phase's `**Summary:**` line — but ONLY when it describes the pass you are looking at.

    Every report is written as its phase's CLOSING act, and several are overwritten in place
    (`report-build.md` holds the newest cycle, not a history). Rendered unconditionally, the card
    therefore showed the PREVIOUS pass while the current one was still working: on a re-entered
    build it printed cycle 1's conclusion under a running cycle 2, and in autopilot — where an item
    is running almost the whole time — that was most of what the owner ever saw. Owner-reported
    2026-08-07.

    The test is one comparison: was the report written AFTER the item last entered this phase? A
    report older than the current phase-entry describes an earlier pass, whichever pass that was.
    Nothing to show is the honest answer, and the card renders nothing rather than a stale claim."""
    path = Path(item_dir) / "reports" / f"report-{phase}.md"
    if not phase or not path.is_file():
        return ""
    try:
        written = path.stat().st_mtime
    except OSError:
        return ""
    # `events` arrives newest-first, so the first entry event is the most recent one. THREE kinds
    # move an item into a phase, not one: `phase.advance` is the forward hop, and the two routes
    # BACK to plan (`review.route` from the owner's send-back, `revise.route` from a build that
    # concluded the plan must change) never log an advance — they set the phase directly. Anchoring
    # on `phase.advance` alone left the re-entered plan phase measured against a stale timestamp, so
    # the exact case this function exists for — a phase you have been in before — was the one it
    # still got wrong. Caught on the second cycle of a live item, 2026-08-07.
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
    """The summary the card shows, and WHICH phase concluded it → `(text, phase)`.

    Preferred: this phase's own, once it has written one. While a phase is still working it has
    written nothing, and the card used to go blank — so an item spends most of an autopilot run
    showing a header and no content, and the owner loses the last thing anybody concluded at exactly
    the moment they have to wait (owner, 2026-08-08).

    So: hold the LAST COMPLETED phase's conclusion until the current one replaces it, and hand back
    which phase it came from so the surface can say so. It is never passed off as the running
    phase's own — a stale claim under a running header is the failure the strict rule above exists
    to prevent, and labelling it is what makes holding it honest."""
    if (own := _live_summary(item_dir, phase, events)):
        return own, phase
    from ...core.kind_profiles import get_profile
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
    """`About this work-item` — what this item IS, in the owner's own framing, as ordered rows.

    It replaced `Item at a glance`, which answered a question nothing asked: Goal restated the title
    printed two inches above it, and Progress restated the tasks and checks the Task tab renders in
    full. This says what a reader opening a strange item actually needs — what kind of work it is,
    what category it was filed under, where it came from, and the problem it exists to solve.

    A LIST, not a dict: order is meaning here (what it is → where it came from → what it's for), and
    a dict's order is an implementation detail nobody should have to rely on. Empty rows are dropped
    rather than rendered blank — `Background` is optional in the brief by design."""
    facts = _arts.triage_facts(item_dir)
    origin = ""
    spawned = item.get("spawned_from") or {}
    if isinstance(spawned, dict) and spawned.get("item"):
        # The NOTE is the human sentence ("came out of the caching work"); the relation and the
        # parent's id are the machine's version of the same edge and live on the graph view.
        origin = str(spawned.get("note") or "").strip() or f"spawned from {spawned['item']}"
    elif item.get("inbox_id"):
        # WHO filed it, not the mechanism (owner, 2026-08-08). "Pushed from the inbox" described the
        # plumbing every inbox item shares; the fact worth a row is whether a person raised this or
        # an agent branched it off, because that is what tells the reader how much intent to read
        # into the ask. The origin list is the inbox row's own, so no new field is invented here.
        who = "agent" if inbox_origin == "agent" else "owner"
        origin = f"Inbox ({who})"
    rows = [("Workflow", str(item.get("kind") or "implementation")),
            ("Category", facts["category"]),
            ("Origin", origin),
            ("Background", facts["background"]),
            ("Problem", facts["problem"])]
    return [{"label": k, "value": v} for k, v in rows if v]


def build_payload(item: dict, item_dir: Path, dev_root: Path, main_repo_dir: Path | None, *,
                  all_items: list[dict], events: list[dict], git_health: dict | None,
                  review_mode: str | None = None, inbox_origin: str = "") -> dict:
    """The whole drilldown, server-side. One read of the item folder feeds every tab, so the surface
    polls one route instead of four."""
    item_dir = Path(item_dir)
    state = gate_briefs.gate_state(item, item_dir, dev_root, main_repo_dir,
                                   all_items=all_items, events=events)
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
                # This phase's own `**Summary:**` line — what it concluded, in the words the owner
                # reads in its report, and ONLY when it describes the pass on screen.
                "summary": summary_text,
                # WHICH phase concluded it. Equal to `phase` when it is this phase's own; an
                # earlier phase while this one is still working, so the card can label it.
                "summary_phase": summary_phase},
        "attention": _attention_card(item, state, hold, paged, actions, proof),
        "about": _about(item, item_dir, inbox_origin),
        "checks": state.get("checks") or [], "blocked_by": state.get("blocked_by") or [],
        "numbers": state.get("numbers") or {},
        "authorizations": state.get("authorizations") or [],
        "paged": paged, "actions": actions, "proof": proof,
        # The standing lenses' read of this cycle (verification-model §3). Item-wide by nature —
        # they answer questions no single task owns — so they sit beside the proof rows, not in one.
        "lenses": [{"lens": ln, **r} for ln, r in _arts.lens_reads(item_dir).items()],
        # Which phases have a report to read — the Reports tab greys the rest rather than offering a
        # tab that opens empty.
        "reports": [p for p in kind_profiles.get_profile(item.get("kind")).phases
                    if (item_dir / "reports" / f"report-{p}.md").is_file()],
    }
