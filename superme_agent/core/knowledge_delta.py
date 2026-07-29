"""Knowledge-delta pipeline — D7's write discipline for the general dev-knowledge anchor docs
(workspace-workflow PRD stage S6).

Anchor docs (`general/{project-prd,spec,roadmap,architecture}.md` + `general/resources/index.md`)
must mirror CURRENT main-codebase truth. NO freehand writes: the CLOSING run supplies structured
edit ops, they are VALIDATED (target section exists · no placeholders · file references resolve
against the tree · deliverable slugs resolve), and a deterministic WRITER applies them.

**Close is the sole writer** (renovation §2.3, built 2026-07-30). The old shape — build stages
`knowledge-delta.yaml`, the merge applies it — is retired: it put the write on the far side of a
decision the owner had not made yet, and left a staged file that every gate had to reason about.
Close runs AFTER review locks the code, so there is no window in which a doc describes something
that has not landed, and nothing to roll back if the run fails.

Blocking children never write general knowledge: their merge target is the parent's branch, so
their content is not on main until the parent lands. The parent's close writes for the family.
Research items never write at all (KIND_PROFILES.knowledge_writes gates the tool).

A standing FRESHNESS LINT (run at every merge + on demand) detects truth decay: anchor-doc file
references that no longer exist, roadmap/PRD pointers that don't resolve.

Deterministic, file-based, spine-free — unit-testable without a daemon.
"""

import os
import re
from datetime import date, datetime
from pathlib import Path

from .artifacts import FILL, _atomic_write
from .dev_knowledge import ANCHOR_DOCS, DevKnowledgeService, _parse_deliverables

# update/append/supersede act on a section's BODY; rename_section rewrites the `## <heading>` LINE
# itself (BV-A2 small-fix: the tool used to edit bodies only, so a heading carrying stale text —
# e.g. a roadmap deliverable heading naming a renamed command — was unreachable).
OPS = ("update", "append", "supersede", "rename_section")
_BODY_OPS = ("update", "append", "supersede")
_DOCS = (*ANCHOR_DOCS, "resources")

# A backticked repo path worth ground-truth checking: contains a `/` or ends in a code/doc
# extension. Deliberately narrow (backticks only) — prose mentions aren't claims.
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


def validate_ops(ops: list, dev_root: Path, repo_dir: Path | None) -> list[str]:
    """Itemized validation issues for a list of edit ops (empty = valid). Checks, per op: shape
    (doc/section/op/content) · known + existing target doc · the `## section` exists in the live
    doc (v1 never invents structure — new sections are an owner/onboarding concern) · content is
    non-empty with no `<fill:…>` placeholders · every backticked file reference exists under
    `repo_dir` (the tree about to become main — a doc cannot acquire a dead pointer at write
    time) · every `d-<slug>` deliverable reference resolves against the PRD."""
    if not isinstance(ops, list) or not ops:
        return ["a delta needs a non-empty list of edit ops"]
    dev = DevKnowledgeService()
    prd_slugs = {d.get("id") for d in
                 _parse_deliverables(dev.read_general_doc(dev_root, "project-prd") or "")}
    issues: list[str] = []
    for i, op in enumerate(ops, 1):
        tag = f"op {i}"
        if not isinstance(op, dict):
            issues.append(f"{tag}: not a mapping")
            continue
        doc, section = str(op.get("doc") or ""), str(op.get("section") or "")
        kind, content = str(op.get("op") or ""), str(op.get("content") or "")
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
        elif section not in _sections(p.read_text()):
            issues.append(f"{tag}: {doc} has no section '## {section}' — existing: "
                          f"{', '.join(_sections(p.read_text())) or '(none)'}")
        if not content.strip():
            issues.append(f"{tag}: empty content")
        if FILL.search(content):
            issues.append(f"{tag}: content contains <fill:…> placeholder(s)")
        if kind == "rename_section":
            # content is the NEW heading LINE (not a body). It must be a single line and must not
            # carry its own `##` marker — the writer supplies exactly one.
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
    return issues


