"""Phase reports — reading them back as text, as labelled facts, and as issues."""

import re
from datetime import datetime
from pathlib import Path

from .text import FILL, _HEADING, _LABEL_LINE, split_sections, log
from .spec import artifact_file
from .cycles import cycle_reports

def _space_labels(text: str) -> str:
    """Put a blank line before every `**Label:**` block that lacks one — markdown folds two
    label lines into one paragraph."""
    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and _LABEL_LINE.match(line) and out and out[-1].strip():
            out.append("")
        out.append(line)
    return "\n".join(out)


# A value meaning the author had nothing to say. Every template says DELETE the block instead.
_DEAD_VALUES = {"", "none", "none.", "n/a", "na", "-", "—", "nothing", "(none)",
                "(first run — n/a)", "(first run - n/a)", "first run — n/a", "first run - n/a"}


def _dead_label(lines: list[str], i: int) -> bool:
    """Is the `**Label:**` at `lines[i]` a block with nothing under it? The next NON-BLANK
    line decides."""
    if lines[i].split(":**", 1)[1].strip().lower() not in _DEAD_VALUES:
        return False
    for nxt in lines[i + 1:]:
        if not nxt.strip():
            continue
        return bool(_LABEL_LINE.match(nxt) or _HEADING.match(nxt))
    return True


def _live_body(lines: list[str]) -> bool:
    """Does a section body hold anything a reader would want? Blank lines, comments and empty
    labels read as nothing."""
    text = re.sub(r"<!--.*?-->", "", "\n".join(lines), flags=re.DOTALL)
    body = text.split("\n")
    return any(ln.strip() and not (_LABEL_LINE.match(ln) and _dead_label(body, k))
               for k, ln in enumerate(body))


def _drop_dead_blocks(text: str) -> str:
    """Delete `**Label:** none` blocks on the READ path — lines that exist only to say
    nothing. Deliberately literal."""
    lines, out = text.split("\n"), []
    i, fenced = 0, False
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and _LABEL_LINE.match(line):
            # Only a same-line value can be dead, and only if the block ends right there.
            if _dead_label(lines, i):
                i += 2                      # drop the label line and its trailing blank
                while out and not out[-1].strip():
                    out.pop()
                out.append("")
                continue
        if not fenced and _HEADING.match(line) and "changed since" in line.lower():
            body = [ln for ln in lines[i + 1:] if not _HEADING.match(ln)]
            joined = "\n".join(body).strip().strip("()").strip().lower()
            # "first run" has a family of phrasings that all mean the same nothing, so it gets a
            # prefix match.
            if joined in _DEAD_VALUES or joined.startswith("first run"):
                break
        if not fenced and _HEADING.match(line):
            # A bare heading reads as a section that failed to render, so it goes.
            j = i + 1
            while j < len(lines) and not _HEADING.match(lines[j]):
                j += 1
            if not _live_body(lines[i + 1:j]):
                i = j
                continue
        out.append(line)
        i += 1
    return "\n".join(out).rstrip() + "\n"


def changed_since(item_dir: Path, since: str | None) -> list[str]:
    """The item's records written since `since`, newest first. Reads MTIMES: every writer
    moves one, and no ledger stays honest."""
    try:
        cutoff = datetime.fromisoformat(str(since)).timestamp()
    except (TypeError, ValueError):
        return []
    root = Path(item_dir)
    hits: list[tuple[float, str]] = []
    for sub in ("artifacts", "reports"):
        for p in sorted((root / sub).glob("*.md")):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff:
                hits.append((mtime, f"{sub}/{p.name}"))
    return [name for _, name in sorted(hits, key=lambda h: -h[0])]


def report_text(item_dir: Path, phase: str) -> dict | None:
    """A phase's user-facing report. `contract` points at the full agent-facing artifact,
    read on demand and never pasted in."""
    path = Path(item_dir) / "reports" / f"report-{phase}.md"
    if not path.is_file():
        return None
    contract = {"triage": "artifacts/brief.md", "plan": "artifacts/plan.md",
                "investigate": "artifacts/investigation.md",
                # Review has a record its report could not reach. Close is still None: its report
                # IS the record.
                "review": "artifacts/review.md"}.get(phase)
    if phase in ("build", "vet"):
        # The cycle the report covers is the newest one — build and vet both project the same file.
        reports = cycle_reports(item_dir)
        contract = f"artifacts/{Path(reports[-1]['path']).name}" if reports else None
    # A link to a missing file is worse than none: it renders, the owner clicks, the doc view
    # 404s.
    if contract and not (Path(item_dir) / contract).is_file():
        contract = None
    try:
        st = path.stat()
    except OSError:
        return None
    return {"phase": phase, "name": f"report-{phase}",
            "text": _drop_dead_blocks(_space_labels(path.read_text(encoding="utf-8"))),
            "path": str(path), "mtime": st.st_mtime, "contract": contract}


# A `**Label:** value` on ONE line — how every one-line fact in a report is written.
_LABEL_VALUE = re.compile(r"^\*\*(?P<label>[^*\n]+?):\*\*[^\S\n]*(?P<value>.*?)\s*$", re.M)


