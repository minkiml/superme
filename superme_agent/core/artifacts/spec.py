"""What an artifact IS: the per-kind section specification, required sections, and where
the file lives."""

import re

from ..vocab import kind_profiles as _kp
from .templates import template_section_spec

# An artifact's own frontmatter, and the family stamp inside it. `self_check` judges a file
# against its own template.
_FM_BLOCK = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_FM_RESEARCH_KIND = re.compile(r"(?m)^research_kind:\s*(\S+)\s*$")

# kind → (template, required sections). `handoff-brief` sections are ALL optional — capture
# friction kills itemizing.
_SPECS: dict[str, dict] = {
    "brief":       {"file": "brief.md",      "required": (), "reader": "agent"},  # derived from template
    "plan":        {"file": "plan.md",       "required": (), "reader": "both"},  # per item-kind, resolved below
    # The research work-segment record — agent-facing, the counterpart of build-vet-<n>.md.
    # Sections derived from its template file.
    "investigation": {"file": "investigation.md", "required": (), "reader": "agent"},
    # Review's own agent-facing record. This holds the record; the report holds the judgment.
    "review":        {"file": "review.md",        "required": (), "reader": "agent"},
    "handoff-brief": {"file": "handoff-brief.md", "required": (), "reader": "agent"},
}
# Legacy plan shapes, READ-ONLY: a plan is judged against the shape it was authored under.
_PLAN_REQUIRED_LEGACY = ("Approach", "Tasks", "Validation criteria")
_PLAN_FEED_SECTIONS = ("Touches", "Behavior preview", "Risks & assumptions")
_PLAN_REQUIRED_V1 = ("Approach", "Tasks", "Inner checks", "Vet plan")
_PLAN_REQUIRED_V2 = ("Approach", "Touches", "Behavior preview", "Tasks",
                     "Risks & assumptions", "Inner checks", "Vet plan")
_PLAN_REQUIRED_RESEARCH_V1 = ("Questions", "Method", "Boundaries", "Done criteria", "Tasks")
ARTIFACT_KINDS = tuple(_SPECS)


def _template_name(artifact: str, item_kind: str | None,
                   research_kind: str | None = None) -> str | None:
    """The skill-template name for a template-backed kind, else None. The family slug IS
    the mapping; an unjudged item gets the base."""
    if artifact == "plan":
        return "plan-research" if item_kind == "research" else "plan"
    if artifact == "review":
        return "review-research" if item_kind == "research" else "review"
    if artifact == "investigation":
        return (_kp.family_template(research_kind) if research_kind in _kp.RESEARCH_KINDS
                else "investigation")
    return artifact if artifact == "brief" else None


def section_spec(artifact: str, item_kind: str | None,
                 research_kind: str | None = None) -> list[tuple[str, bool]]:
    """[(heading, must_be_filled)] the self-check enforces. Template-file kinds derive it from
    their template; embedded legacy kinds require-and-fill their `required` tuple."""
    name = _template_name(artifact, item_kind, research_kind)
    if name:
        return template_section_spec(name)
    return [(h, True) for h in _SPECS[artifact]["required"]]


def required_sections(artifact: str, item_kind: str | None,
                      research_kind: str | None = None) -> tuple[str, ...]:
    return tuple(h for h, _fill in section_spec(artifact, item_kind, research_kind))


def artifact_file(artifact: str) -> str:
    """The on-disk filename for an artifact kind (under the item's artifacts/)."""
    return _SPECS[artifact]["file"]


# The `reader:` LABEL — who each artifact is designed for. A label, never a constraint.
ARTIFACT_READERS: dict[str, str] = {
    **{k: s["reader"] for k, s in _SPECS.items()},
    "prd": "both", "vet-report": "agent", "checkpoint": "agent", "notes": "agent",
    "attempts": "agent",
}
