"""The evidence ledger — validation, verification, diagnoses, lenses and nominations,
recorded as entries a reader can audit rather than sentences to take on trust."""

import hashlib
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .text import FILL, _FENCE, _fenced_blocks, _one_line, split_sections
from .spec import artifact_file
from .vet_plan import _plan_check_ids, parse_vet_plan, plan_vet_depth
from .tasks import parse_tasks
from .cycles import _append_to_section, cycle_reports, scaffold_cycle
from .authorization import authorization_entries

# The plan-authored contract vet executes. Check ids are the JOIN KEY into the evidence ledger, so
# plan and ledger meet.

# Who actually performed the check. `machine` = the kernel ran its `run:` block; `agent` = a
# vetter attested.
BY_MACHINE = "machine"
BY_AGENT = "agent"

# A verdict answers "did this check pass"; a diagnosis answers "where and why did it fail".
KIND_VERDICT = "verdict"
KIND_DIAGNOSIS = "diagnosis"
KIND_LENS = "lens"
KIND_NOMINATION = "nomination"
# The kernel's re-run of a build validation claim. Deliberately NOT a verdict, so an audit never
# counts as a check.
KIND_AUDIT = "audit"

# The two machine lanes, each a tagged fence in its own section. Named, not positional.
VALIDATION_FENCE = "runs"
VERIFICATION_FENCE = "checks"

# Read every cycle, independently of the plan: its checks can only defend what the planner thought
# of.
STANDING_LENSES = ("intent", "safety", "robustness")
LENSES = STANDING_LENSES + ("performance",)
SEVERITIES = ("low", "medium", "high")

# Intent and safety have no severity scale: anything found is a gap. Only `high` robustness gates.
_LENS_GATES_AT = {"intent": SEVERITIES, "safety": SEVERITIES, "robustness": ("high",)}


# --------------------------------------------------------------------------- evidence ledger

def repo_fingerprint(repo_dir: Path | None) -> str:
    """A cheap fingerprint of the repo's CODE STATE: HEAD sha plus `git diff HEAD`.

    Untracked files are excluded, since a test run drops logs and stales green evidence."""
    if not repo_dir or not Path(repo_dir).is_dir():
        return "no-git"
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True,
                              text=True, timeout=10, encoding="utf-8")
        if head.returncode != 0:
            return "no-git"
        diff = subprocess.run(["git", "diff", "HEAD"], cwd=repo_dir,
                              capture_output=True, text=True, timeout=15, encoding="utf-8")
        return hashlib.sha1((head.stdout.strip() + "\n" + diff.stdout).encode()).hexdigest()[:16]
    except (OSError, subprocess.SubprocessError):
        return "no-git"


_EVIDENCE_HEAD = re.compile(r"^### (?P<ts>\S+) — (?P<check>.*)$")


def _resolve_evidence_check(check: str, valid_ids: list[str]) -> str:
    """The check field IS the join key, so a glued key never supersedes its own stale `failed`.

    Returns the exact id, or raises with a hint. Empty `valid_ids` records verbatim."""
    c = check.strip()
    if c in valid_ids:
        return c
    # A real id with a description welded on. Name it, so the fix is one edit and not a hunt.
    glued = next((vid for vid in sorted(valid_ids, key=len, reverse=True)
                  if c == vid or c.startswith(vid + " ") or c.startswith(vid + ":")
                  or c.startswith(vid + " —") or c.startswith(vid + " -")), None)
    if glued:
        raise ValueError(
            f"evidence check {check!r} glues a description onto vet-plan id {glued!r} — record "
            f"against the bare id {glued!r} (the ledger key must equal the plan id, or its verdict "
            "never joins the plan's check and the loop halts on a phantom failure)")
    raise ValueError(
        f"evidence check {check!r} is not a vet-plan check id — record against one of: "
        f"{', '.join(valid_ids)}. (A check not in the plan can't be tracked; add it to the vet "
        "plan first if it's a real requirement.)")


