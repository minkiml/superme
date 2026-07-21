"""Knowledge-delta pipeline — D7's write discipline for the general dev-knowledge anchor docs
(workspace-workflow PRD stage S6).

Anchor docs (`general/{project-prd,spec,roadmap,architecture}.md` + `general/resources/index.md`)
must mirror CURRENT main-codebase truth. NO freehand writes: an implementation item DRAFTS a delta
while its context is hot (structured edit ops, staged item-local in `artifacts/
knowledge-delta.yaml`), the ops are VALIDATED (target section exists · no placeholders · file
references resolve against the tree that is about to become main · deliverable slugs resolve), and
a deterministic WRITER applies them atomically with the merge to main — merge + knowledge-write =
one "codebase changed" event. Pre-merge failure paths are clean by construction: an unapplied
staged file is just a draft; nothing to roll back.

Blocking children never write general knowledge (their merge target is the parent branch) — their
staged delta FOLDS into the parent's, applied when the family merges to main. Research items never
stage at all (KIND_PROFILES.knowledge_writes gates the tool).

A standing FRESHNESS LINT (run at every merge + on demand) detects truth decay: anchor-doc file
references that no longer exist, roadmap/PRD pointers that don't resolve.

Deterministic, file-based, spine-free — unit-testable without a daemon.
"""

import os
import re
from datetime import datetime
from pathlib import Path

import yaml

from .artifacts import FILL, _atomic_write
from .dev_knowledge import ANCHOR_DOCS, DevKnowledgeService, _parse_deliverables

DELTA_FILE = "knowledge-delta.yaml"
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


def delta_path(item_dir: Path) -> Path:
    return Path(item_dir) / "artifacts" / DELTA_FILE


def read_delta(item_dir: Path) -> dict | None:
    """The staged delta record {staged_at, applied_at, folded_into, ops: [...]}, or None. A file
    that doesn't parse reads as None (a broken draft never blocks anything — restage it)."""
    p = delta_path(item_dir)
    if not p.exists():
        return None
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("ops"), list):
        return None
    return data


def pending_delta(item_dir: Path) -> dict | None:
    """The staged delta iff it is still PENDING (neither applied nor folded into a parent)."""
    d = read_delta(item_dir)
    if d and not d.get("applied_at") and not d.get("folded_into"):
        return d
    return None


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


def stage_delta(item_dir: Path, ops: list) -> str:
    """Stage (or RESTAGE — a draft, freely replaceable until applied) the item's delta. The
    caller validates first; staging itself only records. Returns the file path."""
    path = delta_path(item_dir)
    rec = {"staged_at": datetime.now().isoformat(timespec="seconds"),
           "applied_at": None, "folded_into": None, "ops": ops}
    _atomic_write(path, yaml.safe_dump(rec, sort_keys=False, allow_unicode=True))
    return str(path)


def _stamp(item_dir: Path, **fields) -> None:
    d = read_delta(item_dir) or {"ops": []}
    d.update(fields)
    _atomic_write(delta_path(item_dir), yaml.safe_dump(d, sort_keys=False, allow_unicode=True))


def apply_delta(item_dir: Path, dev_root: Path) -> dict:
    """The deterministic writer: apply the item's PENDING ops to the anchor docs, atomically per
    doc, then stamp `applied_at`. `update`/`supersede` REPLACE the target section's body;
    `append` adds content at the section's end. The caller (the merge route) re-validates first —
    this function trusts its input and fails loud on a target that vanished mid-flight."""
    delta = pending_delta(item_dir)
    if not delta:
        return {"applied": 0}
    texts: dict[str, str] = {}
    for op in delta["ops"]:
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
    _stamp(item_dir, applied_at=datetime.now().isoformat(timespec="seconds"))
    return {"applied": len(delta["ops"]), "docs": sorted(texts)}


def fold_into_parent(child_item_dir: Path, parent_item_dir: Path, parent_id: str) -> int:
    """A blocking child's pending delta FOLDS into its parent's staged delta (D7) at the
    child→parent merge; it applies to the anchor docs only when the family merges to main.
    Returns the number of ops moved (0 = nothing pending)."""
    delta = pending_delta(child_item_dir)
    if not delta:
        return 0
    parent = read_delta(parent_item_dir)
    ops = (parent["ops"] if parent and not parent.get("applied_at") else []) + delta["ops"]
    stage_delta(parent_item_dir, ops)
    _stamp(child_item_dir, folded_into=parent_id)
    return len(delta["ops"])


def delta_status(item_dir: Path, dev_root: Path, repo_dir: Path | None) -> dict:
    """The readiness KNOWLEDGE ROW's data: `none-staged` · `staged` (n ops, currently valid) ·
    `invalid` (staged but failing validation — itemized) · `applied` · `folded`."""
    d = read_delta(item_dir)
    if d is None:
        return {"state": "none-staged", "ops": 0}
    if d.get("applied_at"):
        return {"state": "applied", "ops": len(d["ops"]), "applied_at": d["applied_at"]}
    if d.get("folded_into"):
        return {"state": "folded", "ops": len(d["ops"]), "folded_into": d["folded_into"]}
    issues = validate_ops(d["ops"], dev_root, repo_dir)
    if issues:
        return {"state": "invalid", "ops": len(d["ops"]), "issues": issues}
    return {"state": "staged", "ops": len(d["ops"])}


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
