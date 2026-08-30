"""Template files on disk: where each artifact kind's template lives, and its sections."""

import re

from ..vocab import kind_profiles as _kp
from .text import FILL

# The template file is the single source. A `<fill:…>` slot must be filled; a comment-only section
# must merely exist.

_TEMPLATE_HOMES = {
    "brief":         ("triage", "brief-template.md"),
    "plan":          ("plan", "plan-template.md"),
    "plan-research": ("plan", "plan-research-template.md"),
    "build-vet":     ("build", "build-vet-template.md"),
    # The unjudged shape — a research item whose family nobody named. Adding a section here would
    # retro-fail correct records.
    "investigation": ("investigate", "investigation-template.md"),
    # One shape per family: each answers a different question, so each owes a different record.
    # Read from the registry.
    **{_kp.family_template(f.slug): ("investigate", f"{_kp.family_template(f.slug)}-template.md")
       for f in _kp.RESEARCH_FAMILIES},
    "review":          ("review", "review-template.md"),
    "review-research": ("review", "review-research-template.md"),
    "report-plan":          ("plan", "report-plan-template.md"),
    "report-plan-research": ("plan", "report-plan-research-template.md"),
    "report-vet":           ("vet", "report-vet-template.md"),
    # Only the reports a pen derives are registered. One the agent writes whole is named by its
    # own skill.
}
_template_cache: dict[str, str] = {}


def skill_template(name: str) -> str:
    """The template body for `name`, from its authoring skill's `templates/`. Cached for
    the process lifetime."""
    if name not in _template_cache:
        from ...paths import DEV_PLUGIN_DIR
        skill, fname = _TEMPLATE_HOMES[name]
        _template_cache[name] = (
            DEV_PLUGIN_DIR / "skills" / skill / "templates" / fname).read_text(encoding="utf-8")
    return _template_cache[name]


def template_section_spec(name: str) -> list[tuple[str, bool]]:
    """Heading and must-be-filled per `## ` heading.

    The template is the required-shape spec."""
    spec: list[tuple[str, bool]] = []
    cur: str | None = None
    body: list[str] = []
    for line in skill_template(name).splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if cur is not None:
                spec.append((cur, bool(FILL.search("\n".join(body)))))
            cur, body = m.group(1), []
        elif cur is not None:
            body.append(line)
    if cur is not None:
        spec.append((cur, bool(FILL.search("\n".join(body)))))
    return spec
