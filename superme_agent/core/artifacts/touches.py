"""`## Touches`: the fenced yaml naming what the work will change."""

import yaml

from .text import FILL, _fenced_blocks, _split_sections

# gate feeds

# The three plan sections whose STRUCTURED content feeds the gate's answer forms. Absent sections
# return empty.

TOUCH_ACTIONS = ("new", "modify", "read")


def parse_touches(plan_text: str) -> list[dict]:
    """plan.md's `## Touches` fenced yaml → [{component, path, action}]. Tolerant: anything
    unparseable gives []."""
    body = _split_sections(plan_text).get("Touches", "")
    blocks = _fenced_blocks(body)
    raw = blocks[0] if blocks else body
    if FILL.search(raw):
        return []
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return []
    if not isinstance(data, list):
        return []
    return [{"component": str(e.get("component") or "").strip(),
             "path": str(e.get("path") or "").strip(),
             "action": str(e.get("action") or "").strip()}
            for e in data if isinstance(e, dict)]


def touches_hard_issues(plan_text: str) -> list[str]:
    """Gate-blocking structure for a filled `## Touches`: the yaml must parse into ≥1
    complete row with a legal action."""
    rows = parse_touches(plan_text)
    if not rows:
        return ["touches: the fenced yaml must parse into at least one "
                "{component, path, action} row"]
    issues = []
    for r in rows:
        label = r["component"] or r["path"] or "(unnamed)"
        for f in ("component", "path", "action"):
            if not r[f]:
                issues.append(f"touches row {label!r}: missing `{f}`")
        if r["action"] and r["action"] not in TOUCH_ACTIONS:
            issues.append(f"touches row {label!r}: action must be one of "
                          f"{'/'.join(TOUCH_ACTIONS)} (got {r['action']!r})")
    return issues