def record_validation(item_dir: Path, repo_dir: Path | None, *, command: str, result: str,
                      passed: bool, task: str = "") -> dict:
    """Append one BUILD validation run to the cycle report's `## Validation` fence.

    Build is both runner and only witness, so the run is recorded as DATA vet can audit."""
    result = " ".join((result or "").split())
    raw = (command or "").strip()
    if not raw:
        raise ValueError("record_validation needs the COMMAND you ran — a claim with no command "
                         "is the prose this record exists to replace")
    # A newline INSIDE A QUOTE is refused, not flattened: the audit re-runs the stored text, and
    # quotes carry statement separators.
    quote = ""
    for ch in raw:
        if quote:
            if ch == quote:
                quote = ""
            elif ch == "\n":
                raise ValueError(
                    "this command has a newline INSIDE a quoted string, and the record stores the "
                    "command on ONE line — vet re-runs the stored text verbatim, so that newline "
                    "becomes a space and the quoted program changes meaning (an indented block "
                    "turns into a syntax error). Re-run it in single-line form and record that: "
                    "join the statements with `; `, or put the script in a file and record the "
                    "command that runs the file.")
        elif ch in "'\"":
            quote = ch
    command = " ".join(raw.split())
    reports = cycle_reports(item_dir)
    cy = ({"cycle": reports[-1]["cycle"], "path": reports[-1]["path"]}
          if reports else scaffold_cycle(item_dir))
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fp = repo_fingerprint(repo_dir)
    entry = (f"### {ts} — {command}\n"
             + (f"- task: {task}\n" if task else "")
             + f"- result: {result or '(no output)'}\n"
             + f"- passed: {'true' if passed else 'false'}\n"
             + f"- fingerprint: {fp}\n")
    _append_to_section(Path(cy["path"]), "Validation", entry, fence=VALIDATION_FENCE)
    return {"ts": ts, "command": command, "passed": passed, "fingerprint": fp,
            "cycle": cy["cycle"]}


def validation_runs(item_dir: Path, *, cycle: int | None = None) -> list[dict]:
    """Every recorded build validation run, in record order. The head slot holds the
    COMMAND: a run is identified by what executed."""
    out: list[dict] = []
    for r in cycle_reports(item_dir):
        if cycle is not None and r["cycle"] != cycle:
            continue
        body = split_sections(Path(r["path"]).read_text(encoding="utf-8")).get("Validation", "")
        for block in _fenced_blocks(body, lang=VALIDATION_FENCE):
            for e in _parse_ledger_entries(block):
                out.append({"ts": e.get("ts", ""), "command": e.get("check", ""),
                            "task": e.get("task", ""), "result": e.get("result", ""),
                            "passed": bool(e.get("passed")),
                            "fingerprint": e.get("fingerprint", ""), "cycle": r["cycle"]})
    return out


def record_validation_audit(item_dir: Path, repo_dir: Path | None, *, command: str,
                            claimed: bool, actual: bool, result: str) -> dict:
    """Record the kernel's AUDIT of one build validation claim.

    Lands as `kind: audit`, which `evidence_entries` filters out, so it never counts as a check."""
    def _one(s: str) -> str:
        return " ".join((s or "").split())
    command = _one(command)
    if not command:
        raise ValueError("an audit needs the command it re-ran")
    reports = cycle_reports(item_dir)
    if not reports:
        raise ValueError("no cycle report to record into")
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fp = repo_fingerprint(repo_dir)
    entry = (f"### {ts} — {command}\n"
             f"- kind: {KIND_AUDIT}\n"
             f"- how: re-ran build's recorded command\n"
             f"- result: {_one(result) or '(no output)'}\n"
             f"- claimed: {'true' if claimed else 'false'}\n"
             f"- passed: {'true' if actual else 'false'}\n"
             f"- fingerprint: {fp}\n")
    _append_to_section(Path(reports[-1]["path"]), "Verification", entry, fence=VERIFICATION_FENCE)
    return {"ts": ts, "command": command, "claimed": claimed, "actual": actual,
            "agrees": claimed == actual, "cycle": reports[-1]["cycle"]}


def validation_audit(item_dir: Path, *, cycle: int | None = None) -> list[dict]:
    """The audit rows recorded so far. `cycle` scopes to one pass — the loop acts only on
    the pass it is deciding."""
    rows = []
    for e in _ledger(item_dir):
        if e.get("kind") != KIND_AUDIT:
            continue
        if cycle is not None and e.get("cycle") != cycle:
            continue
        claimed, actual = bool(e.get("claimed")), bool(e.get("passed"))
        rows.append({"command": e.get("check", ""), "claimed": claimed, "actual": actual,
                     "agrees": claimed == actual, "result": e.get("result", ""),
                     "cycle": e.get("cycle")})
    return rows


