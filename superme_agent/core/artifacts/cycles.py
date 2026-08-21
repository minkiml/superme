"""Build⟷vet cycle reports: one file per cycle, appended to section by section."""

import re
from datetime import date
from datetime import datetime
from pathlib import Path

from .text import _atomic_write, _one_line, _split_sections
from .templates import skill_template

# cycle reports

# ONE report per cycle, with strictly sequential writers: build, then vet's fence, then the
# driver's outcome.

_CYCLE_FILE = re.compile(r"^build-vet-(\d+)\.md$")
_VET_REPORT_FILE = re.compile(r"^vet-report-(\d+)\.md$")   # legacy files — reader labeling only


_CYCLE_REVISION = re.compile(r"(?m)^plan_revision:\s*(r\d+)\s*$")


def cycle_reports(item_dir: Path) -> list[dict]:
    """All cycle reports in cycle order. `revision` is the plan revision the report was
    scaffolded under, which scopes the loop's guards."""
    adir = Path(item_dir) / "artifacts"
    if not adir.is_dir():
        return []
    out = []
    for p in adir.iterdir():
        m = _CYCLE_FILE.match(p.name)
        if m:
            rev = _CYCLE_REVISION.search(p.read_text()[:400])
            out.append({"cycle": int(m.group(1)), "path": str(p),
                        "revision": rev.group(1) if rev else ""})
    return sorted(out, key=lambda r: r["cycle"])


def latest_cycle_report(item_dir: Path, *, char_cap: int = 8000) -> dict | None:
    """The newest cycle report — the loop's handover payload for the next build cycle,
    capped."""
    reports = cycle_reports(item_dir)
    if not reports:
        return None
    r = reports[-1]
    text = Path(r["path"]).read_text()
    return {**r, "text": text[:char_cap], "truncated": len(text) > char_cap}


def _cycle_closed(text: str) -> bool:
    """A cycle is CLOSED once the driver has appended at least one §Cycle outcome entry."""
    return bool(_OUTCOME_HEAD.search(_split_sections(text).get("Cycle outcome", "")))


def scaffold_cycle(item_dir: Path, *, title: str = "") -> dict:
    """Scaffold the current OPEN cycle's report from the build skill's template. The open
    cycle is the last file while its §Cycle outcome is empty, else last+1."""
    reports = cycle_reports(item_dir)
    cycle = 1
    if reports:
        last = reports[-1]
        cycle = last["cycle"] if not _cycle_closed(Path(last["path"]).read_text()) \
            else last["cycle"] + 1
    adir = Path(item_dir) / "artifacts"
    path = adir / f"build-vet-{cycle}.md"
    if path.exists():
        return {"cycle": cycle, "path": str(path), "created": False}
    # The plan revision this cycle implements, so `build-vet-3` under a rewritten design reads as
    # such.
    from ..plan_revision import current_revision   # local: plan_revision imports this module
    rev = current_revision(item_dir)
    fm = (f"---\nartifact: build-vet\ncycle: {cycle}\nreader: agent\n"
          + (f"plan_revision: {rev}\n" if rev else "")
          + f"created_at: {date.today().isoformat()}\n---\n")
    # An HTML comment in a template is a note to the AUTHOR, not a line of the document it
    # produces.
    body = re.sub(r"[ \t]*<!--.*?-->\n?", "", skill_template("build-vet"), flags=re.DOTALL).format(
        cycle=cycle, title=title or Path(item_dir).name)
    _atomic_write(path, fm + body)
    return {"cycle": cycle, "path": str(path), "created": True}


def _append_to_section(path: Path, heading: str, entry: str, *, fence: str = "") -> None:
    """Append `entry` inside a cycle report's `## {heading}` section, or inside its named
    ```<fence>. Raises when the heading is absent — a mangled file must fail loud.

    The fence is NAMED, so one appender and one parser serve both lanes."""
    lines = path.read_text().splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if re.match(rf"^##\s+{re.escape(heading)}\s*$", ln)), None)
    if start is None:
        raise ValueError(f"cycle report {path.name} has no '## {heading}' section")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    entry_lines = entry.rstrip("\n").splitlines()
    if fence:
        opened = close = None
        for i in range(start + 1, end):
            if lines[i].strip() == f"```{fence}":
                opened = i
                close = next((j for j in range(i + 1, end)
                              if lines[j].strip() == "```"), None)
                break
        if close is not None:
            # A BLANK LINE between entries: packed back to back, six records read as one wall.
            # Presentation only.
            has_entries = any(lines[k].strip() for k in range(opened + 1, close))
            lines[close:close] = ([""] if has_entries else []) + entry_lines
        else:
            lines[end:end] = ["", f"```{fence}", *entry_lines, "```"]
    else:
        lines[end:end] = ["", *entry_lines]
    _atomic_write(path, "\n".join(lines) + "\n")


# §Cycle outcome — the driver's trail

_OUTCOME_HEAD = re.compile(r"^### (?P<ts>\S+) — (?P<decision>\S+)$", re.MULTILINE)


def append_cycle_outcome(item_dir: Path, *, evidence: str, decision: str, reason: str,
                         fingerprint: str = "", failed: list[str] | tuple = (),
                         tokens: int | None = None, budget: int | None = None,
                         loop_exit: str = "") -> dict | None:
    """Append one driver decision to the LATEST cycle report's §Cycle outcome, closing
    the cycle. `loop_exit` is the TYPED exit a revision reads its `concerns` off.

    Returns None when no cycle report exists — the DB `loop.decision` event still carries it."""
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    reports = cycle_reports(item_dir)
    if not reports:
        return None
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    # Name the cycle in a FIELD, never the heading: the heading is parsed, and decorating it
    # blinds the loop's breakers.
    entry = (f"### {ts} — {_one_line(decision)}\n"
             f"- cycle: {reports[-1]['cycle']}\n"
             f"- evidence: {_one_line(evidence)}\n"
             f"- reason: {_one_line(reason)}\n")
    if loop_exit:
        entry += f"- exit: {_one_line(loop_exit)}\n"
    if fingerprint:
        entry += f"- fingerprint: {_one_line(fingerprint)}\n"
    if failed:
        entry += f"- failed: {', '.join(_one_line(str(f)) for f in failed)}\n"
    if tokens is not None and budget is not None:
        entry += f"- tokens: {int(tokens)} / {int(budget)}\n"
    _append_to_section(Path(reports[-1]["path"]), "Cycle outcome", entry)
    return {"ts": ts, "cycle": reports[-1]["cycle"], "decision": decision}


def read_cycle_outcomes(item_dir: Path, *, revision: str | None = None) -> list[dict]:
    """Every driver decision across the cycle reports, in order.

    `revision` scopes the read to ONE generation, so failures recorded before a redesign no longer
    trip the recurrence guard after it. `None` reads the item's whole life."""
    out: list[dict] = []
    for r in cycle_reports(item_dir):
        if revision is not None and r["revision"] != revision:
            continue
        body = _split_sections(Path(r["path"]).read_text()).get("Cycle outcome", "")
        cur: dict | None = None
        for line in body.splitlines():
            m = re.match(r"^### (?P<ts>\S+) — (?P<decision>\S+)$", line)
            if m:
                cur = {"ts": m.group("ts"), "cycle": r["cycle"],
                       "decision": m.group("decision")}
                out.append(cur)
            elif cur is not None:
                kv = re.match(r"^- (evidence|reason|exit|fingerprint|failed|tokens): (.*)$", line)
                if kv:
                    cur[kv.group(1)] = kv.group(2).strip()
    return out
