"""The human-facing reports: interpretations of the agent-facing artifacts, not shorter
versions of them."""

import re
from pathlib import Path

from ..vocab.kind_profiles import get_profile
from .text import atomic_write, clip
from .templates import skill_template
from .spec import artifact_file
from .vet_plan import parse_vet_plan, plan_vet_depth
from .tasks import parse_tasks
from .cycles import cycle_reports
from .authorization import pending_authorizations
from .ledger import (LENSES, diagnoses, evidence_entries, evidence_status, lens_gaps,
                     missing_lenses, proof_rows, undiagnosed_failures, validation_discrepancies)

def _how_checked(c: dict) -> str:
    """One check, as how the owner will know it held.

    Derived from the plan."""
    mode = str(c.get("mode") or "")
    how = {"command": "run for real",
           "interaction": "driven for real and judged",
           "inspection": "read against a stated bar"}.get(mode, mode or "—")
    if c.get("run") and not c.get("rubric"):
        how += " · by SuperMe, not agent-claimed"
    if c.get("source"):
        how += " · a check this project already owned"
    return how


def _slot(text: str | None, heading: str) -> str:
    """A prose slot's body, with the heading stripped when the author repeated it.

    Structure is code's to own."""
    body = (text or "").strip()
    first, _, rest = body.partition("\n")
    if first.startswith("#") and first.lstrip("#").strip().lower() == heading.strip().lower():
        return rest.strip()
    return body


_LENS_LINE = re.compile(r"(?mi)^(\s*[-*]\s+)(" + "|".join(LENSES) + r")(\s*:)")


def _bold_lenses(text: str) -> str:
    """Bold and capitalize the lens name opening a lens bullet.

    One rule owns the label."""
    return _LENS_LINE.sub(
        lambda m: f"{m.group(1)}**{m.group(2).capitalize()}{m.group(3).strip()}**", text)


_LENS_ANY = re.compile(r"(?i)\b(" + "|".join(LENSES) + r")\s*:")
# A lens the author already emphasised still opens its bullet. `_LENS_LINE` matches only
# the plain form, so bolding stays idempotent.
_LENS_OPENER = re.compile(r"(?mi)^\s*[-*]\s+(?:[*_]{1,2})?(" + "|".join(LENSES)
                          + r")(?:[*_]{1,2})?\s*:")


def lens_slot_issues(text: str) -> list[str]:
    """Why the lens slot is not one bullet per lens.

    Lenses run together render as prose and nothing says so."""
    body = (text or "").strip()
    if not body:
        return []
    loose = [ln.strip() for ln in body.splitlines()
             if _LENS_ANY.search(ln) and not _LENS_OPENER.match(ln)]
    if loose:
        return ["`looked_at` runs its lenses together instead of giving each its own bullet. "
                "Write one bullet per lens, opening with the lens name — `- Intent: ...` — "
                f"one fact each, under 20 words. First line that does not: {loose[0][:70]!r}"]
    if not _LENS_OPENER.search(body):
        return [f"`looked_at` opens no bullet with a lens. Each bullet starts with one of "
                f"{', '.join(LENSES)}, as `- Intent: ...`."]
    return []