def validation_discrepancies(item_dir: Path, *, cycle: int | None = None) -> list[dict]:
    """Audit rows where build's claim and the machine disagree. The LAST audit of a
    command wins, so a fix clears it."""
    latest: dict[str, dict] = {}
    for r in validation_audit(item_dir, cycle=cycle):
        latest[r["command"]] = r
    return [r for r in latest.values() if not r["agrees"]]


def _parse_ledger_entries(text: str) -> list[dict]:
    """Line-oriented ledger text → entry dicts, in order. One format, one parser, shared
    by both readers."""
    entries: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = _EVIDENCE_HEAD.match(line)
        if m:
            cur = {"ts": m.group("ts"), "check": m.group("check")}
            entries.append(cur)
        elif cur is not None:
            kv = re.match(r"^- (how|result|note|by|kind|where|why|unknown|met|missed|probed|task|"
                          r"finding|general|passed|claimed|deferred|fingerprint): (.*)$", line)
            if kv:
                k, v = kv.group(1), kv.group(2).strip()
                if k == "finding":
                    # `<severity>: <text>` — the vocabulary is three words, so the first colon
                    # splits it unambiguously.
                    f = re.match(r"^(low|medium|high):\s*(.*)$", v)
                    cur.setdefault("findings", []).append(
                        {"severity": f.group(1), "text": f.group(2)} if f
                        else {"severity": "medium", "text": v})
                    continue
                if k in ("met", "missed"):
                    # One rubric criterion, judged. Repeated lines accumulate: each criterion
                    # stands on its own.
                    cur.setdefault("criteria", []).append({"text": v, "met": k == "met"})
                    continue
                if k == "probed":
                    # One probe per line, accumulating. A lens read is a LIST, and a reader wants
                    # the probes separable.
                    cur.setdefault("probed", []).append(v)
                    continue
                cur[k] = (v == "true") if k in ("passed", "deferred") else v
    return entries


