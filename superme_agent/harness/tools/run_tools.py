"""Run-transport tools — the structured endings of kernel-fired runs (workflow-renovation-v2 §3.2).

Two tools, one pattern: a TWO-GROUP schema splits the payload at declaration time into `machine`
(the kernel routes/executes on it — never rendered to the user) and `user` (rendered wholesale —
the kernel may read it but never branches on it). Validated at call time; the tool RESULT returned
to the agent is minimal (`ok`), never an echo of the payload (no double-carry in long threads).
The payload reaches the firing runner through a per-run SINK dict bound at server build — the
runner reads it after the turn ends. This replaces the retired completion-report / deputy-verdict
FENCES and their regex parsers (core/session_contract.py).

  report_completion — every kernel-fired work-item run's FINAL act (the run protocol in the
                      Current-focus background variant names it; kernel_speech.work_item_preamble).
  deputy_verdict    — the deputy dispatch's FINAL act (its own contract, never report_completion;
                      named by kernel_speech.deputy_preamble).
"""

from __future__ import annotations

from typing import Annotated, Literal, Required, TypedDict

from .registry import ToolSpec, build_mcp_server


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


# The outcomes the kernel routes on. `needs_user` (plan's grill) and `split` (plan's
# sub-item outcome) are declared ahead of their full routing (renovation slices 3+) — until then
# both rest the item at the owner's gate, which is the safe reading of each.
RUN_OUTCOMES = ("success", "partial", "clean_noop", "blocked", "needs_user", "split",
                "revise", "exhausted", "stagnated")

DEPUTY_DECISIONS = ("approve", "send_back", "escalate")
DEPUTY_GATES = ("triage", "plan", "review")


# --------------------------------------------------------------------------- report_completion

# The four types a work-item's landing commit can honestly carry. `feat`/`fix`/`refactor` are a
# complete partition of "code changed" — did observable behaviour GAIN something, get CORRECTED,
# or stay the SAME — which turns an open-ended guess into a three-way decision. `chore` is the
# fourth because dependency/tooling/build items are genuinely none of the three; it is defined
# narrowly (not product code) so it does not become the drawer for "unsure".
# Deliberately absent: `docs` and `style` — vet has nothing to verify in either, so an item that
# would carry them is not an implementation item at all, it is a triage misclassification. `test`
# and `perf` are absent because build writes tests INSIDE the item it serves. Adding a type later
# leaves existing history valid; removing one does not.
COMMIT_TYPES = ("feat", "fix", "refactor", "chore")


class CommitSpec(TypedDict, total=False):
    type: Required[Annotated[Literal["feat", "fix", "refactor", "chore"],
                             "feat = observable behaviour gained something · fix = observable "
                             "behaviour was corrected · refactor = observable behaviour is "
                             "unchanged · chore = not product code at all (dependencies, build, "
                             "tooling). Pick by that question, not by which word sounds closest."]]
    subject: Required[Annotated[str,
                                "the commit subject WITHOUT the type prefix: imperative mood, "
                                "capitalized, no trailing period, at most 50 characters. Describe "
                                "the change to someone reading the project's history who has never "
                                "heard of this work-item — no ids, no phase names, no task numbers."]]


class CompletionMachine(TypedDict, total=False):
    outcome: Required[Annotated[Literal[
        "success", "partial", "clean_noop", "blocked", "needs_user", "split", "revise",
        "exhausted", "stagnated"],
        "success = the work is delivered · partial = you delivered what you could and recorded "
        "the rest as assumptions (a wall you couldn't pass yourself) · clean_noop = nothing to "
        "do · blocked = NOTHING was doable at all (reserve it for that — a wall on SOME tasks is "
        "partial, not blocked) · needs_user = the run must pause for the owner's answers (pair "
        "with user.questions) · split = this item should split into sub-items (plan phase only — "
        "machine.pointers names the child briefs + execution schedule) · revise = the PLAN "
        "must change, so the item goes back to plan. Two phases reach it: review, when the "
        "conversation concludes the work must change (carry the owner's words verbatim in "
        "user.summary), and build, when the plan cannot be built as written — build is guarded "
        "out of amending it, so this is the only way back · exhausted / stagnated "
        "= out of budget or no progress"]]
    counts: Annotated[dict, "small numeric facts about the run (e.g. tasks done / checks passed) "
                            "— numbers only, never prose"]
    pointers: Annotated[list[str], "paths of the artifacts this run produced or updated"]
    commit: Annotated[CommitSpec,
                      "review phase, code-producing items only — how this item should read in the "
                      "project's permanent history once it lands. You are the last phase that "
                      "knows what actually shipped, so you declare it and the kernel writes it."]


