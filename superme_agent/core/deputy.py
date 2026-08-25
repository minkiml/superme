"""The deputy's artifacts: the mandate, and the per-item decision log.

`mandate.md` is per-repo GOVERNANCE in the harness cell, wiped on disconnect. `deputy-log.jsonl`
is per-item continuity in the item dir, so it GCs with the item. Strictness lives in
`kernel_speech`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..harness.tools.run_tools import DEPUTY_DECISIONS
from .vocab.context import Context
from ..paths import LOCAL_HARNESS_DIR

# Bounds the deputy's own bouncing, so a stuck item surfaces to the owner instead of looping.
SEND_BACK_CAP = 3


def deputy_root(ctx: Context) -> Path:
    """The dev-scope root the artifacts hang under, wiped on disconnect.

    Takes a resolved Context: a caller's string would seed a mandate for a project that does not
    exist."""
    return LOCAL_HARNESS_DIR / ctx.id / "dev"


def deputy_dir(dev_root: Path) -> Path:
    return Path(dev_root) / "deputy"


def mandate_path(dev_root: Path) -> Path:
    return deputy_dir(dev_root) / "mandate.md"


def log_path(item_dir: Path) -> Path:
    """This item's deputy decision log. The arg is the ITEM dir: per-item continuity, not per-repo
    governance."""
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
    """The project mandate. Seeds the template on first read so a deputy always has a bar. Best-effort."""
    p = mandate_path(dev_root)
    if p.exists():
        return p.read_text(encoding="utf-8")
    if seed:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(MANDATE_TEMPLATE, encoding="utf-8")
        except OSError:
            pass
    return MANDATE_TEMPLATE


def _log_entries(item_dir: Path) -> list[dict]:
    """Parse deputy-log.jsonl, oldest first. Tolerates a missing file or a torn last line — never
    500 a judgment."""
    p = log_path(item_dir)
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def pending_send_back(item_dir: Path) -> dict | None:
    """The latest decision if it is a `send_back`, else None. Re-delivering one is cheap; losing one
    guarantees a no-op."""
    rows = _log_entries(item_dir)
    last = rows[-1] if rows else None
    return last if last and last.get("decision") == "send_back" else None


def append_decision(item_dir: Path, gate: str, decision: str, because: str, *,
                    change: str | None = None) -> None:
    """Append one deputy call, never rewritten. `decision` is validated so a typo cannot poison
    later counting."""
    if decision not in DEPUTY_DECISIONS:
        raise ValueError(f"unknown deputy decision {decision!r}")
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "gate": gate, "decision": decision,
             "because": " ".join((because or "").split())}
    if change:
        entry["change"] = " ".join(change.split())
    p = log_path(item_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def item_decisions(item_dir: Path) -> list[dict]:
    """This item's deputy calls (every gate), oldest first."""
    return _log_entries(item_dir)


def gate_decisions(item_dir: Path, gate: str) -> list[dict]:
    """This item's deputy calls AT ONE GATE, oldest first. No cross-gate calls, no cross-item precedent."""
    return [r for r in _log_entries(item_dir) if r.get("gate") == gate]


def count_send_backs(item_dir: Path, gate: str | None = None) -> int:
    """How many times the deputy sent this item back — the cap counter. Gate-scoped when `gate` is given."""
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
