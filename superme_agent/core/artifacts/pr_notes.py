"""What the PR page shows beside each task's commits."""

import re
from pathlib import Path

from .text import _one_line, split_sections
from .spec import artifact_file
from .vet_plan import parse_vet_plan
from .cycles import cycle_reports
from .ledger import proof_rows

# One line, and the whole grammar build must hold. `none` is a real answer, and renders as
# nothing.
_NOTE = re.compile(r"^-\s*(?P<task>t\d+)\s*[—-]\s*(?P<body>.+)$")
_NONE = {"none", "none.", "n/a", "-", "—"}


def _bullets(body: str) -> list[str]:
    """A section's `- ` bullets, each folded back into ONE line with its continuations. The
    grammar reads a bullet, not a physical line."""
    out: list[str] = []
    for raw in body.splitlines():
        if raw.lstrip().startswith(("<!--", "<fill:")):
            continue
        if raw.lstrip().startswith("- "):
            out.append(raw.strip())
        elif out and raw.startswith((" ", "\t")) and raw.strip():
            out[-1] += " " + raw.strip()
    return out


def _note_fields(body: str) -> dict:
    """A note's labelled parts, split on the separator FIRST so a `·` in prose cannot start a field.

    A value whose first sentence is `none` is nothing."""
    out: dict = {}
    for part in re.split(r"\s+·\s+", body):
        if m := re.match(r"^(look|deviated)\s*:\s*(.*)$", part.strip(), re.I):
            val = m.group(2).strip()
            head = re.split(r"[.;]", val, maxsplit=1)[0].strip().lower()
            out[m.group(1).lower()] = "" if (val.lower() in _NONE or head in _NONE) else val
    return out


def pr_task_notes(item_dir: Path) -> dict:
    """`{task_id: {look, deviated, cycle}}` from the cycle reports. Oldest cycle first, so a
    task rebuilt in cycle 3 carries cycle 3's note."""
    notes: dict[str, dict] = {}
    for r in cycle_reports(item_dir):
        try:
            section = split_sections(Path(r["path"]).read_text(encoding="utf-8")).get("For the reviewer", "")
        except OSError:
            continue
        for b in _bullets(section):
            if not (m := _NOTE.match(b)):
                continue
            f = _note_fields(m.group("body"))
            if f.get("look") or f.get("deviated"):
                notes[m.group("task")] = {"look": f.get("look", ""),
                                          "deviated": f.get("deviated", ""),
                                          "cycle": r.get("cycle")}
    return notes


def pr_task_guide(item_dir: Path) -> dict:
    """Everything the PR page shows per task. `needed` is the covering check's `proves:` —
    never the task spec, which is build instructions."""
    out: dict[str, dict] = {}
    notes = pr_task_notes(item_dir)
    plan_path = Path(item_dir) / "artifacts" / artifact_file("plan")
    # How many tasks each check defends. One covering `t1, t2` answers "what did THIS task make
    # true" poorly.
    breadth: dict[str, int] = {}
    if plan_path.is_file():
        for c in parse_vet_plan(plan_path.read_text(encoding="utf-8")).get("checks", []):
            breadth[c["id"]] = len(set(re.findall(r"t\d+", str(c.get("covers") or "")))) or 99
    for row in proof_rows(item_dir):
        if not row["task"]:
            continue
        checks = row.get("verified") or []
        n = notes.get(row["task"], {})
        with_proof = [c for c in checks if c.get("proves")]
        with_proof.sort(key=lambda c: breadth.get(c["check"], 99))
        out[row["task"]] = {
            "needed": _one_line(with_proof[0].get("proves")) if with_proof else "",
            "look": n.get("look", ""),
            "deviated": n.get("deviated", ""),
            "cycle": n.get("cycle"),
            "checks": [{"id": c["check"], "ran": bool(c.get("ran")),
                        "passed": bool(c.get("passed")), "deferred": bool(c.get("deferred")),
                        "how": _one_line(c.get("how"))} for c in checks],
        }
    return out
