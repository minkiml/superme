"""The revision grammar of `artifacts/plan.md`: a revision APPENDS a block and edits only what
it names.

Zones: frozen prose · revision log · revision history · the LIVE Tasks zone, always last.
The ordering IS the guarantee.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .artifacts import FILL, atomic_write, split_sections

PLAN_FILE = "plan.md"
LOG = "Revision log"
TASKS = "Tasks"
VET_PLAN = "Verification plan"
LIVE = (TASKS, VET_PLAN)  # pinned last, `Tasks` first — build reads it more
APPEND_ONLY = ("Decisions & clarifications",)
LEGACY_LOG = "Revisions"  # legacy: one section holding `### r<n>` entries

SECTION_OPS = ("update", "append")
TASK_OPS = ("add_task", "edit_task", "remove_task")
OPS = (*SECTION_OPS, *TASK_OPS)
SCOPES = ("resume", "targeted", "redesign")
# Closed vocabulary, assigned by code. `owner_judgment` is the owner's own read.
CONCERNS = ("vet_failure", "budget", "not_converging", "no_progress",
            "authorization_denied", "authorization_granted", "owner_judgment")

# `- [ ] t3 — do the thing` → (prefix, state, id, dash, text).
_TASK = re.compile(r"(?m)^(\s*[-*]\s+\[)([ xX])(\]\s*)(t\d+)(\s*—\s*)(.*)$")
_REV_HEAD = re.compile(r"(?m)^##\s+Revision\s+(r\d+)\b(?:\s*—\s*(\S+))?")
_LEGACY_REV = re.compile(r"(?m)^###\s+(r\d+)\b")
_SPEND_AT = re.compile(r"(?m)^-\s*spend_at:\s*(\d+)")
# An id the plan has USED, even if gone from the live list. Keeps numbers from recycling.
_RETIRED_TASK = re.compile(r"(?m)^\s+-\s+" + TASKS + r"\s+—\s+t(\d+)\s")
_HEADING = re.compile(r"^##\s+(.+?)\s*$")


def plan_path(item_dir: Path) -> Path:
    return Path(item_dir) / "artifacts" / PLAN_FILE


def _one_line(s: str) -> str:
    return " ".join((s or "").split())


def _task_ids(text: str) -> list[str]:
    return [m.group(4) for m in _TASK.finditer(split_sections(text).get(TASKS, ""))]


def _read(item_dir: Path) -> str:
    p = plan_path(item_dir)
    return p.read_text() if p.is_file() else ""


def revision_ids(text: str) -> list[str]:
    """Every recorded revision id, oldest first. Legacy `### r<n>` entries count, so a new block
    never reuses a number."""
    ids = [m.group(1) for m in _REV_HEAD.finditer(text)]
    legacy = _LEGACY_REV.findall(split_sections(text).get(LEGACY_LOG, ""))
    return [r for r in legacy if r not in ids] + ids


def revisions(item_dir: Path) -> list[str]:
    """Recorded revision ids, oldest first (`['r1', 'r2']`). Empty for a plan never revised."""
    return revision_ids(_read(item_dir))


def current_revision(item_dir: Path) -> str:
    """The revision a downstream artifact is implementing — `''` for the original plan."""
    revs = revisions(item_dir)
    return revs[-1] if revs else ""


def _last_block(text: str) -> str:
    """The newest `## Revision r<n>` block's body, or `''`."""
    heads = list(_REV_HEAD.finditer(text))
    if not heads:
        return ""
    start = heads[-1].end()
    nxt = re.compile(r"(?m)^##\s").search(text, start)
    return text[start:nxt.start() if nxt else len(text)]


def spend_at(item_dir: Path) -> int:
    """The build+vet meter reading when the CURRENT revision opened, so each generation gets the
    whole budget."""
    m = _SPEND_AT.search(_last_block(_read(item_dir)))
    return int(m.group(1)) if m else 0


