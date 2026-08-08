"""The per-repo VERIFICATION LIBRARY — proven checks a repo keeps and every later item inherits
(verification-model design §8).

The defect it closes: every item's plan re-derived the same handful of checks from nothing. The
suite runs. The migration is reversible. The endpoint still answers. Each plan wrote them again,
slightly differently, and a repo learned nothing from the last twenty items it verified.

It is repo dev-knowledge — `general/verification.md`, the same tier as the PRD and architecture,
readable and editable by the owner in the same surface. NOT the shared asset pool, which is
cross-repo constitutions.

Two flavours, and the difference is who pays:

- **standing** — attached to EVERY implementation item's plan in this repo. The fundamentals.
- **available** — a catalogue a plan cites by id instead of re-authoring.

Governance, and every rule here exists because its absence has a specific failure:

- **Close writes.** It is already the sole writer of the anchor docs, and the only phase that can
  see whether a check turned out to be general rather than item-shaped.
- **Vet nominates, never writes** (`artifacts.record_nomination`). Vet has no writes into
  `general/`, and a repo-wide commitment must not land before the owner's review gate.
- **An entry must have run and passed at least once** (enforced at nomination) **and carry no item
  specifics** (enforced here, at the write). A library of untested or item-shaped hypotheses is
  worse than no library: the next plan inherits one, it does not work, and that item eats a cycle.
- **Available is the default; only the owner promotes to standing.** A standing entry taxes every
  future item in this repo forever — a spending decision, and the brake on accretion.

Entries are stored in the SAME grammar as a plan's `## Verification plan` checks
(`artifacts.parse_check_blocks`), so inheriting one is a copy rather than a translation.

Deterministic, file-based, spine-free — unit-testable without a daemon.
"""

import re
from pathlib import Path

from .artifacts import (FILL, VET_MODES, _split_sections, _vet_value, parse_check_blocks,
                        _VET_CHECK_ID, is_whole_suite_run)

LIBRARY_DOC = "verification"
TIERS = ("standing", "available")
_SECTION = {"standing": "Standing", "available": "Available"}
_TIER_OF = {v.lower(): k for k, v in _SECTION.items()}

# What "no item specifics" means mechanically. A `covers:` names this item's plan tasks; a bare
# `t<n>` is the same reference in prose; a 12-hex token is a work-item id. Each one turns a repo
# fact into an item fact, and the next plan inherits a check pointing at work that no longer exists.
_TASK_REF = re.compile(r"\bt\d+\b")
_ITEM_ID = re.compile(r"\b[0-9a-f]{12}\b")

_SEED = """# Verification library

Checks this repo has already proven, kept so the next item inherits them instead of re-deriving
them. Close writes here; vet nominates. Every entry has run and come back green at least once.

## Standing

Attached to every implementation item's verification plan in this repo. A standing entry taxes
every future item — promote one here only when the whole repo genuinely owes it.

## Available

Cited by id from a plan's verification plan when it fits the work. This is where a nomination
lands.

An entry is a standing question about the PRODUCT's behaviour, in the product's terms — "a ledger
written by an older version still reads", "money never renders with more than two decimals". The
project's own test suite does NOT belong here: running it is build's validation, which happens
every cycle and is re-run by the kernel to audit build's claim. A library of "the tests pass" is a
library of one thing nobody needed to inherit.
"""


def _dev(dev_root: Path):
    from .dev_knowledge import DevKnowledgeService
    return DevKnowledgeService(), Path(dev_root)


def read_doc(dev_root: Path) -> str:
    dev, root = _dev(dev_root)
    return dev.read_general_doc(root, LIBRARY_DOC) or ""


def seed(dev_root: Path) -> bool:
    """Create the library doc with its two sections if it is missing or has lost them. Called
    before a write rather than at repo connect, so repos that predate the library get one the first
    time close has something to put in it. True when it wrote."""
    text = read_doc(dev_root)
    if all(s in _split_sections(text) for s in _SECTION.values()):
        return False
    dev, root = _dev(dev_root)
    dev.write_general_doc(root, LIBRARY_DOC, _SEED if not text.strip() else
                          text.rstrip() + "\n\n" + "\n\n".join(
                              f"## {s}" for s in _SECTION.values()
                              if s not in _split_sections(text)) + "\n")
    return True


def read_library(dev_root: Path) -> dict:
    """`{standing: [entry, …], available: [entry, …]}` — each entry a check dict (the plan's own
    shape) plus `tier`. An absent or empty doc reads as two empty lists: a repo with no library is
    the normal starting state, never an error."""
    sections = _split_sections(read_doc(dev_root))
    out: dict = {}
    for tier, heading in _SECTION.items():
        out[tier] = [{**c, "tier": tier} for c in parse_check_blocks(sections.get(heading, ""))]
    return out


def entries(dev_root: Path) -> list[dict]:
    lib = read_library(dev_root)
    return [*lib["standing"], *lib["available"]]


def find_entry(dev_root: Path, entry_id: str) -> dict | None:
    return next((e for e in entries(dev_root) if e["id"] == entry_id), None)


def render_entry(check: dict, *, source: str = "") -> str:
    """One check dict → its markdown block. `covers` is deliberately dropped: it names the plan
    tasks of whatever item proved the check, and no other item has those tasks. `source` stamps an
    inherited copy so the plan gate and the plan report can tell it from an authored one."""
    lines = [f"### {check['id']}"]
    for field in ("proves", "traces", "mode", "scenario", "run", "expect"):
        if check.get(field):
            lines.append(f"- {field}: {check[field]}")
    if check.get("rubric"):
        lines.append("- rubric:")
        lines.extend(f"  - {c}" for c in check["rubric"])
    if source:
        lines.append(f"- source: {source}")
    return "\n".join(lines) + "\n"


