"""A filled artifact judged against its own template, and the owner's edits to one."""

import re
import tempfile
from datetime import datetime
from pathlib import Path

from ..vocab.kind_profiles import get_profile
from .text import FILL, atomic_write, _section_filled, split_sections
from .spec import (ARTIFACT_KINDS, _FM_BLOCK, _FM_RESEARCH_KIND, _PLAN_FEED_SECTIONS,
                   _PLAN_REQUIRED_LEGACY, _PLAN_REQUIRED_RESEARCH_V1, _PLAN_REQUIRED_V1,
                   _PLAN_REQUIRED_V2, _SPECS, artifact_file, section_spec)
from .vet_plan import _is_legacy_plan, parse_vet_plan, vet_plan_hard_issues
from .touches import touches_hard_issues
from .cycles import cycle_reports
from .ledger import evidence_status

def self_check(item_dir: Path, artifact: str, *, item_kind: str | None = None,
               path: Path | None = None) -> list[str]:
    """The gate-time validator: itemized issues, empty list means pass. Read-only. `path`
    overrides the default `artifacts/` location."""
    if artifact not in _SPECS:
        raise KeyError(f"unknown artifact kind {artifact!r} — known: {sorted(_SPECS)}")
    path = Path(path) if path else Path(item_dir) / "artifacts" / artifact_file(artifact)
    if not path.exists():
        return [f"{artifact_file(artifact)} does not exist — scaffold it first"]
    text = path.read_text(encoding="utf-8")
    issues: list[str] = []
    fills = FILL.findall(text)
    # A leftover slot in a handoff-brief marks an unfilled optional section; every other kind must
    # clear its slots.
    if fills and artifact != "handoff-brief":
        issues.append(f"{len(fills)} unfilled <fill:…> slot(s) remain — fill or remove them")
    sections = split_sections(text)
    # The shape the file was AUTHORED under, read from its own frontmatter — never the item's
    # current field.
    head = _FM_BLOCK.match(text)
    fam = _FM_RESEARCH_KIND.search(head.group(1)) if head else None
    spec = section_spec(artifact, item_kind, fam.group(1) if fam else None)
    is_impl_plan = (artifact == "plan"
                    and get_profile(item_kind).kind == "implementation")
    is_new_plan = artifact == "plan" and any(
        h in sections for h in ("Intent", "Verification plan", "Decisions & clarifications"))
    # Pre-renovation plans stay valid READ-ONLY, judged against the shape they were authored
    # under. Newest first.
    if artifact == "plan" and not is_new_plan:
        if is_impl_plan and _is_legacy_plan(sections):
            spec = [(h, True) for h in _PLAN_REQUIRED_LEGACY]
            is_impl_plan = False  # legacy shape: no vet-plan rules to enforce
        elif is_impl_plan and any(s in sections for s in _PLAN_FEED_SECTIONS):
            spec = [(h, True) for h in _PLAN_REQUIRED_V2]
        elif is_impl_plan:
            spec = [(h, True) for h in _PLAN_REQUIRED_V1]
        else:
            spec = [(h, True) for h in _PLAN_REQUIRED_RESEARCH_V1]
    for req, needs_fill in spec:
        if req not in sections:
            issues.append(f"missing required section '## {req}'")
        elif needs_fill and not _section_filled(sections[req]):
            issues.append(f"section '## {req}' is empty")
    # The pre-main gate consumes plan.md, so a plan whose checks a fresh agent could not execute
    # is not gate-ready.
    if is_impl_plan and ("Verification plan" in sections or "Vet plan" in sections):
        issues.extend(vet_plan_hard_issues(parse_vet_plan(text)))
    # The change-map feed (old v2 shape only): a plan CARRYING `## Touches` owes parseable rows.
    if is_impl_plan and "Touches" in sections and _section_filled(sections["Touches"]):
        issues.extend(touches_hard_issues(text))
    if artifact == "handoff-brief" and not issues:
        if not any(_section_filled(b) for b in sections.values()):
            issues.append("every section is empty — a brief needs at least one filled section")
    return issues