def record_verification(item_dir: Path, repo_dir: Path | None, *, check: str, how: str,
                        result: str, passed: bool, deferred: bool = False, note: str = "",
                        title: str = "", by: str = BY_AGENT,
                        met: list[str] | None = None, missed: list[str] | None = None) -> dict:
    """Append one entry to the cycle report's check fence. Append-only, so 'verified' is derived.

    `by` is provenance: `machine` beats `agent` and is final for the cycle."""
    # Single-line coerce: the ledger is line-oriented, so an embedded newline corrupts every entry
    # after it.
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    check, how, result, note = _one_line(check), _one_line(how), _one_line(result), _one_line(note)
    if not (check and how and result):
        raise ValueError("evidence needs non-empty check, how, and result")
    # Single source of truth for check state: the ledger key MUST be a plan check id (when one exists).
    plan_path = Path(item_dir) / "artifacts" / artifact_file("plan")
    valid_ids = [c["id"] for c in parse_vet_plan(plan_path.read_text(encoding="utf-8")).get("checks", [])] \
        if plan_path.is_file() else []
    # `depth: none` has no key space, so an entry here could only be one vet invented. Refuse and
    # redirect.
    if plan_vet_depth(item_dir) == "none":
        raise ValueError(
            "the approved plan declares `depth: none` — this item has no verification plan, so "
            "there is no check id to record against and nothing is owed. If you believe something "
            "here SHOULD be checked, say so in your report and in `report_completion`: the depth "
            "call is the plan's, and changing it is a revise at review, not a record here.")
    if valid_ids:
        check = _resolve_evidence_check(check, valid_ids)
    met = [_one_line(t) for t in (met or []) if str(t).strip()]
    missed = [_one_line(t) for t in (missed or []) if str(t).strip()]
    rubric = next((c.get("rubric") or [] for c in parse_vet_plan(plan_path.read_text(encoding="utf-8"))["checks"]
                   if c["id"] == check), []) if plan_path.is_file() else []
    if rubric and not deferred:
        if len(met) + len(missed) != len(rubric):
            raise ValueError(
                f"check {check!r} has {len(rubric)} rubric criteria and you accounted for "
                f"{len(met) + len(missed)}. Judge each one and pass it in `met` or `missed` — a "
                "criterion left out is one nobody knows the answer to, and the row would read as "
                "though it had been judged.")
        if missed and passed:
            raise ValueError(
                f"check {check!r} cannot pass with {len(missed)} criterion(s) missed. A rubric is "
                "the bar, not a score: record it as failed, and say which criteria missed.")
    elif met or missed:
        raise ValueError(f"check {check!r} declares no rubric in the plan — there are no criteria "
                         "to judge. Record the result in `result`.")
    # Target the LATEST cycle report even when the driver closed it; scaffold only when no cycle
    # exists.
    reports = cycle_reports(item_dir)
    cy = ({"cycle": reports[-1]["cycle"], "path": reports[-1]["path"]}
          if reports else scaffold_cycle(item_dir, title=title))
    # A machine entry is the cycle's final word on that check — otherwise kernel execution is
    # decorative.
    if by != BY_MACHINE and any(e["check"] == check and e.get("by") == BY_MACHINE
                                for e in evidence_entries(item_dir)
                                if e.get("cycle") == cy["cycle"]):
        raise ValueError(
            f"check {check!r} was executed by the kernel this cycle — that result stands and "
            "cannot be re-recorded. Read it, judge the rest, and if you believe it is wrong say "
            "so in your report; the plan's `run:` block is what changes it, not a second entry.")
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fp = repo_fingerprint(repo_dir)
    entry = (f"### {ts} — {check}\n"
             f"- how: {how}\n"
             f"- result: {result}\n"
             + (f"- note: {note}\n" if note else "")
             + "".join(f"- met: {t}\n" for t in met)
             + "".join(f"- missed: {t}\n" for t in missed)
             + f"- by: {by}\n"
             + f"- passed: {'true' if passed else 'false'}\n"
             + ("- deferred: true\n" if deferred else "")
             + f"- fingerprint: {fp}\n")
    _append_to_section(Path(cy["path"]), "Verification", entry, fence=VERIFICATION_FENCE)
    return {"ts": ts, "check": check, "passed": passed, "deferred": deferred, "by": by,
            "fingerprint": fp, "cycle": cy["cycle"]}


def record_diagnosis(item_dir: Path, *, check: str, where: str, why: str,
                     unknown: str = "") -> dict:
    """Append vet's DIAGNOSIS of a failed check: where it broke and why. Never the fix.

    `unknown` is load-bearing: a confident guess sends build somewhere nobody looked."""
    item_dir = Path(item_dir)

    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    check, where, why, unknown = (_one_line(check), _one_line(where), _one_line(why),
                                  _one_line(unknown))
    if not (check and where and why):
        raise ValueError("a diagnosis needs the check, `where` (the narrowest located source) and "
                         "`why` (the mechanism, as far as the evidence supports it)")
    latest = {e["check"]: e for e in evidence_entries(item_dir)}
    e = latest.get(check)
    if e is None:
        raise ValueError(f"check {check!r} has no recorded verdict — record the result first; a "
                         "diagnosis explains a failure that is already on the record")
    if e.get("passed") or e.get("deferred"):
        raise ValueError(f"check {check!r} is not failing — a diagnosis explains a FAILURE. If you "
                         "have a concern about a passing check, it belongs in your report's "
                         "observations, where the review gate reads it")
    reports = cycle_reports(item_dir)
    if not reports:
        raise ValueError("no cycle report to record into")
    cy = reports[-1]
    entry = (f"### {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')} — {check}\n"
             f"- kind: {KIND_DIAGNOSIS}\n"
             f"- where: {where}\n"
             f"- why: {why}\n"
             + (f"- unknown: {unknown}\n" if unknown else ""))
    _append_to_section(Path(cy["path"]), "Verification", entry, fence=VERIFICATION_FENCE)
    return {"check": check, "where": where, "why": why, "unknown": unknown, "cycle": cy["cycle"]}


