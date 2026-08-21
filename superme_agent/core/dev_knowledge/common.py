"""Reading one file of the dev tree: its frontmatter, its body, and the ignore rule."""

import re
from datetime import datetime
from pathlib import Path

import yaml

_KNOWLEDGE_IGNORE = """\
# A work-item's `scratch/` is a run's working space — inventories, sorted lists, a helper script.
# Nothing downstream reads it and nothing keeps it past the item, so none of it belongs in the
# knowledge history. One rule, at the root, covers every repo's sub-home.
*/dev/work-items/*/scratch/
"""


def _ensure_knowledge_ignore(dev_root: Path) -> None:
    """Write the knowledge home's `.gitignore` if it has none. At the ROOT,
    because the home may get its own remote."""
    try:
        root = Path(dev_root).parent.parent
        marker = root / ".gitignore"
        if root.is_dir() and not marker.exists():
            marker.write_text(_KNOWLEDGE_IGNORE)
    except OSError:
        pass


def _iso_epoch(iso: str | None) -> float | None:
    """Spine ISO start stamp → epoch seconds, for the card's live elapsed-time timer."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_md(text: str) -> tuple[dict, str]:
    """Split a doc into (frontmatter dict, body). Tolerant: no/!invalid frontmatter -> {}."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, m.group(2)
