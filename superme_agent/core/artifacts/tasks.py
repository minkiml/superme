"""`## Tasks` and `## Decisions`: the two enumerated sections of a plan."""

import re

from .text import split_sections

_TASK_LINE = re.compile(r"^\s*-\s*\[(?P<tick>[ xX])\]\s*(?P<id>t\d+)\b[\s—:-]*(?P<text>.*)$")


def parse_tasks(plan_text: str) -> list[dict]:
    """plan.md's `## Tasks` to an ordered list, keyed by the id build's commit trailers carry.

    `text` is the name the board shows, `detail` the indented spec under it."""
    body = split_sections(plan_text).get("Tasks", "")
    out: list[dict] = []
    cur: dict | None = None
    for line in body.splitlines():
        if (m := _TASK_LINE.match(line)):
            cur = {"id": m.group("id"), "done": m.group("tick").lower() == "x",
                   "text": m.group("text").strip(), "detail": ""}
            out.append(cur)
        elif not line.strip():
            cur = None
        elif line[:1].isspace() and cur is not None:
            cur["detail"] = (cur["detail"] + " " + line.strip()).strip()
        else:
            cur = None
    return out


_DECISION_HEAD = re.compile(r"^### (?P<ts>\S+) — (?P<q>.+)$", re.M)


def parse_decisions(plan_text: str) -> list[dict]:
    """plan.md's `## Decisions & clarifications` ledger: one entry per answered question,
    append-only with owner provenance. The deputy never re-litigates one."""
    body = split_sections(plan_text).get("Decisions & clarifications", "")
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    heads = list(_DECISION_HEAD.finditer(body))
    out: list[dict] = []
    for i, m in enumerate(heads):
        chunk = body[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(body)]
        entry = {"ts": m.group("ts"), "question": m.group("q").strip(),
                 "answer": "", "changed": ""}
        for line in chunk.splitlines():
            s = line.strip()
            if s.startswith("- answer:"):
                entry["answer"] = s[len("- answer:"):].strip()
            elif s.startswith("- changed:"):
                entry["changed"] = s[len("- changed:"):].strip()
        out.append(entry)
    return out
