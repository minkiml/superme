"""The decision ledger's ONE writer: an owner ruling becomes a `D-NNN` entry in `general/decisions.md`.

An owner answers a research proposal's question once. Before this, that answer lived in the item's
own `review.md` and nowhere else, so the next sweep over the same code raised the same question and
the owner answered it again. The answer is the only part worth keeping — the QUESTION is not stored
anywhere, because a re-run of the sweep raises it again by itself, which is the reminder a parked
question could never be.

**Nothing here is authored.** Every field is copied from the typed proposal block the owner ruled on:
the question, the limb it passed, their answer, and the report's own rationale. That is the point of
putting the write in core rather than in a skill — `decisions.md` is append-only and, by its own
contract, never pruned, so a write path into it is a one-way valve. Keeping agents out of that valve
buys one line that can be stated and tested: **every entry traces to a question an owner was asked
and answered.**

This does NOT bend D7 ("an item's kind never writes general dev-knowledge"). D7 governs what an
item's AGENT may write, and the kernel is not the item. D7's rationale — anchor docs describe what is
in the main tree, so a research item owes them nothing — holds for the other six anchor docs and not
for this one: `decisions.md` is immutable HISTORY, not current-state truth, and a settled ruling is
exactly the history it exists to hold.
"""

import re
from pathlib import Path

from . import artifacts as _arts

LEDGER_DOC = "decisions"
_HEADING = re.compile(r"^### (D-\d+)\s*·\s*(.+?)\s*·\s*(.+?)\s*$", re.M)
# The provenance line. It is also the IDEMPOTENCY key: the approve path can fire more than once
# (a resume, a re-run, an owner re-approving after a revision), and an append-only ledger has no
# way to take an entry back. Matching on item + question means the second firing is a no-op rather
# than a duplicate nobody may delete.
_SOURCE = "- **Source**: {item} · owner ruling on: {question}"

_SKELETON = """# {project} — decisions

The append-only ledger of load-bearing choices: what we chose, why, and what we rejected.
Newest last. Never edit a past entry's body — reverse by appending a new one.

## Decisions
"""


def _path(dev_root: Path) -> Path:
    return Path(dev_root) / "general" / f"{LEDGER_DOC}.md"


def read_entries(dev_root: Path) -> list[dict]:
    """Every entry as {id, title, status, body}. Headings ARE the index — the doc's own contract
    says so — so a reader can scan ids and titles without paying for the bodies."""
    p = _path(dev_root)
    if not p.is_file():
        return []
    text = p.read_text()
    out: list[dict] = []
    marks = list(_HEADING.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append({"id": m.group(1), "title": m.group(2), "status": m.group(3),
                    "body": text[m.end():end].strip()})
    return out


def _next_id(entries: list[dict]) -> str:
    """Monotonic, zero-padded, NEVER reused — the id is the grep anchor and the supersession target,
    so it is derived from the highest one ever written, not from the count of live entries."""
    top = 0
    for e in entries:
        try:
            top = max(top, int(e["id"].split("-")[1]))
        except (IndexError, ValueError):
            continue
    return f"D-{top + 1:03d}"


def already_recorded(dev_root: Path, item_id: str, question: str) -> bool:
    """Has this item's ruling on this question already landed? Compared on the collapsed question
    text so a re-wrapped line in a revised report is not read as a second, different ruling."""
    want = " ".join(str(question or "").split())
    if not want:
        return False
    for e in read_entries(dev_root):
        for line in e["body"].splitlines():
            if line.strip().startswith("- **Source**:") and want in " ".join(line.split()):
                if item_id in line:
                    return True
    return False


def render_entry(entry_id: str, prop: dict, *, item_id: str, date: str) -> str:
    """One entry, every field copied. `Why` carries the report's own reasoning — the text the owner
    was looking at when they ruled — rather than a fresh sentence about the ruling."""
    why = prop.get("why_now") or "recorded from a research review's proposed work."
    return (
        f"\n### {entry_id} · {prop['answer']} · accepted\n"
        f"- **Date**: {date}\n"
        f"- **Decision**: {prop['answer']}\n"
        f"- **Why**: {why}\n"
        f"- **Rejected**: the alternative the question offered — "
        f"reserved for the owner because it is {prop.get('reserved_because') or 'unstated'}\n"
        + _SOURCE.format(item=item_id, question=" ".join(str(prop['question']).split())) + "\n"
    )


def record_rulings(dev_root: Path, item_dir: Path, item_id: str, *, date: str,
                   project: str = "Project") -> list[str]:
    """Append one entry per ANSWERED owner-reserved proposal in this item's review record.

    Returns the ids written (empty when there is nothing new). Creates the ledger if the repo has
    none yet — a repo whose first ruling arrives before anyone wrote the doc must not lose it."""
    answered = [p for p in _arts.research_proposals(item_dir)
                if p.get("question") and str(p.get("answer") or "").strip()]
    if not answered:
        return []
    p = _path(dev_root)
    text = p.read_text() if p.is_file() else _SKELETON.format(project=project)
    entries = read_entries(dev_root)
    written: list[str] = []
    for prop in answered:
        if already_recorded(dev_root, item_id, prop["question"]):
            continue
        entry_id = _next_id(entries)
        text = text.rstrip() + "\n" + render_entry(entry_id, prop, item_id=item_id, date=date)
        entries.append({"id": entry_id, "title": prop["answer"], "status": "accepted", "body": ""})
        written.append(entry_id)
    if written:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return written


def settled_index(dev_root: Path) -> str:
    """The ledger as a scan line per entry — what a phase reads BEFORE asking the owner anything.

    Headings only. The ledger grows forever by design, so a reader that opened every body would get
    slower with every ruling ever made, and a per-run cost that grows is a duty that gets dropped."""
    entries = read_entries(dev_root)
    if not entries:
        return "This project has no recorded decisions yet."
    return "\n".join(f"- `{e['id']}` [{e['status']}] {e['title']}" for e in entries)
