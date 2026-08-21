"""`## From you` — the one artifact section the OWNER writes, and how it carries forward."""

import re
from pathlib import Path

from .text import FILL, _LABEL_LINE, _atomic_write, _one_line, _split_sections
from .spec import artifact_file

# --------------------------------------------------------------- `## From you` (the owner's input)

# The one section the OWNER writes. It lives in the triage brief, which the plan phase cold-starts
# from.
FROM_YOU = "From you"
_OWNER_BLOCKS = (("references", "Useful imported references"), ("notes", "Verification notes"))
# The bold source and em-dash are optional, so an older section's free prose still reads as slots.
_OWNER_BULLET = re.compile(r"^\s*[-*]\s+(?:\*\*(?P<source>[^*]+?)\*\*\s*[—-]\s*)?(?P<rest>.+?)\s*$")


def _owner_blocks(body: str) -> dict[str, str]:
    """`## From you`'s body → {references, notes} as RAW text. Only the two headings are
    structural."""
    keyed = {label.lower(): key for key, label in _OWNER_BLOCKS}
    out: dict[str, list[str]] = {key: [] for key, _ in _OWNER_BLOCKS}
    cur: str | None = None
    for line in re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).splitlines():
        if _LABEL_LINE.match(line):
            name, _, rest = line.partition(":**")
            key = keyed.get(name.strip("*").strip().lower())
            # ONLY our two labels are structural: treating an owner's own bold as a delimiter
            # would swallow what they typed.
            if key:
                cur = key
                if rest.strip():
                    out[cur].append(rest.strip())
                continue
        if cur:
            out[cur].append(line)
    return {k: FILL.sub("", "\n".join(v)).strip() for k, v in out.items()}


def _owner_slots(raw: str, *, sourced: bool) -> list[dict]:
    """One block's raw text → its slots. A non-bullet line is still a slot, so older free text
    stays addressable."""
    out: list[dict] = []
    for line in raw.splitlines():
        if not (text := line.strip()):
            continue
        m = _OWNER_BULLET.match(text)
        source, desc = (m.group("source") or "", m.group("rest")) if m else ("", text)
        out.append({"source": source.strip(), "description": desc.strip()} if sourced
                   else {"description": desc.strip()})
    return out


def owner_input(item_dir: Path) -> dict:
    """What the owner wrote into `reports/report-triage.md` § From you. `exists` says whether
    the triage brief is on disk."""
    path = Path(item_dir) / "reports" / "report-triage.md"
    if not path.is_file():
        return {"exists": False, "references": [], "notes": []}
    blocks = _owner_blocks(_split_sections(path.read_text()).get(FROM_YOU, ""))
    return {"exists": True,
            "references": _owner_slots(blocks["references"], sourced=True),
            "notes": _owner_slots(blocks["notes"], sourced=False)}


# The owner's standing input, carried to EVERY phase: each intake phase has its own session, so
# words are otherwise lost.
_CARRY_CAP = 1200
_DECISIONS = "Decisions & clarifications"


def carry_owner_input(item_dir: Path, *, cap: int = _CARRY_CAP) -> str | None:
    """The owner's durable words as one preamble block, or None. Read-only and
    failure-tolerant: never breaks a turn."""
    lines: list[str] = []
    try:
        own = owner_input(item_dir)
        for r in own.get("references") or []:
            src, desc = _one_line(r.get("source")), _one_line(r.get("description"))
            if desc:
                lines.append(f"- reference — {f'**{src}**: ' if src else ''}{desc}")
        for n in own.get("notes") or []:
            if desc := _one_line(n.get("description")):
                lines.append(f"- verification note — {desc}")
    except Exception:
        pass
    try:
        plan = Path(item_dir) / "artifacts" / artifact_file("plan")
        if plan.is_file():
            body = _split_sections(plan.read_text()).get(_DECISIONS, "")
            # Comment-only bodies are the SCAFFOLD, not an answer — the template ships its
            # instructions inside `<!-- -->`.
            body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
            for ln in (l.strip() for l in body.splitlines()):
                if ln and not ln.startswith("#"):
                    lines.append(f"- decision — {ln.lstrip('-* ')}" if not ln.startswith("-")
                                 else f"- decision — {ln.lstrip('-* ')}")
    except Exception:
        pass
    if not lines:
        return None
    block = "\n".join(lines)
    over = len(block) > cap
    if over:
        block = block[:cap].rsplit("\n", 1)[0]
    return (
        "\n**From the owner (carried forward — their words, not a summary):**\n" + block
        + ("\n- …truncated. The rest is in `reports/report-triage.md` § From you and "
           "`artifacts/plan.md` § Decisions & clarifications." if over else "")
        + "\n→ These are STANDING instructions for this item. They outrank your own reading of the "
          "task; if one conflicts with what you were about to do, follow it or say why you cannot."
    )


def _render_from_you(references: list[dict], notes: list[dict]) -> str:
    """The section, rebuilt whole. Both labels stay even when empty — they tell the owner
    the section is theirs."""
    out = [f"## {FROM_YOU}", ""]
    for key, label in _OWNER_BLOCKS:
        lines = []
        for slot in (references if key == "references" else notes):
            desc = _one_line(slot.get("description"))
            if not desc:
                continue    # an empty slot is not a slot — never render a bare bullet
            src = _one_line(slot.get("source"))
            lines.append(f"- **{src}** — {desc}" if src else f"- {desc}")
        out += [f"**{label}:**", ""] + (lines + [""] if lines else [""])
    return "\n".join(out).rstrip() + "\n"


def write_owner_input(item_dir: Path, *, references: list[dict],
                      notes: list[dict]) -> dict:
    """Replace `## From you` in the triage brief, leaving every other byte alone. The
    caller sends the WHOLE list."""
    path = Path(item_dir) / "reports" / "report-triage.md"
    if not path.is_file():
        raise FileNotFoundError("reports/report-triage.md does not exist — triage writes it first")
    text = path.read_text()
    section = _render_from_you(references, notes)
    pattern = re.compile(rf"^##[^\S\n]+{re.escape(FROM_YOU)}[^\S\n]*$.*?(?=^##[^\S\n]|\Z)",
                         re.M | re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _m: section + "\n", text, count=1)
    else:
        text = text.rstrip() + "\n\n" + section
    _atomic_write(path, text.rstrip() + "\n")
    return owner_input(item_dir)