def label_values(text: str) -> dict[str, str]:
    """Every same-line `**Label:** value` → {label lowercased: value}. Same-line only: a list
    below a label is prose."""
    out: dict[str, str] = {}
    for m in _LABEL_VALUE.finditer(re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)):
        value = m.group("value").strip()
        if value and not FILL.search(value):
            out.setdefault(m.group("label").strip().lower(), value)
    return out


def report_summary(item_dir: Path, phase: str) -> str:
    """A phase report's `**Summary:**` line — one sentence, what that phase concluded.
    The Quick View card renders it alone."""
    path = Path(item_dir) / "reports" / f"report-{phase}.md"
    return label_values(path.read_text(encoding="utf-8")).get("summary", "") if path.is_file() else ""


def triage_facts(item_dir: Path) -> dict:
    """What triage established, for the `About this work-item` card. Read from the OWNER's
    brief: this is their own framing."""
    path = Path(item_dir) / "reports" / "report-triage.md"
    if not path.is_file():
        return {"category": "", "background": "", "problem": ""}
    v = label_values(path.read_text(encoding="utf-8"))
    return {"category": v.get("category", ""), "background": v.get("background", ""),
            "problem": v.get("problem") or v.get("goal", "")}


_HEADING = re.compile(r"\A#{1,6}[ \t]\S")
_JUNK_THEN_HEADING = re.compile(r"\A([^\s#]{1,12})(#{1,6}[ \t].*)")
# Fenced blocks and inline code legitimately carry braces; prose in a report does not.
_FENCE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_PLACEHOLDER = re.compile(r"\{[a-z][a-z_]{1,}\}")


def report_body_issues(body: str) -> list[str]:
    """What is wrong with a report body, before it is anyone's file.

    Empty means it may be written."""
    first = next((ln for ln in (body or "").splitlines() if ln.strip()), "")
    out: list[str] = []
    if not _HEADING.match(first):
        if m := _JUNK_THEN_HEADING.match(first):
            out.append(f"the heading line begins {m.group(1)!r} before its `#`. Send the line as "
                       f"`{m.group(2)[:60]}`")
        else:
            out.append("it does not open with its `# ` title line, the way its template does. "
                       f"Your first line is {first[:70]!r}")
    if left := sorted(set(_PLACEHOLDER.findall(_FENCE.sub("", body or "")))):
        out.append("template placeholder(s) left unfilled: " + ", ".join(left[:6]))
    return out


def report_issues(item_dir: Path, name: str) -> list[str]:
    """Itemized issues on a user-facing report: present, and no template slot unfilled.
    A report is COPIED, not scaffolded."""
    path = Path(item_dir) / "reports" / f"{name}.md"
    if not path.is_file():
        return [f"reports/{name}.md does not exist — write it from its template"]
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    left = sorted(set(FILL.findall(text)))
    if left:
        return [f"reports/{name}.md has unfilled slot(s): " + ", ".join(left[:6])]
    return []


# `[^\S\n]*`, not `\s*`: in MULTILINE `\s` matches newlines, so an empty line would capture the
# next heading.
_OWNER_DECISION = re.compile(r"^\*\*Owner's decision:\*\*[^\S\n]*(.+?)\s*$", re.M)


def owner_decision(item_dir: Path) -> str:
    """The itemization outcome `itemize` recorded into review.md. Empty means itemization
    never ran."""
    path = Path(item_dir) / "artifacts" / artifact_file("review")
    if not path.is_file():
        return ""
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    m = _OWNER_DECISION.search(text)
    if not m:
        return ""
    value = m.group(1).strip()
    return "" if FILL.search(value) else value


def proposed_work(item_dir: Path) -> str:
    """A research review record's proposed-work body."""
    path = Path(item_dir) / "artifacts" / artifact_file("review")
    if not path.is_file():
        return ""
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    body = split_sections(text).get("Proposed work", "")
    if FILL.search(body) or not _live_body(body.splitlines()):
        return ""
    return " ".join(body.split())


# --------------------------------------------------------------------------- PR review notes

# What the PR page shows BESIDE each task's commits. Not a second opinion: the review report
# answers whether to land.

def delivered_line(item_dir: Path) -> str:
    """`artifacts/review.md`'s **Delivered** field, read by the landing commit's body.
    Reads the whole PARAGRAPH: the file is prose wrapped for reading."""
    path = Path(item_dir) / "artifacts" / "review.md"
    try:
        if not path.is_file():
            return ""
        parts: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not parts:
                if line.strip().startswith("**Delivered:**"):
                    parts.append(line.split("**Delivered:**", 1)[1].strip())
                continue
            # The field ends where its paragraph does — a blank line, or the next bold field.
            if not line.strip() or line.strip().startswith("**"):
                break
            parts.append(line.strip())
        return " ".join(p for p in parts if p).strip()
    except OSError:
        log.warning("delivered line: could not read %s", path)
    return ""