def derive_concerns(item_dir: Path) -> list[str]:
    """What drove this revision, read off the record, never asserted by the agent. Falls back to
    `owner_judgment`."""
    from .artifacts import authorization_entries, read_cycle_outcomes
    item_dir = Path(item_dir)
    found: list[str] = []
    outcomes = read_cycle_outcomes(item_dir, revision=current_revision(item_dir))
    if outcomes:
        last = outcomes[-1]
        exit_ = str(last.get("exit") or "")
        if exit_ in ("budget", "not_converging", "no_progress"):
            found.append(exit_)
        if str(last.get("evidence") or "") == "failed":
            found.append("vet_failure")
    heads = list(_REV_HEAD.finditer(_read(item_dir)))
    since = (heads[-1].group(2) or "") if heads else ""
    for a in authorization_entries(item_dir):
        if since and str(a.get("id") or "") <= since:
            continue          # resolved in an earlier generation; that revision already carried it
        status = str(a.get("status") or "")
        if status in ("granted", "denied"):
            found.append(f"authorization_{status}")
    ordered = list(dict.fromkeys(found))
    return ordered or ["owner_judgment"]


def _validate_op(op, *, tag: str, scope: str, sections: dict, ids: list[str]) -> list[str]:
    if not isinstance(op, dict):
        return [f"{tag}: not a mapping"]
    issues: list[str] = []
    kind = str(op.get("op") or "")
    content = str(op.get("content") or "")
    if kind not in OPS:
        return [f"{tag}: op must be one of {'/'.join(OPS)} (got {kind!r})"]
    if kind != "remove_task":
        if not content.strip():
            issues.append(f"{tag}: empty content")
        if FILL.search(content):
            issues.append(f"{tag}: content contains <fill:…> placeholder(s)")
    if kind in TASK_OPS:
        if TASKS not in sections:
            return issues + [f"{tag}: this plan has no '## {TASKS}' section"]
        task = str(op.get("task") or "")
        if kind == "add_task":
            return issues
        if not task:
            issues.append(f"{tag}: `task` is required for {kind} — the task id, e.g. `t3`")
        elif task not in ids:
            issues.append(f"{tag}: no task {task!r} in '## {TASKS}' — present: "
                          f"{', '.join(ids) or '(none)'}")
        return issues
    section = str(op.get("section") or "")
    if not section:
        issues.append(f"{tag}: missing section")
    elif section == LOG or section == LEGACY_LOG or section.startswith("Revision "):
        issues.append(f"{tag}: '## {section}' is written by code — the revision history is a "
                      f"record, never an edit target")
    elif section not in sections:
        issues.append(f"{tag}: this plan has no section '## {section}' — existing: "
                      f"{', '.join(s for s in sections if not s.startswith('Revision')) or '(none)'}")
    elif section in APPEND_ONLY and kind != "append":
        issues.append(f"{tag}: '## {section}' is append-only — a recorded answer is a fact, "
                      f"not a draft; use `append`")
    elif section == TASKS and not (scope == "redesign" and kind == "update"):
        issues.append(f"{tag}: '## {TASKS}' takes task-level ops (add_task/edit_task/remove_task) "
                      f"— a section rewrite discards the `- [x]` progress build earned. Rewriting "
                      f"the whole list is legal only at `redesign` scope, where the old tasks are "
                      f"void.")
    return issues


def validate(plan_text: str, changes: list) -> list[str]:
    """Itemized refusals (empty = valid). Read-only — nothing is written on failure."""
    if not isinstance(changes, list) or not changes:
        return ["a revision needs at least one change — even 'keep going' is a change with "
                "`scope: resume` and no ops"]
    sections = split_sections(plan_text)
    ids = _task_ids(plan_text)
    issues: list[str] = []
    for i, ch in enumerate(changes, 1):
        tag = f"change {i}"
        if not isinstance(ch, dict):
            issues.append(f"{tag}: not a mapping")
            continue
        area = str(ch.get("area") or "").strip()
        scope = str(ch.get("scope") or "")
        note = str(ch.get("note") or "").strip()
        if area:
            tag = f"change {i} ({area})"
        else:
            issues.append(f"{tag}: `area` is required — name the concern this change answers, so "
                          f"a concern with no change is visible instead of silent")
        if scope not in SCOPES:
            issues.append(f"{tag}: scope must be one of {'/'.join(SCOPES)} (got {scope!r})")
            continue
        if not note:
            issues.append(f"{tag}: `note` is required — one line on what changed, or why nothing "
                          f"needed to")
        ops = ch.get("ops") or []
        if not isinstance(ops, list):
            issues.append(f"{tag}: `ops` must be a list")
            continue
        if scope == "resume":
            if ops:
                issues.append(f"{tag}: a `resume` change makes NO plan edit — that is the whole "
                              f"point of the scope. If an edit is genuinely needed, say `targeted` "
                              f"and trace it.")
            continue
        if not ops:
            issues.append(f"{tag}: a `{scope}` change owes at least one op — if the plan needs no "
                          f"edit, the scope is `resume`")
            continue
        for j, op in enumerate(ops, 1):
            issues.extend(_validate_op(op, tag=f"{tag} op {j}", scope=scope,
                                       sections=sections, ids=ids))
    return issues