class OpenQuestion(TypedDict, total=False):
    question: Required[Annotated[str, "the question alone, phrased as a question — no rationale, "
                                      "no options; those are the fields below"]]
    recommend: Required[Annotated[str, "the answer you recommend, stated as an answer the owner "
                                       "can simply accept"]]
    why: Required[Annotated[str, "one line — the ground for that recommendation"]]
    instead: Annotated[str, "the alternative and the condition that would select it, as "
                            "'<alternative>, if <when you would pick it>'"]


class CompletionUser(TypedDict, total=False):
    summary: Required[Annotated[str, "one line — what this run accomplished or why it stopped"]]
    next: Required[Annotated[str, "one line — what should happen next"]]
    questions: Annotated[list[OpenQuestion],
                         "outcome needs_user only — one entry per open question, each carrying "
                         "its own recommendation; the owner can settle the round by accepting them"]


class ReportCompletionArgs(TypedDict, total=False):
    machine: Required[Annotated[CompletionMachine,
                                "routed by the kernel — never rendered to the user"]]
    user: Required[Annotated[CompletionUser,
                             "rendered to the user wholesale — never routed on"]]


def _open_questions(raw) -> tuple[list[dict], str]:
    """Normalize `user.questions` to the four-field shape, or return the retry-shaped complaint.
    A bare string is refused rather than wrapped: a question that arrived as prose is one the
    agent has not separated from its own reasoning, and that is exactly what the fields fix."""
    out: list[dict] = []
    for i, q in enumerate(raw or [], 1):
        if not isinstance(q, dict):
            return [], (f"user.questions[{i}] is a plain string. Each question is an object: "
                        "{question, recommend, why, instead?} — the question alone in `question`, "
                        "your recommended answer in `recommend`, its one-line ground in `why`.")
        fields = {k: str(q.get(k) or "").strip() for k in ("question", "recommend", "why", "instead")}
        missing = [k for k in ("question", "recommend", "why") if not fields[k]]
        if missing:
            return [], (f"user.questions[{i}] is missing {', '.join(missing)}. A question without a "
                        "recommendation is research that is not finished — answer it yourself or "
                        "recommend one.")
        out.append({k: v for k, v in fields.items() if v})
    return out, ""


SUBJECT_MAX = 50


def _commit_spec(raw) -> tuple[dict | None, str]:
    """Normalize `machine.commit`, or return the retry-shaped complaint.

    The commit-message rules that are MECHANICAL are checked here rather than asked for in prose,
    because this is the one artifact of a work-item that outlives the workspace: a subject that is
    too long or ends in a period is wrong forever. Imperative mood is the one rule left to the
    agent — nothing can check it."""
    if raw in (None, {}):
        return None, ""
    if not isinstance(raw, dict):
        return None, ("machine.commit is an object: {type, subject} — the conventional type, and "
                      "the subject WITHOUT its prefix.")
    ctype = str(raw.get("type") or "").strip()
    subject = str(raw.get("subject") or "").strip()
    if ctype not in COMMIT_TYPES:
        return None, (f"machine.commit.type must be one of: {', '.join(COMMIT_TYPES)}. Ask which "
                      "one by the behaviour: gained (feat) · corrected (fix) · unchanged "
                      "(refactor) · not product code (chore).")
    if not subject:
        return None, "machine.commit.subject is required — the summary line, no type prefix."
    if subject.lower().startswith(tuple(f"{t}:" for t in COMMIT_TYPES)):
        return None, ("machine.commit.subject carries its own type prefix. Give the subject alone; "
                      "the type is the separate field and the kernel joins them.")
    if len(subject) > SUBJECT_MAX:
        return None, (f"machine.commit.subject is {len(subject)} characters, over {SUBJECT_MAX}. "
                      "Say the change, not its justification — the body carries the why.")
    if subject.endswith("."):
        return None, "machine.commit.subject must not end in a period."
    if subject[0].isalpha() and not subject[0].isupper():
        return None, "machine.commit.subject starts lowercase — capitalize it."
    return {"type": ctype, "subject": subject}, ""


