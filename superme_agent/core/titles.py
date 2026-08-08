"""The one rule for a work-item's title, in one place.

A title is a LABEL — the line a human reads on a card, in the attention feed, in the kanban
column. It is not the ask, and it is not identity: the id is an opaque 12-hex token fully
decoupled from it (see `create_work_item`), so nothing structural breaks when a title changes
and nothing is gained by packing meaning into it.

Three callers mint titles, and they need different treatment:

  · `itemize_and_launch` and `create_inbox_item` are AGENTS. They get `check_title`, which
    returns a retry-shaped complaint the model can act on — the same contract `_commit_spec`
    uses for commit subjects. An agent that writes a bad title should be told, not corrected
    behind its back, or it writes the same title next time.

  · The owner's inbox push is a HUMAN, and the title there is optional. You cannot bounce a
    person who has already walked away, so that path gets `normalize_title`, which never fails:
    it takes the first sentence and truncates on a word boundary. The result is deliberately
    only adequate — the good name comes later, from triage, which has read the whole ask.

The rules are the ones a machine can actually check. Whether a title is DESCRIPTIVE is left to
the writer; nothing here can measure that, and a rule that pretends to would just be noise.
"""

import re

TITLE_MAX = 60

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def one_line(raw) -> str:
    """Collapse any run of whitespace (including newlines) to single spaces, and strip."""
    return " ".join(str(raw or "").split())


def check_title(raw, *, description: str = "") -> str:
    """Return the complaint for a title an AGENT wrote, or "" if it passes.

    The message says what is wrong and what to do instead, because it is delivered back into the
    agent's turn as a tool error and is the only instruction it gets."""
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
    """Coerce anything into a usable title. Never raises — this is the floor under the paths
    where no one can be asked to try again.

    A title that already passes is returned untouched, so this only ever acts on the broken case.
    """
    title = one_line(raw)
    if not title:
        return ""
    if not check_title(title, description=description):
        return title
    # The first sentence of a request is nearly always the request itself ("tally search — find
    # entries by note text. I log a lot of notes and…"), which makes a far better label than the
    # first N characters of the whole paragraph.
    first = _SENTENCE_END.split(title, 1)[0].strip()
    first = first.rstrip(".!?").strip() or title
    if len(first) <= TITLE_MAX:
        return first
    cut = first[:TITLE_MAX - 1]
    if " " in cut[TITLE_MAX // 2:]:                  # only break on a word if one is near the end
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:-—") + "…"