# owner edits

# Only the brief and the plan are owner-editable, because both state INTENT.
OWNER_EDITABLE: tuple[str, ...] = ("brief", "plan")

_EDITED_LINE = re.compile(r"(?m)^edited_by_owner:.*\n?")


def owner_edited_at(text: str) -> str | None:
    """The `edited_by_owner` stamp, or None. Readers use it to know the document is not what
    the agent last wrote."""
    m = _FM_BLOCK.match(text or "")
    if not m:
        return None
    got = re.search(r"(?m)^edited_by_owner:\s*(\S+)\s*$", m.group(1))
    return got.group(1) if got else None


def owner_edit(item_dir: Path, artifact: str, text: str, *,
               item_kind: str | None = None) -> list[str]:
    """Replace an owner-editable artifact, stamping `edited_by_owner`. WRITES NOTHING when the
    text breaks the contract — the same validator the gate runs.

    The stamp is the point: an agent re-reading this plan is reading the OWNER's words."""
    if artifact not in OWNER_EDITABLE:
        raise ValueError(f"{artifact!r} is not owner-editable — only {', '.join(OWNER_EDITABLE)} "
                         "state intent; the rest are records of what a run did")
    path = Path(item_dir) / "artifacts" / artifact_file(artifact)
    if not path.exists():
        return [f"{artifact_file(artifact)} does not exist — nothing to edit"]
    body = (text or "").replace("\r\n", "\n")
    stamp = datetime.now().isoformat(timespec="seconds")
    if (m := _FM_BLOCK.match(body)):
        fm = _EDITED_LINE.sub("", m.group(1)).rstrip()
        body = f"---\n{fm}\nedited_by_owner: {stamp}\n---\n" + body[m.end():]
    else:
        # An edit that dropped the frontmatter gets it back: downstream readers key on `artifact:`
        # and `item_kind:`.
        head = _FM_BLOCK.match(path.read_text(encoding="utf-8"))
        keep = _EDITED_LINE.sub("", head.group(1)).rstrip() if head else f"artifact: {artifact}"
        body = f"---\n{keep}\nedited_by_owner: {stamp}\n---\n" + body.lstrip("\n")
    # Judge the CANDIDATE, never the file: validating after the write leaves a rejected version
    # readable meanwhile.
    probe = Path(tempfile.mkdtemp(prefix="superme-edit-")) / path.name
    try:
        probe.write_text(body, encoding="utf-8")
        if (issues := self_check(item_dir, artifact, item_kind=item_kind, path=probe)):
            return issues
    finally:
        probe.unlink(missing_ok=True)
        probe.parent.rmdir()
    atomic_write(path, body)
    return []


def artifact_status(item: dict, item_dir: Path, repo_dir: Path | None = None) -> dict:
    """The COMPUTED per-artifact status map: {kind → {required, present, issues, status}},
    derived and never stored. The `plan` row also carries the evidence verdict."""
    profile = get_profile(item.get("kind"))
    out: dict[str, dict] = {}
    for kind in ARTIFACT_KINDS:
        if kind == "handoff-brief":
            continue  # lives in preliminary/, not artifacts/
        present = (Path(item_dir) / "artifacts" / artifact_file(kind)).exists()
        row: dict = {"required": kind in profile.required_artifacts, "present": present}
        if present:
            issues = self_check(item_dir, kind, item_kind=profile.kind)
            row["issues"] = issues
            row["status"] = "ok" if not issues else "incomplete"
        else:
            row["status"] = "missing"
        # The derived check verdict rides the `plan` row — the plan owns the vet checks.
        if kind == "plan" and cycle_reports(item_dir):
            row["evidence"] = evidence_status(item_dir, repo_dir)
        out[kind] = row
    return out