def record_lens(item_dir: Path, *, probed: list[str] | str, lens: str,
                findings: list[dict] | None = None) -> dict:
    """Record one standing lens's read of this cycle. No findings is a complete record.

    `probed` is a LIST, one probe per entry. No quotas: a quota manufactures findings."""
    item_dir = Path(item_dir)

    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    lens = _one_line(lens).lower()
    probes = [p for p in (_one_line(x) for x in
                          ([probed] if isinstance(probed, str) else list(probed or [])))
              if p]
    if lens not in LENSES:
        raise ValueError(f"unknown lens {lens!r} — the lenses are {', '.join(LENSES)}")
    if not probes:
        raise ValueError(
            f"the {lens} lens needs `probed`: what you actually examined or tried, one probe per "
            "entry. A lens with no findings and no probe record is indistinguishable from a lens "
            "that was skipped.")
    rows: list[dict] = []
    for f in findings or []:
        sev, text = _one_line(str(f.get("severity") or "")).lower(), _one_line(str(f.get("text") or ""))
        if not text:
            continue
        if sev not in SEVERITIES:
            raise ValueError(f"finding severity must be one of {', '.join(SEVERITIES)} (got "
                             f"{sev!r}) — severity is what decides whether it gates")
        rows.append({"severity": sev, "text": text})
    reports = cycle_reports(item_dir)
    if not reports:
        raise ValueError("no cycle report to record into")
    cy = reports[-1]
    entry = (f"### {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')} — {lens}\n"
             f"- kind: {KIND_LENS}\n"
             + "".join(f"- probed: {p}\n" for p in probes)
             + "".join(f"- finding: {r['severity']}: {r['text']}\n" for r in rows))
    _append_to_section(Path(cy["path"]), "Verification", entry, fence=VERIFICATION_FENCE)
    return {"lens": lens, "probed": probes, "findings": rows, "cycle": cy["cycle"],
            "gates": bool(_gating(lens, rows))}


def record_nomination(item_dir: Path, *, check: str, general: str) -> dict:
    """Nominate one of this item's checks for the repo's verification library.

    Vet nominates, close writes, and only a check that PASSED here qualifies."""
    item_dir = Path(item_dir)

    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    check, general = _one_line(check), _one_line(general)
    if not (check and general):
        raise ValueError("a nomination needs the check and `general` — what it defends about this "
                         "REPO, said without mentioning this item")
    if not any(e["check"] == check and e.get("passed") for e in evidence_entries(item_dir)):
        raise ValueError(
            f"check {check!r} has never passed here — only a check that has actually run and come "
            "back green may enter the library. An untested entry costs the next item a cycle.")
    reports = cycle_reports(item_dir)
    if not reports:
        raise ValueError("no cycle report to record into")
    cy = reports[-1]
    entry = (f"### {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')} — {check}\n"
             f"- kind: {KIND_NOMINATION}\n"
             f"- general: {general}\n")
    _append_to_section(Path(cy["path"]), "Verification", entry, fence=VERIFICATION_FENCE)
    return {"check": check, "general": general, "cycle": cy["cycle"]}


def nominations(item_dir: Path) -> dict[str, dict]:
    """Every check nominated for the library across this item's cycles. Not this-cycle-only:
    a nomination is a claim about the REPO."""
    out: dict[str, dict] = {}
    for e in _ledger(item_dir):
        if e.get("kind") == KIND_NOMINATION:
            out[e["check"]] = {"general": str(e.get("general") or ""), "cycle": e.get("cycle")}
    return out


def lens_reads(item_dir: Path) -> dict[str, dict]:
    """This cycle's lens reads → {lens: {probed, findings, cycle}}. THIS cycle only: last
    cycle's finding describes code that has moved."""
    reports = cycle_reports(item_dir)
    if not reports:
        return {}
    cycle = reports[-1]["cycle"]
    out: dict[str, dict] = {}
    for e in _ledger(item_dir):
        if e.get("kind") == KIND_LENS and e.get("cycle") == cycle:
            # `probed` is a list. An older single `- probed:` line parses to a one-item list, so
            # no reader cares.
            raw = e.get("probed") or []
            out[e["check"]] = {"probed": [raw] if isinstance(raw, str) else list(raw),
                               "findings": list(e.get("findings") or []),
                               "cycle": e.get("cycle")}
    return out


def _gating(lens: str, findings: list[dict]) -> list[dict]:
    at = _LENS_GATES_AT.get(lens, ())
    return [f for f in findings if f.get("severity") in at]