def write_plan_user_report(item_dir: Path, *, summary: str, approach: str = "",
                           confirm: str = "", decisions: str = "", assumptions: str = "",
                           item_kind: str | None = None) -> dict:
    """The owner's answer to what is being built.

    Facts are derived from plan.md."""
    # An omitted optional slot arrives as None, not "". Normalize once, here, where the type is
    # declared.
    approach, confirm = approach or "", confirm or ""
    decisions, assumptions = decisions or "", assumptions or ""
    item_dir = Path(item_dir)
    plan_path = item_dir / "artifacts" / artifact_file("plan")
    plan = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else ""
    vp = parse_vet_plan(plan)
    tasks = parse_tasks(plan)
    research = get_profile(item_kind).kind == "research"
    if not tasks:
        raise ValueError("plan.md declares no `## Tasks` — scaffold and fill the plan first; a "
                         "report over nothing would read as 'nothing needs proving'")
    # One row per check: the owner is approving an exam, and `proves:` Is the row, so it is never
    # clipped.
    rows = [f"| {c.get('proves') or '—'} | {_how_checked(c)} |" for c in vp.get("checks", [])]
    # A research item declares no checks by design, so the gap call-out is about implementation
    # plans only.
    uncovered = [] if research else [r["task"] for r in proof_rows(item_dir)
                                     if r["task"] and not r["verified"]]
    gap_text = ", ".join(
        clip(t["text"], 60) for t in tasks if t["id"] in set(uncovered))
    gaps = (f"\n\n**Nothing will prove:** {gap_text} — either a check is missing, or that work "
            "genuinely needs no proof and the gate is where to say so." if uncovered else "")
    # Assembled here, not as separate template slots, so an empty block leaves no blank line
    # behind.
    blocks = [
        f"## Decisions & Assumptions\n\n**Decisions:**\n{decisions.strip()}"
        if decisions.strip() else "",
        f"**Assumptions:**\n{assumptions.strip()}" if assumptions.strip() else "",
        f"**Stats:** {len(tasks)} task(s)"
        + ("" if research else f" · {len(vp.get('checks', []))} check(s)")
        + (f" · {len(uncovered)} with nothing to prove them" if uncovered else ""),
    ]
    # A "Decisions & Assumptions" heading with only assumptions under it still needs the heading.
    if assumptions.strip() and not decisions.strip():
        blocks[1] = "## Decisions & Assumptions\n\n" + blocks[1]
    body = skill_template("report-plan-research" if research else "report-plan")
    body = re.sub(r"<!--.*?-->\n?", "", body, flags=re.DOTALL)   # authoring note, not report content
    body = body.format(
        summary=(summary or "").strip() or "—",
        approach=_slot(approach, "What we're trying to find out" if research else "Approach") or "—",
        confirm=(f"\n\n{c}" if (c := _slot(confirm, "How we'll look, and what we won't"
                                           if research else "How I'll confirm it worked")) else ""),
        coverage="\n".join(rows) or "| _no checks declared_ | — |",
        gaps=gaps, blocks="\n\n".join(b for b in blocks if b))
    rdir = item_dir / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    path = rdir / "report-plan.md"
    atomic_write(path, body)
    return {"path": str(path), "tasks": len(tasks), "checks": len(vp.get("checks", [])),
            "uncovered": uncovered}


