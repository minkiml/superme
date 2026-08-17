"""The decision ledger's ONE writer: a RULE an owner's ruling established becomes a `D-NNN` entry.

An owner answers a research proposal's question once, and that answer lives in the item's own
`review.md` and nowhere else — so the next sweep over the same code raises the same question. The
fix is not to store the answer. **Most answers are not worth storing.** "Delete this file" is an
instruction: it is spent the moment the file is gone, and a reader who never heard of the item
learns nothing from it. What is worth storing is the RULE the answer established, when it
established one — a sentence that binds work nobody has proposed yet.

So the promotion test is `Rule`, and the common case is that there is none. See
`artifacts.proposal_promotable` for why the reserved reason cannot serve as that test: it says the
call was the owner's, which is a property of the action, not of the knowledge.

**Nothing here is authored.** Every field is copied from the typed proposal block the owner ruled on:
the rule, their ruling, the question, and the report's own rationale. That is the point of putting
the write in core rather than in a skill — `decisions.md` is append-only and, by its own contract,
never pruned, so a write path into it is a one-way valve. Keeping agents out of that valve buys one
line that can be stated and tested: **every entry traces to a question an owner was asked and
answered.** What no code can check is whether the rule really generalizes; that is the review
agent's judgement, which is why the owner sees the rule at the gate before approving.

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

The append-only ledger of standing rules: what now holds, why, and what settled it. An entry earns
its place by binding work nobody has proposed yet — a one-off instruction belongs to its work item.
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
    """One entry, every field copied. The HEADING is the rule, because the heading is the whole index
    a later phase reads (`settled_index`) — a reader scanning ids must see what holds, not which
    ticket it came from. `Why` carries the report's own reasoning, the text the owner was looking at
    when they ruled, rather than a fresh sentence about the ruling.

    There is no `Rejected` line. Nothing in the typed block records what was turned down, and an
    authored one is filler: the previous shape wrote "the alternative the question offered", which
    told a future reader exactly nothing."""
    rule = " ".join(str(prop["rule"]).split())
    why = prop.get("why_now") or "recorded from a research review's proposed work."
    return (
        f"\n### {entry_id} · {rule} · accepted\n"
        f"- **Date**: {date}\n"
        f"- **Rule**: {rule}\n"
        f"- **Why**: {why}\n"
        f"- **Ruling that settled it**: {prop['answer']}\n"
        + _SOURCE.format(item=item_id, question=" ".join(str(prop['question']).split())) + "\n"
    )


def record_rulings(dev_root: Path, item_dir: Path, item_id: str, *, date: str,
                   project: str = "Project") -> list[str]:
    """Append one entry per PROMOTABLE ruling in this item's review record — an answered question
    whose answer was written down as a rule. An answered question with no rule records nothing: it
    did its job inside the item and is spent.

    Returns the ids written (empty when there is nothing new, which is the ordinary outcome).
    Creates the ledger if the repo has none yet — a repo whose first rule arrives before anyone
    wrote the doc must not lose it."""
    answered = [p for p in _arts.research_proposals(item_dir) if _arts.proposal_promotable(p)]
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
        entries.append({"id": entry_id, "title": prop["rule"], "status": "accepted", "body": ""})
        written.append(entry_id)
    if written:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return written


def entries_for_item(dev_root: Path, item_id: str) -> list[dict]:
    """The entries THIS item's gate recorded — read back from the same provenance line the writer
    stamps. Shown on the item's own drilldown so the owner sees, where they ruled, which rule their
    ruling established: a memory nobody is told about is one they cannot rely on, and a rule they
    never saw stated is one they never agreed to."""
    want = str(item_id or "")
    if not want:
        return []
    return [e for e in read_entries(dev_root)
            if any(line.strip().startswith("- **Source**:") and want in line
                   for line in e["body"].splitlines())]


def settled_index(dev_root: Path) -> str:
    """The ledger as a scan line per entry — what a phase reads BEFORE asking the owner anything.

    Headings only, which works because the heading IS the rule. The ledger grows forever by design,
    so a reader that opened every body would get slower with every rule ever set, and a per-run cost
    that grows is a duty that gets dropped."""
    entries = read_entries(dev_root)
    if not entries:
        return "This project has no recorded decisions yet."
    return "\n".join(f"- `{e['id']}` [{e['status']}] {e['title']}" for e in entries)
