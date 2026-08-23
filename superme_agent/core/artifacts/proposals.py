"""Research proposals: the typed `## Proposed work` a research item files."""

import re
from pathlib import Path

from .text import split_sections, clip
from .spec import artifact_file
from .vet_plan import vet_value

# ----------------------------------------------------- research proposals (the typed `## Proposed work`)

# A research item's findings imply work, but research may not DECIDE — each proposal carries its
# ruling.
_PROPOSAL_FIELDS = {
    "Title": "title", "Kind": "kind", "Why now": "why_now", "Delivers": "delivers",
    "Default applied": "default_applied", "Question": "question",
    "Reserved because": "reserved_because", "Suggested": "suggested", "Answer": "answer",
    # The rule the answer establishes, if any. Written WITH `Answer`, never before it. Empty is
    # the normal case.
    "Rule": "rule",
    # `Depends-on` is free prose and gates nothing. It gets its own key so it cannot run on into
    # `Why now`.
    "Depends-on": "legacy_depends_on",
}
_PROPOSAL_FIELD = re.compile(r"^\s*\*\*(" + "|".join(_PROPOSAL_FIELDS) + r"):\*\*\s*(.*)$")
# Closed set: an agent that must name which limb a question passes writes fewer questions.
RESERVED_REASONS = ("destructive", "expensive_to_reverse")
# `Becomes work` is a yes/no, and absent means yes — only a ruling that emptied a proposal must
# say so.
BECOMES_WORK = ("yes", "no")


def research_proposals(item_dir: Path) -> list[dict]:
    """`## Proposed work` → one dict per proposal. A block opens at `**Title:**`; a line
    matching no field header continues the one above."""
    path = Path(item_dir) / "artifacts" / artifact_file("review")
    if not path.is_file():
        return []
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    body = split_sections(text).get("Proposed work", "")
    out: list[dict] = []
    cur: dict | None = None
    field: str | None = None
    for line in body.splitlines():
        m = _PROPOSAL_FIELD.match(line)
        if m:
            key = _PROPOSAL_FIELDS[m.group(1)]
            if key == "title":
                cur = {v: "" for v in _PROPOSAL_FIELDS.values()}
                out.append(cur)
            if cur is None:      # a stray field before any title — nothing to attach it to
                continue
            field = key
            cur[key] = vet_value(m.group(2))
        elif cur is not None and field and line.strip():
            cur[field] = (cur[field] + " " + vet_value(line)).strip()
    return [p for p in out if p["title"]]


def proposal_is_withheld(prop: dict) -> bool:
    """A proposal the owner has not ruled on: it asks a question and carries no answer."""
    return bool(prop.get("question")) and not str(prop.get("answer") or "").strip()


def filed_and_withheld(props: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split proposals into (files now, waits for the owner). Per-proposal: one open
    question must not hold settled siblings."""
    filed = [p for p in props if not proposal_is_withheld(p)]
    return filed, [p for p in props if proposal_is_withheld(p)]


def proposal_promotable(prop: dict) -> bool:
    """Does this ruling establish something a LATER reader can use? The test is `Rule`,
    not `Reserved because`.

    Those answer different questions: a one-off destructive act produces a one-off answer. The common
    case is EMPTY."""
    return bool(str(prop.get("rule") or "").strip()) and bool(str(prop.get("answer") or "").strip())


def proposal_becomes_work(prop: dict) -> bool:
    """Does this proposal still describe WORK once the owner ruled on it? A ruling that
    emptied it leaves nothing to plan.

    Absent reads as YES: the ordinary proposal is work, and only an emptied one declares otherwise."""
    return str(prop.get("becomes_work") or "yes").strip().lower() != "no"


def filed_and_settled(props: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the FILE-ABLE proposals into (become work, settled with nothing to do).
    The second list is reported, never filed."""
    filed, _ = filed_and_withheld(props)
    return ([p for p in filed if proposal_becomes_work(p)],
            [p for p in filed if not proposal_becomes_work(p)])


def research_proposal_issues(props: list[dict]) -> list[str]:
    """Structural faults in the proposal blocks, read at the review gate.
    A malformed ruling field is worse than none."""
    issues: list[str] = []
    for p in props:
        label = clip(p.get("title") or "(untitled)", 60)
        if p.get("question"):
            reason = p.get("reserved_because", "")
            if not reason:
                issues.append(f"proposal {label!r}: asks the owner a question with no "
                              "`Reserved because` — name the limb it passes "
                              f"({' or '.join(RESERVED_REASONS)}) or decide it yourself")
            elif reason not in RESERVED_REASONS:
                # "Reserved BECAUSE" invites a reason, so the message names where the prose
                # belongs, not just what is wrong.
                first = reason.split()[0].rstrip(":,;.").strip("`*")
                hint = (f" — the word {first!r} is right; delete everything after it and put the "
                        "reasoning in `Suggested`" if first in RESERVED_REASONS else "")
                issues.append(f"proposal {label!r}: `Reserved because` must be one of "
                              f"{'/'.join(RESERVED_REASONS)} and nothing else "
                              f"(got {reason!r}){hint}")
            if not p.get("suggested"):
                issues.append(f"proposal {label!r}: a question with no `Suggested` makes the owner "
                              "do the research again — state the answer you would give")
        if p.get("answer") and not p.get("question"):
            issues.append(f"proposal {label!r}: carries an `Answer` with no `Question` — a ruling "
                          "with no question recorded cannot be read back")
        becomes = str(p.get("becomes_work") or "").strip().lower()
        if becomes and becomes not in BECOMES_WORK:
            issues.append(f"proposal {label!r}: `Becomes work` must be "
                          f"{' or '.join(BECOMES_WORK)} (got {p['becomes_work']!r})")
        elif becomes == "no" and not str(p.get("answer") or "").strip():
            issues.append(f"proposal {label!r}: says `Becomes work: no` with no `Answer` — a "
                          "proposal is only emptied by a ruling, so with no ruling it is still work")
        if str(p.get("rule") or "").strip() and not str(p.get("answer") or "").strip():
            issues.append(f"proposal {label!r}: carries a `Rule` with no `Answer` — a rule is what "
                          "the owner's ruling established, so it cannot be written before there is "
                          "a ruling to establish it")
        if p.get("question") and p.get("default_applied"):
            issues.append(f"proposal {label!r}: carries both `Default applied` and `Question` — a "
                          "call is either yours to make or the owner's, never both")
    return issues