def _report_completion(*, completion_sink: dict | None = None, **_):
    """Validate + deliver one run's completion payload into the runner's per-run sink. The stored
    shape keeps top-level outcome/summary/next (what every existing consumer of the run.report
    event reads) alongside the full two-group payload."""
    async def report_completion(args: dict) -> dict:
        machine = args.get("machine") or {}
        user = args.get("user") or {}
        outcome = str(machine.get("outcome") or "").strip()
        
        if outcome not in RUN_OUTCOMES:
            return _err(f"machine.outcome must be one of: {', '.join(RUN_OUTCOMES)}.")
        summary = str(user.get("summary") or "").strip()
        nxt = str(user.get("next") or "").strip()
        if not summary or not nxt:
            return _err("user.summary and user.next are both required (one line each).")
        questions, bad = _open_questions(user.get("questions"))
        if bad:
            return _err(bad)
        if outcome == "needs_user" and not questions:
            return _err("outcome needs_user requires user.questions — what the owner must answer.")
        if questions and outcome != "needs_user":
            return _err("user.questions rides only with outcome needs_user.")
        commit, bad = _commit_spec(machine.get("commit"))
        if bad:
            return _err(bad)

        payload = {
            "outcome": outcome, "summary": summary, "next": nxt,
            "machine": {"outcome": outcome,
                        **({"counts": machine["counts"]} if machine.get("counts") else {}),
                        **({"pointers": [str(p) for p in machine["pointers"]]}
                           if machine.get("pointers") else {}),
                        **({"commit": commit} if commit else {})},
            "user": {"summary": summary, "next": nxt,
                     **({"questions": questions} if questions else {})},
        }
        
        if completion_sink is not None:
            completion_sink["report"] = payload
        return _ok("ok")
    return report_completion


# --------------------------------------------------------------------------- deputy_verdict

class VerdictMachine(TypedDict, total=False):
    decision: Required[Annotated[Literal["approve", "send_back", "escalate"],
        "approve advances the phase · send_back posts your change into the work-item and routes "
        "it back through build⟷vet · escalate pages the owner"]]
    gate: Required[Annotated[Literal["triage", "plan", "review"], "the gate you are judging"]]
    authorize: Annotated[str, "send_back only — the id of the DELEGATED authorization request you "
                              "are granting (from authorizations.md); the kernel re-checks scope "
                              "and refuses a grant the owner has not delegated"]


def _lines(raw) -> list[str]:
    """A list field → its non-empty lines, one point each. A single string is accepted and read as
    one point rather than refused: the shape is the contract, but a deputy that writes one line
    instead of a one-item list has still said the thing."""
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if not isinstance(raw, (list, tuple)):
        return []
    return [" ".join(str(x).split()) for x in raw if str(x).strip()]


def _escalation_md(esc: dict) -> str:
    """The page card, assembled HERE so every escalation reads the same (owner, 2026-08-08).

    The deputy supplies the parts; the kernel supplies the shape. Letting each run format its own
    produced one prose blob per escalation, and the owner reading it cold had to find the summary,
    the worry and the ask inside a wall. Bold labels + bullets, because the surfaces that render
    this already colour markdown — the structure is what makes the colour mean something."""
    out = [f"**Issue summary:** {' '.join(str(esc.get('summary') or '').split())}"]
    for label, key in (("Concern", "concerns"), ("What to do", "what_to_do")):
        if (points := _lines(esc.get(key))):
            out += ["", f"**{label}:**", *(f"- {p}" for p in points)]
    return "\n".join(out)


class VerdictEscalation(TypedDict, total=False):
    """The owner's page card. THREE PARTS, and the middle two are LISTS — one point per entry.

    A paged owner reads this cold, usually on a phone, deciding whether to stop what they are doing.
    Prose ran the situation, the worry and the ask together into one block they had to parse; the
    parts are what they actually scan. The kernel renders the markdown (see `_escalation_md`), so
    the shape cannot drift between deputies."""
    summary: Required[Annotated[str, "ONE line, plain and concrete: what is going on. No preamble, "
                                     "no restating the item title"]]
    concerns: Required[Annotated[list[str], "why this genuinely needs the owner — one short, plain "
                                            "line per concern, each standing on its own"]]
    what_to_do: Required[Annotated[list[str], "the owner's options or steps — one short, plain line "
                                              "each: the exact command or click path and what they "
                                              "should see, or, for a decision, each option with "
                                              "your recommendation marked"]]


class VerdictUser(TypedDict, total=False):
    checked: Required[Annotated[str, "what you actually inspected — artifacts by name (at review, "
                                     "the vet results too) and what convinced you; never a "
                                     "paraphrase of the brief"]]
    because: Required[Annotated[str, "ONE sentence under 200 characters — the ground for the "
                                     "decision, as the owner reads it in the channel. Not a "
                                     "summary of your reasoning: the single fact that decided it. "
                                     "Longer detail belongs in `checked`"]]
    change: Annotated[str, "send_back only — the one specific, actionable change the build/vet "
                           "agents must make"]
    escalation: Annotated[VerdictEscalation, "escalate only — the owner's page card"]


