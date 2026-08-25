"""Knowledge-delta pipeline — write discipline for the dev-knowledge anchor docs.

No freehand writes: the CLOSING run supplies structured ops, they are validated, a
deterministic writer applies them. Close is the sole writer, so no doc outruns the code.
"""

import os
import re
from datetime import date, datetime
from pathlib import Path

from . import verification_library as _vl
from .artifacts import FILL, atomic_write
from .dev_knowledge import ANCHOR_DOCS, DevKnowledgeService, parse_deliverables

# The first three act on a section's BODY; rename_section rewrites the `## <heading>` LINE.
OPS = ("update", "append", "supersede", "rename_section")
# These two REPLACE a body, so each carries the text it expects to be replacing.
_REPLACING_OPS = ("update", "supersede")
_DOCS = (*ANCHOR_DOCS, "resources")

# Backticks only, deliberately: a prose mention is not a claim worth ground-truth checking.
_PATH_REF = re.compile(r"`([\w./-]+(?:/[\w./-]+|\.(?:py|ts|tsx|js|jsx|md|ya?ml|json|toml|sh)))`")
_SLUG_REF = re.compile(r"\bd-[a-z0-9][a-z0-9-]*\b")


def _doc_path(dev_root: Path, doc: str) -> Path | None:
    if doc == "resources":
        return Path(dev_root) / "general" / "resources" / "index.md"
    if doc in ANCHOR_DOCS:
        return Path(dev_root) / "general" / f"{doc}.md"
    return None


def _sections(text: str) -> list[str]:
    return re.findall(r"(?m)^##\s+(.+?)\s*$", text)


def _section_body(text: str, section: str) -> str | None:
    """The body under `## <section>`, or None when the heading is absent."""
    m = re.search(rf"(?ms)^##\s+{re.escape(section)}\s*\n(.*?)(?=^##\s|\Z)", text)
    return m.group(1) if m else None


def _same(a: str, b: str) -> bool:
    """Two section bodies read as the same text. Whitespace is layout, not content."""
    return " ".join((a or "").split()) == " ".join((b or "").split())


def validate_ops(ops: list, dev_root: Path, repo_dir: Path | None) -> list[str]:
    """Itemized validation issues for a list of edit ops; empty means valid. v1 never invents structure."""
    if not isinstance(ops, list) or not ops:
        return ["a delta needs a non-empty list of edit ops"]
    dev = DevKnowledgeService()
    prd_slugs = {d.get("id") for d in
                 parse_deliverables(dev.read_general_doc(dev_root, "project-prd") or "")}
    issues: list[str] = []
    seen: set[tuple[str, str]] = set()
    for i, op in enumerate(ops, 1):
        tag = f"op {i}"
        if not isinstance(op, dict):
            issues.append(f"{tag}: not a mapping")
            continue
        doc, section = str(op.get("doc") or ""), str(op.get("section") or "")
        kind, content = str(op.get("op") or ""), str(op.get("content") or "")
        replaces = kind in _REPLACING_OPS
        if kind not in OPS:
            issues.append(f"{tag}: op must be one of {'/'.join(OPS)} (got {kind!r})")
        p = _doc_path(dev_root, doc)
        if p is None:
            issues.append(f"{tag}: unknown doc {doc!r} — known: {', '.join(_DOCS)}")
            continue
        if not p.exists():
            issues.append(f"{tag}: {doc} has no anchor doc yet (onboarding seeds it)")
            continue
        if not section:
            issues.append(f"{tag}: missing section")
        elif section not in _sections(p.read_text(encoding="utf-8")):
            issues.append(f"{tag}: {doc} has no section '## {section}' — existing: "
                          f"{', '.join(_sections(p.read_text(encoding='utf-8'))) or '(none)'}")
        # Ops read the doc on disk, so a second op on one section reads pre-first-op text.
        elif (doc, section) in seen:
            issues.append(f"{tag}: {doc} § {section} is edited twice in this call — one op per "
                          f"section, carrying the whole body you want it to end with")
        # Two closes from one project state both replace this body; the later would silently
        # drop the other's write.
        elif replaces:
            live = _section_body(p.read_text(encoding="utf-8"), section) or ""
            if "expect" not in op:
                issues.append(f"{tag}: {kind} needs `expect` — the section body you composed "
                              f"against, so a section that moved under you refuses instead of "
                              f"overwriting. Read {doc} § {section} and pass what is there")
            elif not _same(str(op.get("expect") or ""), live):
                head = " ".join(live.split())[:180]
                issues.append(f"{tag}: {doc} § {section} MOVED since you read it — another item "
                              f"closed into it. Nothing was written. Re-read the section, fold "
                              f"your change into what is there now, and re-apply with the new "
                              f"`expect`. It currently starts: {head!r}")
        seen.add((doc, section))
        if not content.strip():
            issues.append(f"{tag}: empty content")
        if FILL.search(content):
            issues.append(f"{tag}: content contains <fill:…> placeholder(s)")
        if kind == "rename_section":
            # A single line, and no `##` marker — the writer supplies exactly one.
            if "\n" in content.strip():
                issues.append(f"{tag}: rename_section content is one heading line, not a body")
            if content.lstrip().startswith("#"):
                issues.append(f"{tag}: rename_section content is the heading TEXT — omit the '##'")
        for ref in _PATH_REF.findall(content):
            if os.path.isabs(ref):
                issues.append(f"{tag}: file reference must be repo-relative: {ref}")
            elif not repo_dir or not (Path(repo_dir) / ref).exists():
                issues.append(f"{tag}: referenced file does not exist in the tree: {ref}")
        for slug in _SLUG_REF.findall(content):
            if slug not in prd_slugs:
                issues.append(f"{tag}: deliverable {slug!r} is not defined in the project PRD")
        # The one anchor doc whose content is a CONTRACT: a later plan inherits its entries
        # verbatim.
        if doc == _vl.LIBRARY_DOC and kind != "rename_section":
            issues.extend(f"{tag}: {i}" for i in _vl.entry_issues(content))
    return issues


