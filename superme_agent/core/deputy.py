"""The deputy's durable artifacts — the mandate (governance) and the per-item decision log (continuity).
See general_docs/deputy-memory-lifecycle-design.md for the model.

The deputy SESSION is disposable (minted per gate). Two things carry forward, and they are DIFFERENT
KINDS of thing living in DIFFERENT homes:

  - **mandate.md** — the standing acceptance bar for THIS project. A GOVERNANCE artifact (sibling of
    constitutions), so it lives in the per-repo harness cell `local-harness/<id>/dev/deputy/` (via
    `deputy_root`), seeded from a template on connect/first-read and wiped on disconnect. The project's
    real bar lives in project-prd.md's success signals; the mandate adds what the PRD can't say, and is
    where cross-item consistency is CURATED (not reverse-engineered from a log tail).
  - **deputy-log.jsonl** — an append-only per-ITEM ledger in the item's own dir (`work-items/<id>/`),
    one JSON object per call. It is the forgetful deputy's CONTINUITY cache (each dispatch reads its own
    prior calls at this gate), NOT the accountability record: the permanent, never-deleted accountability
    trace already lives in the deputy run row (`run.outcome`) and the dev events
    (`deputy.approve`/`escalate`/`query`). So this file rightly GC's with the item — no never-delete
    conflict (the record survives via run+events).

Pure + file-based — no daemon imports; unit-testable without a spine. The strictness LEVELS live in
`kernel_speech` (they're prompt text); this module owns the on-disk artifacts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..harness.tools.run_tools import DEPUTY_DECISIONS
from ..paths import LOCAL_HARNESS_DIR

# Below this, the item's early-gate advances (triage/plan) are cheap and machine-checked; the
# send-back CAP (design §5.2 open-question 2, settled: max 3) bounds the deputy's own bouncing so a
# stuck item surfaces to the owner instead of looping build⟷vet forever.
SEND_BACK_CAP = 3


def deputy_root(repo_id: str) -> Path:
    """The dev-scope root under which the deputy's artifacts hang, in the per-repo operational cell:
    `local-harness/<id>/dev`. The mandate is a GOVERNANCE artifact (a sibling of constitutions), so
    it lives in the harness cell — NOT the knowledge home — and is wiped when the repo disconnects
    (the disconnect route rmtree's the whole cell). See general_docs/deputy-memory-lifecycle-design.md.
    `deputy_dir`/`mandate_path`/`log_path` all hang off the value this returns."""
    return LOCAL_HARNESS_DIR / repo_id / "dev"


def deputy_dir(dev_root: Path) -> Path:
    return Path(dev_root) / "deputy"


def mandate_path(dev_root: Path) -> Path:
    return deputy_dir(dev_root) / "mandate.md"


def log_path(item_dir: Path) -> Path:
    """This item's deputy decision log — an append-only JSONL trail in the item's OWN dir (one object
    per call). Note the arg is the ITEM dir (`work-items/<id>`), not the deputy home: the log is a
    per-item continuity cache, whereas the mandate is per-repo governance (see `mandate_path`)."""
    return Path(item_dir) / "deputy-log.jsonl"


MANDATE_TEMPLATE = (
    "# Deputy mandate\n\n"
    "Standing instructions for the agent that judges this project's gates on the owner's behalf "
    "while they are away. Read alongside `project-prd.md` — the deliverables' **success signals** "
    "there are the real acceptance bar; this file adds only what the PRD can't say.\n\n"
    "## This project's bar\n"
    "- _(What \"good\" means here beyond the per-deliverable success signals. Left blank ⇒ judge to "
    "the PRD signals + the general deputy floor.)_\n\n"
    "## Reserved for the owner — always escalate, never decide\n"
    "- Anything that changes the project's scope, direction, or public contract.\n"
    "- _(Add project-specific owner-only calls here.)_\n\n"
    "## Runbook conventions\n"
    "- When escalating a review, hand up a concrete runbook: what to open/run, what they should "
    "see, and the deliverable's success signal verbatim.\n"
)


def read_mandate(dev_root: Path, *, seed: bool = True) -> str:
    """The project mandate text. Seeds the template on first read (so a deputy always has a mandate
    to judge against) unless `seed=False`. Best-effort seeding — a read-only filesystem just yields
    the template text in-memory."""
    p = mandate_path(dev_root)
    if p.exists():
        return p.read_text()
    if seed:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(MANDATE_TEMPLATE)
        except OSError:
            pass
    return MANDATE_TEMPLATE


def _log_entries(item_dir: Path) -> list[dict]:
    """Parse this item's deputy-log.jsonl into rows (oldest first). Tolerant of a missing file or a
    torn last line — a decision is a record, never a reason to 500 a judgment."""
    p = log_path(item_dir)
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def pending_send_back(item_dir: Path) -> dict | None:
    """The item's latest decision when it is a `send_back`, else None.

    Read by Resume. A stopped item is by definition one whose run did not finish, so if the last
    thing the deputy said was "go fix this", that instruction has not been carried out — and
    re-firing the plain phase prompt drops it. Measured live: a research item resumed into its
    finished investigate session, replied "already complete", and no-oped, while the deputy went on
    correctly sending it back at a full pass per round.

    Re-delivering an instruction that WAS somehow acted on costs a cheap "already done"; losing one
    costs a guaranteed no-op and another deputy pass. The asymmetry decides it."""
    rows = _log_entries(item_dir)
    last = rows[-1] if rows else None
    return last if last and last.get("decision") == "send_back" else None


def append_decision(item_dir: Path, gate: str, decision: str, because: str, *,
                    change: str | None = None, authorize: str | None = None) -> None:
    """Append one deputy call to THIS item's log (one JSON object per line, never rewritten).
    `decision` is validated against DEPUTY_DECISIONS so a typo can't poison later reads/counting.
    `change` (the send-back's asked-for fix) and `authorize` (a delegated-grant id) ride along when
    present — they feed the next dispatch's delta."""
    if decision not in DEPUTY_DECISIONS:
        raise ValueError(f"unknown deputy decision {decision!r}")
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "gate": gate, "decision": decision,
             "because": " ".join((because or "").split())}
    if change:
        entry["change"] = " ".join(change.split())
    if authorize:
        entry["authorize"] = authorize
    p = log_path(item_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def item_decisions(item_dir: Path) -> list[dict]:
    """This item's deputy calls (every gate), oldest first."""
    return _log_entries(item_dir)


def gate_decisions(item_dir: Path, gate: str) -> list[dict]:
    """This item's deputy calls AT ONE GATE, oldest first — the continuity a fresh dispatch at that
    gate reads. Item+gate scoped by design: a gate's deputy never sees another gate's calls, and there
    is no cross-item precedent here (that is curated into the mandate)."""
    return [r for r in _log_entries(item_dir) if r.get("gate") == gate]


def count_send_backs(item_dir: Path, gate: str | None = None) -> int:
    """How many times the deputy has already sent this item back — the send-back cap counter. Scoped
    to a gate when `gate` is given (else item-wide, every gate)."""
    rows = gate_decisions(item_dir, gate) if gate else _log_entries(item_dir)
    return sum(1 for r in rows if r.get("decision") == "send_back")


def log_digest(item_dir: Path, gate: str) -> str:
    """The digest injected into a dispatch: this item's prior calls AT THIS GATE (its continuity).
    Empty when there are none (a first judgment carries no digest). Item+gate scoped — no cross-item
    precedent, no cross-gate calls."""
    mine = gate_decisions(item_dir, gate)
    if not mine:
        return ""
    lines = ["Your prior calls at this gate on this item:"]
    for r in mine:
        line = f"- **{r.get('decision')}** — {r.get('because') or ''}"
        if r.get("change"):
            line += f" (asked: {r['change']})"
        lines.append(line)
    return "\n".join(lines)
