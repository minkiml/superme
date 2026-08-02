"""The revision grammar of `artifacts/plan.md` (workflow-renovation-v2 §3-bis).

A `revise` sends review's conversation back to the plan phase — the ONLY way back, and plan is the
only writer of this file. Regenerating plan.md wholesale re-creates solved problems every round and
silently discards the `- [x]` progress build earned, so a revision instead **appends a block** and
makes only the surgical edits it names. Untouched sections are provably untouched because nothing
wrote them.

The document has three zones, in reading order — what we intended → how it changed → what is true
now:

    ## Intent · ## Design · ## Decisions & clarifications   frozen prose (Q&A stays append-only)
    ## Revision log                                         CODE-OWNED mechanical index
    ## Revision r1 — <ts> · ## Revision r2 — <ts>            HISTORY, appended, never edited
    ## Tasks · ## Verification plan                          LIVE — always last, mutated in place

**The ordering IS the guarantee**: the live zone is pinned last, so nothing below `## Tasks` can
contradict it. `_pin_live_zone` enforces that on every revision (and migrates a plan authored
before this grammar), and the insert lands new blocks above it.

The rules, all refusals rather than warnings:

- **Scope is per CHANGE, not per revision.** One review conversation carries several concerns at
  once — redesign the caching approach WHILE resuming the CLI tasks. A revision-level scope would
  have to take the max of them, and `redesign` voids tasks, so the parts that were fine would lose
  progress they earned. A revision is a SET of changes, each with its own reach.
- **`resume` may not touch the plan.** This is the proportionality guard: an agent handed *"keep
  going, you're close"* records that without editing anything. If it judges a real edit is needed
  it must say `targeted` and trace it — so the scope is a CLAIM about how far the plan moved, not
  decoration. (A `resume` change with ops is refused, and so is a `targeted`/`redesign` change with
  none — the empty-op refusal that used to apply to every revision is what manufactured the
  over-editing this grammar prevents.)
- **`## Tasks` takes task-level ops** (add / edit one / remove one). A checkbox is real progress; a
  section-level rewrite is legal only at `redesign` scope, where the old tasks are void.
- **`## Decisions & clarifications` is append-only** — an answered question is a fact, not a draft.
- **`## Revision log` and the `## Revision r<n>` blocks are CODE-OWNED**: the agent never writes
  them and no op may target them. The log holds NO prose, so it can never become a second version
  of the truth; it exists so a reader can see the shape of the history without scrolling it.

`concerns` are sourced from CODE (the loop's typed exit, the authorization ledger) — never guessed
by the agent — which is what makes the history queryable. `spend_at` records the meter reading at
revision time: a revision opens a **generation**, and the loop's budget and recurrence guards read
since the current revision, so another generation starts fresh. Only a revision opens a generation
and only a human triggers a revision, so total spend stays owner-bounded.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .artifacts import FILL, _atomic_write, _split_sections

PLAN_FILE = "plan.md"
LOG = "Revision log"
TASKS = "Tasks"
VET_PLAN = "Verification plan"
LIVE = (TASKS, VET_PLAN)          # pinned last, in this order; `Tasks` first — build reads it more
APPEND_ONLY = ("Decisions & clarifications",)
LEGACY_LOG = "Revisions"          # pre-§3-bis: one section holding `### r<n>` entries

SECTION_OPS = ("update", "append")
TASK_OPS = ("add_task", "edit_task", "remove_task")
OPS = (*SECTION_OPS, *TASK_OPS)
SCOPES = ("resume", "targeted", "redesign")
# Closed vocabulary (§3-bis.3). Code assigns these; `owner_judgment` covers the case where the
# driver is purely the owner's read of the work.
CONCERNS = ("vet_failure", "budget", "not_converging", "no_progress",
            "authorization_denied", "authorization_granted", "owner_judgment")

# `- [ ] t3 — do the thing` → (prefix, state, id, dash, text). The template's grammar; a plan whose
# tasks don't carry ids can still be revised by section op at redesign scope.
_TASK = re.compile(r"(?m)^(\s*[-*]\s+\[)([ xX])(\]\s*)(t\d+)(\s*—\s*)(.*)$")
_REV_HEAD = re.compile(r"(?m)^##\s+Revision\s+(r\d+)\b(?:\s*—\s*(\S+))?")
_LEGACY_REV = re.compile(r"(?m)^###\s+(r\d+)\b")
_SPEND_AT = re.compile(r"(?m)^-\s*spend_at:\s*(\d+)")
# `    - Tasks — t3 removed` in a revision block: an id the plan has USED, even if it no longer
# appears in the live list. Read by `task_high_water` so numbers never get recycled.
_RETIRED_TASK = re.compile(r"(?m)^\s+-\s+" + TASKS + r"\s+—\s+t(\d+)\s")
_HEADING = re.compile(r"^##\s+(.+?)\s*$")


def plan_path(item_dir: Path) -> Path:
    return Path(item_dir) / "artifacts" / PLAN_FILE


def _one_line(s: str) -> str:
    return " ".join((s or "").split())


def _task_ids(text: str) -> list[str]:
    return [m.group(4) for m in _TASK.finditer(_split_sections(text).get(TASKS, ""))]


def _read(item_dir: Path) -> str:
    p = plan_path(item_dir)
    return p.read_text() if p.is_file() else ""


def revision_ids(text: str) -> list[str]:
    """Every recorded revision id in a plan's text, oldest first. Pre-§3-bis plans kept them as
    `### r<n>` inside one `## Revisions` section — those still count, so an in-flight item's gate
    check doesn't go red retroactively AND a new block never reuses a legacy number."""
    ids = [m.group(1) for m in _REV_HEAD.finditer(text)]
    legacy = _LEGACY_REV.findall(_split_sections(text).get(LEGACY_LOG, ""))
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
    """The build+vet meter reading when the CURRENT revision opened — the boundary the loop
    subtracts so each generation gets the whole budget. 0 for a plan never revised (and for
    pre-§3-bis blocks, which recorded no reading: the item then meters from birth, as it did)."""
    m = _SPEND_AT.search(_last_block(_read(item_dir)))
    return int(m.group(1)) if m else 0