class DeputyVerdictArgs(TypedDict, total=False):
    machine: Required[Annotated[VerdictMachine, "executed by the kernel — never rendered"]]
    user: Required[Annotated[VerdictUser, "the Deputy sub-tab's approval-trace row — never "
                                          "routed on"]]


def _deputy_verdict(*, verdict_sink: dict | None = None, **_):
    """Validate + deliver the deputy's verdict into the dispatch's per-run sink, flattened to the
    shape the verdict executor acts on. Cross-field floor: `change` iff send_back (optional on a
    grant) · `escalation` iff escalate · `authorize` only with send_back."""
    async def deputy_verdict(args: dict) -> dict:
        machine = args.get("machine") or {}
        user = args.get("user") or {}
        decision = str(machine.get("decision") or "").strip()
        gate = str(machine.get("gate") or "").strip()
        if decision not in DEPUTY_DECISIONS:
            return _err(f"machine.decision must be one of: {', '.join(DEPUTY_DECISIONS)}.")
        if gate not in DEPUTY_GATES:
            return _err(f"machine.gate must be one of: {', '.join(DEPUTY_GATES)}.")
        checked = str(user.get("checked") or "").strip()
        because = str(user.get("because") or "").strip()
        if not checked or not because:
            return _err("user.checked and user.because are both required.")
        # `because` is the decision bubble the owner reads in the channel, so the length limit is
        # enforced, not suggested — an unenforced cap produced 300-char run-ons that truncated
        # mid-sentence. The reasoning that didn't fit belongs in `checked`, which the Deputy tab shows.
        if len(because) > 200:
            return _err(f"user.because is {len(because)} characters — it is the one line the owner "
                        "reads in the channel, so it must be a single sentence under 200. Say the "
                        "fact that decided it; move the rest into `checked`.")
        change = str(user.get("change") or "").strip()
        authorize = str(machine.get("authorize") or "").strip()
        esc = user.get("escalation") or {}
        if decision == "send_back":
            if not change and not authorize:
                return _err("send_back requires user.change — the one specific, actionable change "
                            "(or machine.authorize when granting a delegated request).")
        elif change:
            return _err("user.change rides only with decision send_back.")
        if authorize and decision != "send_back":
            return _err("machine.authorize rides only with decision send_back (a grant is a "
                        "send_back variant).")
        if decision == "escalate":
            if not str(esc.get("summary") or "").strip():
                return _err("escalate requires user.escalation.summary — ONE plain line saying "
                            "what is going on.")
            for key in ("concerns", "what_to_do"):
                if not _lines(esc.get(key)):
                    return _err(f"escalate requires user.escalation.{key} — a LIST, one short "
                                "plain line per point. A paragraph is not a list.")
        elif esc:
            return _err("user.escalation rides only with decision escalate.")
        escalation_text = _escalation_md(esc) if decision == "escalate" else ""
        if verdict_sink is not None:
            verdict_sink["verdict"] = {
                "decision": decision, "gate": gate, "checked": checked, "because": because,
                "change": change, "authorize": authorize, "escalation": escalation_text,
            }
        return _ok("ok")
    return deputy_verdict


# --------------------------------------------------------------------------- servers

REPORT_COMPLETION_TOOL = ToolSpec(
    "report_completion",
    "Report this run's structured completion payload to the kernel. Call once, as the run's "
    "final action.",
    ReportCompletionArgs, _report_completion,
)

DEPUTY_VERDICT_TOOL = ToolSpec(
    "deputy_verdict",
    "Emit the verdict for the gate being judged; the kernel executes it. Call once, as the "
    "dispatch's final action.",
    DeputyVerdictArgs, _deputy_verdict,
)


def make_run_report_server(sink: dict):
    """The `run` MCP server (report_completion only), bound to one run's sink — mounted by every
    kernel-fired work-item runner, read by it after the turn ends."""
    return build_mcp_server("run", [REPORT_COMPLETION_TOOL], completion_sink=sink)


def make_deputy_verdict_server(sink: dict):
    """The `deputy` MCP server (deputy_verdict only), bound to one dispatch's sink."""
    return build_mcp_server("deputy", [DEPUTY_VERDICT_TOOL], verdict_sink=sink)