def lens_gaps(item_dir: Path) -> list[dict]:
    """The lens findings that send this item back to build. A failing lens routes like any other
    failed check."""
    reads = lens_reads(item_dir)
    return [{"lens": ln, **f} for ln in LENSES
            for f in _gating(ln, reads.get(ln, {}).get("findings") or [])]


def missing_lenses(item_dir: Path) -> list[str]:
    """Standing lenses with no read this cycle. `performance` is never here — without a
    budget, demanding it buys opinions."""
    reads = lens_reads(item_dir)
    return [ln for ln in STANDING_LENSES if ln not in reads]


def undiagnosed_failures(item_dir: Path) -> list[str]:
    """Checks whose latest verdict failed with no diagnosis in that same cycle.
    Same-cycle: the code moved, so last cycle's cause may be gone."""
    latest = {e["check"]: e for e in evidence_entries(item_dir)}
    diag = diagnoses(item_dir)
    return [c for c, e in latest.items()
            if not e.get("passed") and not e.get("deferred")
            and diag.get(c, {}).get("cycle") != e.get("cycle")]


def evidence_entries(item_dir: Path) -> list[dict]:
    """Every recorded VERDICT, in cycle order.

    Diagnoses share the fence and are filtered out: one leaking in reads as a second failure."""
    return [e for e in _ledger(item_dir) if e.get("kind", KIND_VERDICT) == KIND_VERDICT]


def _ledger(item_dir: Path) -> list[dict]:
    """Every entry in the `## Verification` fences, verdicts and diagnoses alike, in record order."""
    entries: list[dict] = []
    for r in cycle_reports(item_dir):
        body = split_sections(Path(r["path"]).read_text(encoding="utf-8")).get("Verification", "")
        for block in _fenced_blocks(body):
            for e in _parse_ledger_entries(block):
                entries.append({**e, "cycle": r["cycle"]})
    return entries


def diagnoses(item_dir: Path) -> dict[str, dict]:
    """The latest diagnosis per check.

    A diagnosis is a separate act from the verdict, so merging them would let an agent rewrite a
    machine verdict."""
    out: dict[str, dict] = {}
    for e in _ledger(item_dir):
        if e.get("kind") == KIND_DIAGNOSIS:
            out[e["check"]] = {"where": str(e.get("where") or ""),
                               "why": str(e.get("why") or ""),
                               "unknown": str(e.get("unknown") or ""),
                               "cycle": e.get("cycle")}
    return out


# Proof: the connected view

# One row per BUILT THING, carrying its own validation → verification. Joined on the plan's `##
# Tasks` id.
_TAGGED_BULLET = re.compile(r"^\s*[-*]\s*[*`_]{0,2}(t\d+)\b[*`_]{0,2}[\s—:.\-]*(.*)$")


def _tagged_bullets(body: str) -> tuple[dict[str, list[str]], list[str]]:
    """A report section's bullets split by leading task id. A bullet is a BLOCK: continuation
    lines belong to the bullet above."""
    by_task: dict[str, list[str]] = {}
    loose: list[str] = []
    cur: list[str] | None = None
    fenced = False
    for line in (body or "").splitlines():
        # A fenced block is the section's MACHINE lane, not prose — read line-wise its fields
        # spill into the bullets.
        if _FENCE.match(line.strip()):
            fenced = not fenced
            cur = None
            continue
        if fenced or FILL.search(line):     # machine lane, or an unfilled slot: not content
            continue
        m = _TAGGED_BULLET.match(line)
        if m:
            cur = by_task.setdefault(m.group(1), [])
            cur.append(m.group(2).strip())
        elif re.match(r"^\s*[-*]\s+\S", line):
            cur = None
            loose.append(line.strip().lstrip("-* ").strip())
        elif line.strip() and cur is not None and cur:
            cur[-1] = (cur[-1] + " " + line.strip()).strip()
        elif line.strip() and cur is None and loose:
            loose[-1] = (loose[-1] + " " + line.strip()).strip()
    return {k: [v for v in vals if v] for k, vals in by_task.items()}, [v for v in loose if v]