def derive_concerns(item_dir: Path) -> list[str]:
    """What drove this revision, read off the record — never asserted by the agent (§3-bis.3): the
    loop's typed exit and the evidence verdict from the generation that just ended, plus every
    authorization resolved since the last revision. Falls back to `owner_judgment` when nothing
    mechanical explains it — the owner simply wants something different."""
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
    ordered = list(dict.fromkeys(found))          # de-duped, first-seen order
    return ordered or ["owner_judgment"]


# --------------------------------------------------------------------------- validation

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
    sections = _split_sections(plan_text)
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


# --------------------------------------------------------------------------- section machinery

def _replace_section(text: str, section: str, body: str) -> str:
    # `[ \t]*\n` not `\s*\n`: a greedy `\s*` swallows the blank lines BELOW the heading into the
    # heading group, so a body rewrite could never clear them.
    m = re.search(rf"(?ms)^(##\s+{re.escape(section)}[ \t]*\n)(.*?)(?=^##\s|\Z)", text)
    if not m:
        raise ValueError(f"section '## {section}' vanished from plan.md — re-read it and restage")
    return text[:m.start(2)] + body.rstrip() + "\n\n" + text[m.end(2):]


def _section_body(text: str, section: str) -> str:
    return _split_sections(text).get(section, "")


