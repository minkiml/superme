"""How an artifact is CREATED: the deterministic scaffolder, and the handoff brief."""

import re
from datetime import date
from pathlib import Path

from ..vocab.kind_profiles import get_profile
from .text import atomic_write
from .templates import skill_template
from .spec import _SPECS, _template_name, artifact_file, required_sections

_HANDOFF = """# Handoff brief — {title}

## Background & why raised
<fill:the problem/story — why this came up>

## Discussion summary
<fill:what was discussed and concluded so far>

## Direction & options
<fill:high-level direction or alternatives, with leanings>

## Constraints & notes
<fill:constraints, tried-but-failed, out-of-scope>
"""


def _template(artifact: str, item_kind: str | None, research_kind: str | None = None) -> str:
    name = _template_name(artifact, item_kind, research_kind)
    if name:
        return skill_template(name)
    return {"handoff-brief": _HANDOFF}[artifact]


def _inject_checks(body: str, blocks: list[str]) -> str:
    """Append ready-made check blocks to the end of a plan's `## Verification plan` section."""
    m = re.search(r"(?ms)^##\s+Verification plan\s*$.*?(?=^##\s|\Z)", body)
    if not m or not blocks:
        return body
    add = "\n" + "\n".join(b.rstrip() + "\n" for b in blocks)
    return body[:m.end()].rstrip() + "\n" + add + "\n" + body[m.end():]


def scaffold(item_dir: Path, artifact: str, *, title: str = "", item_kind: str | None = None,
             item_id: str | None = None, standing: list[str] | None = None,
             research_kind: str | None = None) -> dict:
    """Deterministically scaffold one artifact skeleton. Never overwrites, and unknown kinds fail loud.

    The KERNEL attaches the repo's standing checks: a hand-copied entry is one rewording from
    another check."""
    if artifact not in _SPECS:
        raise KeyError(f"unknown artifact kind {artifact!r} — known: {sorted(_SPECS)}")
    item_kind = get_profile(item_kind).kind
    from ..vocab.kind_profiles import RESEARCH_KINDS
    if research_kind not in RESEARCH_KINDS:
        research_kind = None  # forgiving, like kind_profiles.research_kind — unjudged is a state
    adir = Path(item_dir) / "artifacts"
    path = adir / artifact_file(artifact)
    sections = list(required_sections(artifact, item_kind, research_kind))
    if path.exists():
        return {"path": str(path), "created": False, "sections": sections, "inherited": 0}
    # `research_kind:` is stamped so the file carries the shape it was authored under. Re-
    # classifying mid-flight cannot turn it red.
    fm = (f"---\nartifact: {artifact}\n"
          + (f"item: {item_id}\n" if item_id else "")
          + f"item_kind: {item_kind}\n"
          + (f"research_kind: {research_kind}\n" if research_kind else "")
          + f"reader: {_SPECS[artifact]['reader']}\n"
          + f"created_at: {date.today().isoformat()}\n---\n")
    tmpl = _template(artifact, item_kind, research_kind)
    heading = title or (item_id or "work-item")
    # The family is named ONCE in the heading, so a naive render doubles it. Read it off the
    # template.
    m = re.match(r"#[ \t]+(.+?)[ \t]+—[ \t]+\{title\}", tmpl)
    if m and heading.lower().startswith(m.group(1).lower() + " — "):
        heading = heading[len(m.group(1)) + 3:].lstrip()
    body = tmpl.format(title=heading)
    if artifact == "plan" and standing:
        body = _inject_checks(body, standing)
    atomic_write(path, fm + body)
    return {"path": str(path), "created": True, "sections": sections,
            "inherited": len(standing or []) if artifact == "plan" else 0}


# handoff brief

_BRIEF_SECTIONS = (("Background & why raised", "background"),
                   ("Discussion summary", "discussion"),
                   ("Direction & options", "direction"),
                   ("Constraints & notes", "constraints"))


def write_handoff_brief(folder: Path, title: str, *, background: str = "", discussion: str = "",
                        direction: str = "", constraints: str = "") -> str:
    """Render an inbox item's `handoff-brief.md`: code owns form, the caller supplies
    prose. An existing brief is APPENDED to."""
    folder = Path(folder)
    path = folder / "handoff-brief.md"
    provided = {"background": (background or "").strip(), "discussion": (discussion or "").strip(),
                "direction": (direction or "").strip(), "constraints": (constraints or "").strip()}
    if path.exists():
        add = "\n".join(f"**{h}:** {provided[k]}" for h, k in _BRIEF_SECTIONS if provided[k])
        if add:
            atomic_write(path, path.read_text(encoding="utf-8").rstrip() + "\n\n---\n"
                          f"*(appended {date.today().isoformat()})*\n\n" + add + "\n")
        return str(path)
    fm = (f"---\nartifact: handoff-brief\ntitle: {title!r}\nreader: agent\n"
          f"created_at: {date.today().isoformat()}\n---\n")
    body = f"# Handoff brief — {title}\n"
    for heading, key in _BRIEF_SECTIONS:
        body += f"\n## {heading}\n{provided[key] or f'<fill:{key}>'}\n"
    atomic_write(path, fm + body)
    return str(path)
