"""Text primitives the artifact grammar is built from: fill slots, sections, fenced
blocks, and the atomic write every artifact file goes through."""

import logging
import os
import re
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

FILL = re.compile(r"<fill:[^>]*>")


def atomic_write(path: Path, text: str) -> None:
    """tmp + os.replace in the target dir — a reader never sees a half-written artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def split_sections(text: str) -> dict[str, str]:
    """`## Heading` → body map (frontmatter stripped by the caller or tolerated here)."""
    out: dict[str, str] = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            # CONCATENATE a repeated heading: the writer appends to the FIRST, so a reader of the
            # LAST sees nothing.
            out.setdefault(cur, "")
        elif cur is not None:
            out[cur] += line + "\n"
    return out


def _section_filled(body: str) -> bool:
    """Non-empty after dropping fill markers, html comments, and blank lines."""
    cleaned = FILL.sub("", re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL))
    return bool(cleaned.strip())


def clip(text: str, limit: int) -> str:
    """Trim to `limit` at a WORD boundary, with an ellipsis when anything was cut. A hard slice
    reads as a rendering bug."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0]
    return (head if len(head) >= limit // 2 else text[:limit]).rstrip(" ,;:·-") + "…"
_FENCE = re.compile(r"^```[\w-]*\s*$")


def _fenced_blocks(body: str, *, lang: str = "") -> list[str]:
    """The contents of every ``` fenced block in a section body. `lang` keeps only blocks
    opened with that tag."""
    blocks, cur, keep = [], None, True
    for line in body.splitlines():
        s = line.strip()
        if _FENCE.match(s):
            if cur is None:
                cur, keep = [], (not lang or s == f"```{lang}")
            else:
                if keep:
                    blocks.append("\n".join(cur))
                cur = None
        elif cur is not None:
            cur.append(line)
    return blocks


_LABEL_LINE = re.compile(r"^\*\*[^*]+:\*\*")
_HEADING = re.compile(r"^#{1,6}\s")


def _one_line(s: str) -> str:
    """A slot is one bullet, so it is one line — a pasted newline would split it into slots
    nobody added."""
    return " ".join(str(s or "").split())