def entry_issues(block: str) -> list[str]:
    """Itemized reasons a block cannot enter the library (empty = it may). Mechanical, and each rule
    maps to a way an inherited check wastes the next item's cycle."""
    checks = parse_check_blocks(block)
    if not checks:
        return ["a library entry is a `### <entry-id>` block with the check's fields under it"]
    issues: list[str] = []
    for c in checks:
        label = c.get("id") or "(unnamed)"
        if not _VET_CHECK_ID.match(c.get("id") or ""):
            issues.append(f"library entry {label!r}: id must be a lowercase slug ([a-z0-9-]+)")
        for field in ("proves", "traces", "mode", "scenario"):
            if not c.get(field):
                issues.append(f"library entry {label!r}: missing `{field}`")
        if c.get("mode") and c["mode"] not in VET_MODES:
            issues.append(f"library entry {label!r}: mode must be one of {'/'.join(VET_MODES)}")
        if not c.get("expect") and not c.get("rubric"):
            issues.append(f"library entry {label!r}: has no way to fail — it needs an `expect` "
                          "line, a rubric, or both")
        if c.get("covers"):
            issues.append(f"library entry {label!r}: drop `covers` — it names the plan tasks of the "
                          "item that proved this check, and no other item has them")
        # The library is a VERIFICATION catalogue. A whole-suite run is build's validation, which
        # every cycle already performs and the kernel audits — inheriting it here would hand each
        # future plan a check that re-runs the suite and files the result as the item's own proof.
        # That is exactly how it got here (design amendment 2026-08-07).
        if c.get("run") and is_whole_suite_run(str(c["run"])):
            issues.append(f"library entry {label!r}: `run:` is the project's whole test suite — "
                          "that is BUILD's validation, not a verification asset. The library holds "
                          "standing questions about the PRODUCT's behaviour, in the product's terms")
    if FILL.search(block):
        issues.append("unfilled <fill:…> slot(s) — an entry the next plan inherits cannot have gaps")
    for pat, what in ((_TASK_REF, "a task id (t<n>)"), (_ITEM_ID, "a work-item id")):
        if pat.search(FILL.sub("", block)):
            issues.append(f"the entry mentions {what} — a library entry describes the REPO, and "
                          "the item that proved it will be gone by the time this is inherited")
    return issues


# --- structural edits: the owner's promote/demote/drop ------------------------------------------
# Line-level surgery on the doc rather than a re-render, because the doc is the owner's to write in:
# prose under a heading, an entry they typed by hand, a comment on why something is standing. A
# read-modify-render round trip would quietly delete all of it.

def _blocks(body_lines: list[str]) -> list[tuple[str, list[str]]]:
    """A section's lines → [(entry-id or "", its lines)] — leading prose comes back under `""`."""
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in body_lines:
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            out.append((_vet_value(m.group(1)), [line]))
        else:
            out[-1][1].append(line)
    return out


def _sections(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Doc → (head lines before the first `## `, [(heading, body lines)])."""
    head: list[str] = []
    secs: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            secs.append((m.group(1).strip(), []))
        elif secs:
            secs[-1][1].append(line)
        else:
            head.append(line)
    return head, secs


def _render(head: list[str], secs: list[tuple[str, list[str]]]) -> str:
    parts = ["\n".join(head).rstrip()]
    for heading, body in secs:
        parts.append(f"## {heading}\n\n" + "\n".join(body).strip("\n"))
    return "\n\n".join(p for p in parts if p.strip()).rstrip() + "\n"


def _rewrite(dev_root: Path, entry_id: str, tier: str | None) -> bool:
    """Move `entry_id` to `tier`'s section, or drop it when tier is None. False when the entry isn't
    there (or the destination section is)."""
    text = read_doc(dev_root)
    head, secs = _sections(text)
    dest = _SECTION.get(tier or "")
    if tier is not None and not any(h == dest for h, _ in secs):
        return False
    moved: list[str] | None = None
    for i, (heading, body) in enumerate(secs):
        if heading.lower() not in _TIER_OF:
            continue
        kept: list[str] = []
        for eid, lines in _blocks(body):
            if eid == entry_id and moved is None:
                moved = [ln for ln in lines]
                continue
            kept.extend(lines)
        secs[i] = (heading, kept)
    if moved is None:
        return False
    if tier is not None:
        for i, (heading, body) in enumerate(secs):
            if heading == dest:
                secs[i] = (heading, [*body, "", *[ln.rstrip() for ln in moved]])
    dev, root = _dev(dev_root)
    dev.write_general_doc(root, LIBRARY_DOC, _render(head, secs))
    return True


def move_entry(dev_root: Path, entry_id: str, tier: str) -> bool:
    """Promote to standing / demote to available. The owner's call, and the only way an entry
    becomes standing — nothing in the loop may spend every future item's time on its own."""
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {'/'.join(TIERS)} (got {tier!r})")
    return _rewrite(dev_root, entry_id, tier)


def drop_entry(dev_root: Path, entry_id: str) -> bool:
    """Remove an entry entirely — the escape hatch for one that turned out not to generalise."""
    return _rewrite(dev_root, entry_id, None)


def standing_blocks(dev_root: Path) -> list[str]:
    """The standing entries as plan-ready blocks, stamped `source: standing`. The plan scaffold
    injects these, so inheritance is mechanical: a planner cannot forget what the repo always owes,
    and cannot silently reword it either."""
    return [render_entry(e, source="standing") for e in read_library(dev_root)["standing"]]