def write_vet_user_report(item_dir: Path, repo_dir: Path | None, *, summary: str = "",
                          confirms: str = "", looked_at: str = "", unknown: str = "") -> dict:
    """Vet writes the narrative, code writes `What didn't hold`.

    One writer each, so vet cannot write around a red check."""
    if bad := lens_slot_issues(_slot(looked_at, "What else was looked at")):
        raise ValueError("; ".join(bad) + " Nothing was written.")
    item_dir = Path(item_dir)
    plan_path = item_dir / "artifacts" / artifact_file("plan")
    plan_ids = [c["id"] for c in parse_vet_plan(plan_path.read_text(encoding="utf-8")).get("checks", [])] \
        if plan_path.is_file() else []
    entries = evidence_entries(item_dir)
    by_check: dict[str, list[dict]] = {}
    for e in entries:
        by_check.setdefault(e["check"], []).append(e)
    no_vet = plan_vet_depth(item_dir) == "none" and not entries
    missing = [] if no_vet else [c for c in plan_ids if c not in by_check]
    if missing:
        raise ValueError("; ".join(
            f"plan check {c!r} has no recorded entry — run it and record_verification first "
            "(an unrecorded check doesn't exist)" for c in missing))
    if not by_check and not no_vet:
        raise ValueError("no checks recorded — record_verification for every plan check first")
    # The lenses run on every cycle: depth governs what is executed, not whether the work is read.
    if (missing := missing_lenses(item_dir)):
        raise ValueError("; ".join(
            f"the {ln} lens has no read this cycle — call record_lens with what you probed (no "
            "findings is a fine answer, and saying what you probed is what makes it one)"
            for ln in missing))
    # The diagnosis duty has its teeth here: "3 checks failing" with no where sends the next cycle
    # hunting.
    if (undiag := undiagnosed_failures(item_dir)):
        raise ValueError("; ".join(
            f"check {c!r} is failing with no diagnosis this cycle — call record_diagnosis with "
            "`where` it broke and `why`, so the next build cycle starts at the cause instead of "
            "the symptom (never the fix: that is build's to reason out)" for c in undiag))
    ev = evidence_status(item_dir, repo_dir)
    checks = plan_ids + [c for c in by_check if c not in plan_ids]
    deferred_auth = {a["check"] for a in pending_authorizations(item_dir) if a.get("check")}

    # Each check's `proves:`, so a red row says what stopped being true instead of naming an id
    # nobody remembers.
    proves_of = {c["id"]: str(c.get("proves") or "")
                 for c in (parse_vet_plan(plan_path.read_text(encoding="utf-8")).get("checks", [])
                           if plan_path.is_file() else [])}
    failed = [c for c in checks
              if (h := by_check.get(c)) and not h[-1].get("passed") and not h[-1].get("deferred")]
    deferred_all = sorted(deferred_auth | {c for c, h in by_check.items()
                                           if h and h[-1].get("deferred")})

    # `## What didn't hold` is authored here, off the ledger, so a red check reaches the owner
    # regardless.
    diag = diagnoses(item_dir)
    lines = []
    for c in failed:
        d = diag.get(c) or {}
        detail = " · ".join(filter(None, [
            f"broke in {d['where']}" if d.get("where") else "",
            d.get("why", ""),
            f"_(undetermined: {d['unknown']})_" if d.get("unknown") else ""]))
        lines.append(f"- **{proves_of.get(c) or f'check `{c}`'}** — did not hold. {detail}".rstrip())
    for c in deferred_all:
        lines.append(f"- **{proves_of.get(c) or f'check `{c}`'}** — not checked: deferred pending "
                     "your authorization.")
    # A lens finding that gates belongs here for the same reason a failed check does: it sends the
    # item back.
    for g in lens_gaps(item_dir):
        lines.append(f"- **{g['text']}** — raised by the {g['lens']} reading ({g['severity']}).")
    # A build claim the kernel could not reproduce. The count is the finding.
    if (disagreed := validation_discrepancies(
            item_dir, cycle=(cycle_reports(item_dir) or [{}])[-1].get("cycle"))):
        n = len(disagreed)
        lines.append(
            f"- **The build's own proof did not hold up when re-run here** — "
            f"{n} of its checks behaved differently the second time. That does not mean the work is "
            f"wrong, but it does mean the build's word for it is not evidence. The commands and "
            f"their output are in this cycle's build record.")
    machine = ("## What didn't hold\n" + "\n".join(lines) + "\n\n") if lines else ""
    # A `depth: none` item still gets a reading, and that reading can gate — so this note precedes
    # the block.
    if ev.get("not_required"):
        machine = ("## What was owed\nNothing. The approved plan declares `depth: none` — this "
                   "item has no observable surface to check, so the reading below is the whole "
                   "record.\n\n") + machine

    verdict = {"passed": "all checks green and fresh",
               "failed": f"{len(failed)} check(s) failing: " + ", ".join(failed),
               "stale": "green but STALE — code moved after the checks ran",
               "deferred": "green except checks deferred pending authorization",
               "unverified": "nothing recorded"}.get(ev.get("status", ""), ev.get("status", ""))
    if ev.get("not_required"):
        verdict = "no checks were owed — the approved plan declares `depth: none`"
    body = skill_template("report-vet")
    body = re.sub(r"<!--.*?-->\n?", "", body, flags=re.DOTALL)   # authoring note, not report content
    body = body.format(
        summary=(summary or "").strip() or verdict,
        confirms=_slot(confirms, "What this confirms") or "_nothing recorded this cycle_",
        machine=machine,
        looked_at=_bold_lenses(_slot(looked_at, "What else was looked at"))
        or "_no reading recorded_",
        unknown=(f"\n## What I can't tell you\n{u}\n"
                 if (u := _slot(unknown, "What I can't tell you")) else ""))
    rdir = item_dir / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    path = rdir / "report-vet.md"
    atomic_write(path, body)
    return {"path": str(path), "verdict": ev.get("status", ""), "failed": failed}