def proof_rows(item_dir: Path) -> list[dict]:
    """The plan's tasks in order, then one item-wide row for the rest.

    A check the loop has not reached is still a row: the exam is decided at plan."""
    item_dir = Path(item_dir)
    plan_path = item_dir / "artifacts" / artifact_file("plan")
    plan = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else ""
    tasks = parse_tasks(plan)
    checks = parse_vet_plan(plan).get("checks", [])
    # check id → the task ids it defends, straight off the approved plan.
    covers_of = {c["id"]: str(c.get("covers") or "") for c in checks}
    built: dict[str, list[str]] = {}
    validated: dict[str, list[str]] = {}
    built_loose: list[str] = []
    valid_loose: list[str] = []
    for r in cycle_reports(item_dir):
        sections = split_sections(Path(r["path"]).read_text(encoding="utf-8"))
        b, bl = _tagged_bullets(sections.get("Built", ""))
        v, vl = _tagged_bullets(sections.get("Validation", ""))
        for src, dst in ((b, built), (v, validated)):
            for k, vals in src.items():
                dst.setdefault(k, []).extend(vals)
        built_loose += bl
        valid_loose += vl

    # Each check's pass/fail sequence by cycle, so the surface can render `c3 ✗→✓`. Latest-per-
    # check loses that.
    history: dict[str, list[dict]] = {}
    for e in evidence_entries(item_dir):
        history.setdefault(e["check"], []).append(
            {"cycle": e.get("cycle"), "passed": bool(e.get("passed"))})

    # The planned exam joined with what the loop recorded. A dropped check still shows — its
    # verdict was real.
    verdicts = {r["check"]: r for r in verdict_rows(item_dir)}
    # `by` on a PLANNED row is a promise, not a record. Once it runs, the ledger's `by` overwrites
    # it.
    planned = [{"check": c["id"],
                # The plan's own sentence for what a green MEANS. Never re-derived from `run:` —
                # that is the drift `proves` ends.
                "proves": str(c.get("proves") or ""),
                "expect": str(c.get("expect") or ""),
                "mode": str(c.get("mode") or ""), "ran": False,
                # A rubric check is judged, so the kernel never runs it (see services/checks.py).
                "by": BY_MACHINE if (c.get("run") and not c.get("rubric")) else BY_AGENT,
                # Where the check came from: "" is authored here, `standing`/`library` is
                # inherited from the repo.
                "source": str(c.get("source") or ""),
                # The criteria the plan set, readable at the plan gate. The recorded judgment
                # lands beside them.
                "rubric": [str(r) for r in (c.get("rubric") or [])], "criteria": [],
                "passed": False, "deferred": False, "cycle": None, "how": "", "result": ""}
               for c in checks]
    ordered = planned + [{"check": k, "proves": "", "expect": "", "mode": "", "rubric": []}
                         for k in verdicts if k not in covers_of]
    verified: dict[str, list[dict]] = {}
    verified_loose: list[dict] = []
    for base in ordered:
        v = verdicts.get(base["check"])
        row = {**base, **(v or {}), "ran": v is not None,
               "history": history.get(base["check"], [])}
        # Only tasks the plan DECLARES can hold a check. An unattributable one lands item-wide
        # rather than disappearing.
        known = {t["id"] for t in tasks}
        covers = [t for t in re.findall(r"t\d+", covers_of.get(row["check"], "")) if t in known]
        if covers:
            for t in covers:
                verified.setdefault(t, []).append(row)
        else:
            verified_loose.append(row)

    rows = [{"task": t["id"], "text": t["text"], "detail": t.get("detail", ""), "done": t["done"],
             "built": built.get(t["id"], []), "validated": validated.get(t["id"], []),
             "verified": verified.get(t["id"], [])}
            for t in tasks]
    if built_loose or valid_loose or verified_loose:
        rows.append({"task": "", "text": "item-wide", "detail": "", "done": False,
                     "built": built_loose,
                     "validated": valid_loose, "verified": verified_loose})
    return rows


