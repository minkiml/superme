"""Surgical revision of `artifacts/plan.md` (workflow-renovation-v2 §2.1 "A revise must be
surgical").

A `revise` sends review's conversation back to the plan phase. Regenerating plan.md wholesale
re-creates solved problems every cycle and silently discards the `- [x]` progress build earned,
so the plan turn changes the file through SECTION OPS instead — the same tool-mediated discipline
the anchor docs already use (core/knowledge_delta). Untouched sections are provably untouched
because nothing wrote them.

The rules this module enforces, all of them refusals rather than warnings:

- `## Tasks` takes TASK-LEVEL ops only (add / edit one / remove one). A checkbox is real progress;
  a section-level rewrite of Tasks is only legal at `redesign` scope, where the old tasks are void
  by definition and every checkbox resets.
- `## Decisions & clarifications` is append-only (the existing rule — an answered question is a
  fact, not a draft).
- Every op names the feedback point it answers (`answers`), so a dropped point is visible in the
  revision block rather than silently absent.
- `## Revisions` is CODE-OWNED: the agent never writes it, and no op may target it. It is created
  on the first revision (never in the template — a plan that was never revised should not carry an
  empty log, and adding it to the template would retroactively fail every in-flight plan's
  self-check).

The revision block is what the next build reads first: which feedback drove this, at what scope,
and — on a redesign — what prior work is superseded and must be undone. Cycle reports stamp the
revision they implement (`artifacts.scaffold_cycle`), so `build-vet-3` under a rewritten design is
legible as such.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .artifacts import FILL, _atomic_write, _split_sections

PLAN_FILE = "plan.md"
REVISIONS = "Revisions"
TASKS = "Tasks"
APPEND_ONLY = ("Decisions & clarifications",)

SECTION_OPS = ("update", "append")
TASK_OPS = ("add_task", "edit_task", "remove_task")
OPS = (*SECTION_OPS, *TASK_OPS)
SCOPES = ("targeted", "redesign")

# `- [ ] t3 — do the thing` → (prefix, state, id, dash, text). The template's grammar; a plan whose
# tasks don't carry ids can still be revised by section op at redesign scope.
_TASK = re.compile(r"(?m)^(\s*[-*]\s+\[)([ xX])(\]\s*)(t\d+)(\s*—\s*)(.*)$")
_REV_HEAD = re.compile(r"(?m)^###\s+(r\d+)\b")


def plan_path(item_dir: Path) -> Path:
    return Path(item_dir) / "artifacts" / PLAN_FILE


def _task_ids(text: str) -> list[str]:
    return [m.group(4) for m in _TASK.finditer(_split_sections(text).get(TASKS, ""))]


def revisions(item_dir: Path) -> list[str]:
    """Recorded revision ids, oldest first (`['r1', 'r2']`). Empty for a plan never revised."""
    p = plan_path(item_dir)
    if not p.is_file():
        return []
    return _REV_HEAD.findall(_split_sections(p.read_text()).get(REVISIONS, ""))


def current_revision(item_dir: Path) -> str:
    """The revision a downstream artifact is implementing — `''` for the original plan."""
    revs = revisions(item_dir)
    return revs[-1] if revs else ""


def validate(plan_text: str, ops: list, scope: str) -> list[str]:
    """Itemized refusals (empty = valid). Read-only — nothing is written on failure."""
    if scope not in SCOPES:
        return [f"scope must be one of {'/'.join(SCOPES)} (got {scope!r})"]
    if not isinstance(ops, list) or not ops:
        return ["a revision needs a non-empty list of ops"]
    sections = _split_sections(plan_text)
    ids = _task_ids(plan_text)
    issues: list[str] = []
    for i, op in enumerate(ops, 1):
        tag = f"op {i}"
        if not isinstance(op, dict):
            issues.append(f"{tag}: not a mapping")
            continue
        kind = str(op.get("op") or "")
        content = str(op.get("content") or "")
        if kind not in OPS:
            issues.append(f"{tag}: op must be one of {'/'.join(OPS)} (got {kind!r})")
            continue
        if not str(op.get("answers") or "").strip():
            issues.append(f"{tag}: `answers` is required — name the feedback point this op "
                          f"answers, so a dropped point is visible instead of silent")
        if kind != "remove_task":
            if not content.strip():
                issues.append(f"{tag}: empty content")
            if FILL.search(content):
                issues.append(f"{tag}: content contains <fill:…> placeholder(s)")
        if kind in TASK_OPS:
            if TASKS not in sections:
                issues.append(f"{tag}: this plan has no '## {TASKS}' section")
                continue
            task = str(op.get("task") or "")
            if kind == "add_task":
                continue
            if not task:
                issues.append(f"{tag}: `task` is required for {kind} — the task id, e.g. `t3`")
            elif task not in ids:
                issues.append(f"{tag}: no task {task!r} in '## {TASKS}' — present: "
                              f"{', '.join(ids) or '(none)'}")
            continue
        section = str(op.get("section") or "")
        if not section:
            issues.append(f"{tag}: missing section")
        elif section == REVISIONS:
            issues.append(f"{tag}: '## {REVISIONS}' is written by code — never edit it")
        elif section not in sections:
            issues.append(f"{tag}: this plan has no section '## {section}' — existing: "
                          f"{', '.join(sections) or '(none)'}")
        elif section in APPEND_ONLY and kind != "append":
            issues.append(f"{tag}: '## {section}' is append-only — a recorded answer is a fact, "
                          f"not a draft; use `append`")
        elif section == TASKS and not (scope == "redesign" and kind == "update"):
            issues.append(f"{tag}: '## {TASKS}' takes task-level ops (add_task/edit_task/"
                          f"remove_task) — a section rewrite discards the `- [x]` progress build "
                          f"earned. Rewriting the whole list is legal only at `redesign` scope, "
                          f"where the old tasks are void.")
    return issues


def _replace_section(text: str, section: str, body: str) -> str:
    m = re.search(rf"(?ms)^(##\s+{re.escape(section)}\s*\n)(.*?)(?=^##\s|\Z)", text)
    if not m:
        raise ValueError(f"section '## {section}' vanished from plan.md — re-read it and restage")
    return text[:m.start(2)] + body.rstrip() + "\n\n" + text[m.end(2):]


def _section_body(text: str, section: str) -> str:
    return _split_sections(text).get(section, "")


def _next_task_id(text: str) -> str:
    nums = [int(t[1:]) for t in _task_ids(text)]
    return f"t{max(nums) + 1 if nums else 1}"


def _apply_task_op(text: str, op: dict) -> tuple[str, str]:
    """One task-level edit → (new text, what changed, for the revision block)."""
    kind, task = str(op["op"]), str(op.get("task") or "")
    content = str(op.get("content") or "").strip()
    body = _section_body(text, TASKS)
    if kind == "add_task":
        new_id = _next_task_id(text)
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


def _reset_checkboxes(text: str) -> str:
    """Redesign: the old tasks are void, so every checkbox resets. Build's first act in the next
    cycle is reconciling the worktree with the new design — undo-FORWARD (new commits), never a
    reset/force, so history stays honest."""
    body = _TASK.sub(lambda m: f"{m.group(1)} {m.group(3)}{m.group(4)}{m.group(5)}{m.group(6)}",
                     _section_body(text, TASKS))
    return _replace_section(text, TASKS, body)


def _append_revision(text: str, *, rev: str, scope: str, feedback: str, changes: list[str],
                     superseded: str) -> str:
    entry = [f"### {rev} — {datetime.now().isoformat(timespec='seconds')} — {scope}",
             f"- feedback: {feedback.strip()}"]
    entry += [f"- {c}" for c in changes]
    if scope == "redesign":
        entry.append(f"- superseded: {superseded.strip() or 'the previous design in full'}")
    block = "\n".join(entry)
    if REVISIONS in _split_sections(text):
        return _replace_section(text, REVISIONS,
                                (_section_body(text, REVISIONS).rstrip() + "\n\n" + block).strip())
    return text.rstrip() + (
        f"\n\n## {REVISIONS}\n"
        f"<!-- written by `revise_plan` — which feedback drove each revision, what it changed, and "
        f"what it superseded. The next build reads this before the plan body. Never hand-edit. -->\n"
        f"{block}\n")


def revise(item_dir: Path, *, ops: list, scope: str, feedback: str,
           superseded: str = "") -> dict:
    """Apply one revision pass to plan.md, atomically, and record its revision block.

    The caller validates first (`validate`); this function trusts its input and fails loud on a
    section that vanished mid-flight. Returns {revision, ops, changed, path}."""
    path = plan_path(item_dir)
    text = path.read_text()
    changes: list[str] = []
    for op in ops:
        kind = str(op["op"])
        answers = str(op.get("answers") or "").strip()
        if kind in TASK_OPS:
            text, what = _apply_task_op(text, op)
            changes.append(f"{TASKS} — {what}: {answers}")
            continue
        section, content = str(op["section"]), str(op["content"]).rstrip()
        # Forgiving writer (knowledge_delta precedent): agents routinely re-include the `## Heading`
        # at the top of a body. The heading stays in place, so a re-included one would double it.
        content = re.sub(rf"(?ms)\A\s*##\s+{re.escape(section)}\s*\n+", "", content).rstrip()
        body = (_section_body(text, section).rstrip() + "\n\n" + content) if kind == "append" \
            else content
        text = _replace_section(text, section, body)
        changes.append(f"{section} ({kind}): {answers}")
    if scope == "redesign" and TASKS in _split_sections(text):
        text = _reset_checkboxes(text)
    rev = f"r{len(_REV_HEAD.findall(_section_body(text, REVISIONS))) + 1}"
    text = _append_revision(text, rev=rev, scope=scope, feedback=feedback, changes=changes,
                            superseded=superseded)
    _atomic_write(path, text)
    return {"revision": rev, "ops": len(ops), "changed": changes, "path": str(path)}