def apply_ops(dev_root: Path, ops: list) -> dict:
    """The deterministic writer: apply already-VALIDATED ops. Trusts its input and fails loud on a
    target that vanished."""
    if not ops:
        return {"applied": 0, "docs": []}
    texts: dict[str, str] = {}
    for op in ops:
        doc = str(op["doc"])
        p = _doc_path(dev_root, doc)
        text = texts.get(doc) if doc in texts else p.read_text(encoding="utf-8")
        section, content = str(op["section"]), str(op["content"]).rstrip()
        m = re.search(rf"(?ms)^(##\s+{re.escape(section)}\s*\n)(.*?)(?=^##\s|\Z)", text)
        if not m:
            raise ValueError(f"section '## {section}' vanished from {doc} — restage the delta")
        if op["op"] == "rename_section":
            # Rewrite the heading LINE, leaving the body untouched.
            texts[doc] = text[:m.start(1)] + f"## {content.strip()}\n" + text[m.end(1):]
            continue
        # Agents often re-include the `## <section>` heading. The heading stays here, so a re-
        # included one doubles.
        content = re.sub(rf"(?ms)\A\s*##\s+{re.escape(section)}\s*\n+", "", content).rstrip()
        if op["op"] == "append":
            body = m.group(2).rstrip() + "\n\n" + content + "\n\n"
        else:  # both replace; the kind is intent, recorded in the event
            body = content + "\n\n"
        texts[doc] = text[:m.start(2)] + body + text[m.end(2):]
    for doc, text in texts.items():
        atomic_write(_doc_path(dev_root, doc), text)
    return {"applied": len(ops), "docs": sorted(texts)}


# `delta-<N>.md`, N advancing weekly from a Monday epoch — monotonic, so weeks never collide.

_EPOCH_MONDAY = date(1970, 1, 5)


def change_log_index(when: date | None = None) -> int:
    return ((when or date.today()) - _EPOCH_MONDAY).days // 7


def change_log_path(dev_root: Path, when: date | None = None) -> Path:
    return (Path(dev_root) / "general" / "change-logs"
            / f"delta-{change_log_index(when)}.md")


def append_change_log(dev_root: Path, item_id: str, title: str, ops: list,
                      when: date | None = None) -> str:
    """Append this item's entry to the current week's log. Append-only: the log is history."""
    path = change_log_path(dev_root, when)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "" if path.exists() else (
        f"# Change log — week {change_log_index(when)}\n\n"
        "What each closed work-item changed in the anchor docs, newest last.\n")
    stamp = datetime.now().isoformat(timespec="seconds")
    rows = "\n".join(
        f"| `{op.get('doc')}` · {op.get('section')} | {op.get('op')} | "
        f"{' '.join(str(op.get('content') or '').split())[:90]} | `{item_id}` |"
        for op in ops)
    entry = (f"\n## [{stamp}] {title or item_id}\n\n"
             "| doc · section | op | what changed | source item |\n"
             "|---|---|---|---|\n" + rows + "\n")
    atomic_write(path, (head + (path.read_text(encoding="utf-8") if path.exists() else "")).rstrip() + "\n" + entry)
    return str(path)


def freshness_lint(dev_root: Path, repo_dir: Path | None) -> list[str]:
    """The standing truth-decay check: dead file references and unresolvable roadmap pointers.
    Warnings, never blockers."""
    warnings: list[str] = []
    dev = DevKnowledgeService()
    for doc in _DOCS:
        p = _doc_path(dev_root, doc)
        if not p or not p.exists():
            continue
        for ref in _PATH_REF.findall(p.read_text(encoding="utf-8")):
            if repo_dir and not os.path.isabs(ref) and not (Path(repo_dir) / ref).exists():
                warnings.append(f"{doc}: referenced file no longer exists: `{ref}`")
    try:
        board = dev.roadmap_board(dev_root)
        for o in board.get("orphans") or []:
            parts = ", ".join(f"{k}={v}" for k, v in o.items() if k != "reason")
            warnings.append(f"roadmap: {o.get('reason', 'pointer')} break — {parts}")
    except Exception:  # noqa: BLE001 — lint must never take a merge down
        pass
    return warnings