def _replace_section(text: str, section: str, body: str) -> str:
    # `[ \t]*\n` not `\s*\n`: a greedy `\s*` swallows the blank lines below the heading.
    m = re.search(rf"(?ms)^(##\s+{re.escape(section)}[ \t]*\n)(.*?)(?=^##\s|\Z)", text)
    if not m:
        raise ValueError(f"section '## {section}' vanished from plan.md — re-read it and restage")
    return text[:m.start(2)] + body.rstrip() + "\n\n" + text[m.end(2):]


def _section_body(text: str, section: str) -> str:
    return split_sections(text).get(section, "")


def _blocks(text: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """(preamble, [(heading, its lines)]) — the document as ordered `##` sections, so they can be
    reordered without touching their contents."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if _HEADING.match(ln)]
    if not starts:
        return text, []
    pre = "\n".join(lines[:starts[0]])
    out: list[tuple[str, list[str]]] = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        out.append((_HEADING.match(lines[i]).group(1), lines[i:end]))
    return pre, out


def _pin_live_zone(text: str) -> str:
    """Move `## Tasks` then `## Verification plan` to the end. Idempotent and lossless — sections
    move whole."""
    pre, blocks = _blocks(text)
    if not blocks:
        return text
    live = [b for name in LIVE for b in blocks if b[0] == name]
    if not live or [b[0] for b in blocks[-len(live):]] == [b[0] for b in live]:
        return text
    rest = [b for b in blocks if b not in live]
    body = "\n".join(ln for _, lines in (*rest, *live) for ln in lines)
    return (pre.rstrip("\n") + "\n\n" if pre.strip() else "") + body.rstrip() + "\n"


def _insert_before_live(text: str, chunk: str) -> str:
    """Insert `chunk` immediately above the live zone (at EOF when the plan has none)."""
    lines = text.splitlines()
    at = next((i for i, ln in enumerate(lines)
               if (m := _HEADING.match(ln)) and m.group(1) in LIVE), None)
    if at is None:
        return text.rstrip() + "\n\n" + chunk.rstrip() + "\n"
    # A blank line above matters: `---` glued to a text line is a setext heading, not a rule.
    pad = [""] if at > 0 and lines[at - 1].strip() else []
    lines[at:at] = [*pad, *chunk.rstrip().splitlines(), ""]
    return "\n".join(lines).rstrip() + "\n"


def task_high_water(text: str) -> int:
    """The highest task number the plan ever reached. `SuperMe-Task:` commit trailers are permanent,
    so ids never repeat."""
    live = [int(t[1:]) for t in _task_ids(text)]
    retired = [int(n) for n in _RETIRED_TASK.findall(text)]
    return max([0, *live, *retired])


def _apply_task_op(text: str, op: dict, *, new_id: str = "") -> tuple[str, str]:
    """One task-level edit → (new text, what changed, for the revision block)."""
    kind, task = str(op["op"]), str(op.get("task") or "")
    content = str(op.get("content") or "").strip()
    body = _section_body(text, TASKS)
    if kind == "add_task":
        body = body.rstrip() + f"\n- [ ] {new_id} — {content}"
        return _replace_section(text, TASKS, body), f"{new_id} added"
    # A task is a BLOCK, not a line — long tasks wrap, and a line-wise remove would orphan them.
    lines: list[str] = []
    skipping = False
    for line in body.splitlines():
        m = _TASK.match(line)
        if m:
            skipping = m.group(4) == task
            if skipping:
                if kind == "edit_task":
                    # The checkbox STATE survives an edit: build ticked it against work it did.
                    lines.append(f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}"
                                 f"{m.group(5)}{content}")
                continue
            lines.append(line)
        elif not skipping:
            lines.append(line)
    return _replace_section(text, TASKS, "\n".join(lines)), \
        f"{task} {'edited' if kind == 'edit_task' else 'removed'}"


def _tidy_tasks(text: str) -> str:
    """Collapse the blank lines a removal leaves. The live zone is read at every gate."""
    kept: list[str] = []
    for ln in _section_body(text, TASKS).splitlines():
        if ln.strip() or (kept and kept[-1].strip()):
            kept.append(ln)
    return _replace_section(text, TASKS, "\n".join(kept))


def _apply_section_op(text: str, op: dict) -> tuple[str, str]:
    section, kind = str(op["section"]), str(op["op"])
    content = str(op["content"]).rstrip()
    # Agents routinely re-include the `## Heading`; it stays in place, so one would double it.
    content = re.sub(rf"(?ms)\A\s*##\s+{re.escape(section)}\s*\n+", "", content).rstrip()
    body = (_section_body(text, section).rstrip() + "\n\n" + content) if kind == "append" \
        else content
    return _replace_section(text, section, body), f"{section} ({kind})"


_LOG_NOTE = ("<!-- CODE-OWNED index, written by `revise_plan`: the shape of the history, with no "
             "prose, so it can never disagree with the blocks below. Never hand-edit. -->")


def _ensure_log(text: str) -> str:
    if LOG in split_sections(text):
        return text
    return _insert_before_live(text, f"## {LOG}\n{_LOG_NOTE}\n")


def _append_log_line(text: str, line: str) -> str:
    """Add one index row to `## Revision log`, after the last row there. Appending at the end would
    misfile it."""
    body = _section_body(text, LOG).splitlines()
    at = max((i for i, ln in enumerate(body) if ln.startswith("- r")),
             default=max((i for i, ln in enumerate(body) if ln.strip()), default=-1))
    body.insert(at + 1, line)
    return _replace_section(text, LOG, "\n".join(body))


def _render_block(*, rev: str, ts: str, concerns: list[str], spend: int, feedback: str,
                  directive: str, still_in_force: str, changes: list[dict]) -> str:
    out = ["---", "", f"## Revision {rev} — {ts}",
           f"- concerns: {', '.join(concerns) or 'owner_judgment'}",
           f"- spend_at: {int(spend)}",
           f"- feedback: {_one_line(feedback)}",
           f"- directive: {_one_line(directive)}",
           f"- still in force: {_one_line(still_in_force)}",
           "- changes:"]
    for ch in changes:
        out.append(f"  - {ch['area']} — {ch['scope']} — {_one_line(ch['note'])}")
        if ch.get("superseded"):
            out.append(f"    - supersedes: {_one_line(ch['superseded'])}")
        for what in ch.get("applied") or ():
            out.append(f"    - {what}")
    return "\n".join(out) + "\n"


def revise(item_dir: Path, *, changes: list, feedback: str, directive: str = "",
           still_in_force: str = "", concerns: list[str] | None = None,
           spend: int = 0) -> dict:
    """Apply one revision atomically: the named edits, then the block explaining them, above the
    pinned live zone. Trusts validated input."""
    path = plan_path(item_dir)
    text = _pin_live_zone(path.read_text())
    applied: list[dict] = []
    n_ops = 0
    hwm = task_high_water(text)
    touched_tasks = False
    for ch in changes:
        done: list[str] = []
        for op in ch.get("ops") or []:
            if str(op["op"]) in TASK_OPS:
                hwm += 1 if str(op["op"]) == "add_task" else 0
                text, what = _apply_task_op(text, op, new_id=f"t{hwm}")
                done.append(f"{TASKS} — {what}")
                touched_tasks = True
            else:
                text, what = _apply_section_op(text, op)
                done.append(what)
            n_ops += 1
        applied.append({"area": str(ch.get("area") or ""), "scope": str(ch.get("scope") or ""),
                        "note": str(ch.get("note") or ""),
                        "superseded": str(ch.get("superseded") or ""), "applied": done})
    if touched_tasks:
        text = _tidy_tasks(text)
    ts = datetime.now().isoformat(timespec="seconds")
    rev = f"r{len(revision_ids(text)) + 1}"
    tags = list(concerns or ["owner_judgment"])
    text = _append_log_line(
        _ensure_log(text),
        f"- {rev} — {ts} — concerns: {', '.join(tags)} "
        f"— scopes: {', '.join(dict.fromkeys(c['scope'] for c in applied))} "
        f"— {', '.join(c['area'] for c in applied)}")
    text = _insert_before_live(text, _render_block(
        rev=rev, ts=ts, concerns=tags, spend=spend, feedback=feedback, directive=directive,
        still_in_force=still_in_force, changes=applied))
    atomic_write(path, text)
    return {"revision": rev, "ops": n_ops, "concerns": tags, "path": str(path),
            "changed": [f"{c['area']} ({c['scope']})"
                        + (f": {', '.join(c['applied'])}" if c["applied"] else "")
                        for c in applied]}