def verdict_rows(item_dir: Path) -> list[dict]:
    """The LATEST verdict per check, in first-seen order.

    A check that failed in c1 and passed in c3 IS passing, and two rows invite averaging."""
    latest: dict[str, dict] = {}
    for e in evidence_entries(item_dir):
        latest[e["check"]] = e
    diag = diagnoses(item_dir)
    return [{"check": c, "passed": bool(e.get("passed")), "deferred": bool(e.get("deferred")),
             "cycle": e.get("cycle"), "how": str(e.get("how") or ""),
             "result": str(e.get("result") or ""),
             # Pre-provenance entries read as `agent` — that is what they were.
             "by": str(e.get("by") or BY_AGENT),
             # Per-criterion judgment (empty on a check with no rubric).
             "criteria": list(e.get("criteria") or []),
             # The located cause, from THIS cycle's diagnosis: a cause the code has moved past
             # misleads.
             **{k: (diag.get(c, {}).get(k, "") if diag.get(c, {}).get("cycle") == e.get("cycle")
                    else "") for k in ("where", "why", "unknown")}}
            for c, e in latest.items()]


_NO_VET_LINE = "**Nothing to verify.**"


def note_no_verification(item_dir: Path) -> str | None:
    """Write the `depth: none` cycle's `## Verification`, code-written and quoting the plan's reason.

    Derived, so an empty fence cannot read as a vet that gave up. Idempotent."""
    item_dir = Path(item_dir)
    reports = cycle_reports(item_dir)
    if not reports:
        return None
    path = Path(reports[-1]["path"])
    if _NO_VET_LINE in path.read_text(encoding="utf-8"):
        return None
    plan = item_dir / "artifacts" / artifact_file("plan")
    reason = " ".join(str(parse_vet_plan(plan.read_text(encoding="utf-8")).get("reason") or "").split()) \
        if plan.is_file() else ""
    _append_to_section(path, "Verification",
                       f"{_NO_VET_LINE} The approved plan declares `depth: none`"
                       + (f" — {reason}" if reason else "")
                       + ". No check was owed, so none was run.\n")
    return str(path)


def evidence_status(item_dir: Path, repo_dir: Path | None, *, scope_to_plan: bool = True) -> dict:
    """The derived verdict over the ledger: `unverified` · `failed` · `stale` · `passed`.

    Scoped to the CURRENT plan's checks, so a renamed check's orphan cannot pin the loop red."""
    entries = evidence_entries(item_dir)
    ids = _plan_check_ids(item_dir) if scope_to_plan else None
    auths = authorization_entries(item_dir)
    deferred_by_auth = {a["check"] for a in auths if a.get("status") == "pending" and a.get("check")}
    # A DENIED request WAIVES its check: the change won't happen, so the item closes with the gap
    # on record.
    waived_by_auth = {a["check"] for a in auths if a.get("status") == "denied" and a.get("check")} \
        - deferred_by_auth
    if ids is not None:
        deferred_by_auth &= ids
        waived_by_auth &= ids
    if not entries and not deferred_by_auth:
        # `depth: none` means an empty ledger is the CORRECT ledger, so it reports `passed`, not
        # `unverified`.
        if plan_vet_depth(item_dir) == "none":
            return {"status": "passed", "entries": 0, "not_required": True}
        return {"status": "unverified", "entries": 0}
    latest: dict[str, dict] = {}
    for e in entries:               # last entry per check wins
        latest[e["check"]] = e
    orphaned: list[str] = []
    if ids is not None:
        orphaned = sorted(c for c in latest if c not in ids)
        latest = {c: e for c, e in latest.items() if c in ids}
    deferred = sorted({c for c, e in latest.items() if e.get("deferred")} | deferred_by_auth)
    waived = sorted(waived_by_auth)
    extra = {**({"orphaned": orphaned} if orphaned else {}),
             **({"waived": waived} if waived else {})}
    excused = set(deferred) | set(waived)
    if not latest and not deferred:   # every recorded check is an orphan/waived and nothing is deferred
        return {"status": "unverified", "entries": len(entries), **extra}
    now_fp = repo_fingerprint(repo_dir)
    failed = [c for c, e in latest.items() if not e.get("passed") and c not in excused]
    if failed:
        return {"status": "failed", "entries": len(entries), "failed_checks": failed, **extra}
    if deferred:
        return {"status": "deferred", "entries": len(entries), "deferred_checks": deferred, **extra}
    stale = [c for c, e in latest.items() if e.get("fingerprint") != now_fp and c not in excused]
    if stale:
        return {"status": "stale", "entries": len(entries), "stale_checks": stale, **extra}
    return {"status": "passed", "entries": len(entries), **extra}
