"""The decision ledger's ONE writer: a RULE an owner's ruling established becomes a `D-NNN` entry.

Most answers are not worth storing. "Delete this file" is spent the moment the file is gone. What
is worth storing is the RULE the answer established — a sentence binding work nobody has proposed
yet. So the promotion test is `Rule`, and the common case is that there is none.

Nothing here is authored: every field is copied from the typed proposal the owner ruled on.
`decisions.md` is append-only and never pruned, so a write path into it is a one-way valve.
"""

import re
from pathlib import Path

from . import artifacts as _arts

LEDGER_DOC = "decisions"
_HEADING = re.compile(r"^### (D-\d+)\s*·\s*(.+?)\s*·\s*(.+?)\s*$", re.M)
# Also the IDEMPOTENCY key: approve can fire more than once, and an append-only ledger cannot
# take an entry back.
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
    """Every entry as {id, title, status, body}. Headings ARE the index, so ids and titles scan cheaply."""
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
    """Monotonic, zero-padded, NEVER reused — derived from the highest ever written, not the count."""
    top = 0
    for e in entries:
        try:
            top = max(top, int(e["id"].split("-")[1]))
        except (IndexError, ValueError):
            continue
    return f"D-{top + 1:03d}"


def already_recorded(dev_root: Path, item_id: str, question: str) -> bool:
    """Has this ruling already landed? Compared on collapsed text, so a re-wrap is not a new ruling."""
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
    """One entry, every field copied. The HEADING is the rule, because the heading is the whole
        index a later phase reads."""
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
    """Append one entry per PROMOTABLE ruling. An answered question with no rule records nothing.

        Returns the ids written. Creates the ledger if the repo has none yet."""
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
    """The entries this item's gate recorded, read back from the provenance line the writer stamps."""
    want = str(item_id or "")
    if not want:
        return []
    return [e for e in read_entries(dev_root)
            if any(line.strip().startswith("- **Source**:") and want in line
                   for line in e["body"].splitlines())]


def settled_index(dev_root: Path) -> str:
    """The ledger as one scan line per entry — what a phase reads before asking anything.

        Headings only: the ledger grows forever, and a per-run cost that grows is a duty that gets dropped."""
    entries = read_entries(dev_root)
    if not entries:
        return "This project has no recorded decisions yet."
    return "\n".join(f"- `{e['id']}` [{e['status']}] {e['title']}" for e in entries)