def apply_ops(dev_root: Path, ops: list) -> dict:
    """The deterministic writer: apply already-VALIDATED ops to the anchor docs, atomically per
    doc. `update`/`supersede` REPLACE the target section's body; `append` adds at the section's
    end; `rename_section` rewrites the heading LINE. The caller validates first — this function
    trusts its input and fails loud on a target that vanished mid-flight.

    Called at CLOSE, by the closing run (renovation §2.3). The staging half — `knowledge-delta.yaml`
    drafted at build and applied at the merge — is RETIRED: knowledge writes have one owner, and it
    is the phase that runs after the code is locked, so there is no window in which a doc describes
    something that hasn't landed."""
    if not ops:
        return {"applied": 0, "docs": []}
    texts: dict[str, str] = {}
    for op in ops:
        doc = str(op["doc"])
        p = _doc_path(dev_root, doc)
        text = texts.get(doc) if doc in texts else p.read_text()
        section, content = str(op["section"]), str(op["content"]).rstrip()
        m = re.search(rf"(?ms)^(##\s+{re.escape(section)}\s*\n)(.*?)(?=^##\s|\Z)", text)
        if not m:
            raise ValueError(f"section '## {section}' vanished from {doc} — restage the delta")
        if op["op"] == "rename_section":
            # Rewrite the `## <heading>` LINE, leaving the body (group 2) untouched. content is the
            # new heading text (validate_ops guarantees single-line, no leading '#').
            texts[doc] = text[:m.start(1)] + f"## {content.strip()}\n" + text[m.end(1):]
            continue
        # Forgiving writer: `content` is the section BODY, but agents frequently re-include the
        # `## <section>` heading at the top of their content (conflating "the section" with "its
        # body"). The heading is kept in place (group 1), so a re-included heading would DOUBLE it.
        # Strip a leading heading that repeats this section's own title.
        content = re.sub(rf"(?ms)\A\s*##\s+{re.escape(section)}\s*\n+", "", content).rstrip()
        if op["op"] == "append":
            body = m.group(2).rstrip() + "\n\n" + content + "\n\n"
        else:  # update | supersede — both replace; the op kind is intent, recorded in the event
            body = content + "\n\n"
        texts[doc] = text[:m.start(2)] + body + text[m.end(2):]
    for doc, text in texts.items():
        _atomic_write(_doc_path(dev_root, doc), text)
    return {"applied": len(ops), "docs": sorted(texts)}


# --------------------------------------------------------------------------- the change log
# `general/change-logs/delta-<N>.md`, N advancing WEEKLY (whole weeks since 1970-01-05, a Monday —
# a monotonic integer, so files sort and no two weeks collide). One entry per item that wrote:
# the heading names it, the table says which doc/section changed and how. The anchor docs say what
# is true NOW; this says when each truth arrived and which item brought it.

_EPOCH_MONDAY = date(1970, 1, 5)


def change_log_index(when: date | None = None) -> int:
    return ((when or date.today()) - _EPOCH_MONDAY).days // 7


def change_log_path(dev_root: Path, when: date | None = None) -> Path:
    return (Path(dev_root) / "general" / "change-logs"
            / f"delta-{change_log_index(when)}.md")


def append_change_log(dev_root: Path, item_id: str, title: str, ops: list,
                      when: date | None = None) -> str:
    """Append this item's entry to the current week's change log, creating the file on the week's
    first write. Append-only: the log is history, so an entry is never rewritten."""
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
    _atomic_write(path, (head + (path.read_text() if path.exists() else "")).rstrip() + "\n" + entry)
    return str(path)


def freshness_lint(dev_root: Path, repo_dir: Path | None) -> list[str]:
    """The standing truth-decay check (run at every merge + on demand): anchor-doc file
    references that no longer exist on main, and roadmap-board pointer breaks (a wave naming a
    deliverable the PRD doesn't define, an item pointing at a ghost). Warnings, never blockers —
    they surface in the next readiness knowledge row."""
    warnings: list[str] = []
    dev = DevKnowledgeService()
    for doc in _DOCS:
        p = _doc_path(dev_root, doc)
        if not p or not p.exists():
            continue
        for ref in _PATH_REF.findall(p.read_text()):
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
