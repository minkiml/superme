"""The one rule for a work-item's title. A title is a LABEL, not the ask and not identity.

`check_title` returns a complaint an AGENT can act on. `normalize_title` never fails, because
you cannot bounce a person who already walked away.
"""

import re

TITLE_MAX = 60

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def one_line(raw) -> str:
    """Collapse any run of whitespace (including newlines) to single spaces, and strip."""
    return " ".join(str(raw or "").split())


def check_title(raw, *, description: str = "") -> str:
    """The complaint for a title an AGENT wrote, or "" if it passes. It is the only instruction it gets."""
    title = one_line(raw)
    if not title:
        return ("A work-item needs a `title` — the short line that names it on the board "
                "(e.g. \"Add dark mode support\"), not the request itself.")
    if len(title) > TITLE_MAX:
        return (f"`title` is {len(title)} characters, over {TITLE_MAX}. It is a card label, not "
                "the ask — name the change in a few words and let the description carry the rest.")
    if title.endswith((".", "!", "?")):
        return "`title` must not end in sentence punctuation — it is a label, not a sentence."
    if description and title.lower() == one_line(description).lower():
        return ("`title` is a copy of the description. The title names the work in a few words; "
                "the description is where the ask goes.")
    return ""


def normalize_title(raw, *, description: str = "") -> str:
    """Coerce anything into a usable title. Never raises — the floor where nobody can be asked again."""
    title = one_line(raw)
    if not title:
        return ""
    if not check_title(title, description=description):
        return title
    # The first sentence is nearly always the request itself, and a better label than N
    # characters.
    first = _SENTENCE_END.split(title, 1)[0].strip()
    first = first.rstrip(".!?").strip() or title
    if len(first) <= TITLE_MAX:
        return first
    cut = first[:TITLE_MAX - 1]
    if " " in cut[TITLE_MAX // 2:]:                  # only break on a word if one is near the end
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:-—") + "…"
