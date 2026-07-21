"""The deputy's durable, per-project artifacts — mandate + decision log (autopilot slice 4).

The deputy SESSION is disposable (minted fresh per gate, dies when the gate is decided). What
carries forward across an item's gates and across the project's life is written to disk here, in
the repo's dev-knowledge home (`<id>-knowledge/dev/deputy/`):

  - **mandate.md** — who the deputy is for THIS project and the standing acceptance bar. Durable,
    versioned like any other artifact. Seeded from a template on first read; the owner / onboarding
    sharpens it (the project's real bar lives in project-prd.md's success signals).
  - **decisions.md** — an append-only ledger, one line per call: "I <decided> at <gate> on <item>
    because <ground>." It is the deputy's ONLY continuity (each dispatch reads it, never a live
    transcript) AND the owner's accountability trail ("here's what I passed on your behalf, and
    why"). Never rewritten, only appended — the same never-delete discipline the run trace runs
    under.

Pure + file-based — no daemon imports; unit-testable without a spine. The strictness LEVELS live in
`kernel_speech` (they're prompt text); this module owns the on-disk artifacts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .session_contract import DEPUTY_DECISIONS

# Below this, the item's early-gate advances (triage/plan) are cheap and machine-checked; the
# send-back CAP (design §5.2 open-question 2, settled: max 3) bounds the deputy's own bouncing so a
# stuck item surfaces to the owner instead of looping build⟷vet forever.
SEND_BACK_CAP = 3


def deputy_dir(dev_root: Path) -> Path:
    return Path(dev_root) / "deputy"


def mandate_path(dev_root: Path) -> Path:
    return deputy_dir(dev_root) / "mandate.md"


def log_path(dev_root: Path) -> Path:
    return deputy_dir(dev_root) / "decisions.md"


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


def _log_entries(dev_root: Path) -> list[dict]:
    """Parse decisions.md into rows (oldest first). Line shape (append-only):
    `<iso> · <item_id> · <gate> · <decision> — <because>`."""
    p = log_path(dev_root)
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [s.strip() for s in line.split("·", 3)]
        if len(parts) < 4:
            continue
        when, item_id, gate, tail = parts
        decision, _, because = tail.partition("—")
        rows.append({"at": when, "item_id": item_id, "gate": gate,
                     "decision": decision.strip(), "because": because.strip()})
    return rows


def append_decision(dev_root: Path, item_id: str, gate: str, decision: str, because: str) -> None:
    """Append one call to the ledger. Never rewrites. `decision` is validated against
    DEPUTY_DECISIONS so a typo can't poison later parsing/counting."""
    if decision not in DEPUTY_DECISIONS:
        raise ValueError(f"unknown deputy decision {decision!r}")
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    because = " ".join((because or "").split())  # one line — the ledger is line-per-entry
    line = f"{when} · {item_id} · {gate} · {decision} — {because}\n"
    p = log_path(dev_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(line)


def item_decisions(dev_root: Path, item_id: str) -> list[dict]:
    """This item's prior deputy calls, oldest first — the continuity a fresh dispatch reads."""
    return [r for r in _log_entries(dev_root) if r["item_id"] == item_id]


def count_send_backs(dev_root: Path, item_id: str) -> int:
    """How many times the deputy has already sent THIS item back — the send-back cap counter."""
    return sum(1 for r in item_decisions(dev_root, item_id) if r["decision"] == "send_back")


def log_digest(dev_root: Path, item_id: str, *, recent: int = 6) -> str:
    """The digest injected into a dispatch: this item's full trail (its continuity) plus the last
    few cross-item calls (standing precedent). Bounded, oldest-first, human-legible."""
    mine = item_decisions(dev_root, item_id)
    others = [r for r in _log_entries(dev_root) if r["item_id"] != item_id][-recent:]
    lines: list[str] = []
    if mine:
        lines.append("On this item:")
        lines += [f"- {r['gate']}: **{r['decision']}** — {r['because']}" for r in mine]
    if others:
        lines.append("Recent calls on other items (precedent):")
        lines += [f"- `{r['item_id']}` {r['gate']}: {r['decision']} — {r['because']}"
                  for r in others]
    return "\n".join(lines)