def _blocks(text: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """(preamble, [(heading, its lines including the heading)]) — the document as an ordered list
    of `##` sections, so they can be reordered without touching their contents."""
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
    """Move `## Tasks` then `## Verification plan` to the end of the document, in that order.
    Idempotent, and lossless — sections are moved whole, never rewritten. This is what makes the
    reading guarantee real ("nothing below Tasks can contradict it") for plans authored before this
    grammar, whose Tasks sat mid-document."""
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
    """The highest task number the plan has EVER reached — the live list plus every id the revision
    history names. A new task counts up from here, never into a number a removed task used:
    `SuperMe-Task: t3` trailers on already-landed commits are permanent, so reusing `t3` would
    silently re-attribute another task's history to the new one. (Same failure as
    work-item-id-identity's freed-slug reclamation.)"""
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
    # A task is a BLOCK, not a line: the template wraps long tasks across continuation lines, so
    # everything up to the next `- [ ] t<n>` belongs to the task above it. Live-gate finding
    # (2026-07-28): a line-wise remove dropped `- [ ] t3 —` and left its three wrapped lines glued
    # onto t2, silently changing what t2 said. Edit replaces the whole block for the same reason.
    lines: list[str] = []
    skipping = False
    for line in body.splitlines():
        m = _TASK.match(line)
        if m:
            skipping = m.group(4) == task
            if skipping:
                if kind == "edit_task":
                    # The checkbox STATE survives an edit — build ticked it against work it
                    # actually did; whether that work still counts is the revision block's
                    # business, not this line's.
                    lines.append(f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}"
                                 f"{m.group(5)}{content}")
                continue
            lines.append(line)
        elif not skipping:
            lines.append(line)
    return _replace_section(text, TASKS, "\n".join(lines)), \
        f"{task} {'edited' if kind == 'edit_task' else 'removed'}"


def _tidy_tasks(text: str) -> str:
    """Collapse the blank lines a removal leaves behind. The live zone is read at every gate and by
    every build cycle, so it does not accumulate holes."""
    kept: list[str] = []
    for ln in _section_body(text, TASKS).splitlines():
        if ln.strip() or (kept and kept[-1].strip()):
            kept.append(ln)
    return _replace_section(text, TASKS, "\n".join(kept))


def _apply_section_op(text: str, op: dict) -> tuple[str, str]:
    section, kind = str(op["section"]), str(op["op"])
    content = str(op["content"]).rstrip()
    # Forgiving writer (knowledge_delta precedent): agents routinely re-include the `## Heading`
    # at the top of a body. The heading stays in place, so a re-included one would double it.
    content = re.sub(rf"(?ms)\A\s*##\s+{re.escape(section)}\s*\n+", "", content).rstrip()
    body = (_section_body(text, section).rstrip() + "\n\n" + content) if kind == "append" \
        else content
    return _replace_section(text, section, body), f"{section} ({kind})"


# --------------------------------------------------------------------------- the write

_LOG_NOTE = ("<!-- CODE-OWNED index, written by `revise_plan`: the shape of the history, with no "
             "prose, so it can never disagree with the blocks below. Never hand-edit. -->")


def _ensure_log(text: str) -> str:
    if LOG in _split_sections(text):
        return text
    return _insert_before_live(text, f"## {LOG}\n{_LOG_NOTE}\n")


def _append_log_line(text: str, line: str) -> str:
    """Add one index row to `## Revision log`, after the last row already there. Positional rather
    than end-of-section: the section's BODY runs to the next `##`, so it swallows the `---` rule
    that opens the first revision block — appending at the end would file the row under it."""
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
    """Apply one revision to plan.md, atomically: the named edits, then the appended block that
    explains them, inserted above the pinned live zone.

    The caller validates first (`validate`) and supplies `concerns` from the record; this function
    trusts its input and fails loud on a section that vanished mid-flight.
    Returns {revision, ops, changed, path}."""
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
    _atomic_write(path, text)
    return {"revision": rev, "ops": n_ops, "concerns": tags, "path": str(path),
            "changed": [f"{c['area']} ({c['scope']})"
                        + (f": {', '.join(c['applied'])}" if c["applied"] else "")
                        for c in applied]}
