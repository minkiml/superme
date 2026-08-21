"""The deputy's durable artifacts: the mandate (governance) and the per-item decision log.

The deputy SESSION is disposable, minted per gate. Two things carry forward, and they are
different kinds of thing in different homes:

  mandate.md        the standing acceptance bar for this project. A governance artifact, so it
                    lives in the per-repo harness cell and is wiped on disconnect.
  deputy-log.jsonl  an append-only per-ITEM ledger in the item's own dir. A continuity cache,
                    not the accountability record — that lives in the run row and dev events,
                    so this file rightly GCs with the item.

Pure and file-based. The strictness LEVELS live in `kernel_speech`; this owns the artifacts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..harness.tools.run_tools import DEPUTY_DECISIONS
from ..paths import LOCAL_HARNESS_DIR

# Bounds the deputy's own bouncing, so a stuck item surfaces to the owner instead of looping.
SEND_BACK_CAP = 3


def deputy_root(repo_id: str) -> Path:
    """The dev-scope root the deputy's artifacts hang under. The mandate is GOVERNANCE, so it
    lives in the harness cell, not the knowledge home, and is wiped when the repo disconnects."""
    return LOCAL_HARNESS_DIR / repo_id / "dev"


def deputy_dir(dev_root: Path) -> Path:
    return Path(dev_root) / "deputy"


def mandate_path(dev_root: Path) -> Path:
    return deputy_dir(dev_root) / "mandate.md"


def log_path(item_dir: Path) -> Path:
    """This item's deputy decision log. Note the arg is the ITEM dir: the log is per-item
    continuity, whereas the mandate is per-repo governance."""
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
    """The project mandate text. Seeds the template on first read so a deputy always has a bar
    to judge against. Best-effort — a read-only filesystem yields the template in memory."""
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
    """Parse this item's deputy-log.jsonl, oldest first. Tolerant of a missing file or a torn
    last line — a decision is a record, never a reason to 500 a judgment."""
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

    Read by Resume. A stopped item's run did not finish, so a last "go fix this" was never carried
    out, and re-firing the plain phase prompt drops it. Re-delivering one already acted on costs a
    cheap "already done"; losing one costs a guaranteed no-op."""
    rows = _log_entries(item_dir)
    last = rows[-1] if rows else None
    return last if last and last.get("decision") == "send_back" else None


def append_decision(item_dir: Path, gate: str, decision: str, because: str, *,
                    change: str | None = None, authorize: str | None = None) -> None:
    """Append one deputy call to this item's log, never rewritten. `decision` is validated so a
    typo cannot poison later counting. `change` and `authorize` ride along when present."""
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
    """This item's deputy calls AT ONE GATE, oldest first — the continuity a fresh dispatch
    reads. Item and gate scoped: no cross-gate calls, and no cross-item precedent."""
    return [r for r in _log_entries(item_dir) if r.get("gate") == gate]


def count_send_backs(item_dir: Path, gate: str | None = None) -> int:
    """How many times the deputy has sent this item back — the cap counter. Gate-scoped when
    `gate` is given, else item-wide."""
    rows = gate_decisions(item_dir, gate) if gate else _log_entries(item_dir)
    return sum(1 for r in rows if r.get("decision") == "send_back")


def log_digest(item_dir: Path, gate: str) -> str:
    """The digest injected into a dispatch: this item's prior calls at this gate. Empty for a
    first judgment."""
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
