"""Background-run completion contract — the READER side (workspace-workflow S5 / D2).

The kernel's structured end-of-run handshake: a background run ends with a fenced
`completion-report` block the kernel parses (outcome → run row + status router). Since BV-A1/BV-A2
the build⟷vet loop NEVER halts mid-loop — a non-crashed build always advances toward vet, the
recorded gap surfaces at review, and a contract change the build can't self-authorize is DEFERRED
via `request_authorization` (also surfaces at review), never a mid-build page. Only an infra crash
(`turn_error`) stops there. `approval_required` stays in the accept-list below for backward-compat
(an old report parses rather than being discarded) but it no longer holds — it advances like
`partial`. The WRITER side — the text that
asks for the fence — is `kernel_speech.BACKGROUND_RUN_CONTRACT` (appended to the system prompt on
`run_turn(background=True)` turns); keep the two in lockstep (the gstack dead-channel lesson —
one contract, tested both directions in scripts/test_thread3.py).

The kernel-assembled prompt blocks that used to live here (orient / handoff assemblers) moved to
`core/kernel_speech.py` — the registry of everything the kernel says to an agent.

Pure functions — no daemon imports.
"""

import re

from . import kernel_speech

RUN_OUTCOMES = ("success", "partial", "clean_noop", "blocked", "approval_required",
                "exhausted", "stagnated")

_REPORT_FENCE = re.compile(r"```completion-report\s*\n(.*?)```", re.S)

DEPUTY_DECISIONS = ("approve", "send_back", "escalate")

_VERDICT_FENCE = re.compile(r"```deputy-verdict\s*\n(.*?)```", re.S)


def completion_report_instructions() -> str:
    """The seam for the §7 report_completion tool swap: returns the background-run contract text
    (born in kernel_speech). Today it's prose the system layer appends; when the tool lands, this
    shrinks to "finish by calling report_completion" in ONE place."""
    return kernel_speech.BACKGROUND_RUN_CONTRACT


def parse_completion_report(text: str | None) -> dict | None:
    """Parse the LAST completion-report fence out of a run's final text → {outcome, summary, next},
    or None when absent/invalid (the caller treats that as a legacy/unstructured run). The outcome
    must be one of RUN_OUTCOMES — an unknown value invalidates the report rather than guessing."""
    if not text:
        return None
    matches = _REPORT_FENCE.findall(text)
    if not matches:
        return None
    fields: dict[str, str] = {}
    for line in matches[-1].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip().lower()] = v.strip()
    outcome = fields.get("outcome", "")
    if outcome not in RUN_OUTCOMES:
        return None
    return {"outcome": outcome, "summary": fields.get("summary", ""),
            "next": fields.get("next", "")}


def parse_deputy_verdict(text: str | None) -> dict | None:
    """Parse the LAST `deputy-verdict` fence out of a deputy dispatch's final text →
    {decision, gate, checked, because, change, escalation}, or None when absent/invalid. The writer
    side is `kernel_speech.DEPUTY_VERDICT_CONTRACT` (keep in lockstep). `decision` must be one of
    DEPUTY_DECISIONS — an unknown value invalidates the verdict rather than guessing (a malformed
    verdict falls through to the owner, never a silent auto-advance). Values may span multiple lines
    (a key with no `:` continues the previous field), so a runbook `escalation` survives wrapping."""
    if not text:
        return None
    matches = _VERDICT_FENCE.findall(text)
    if not matches:
        return None
    fields: dict[str, str] = {}
    key = None
    for line in matches[-1].splitlines():
        m = re.match(r"\s*([A-Za-z_]+)\s*:\s*(.*)$", line)
        if m and m.group(1).strip().lower() in (
                "decision", "gate", "checked", "because", "change", "authorize", "escalation"):
            key = m.group(1).strip().lower()
            fields[key] = m.group(2).strip()
        elif key is not None and line.strip():
            fields[key] = (fields[key] + "\n" + line.rstrip()).strip()
    decision = fields.get("decision", "")
    if decision not in DEPUTY_DECISIONS:
        return None
    return {"decision": decision, "gate": fields.get("gate", ""),
            "checked": fields.get("checked", ""), "because": fields.get("because", ""),
            "change": fields.get("change", ""), "authorize": fields.get("authorize", ""),
            "escalation": fields.get("escalation", "")}
