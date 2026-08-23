"""The verification plan's grammar — check blocks, rubric items, and the structural
issues that block a gate."""

import re
from pathlib import Path

from .text import FILL, split_sections
from .spec import artifact_file

VET_DEPTHS = ("none", "checks", "scenarios")
VET_MODES = ("command", "interaction", "inspection")
VET_CHECK_ID = re.compile(r"^[a-z0-9-]+$")
_VET_HEADER_KEY = re.compile(r"^(depth|reason|env):\s*(.*)$")
_VET_FIELD = re.compile(
    r"^-\s*(proves|traces|covers|mode|scenario|run|rubric|expect|source):\s*(.*)$")
# A rubric criterion: an INDENTED bullet under `- rubric:`. Indentation separates fields, the
# bullet separates criteria.
_RUBRIC_ITEM = re.compile(r"^\s+[-*]\s+\S")
# The literal command the KERNEL runs. One line, because a check is one exit code.
_VET_CHECK_HEAD = re.compile(r"^###\s+(.+?)\s*$")
# Vagueness heuristic for `expect`. A banned-word list is too brittle to BLOCK on — hence soft.
_VET_VAGUE = re.compile(r"\b(works|correctly|properly|as expected)\b", re.IGNORECASE)
_VET_EXPECT_MIN = 40
# `proves` is written FOR the owner: "exit code 0" tells nobody whether a green demonstrates the
# intent.
_PROVES_MACHINE = re.compile(
    r"\bexit(?:s|ed)?[- ]?(?:code|status)\b|\bexit\s+(?:0|1|zero|non-?zero)\b|\bstdout\b|"
    r"\bstderr\b|\breturns?\s+(?:0|1|zero)\b|"
    r"\b(?:tests?|suite|script|command|check)\s+(?:pass(?:es|ed)?|succeeds?|is green)\b",
    re.IGNORECASE)
_PROVES_MIN = 25


def vet_value(raw: str) -> str:
    """A field value with unfilled `<fill:…>` markers treated as absent."""
    return FILL.sub("", raw or "").strip()


def parse_check_blocks(body: str) -> list[dict]:
    """`### <check-id>` blocks → check dicts, in order. Plan and library share this
    grammar, so an entry inherits verbatim."""
    out: list[dict] = []
    cur: dict | None = None
    last = ""              # the field a wrapped continuation line belongs to
    for line in body.splitlines():
        h = _VET_CHECK_HEAD.match(line)
        if h:
            last = ""
            # `covers` joins the Proof view; absent is not hard. `proves` is the one HUMAN field,
            # and without it readers drift.
            cur = {"id": vet_value(h.group(1)), "proves": "", "traces": "", "covers": "",
                   "mode": "", "scenario": "", "run": "", "expect": "", "rubric": [], "source": ""}
            out.append(cur)
            continue
        if cur is None:
            continue
        m = _VET_FIELD.match(line.strip())
        if m:
            # `rubric:` holds a LIST — indented bullets, each judged and recorded separately.
            # Everything else is one value.
            cur[m.group(1)] = [] if m.group(1) == "rubric" else vet_value(m.group(2))
            last = m.group(1)
        elif last and line.startswith((" ", "\t")) and line.strip():
            if last == "rubric":
                text = vet_value(re.sub(r"^\s*[-*]\s*", "", line))
                if _RUBRIC_ITEM.match(line):
                    cur["rubric"].append(text)
                elif cur["rubric"] and text:
                    cur["rubric"][-1] += " " + text
                continue
            # A wrapped field — markdown folds it, so we do too. `expect:` is exactly the field
            # long enough to wrap.
            cur[last] = (cur[last] + " " + line.strip()).strip()
    return out


def parse_vet_plan(plan_text: str) -> dict:
    """Parse plan.md's `## Verification plan` → {present, depth, reason, env, checks}.
    Pure text → data; validity is judged separately."""
    sections = split_sections(plan_text)
    body = sections.get("Verification plan")
    if body is None:
        body = sections.get("Vet plan")
    if body is None:
        return {"present": False, "depth": "", "reason": "", "env": "", "checks": []}
    out: dict = {"present": True, "depth": "", "reason": "", "env": "", "checks": []}
    lines = body.splitlines()
    first = next((i for i, ln in enumerate(lines) if _VET_CHECK_HEAD.match(ln)), len(lines))
    for line in lines[:first]:
        m = _VET_HEADER_KEY.match(line.strip())
        if m:
            out[m.group(1)] = vet_value(m.group(2))
    out["checks"] = parse_check_blocks("\n".join(lines[first:]))
    return out


# An UNNARROWED suite runner is build's validation, not the item's exam. Only the bare form is
# refused.
_SUITE_RUNNERS = (
    r"pytest", r"py\.test", r"python3?\s+-m\s+pytest", r"python3?\s+-m\s+unittest(\s+discover)?",
    r"npm\s+(run\s+)?test", r"yarn\s+test", r"pnpm\s+(run\s+)?test", r"jest", r"vitest",
    r"go\s+test", r"cargo\s+test", r"bundle\s+exec\s+rspec", r"rspec", r"mvn\s+test",
    r"gradle\s+test", r"dotnet\s+test", r"make\s+test", r"tox",
)
# …and what NARROWS one to a single behaviour. Anything here and the invocation is a real check.
_SUITE_NARROWERS = re.compile(
    r"(::|-k\b|--filter\b|-m\b|--run\b|--testNamePattern\b|-t\b|-e\b|--only\b|"
    r"[\w./-]+\.(py|js|ts|tsx|rb|go|java|cs)\b|[\w-]+\.[A-Za-z_]\w*)")
_SUITE_RUN = re.compile(r"^\s*(?:[\w./-]*/)?(?:" + "|".join(_SUITE_RUNNERS) + r")\b", re.IGNORECASE)


def is_whole_suite_run(cmd: str) -> bool:
    """Is this `run:` the project's whole test suite, unnarrowed? A narrowed invocation
    drives one behaviour and is a fine check."""
    cmd = " ".join((cmd or "").split())
    if not cmd or not _SUITE_RUN.match(cmd):
        return False
    # Only the runner's OWN arguments narrow it. `-q`/`--tb=short` are noise controls, not
    # selectors.
    head = re.split(r"&&|\|\||;|\|", cmd)[0]
    tail = head[_SUITE_RUN.match(head).end():] if _SUITE_RUN.match(head) else ""
    return not _SUITE_NARROWERS.search(tail)


def vet_plan_hard_issues(vp: dict) -> list[str]:
    """The gate-blocking structural rules, every one mechanically decidable.

    `proves` is HARD where `covers` is not: a missing `covers` costs a join, a missing `proves` means
    nobody can say what a green MEANS."""
    if not vp.get("present"):
        return ["missing required section '## Verification plan'"]
    issues: list[str] = []
    depth, reason, env = vp.get("depth", ""), vp.get("reason", ""), vp.get("env", "")
    checks = vp.get("checks", [])
    if depth not in VET_DEPTHS:
        issues.append(f"vet plan: depth must be one of {'/'.join(VET_DEPTHS)}"
                      + (f" (got {depth!r})" if depth else " — it is missing"))
    if not reason:
        issues.append("vet plan: reason is required (one line, even for depth: none)")
    if depth == "none" and checks:
        issues.append(f"vet plan: depth is none but {len(checks)} check(s) are declared — "
                      "drop the checks or raise the depth")
    if depth in ("checks", "scenarios") and not checks:
        issues.append(f"vet plan: depth {depth} requires at least one `### <check-id>` check")
    seen: set[str] = set()
    for c in checks:
        cid = c.get("id", "")
        label = cid or "(unnamed)"
        if not VET_CHECK_ID.match(cid or ""):
            issues.append(f"vet plan check {label!r}: id must be a lowercase slug ([a-z0-9-]+) — "
                          "it keys the evidence ledger")
        if cid in seen:
            issues.append(f"vet plan check {label!r}: duplicate id")
        seen.add(cid)
        for field_name in ("proves", "traces", "mode", "scenario"):
            if not c.get(field_name):
                # The remedy travels with the complaint: a bare "missing scenario" reads as
                # "`run:` was the wrong field".
                extra = (" — a check with `run:` still needs the prose scenario BESIDE it; add "
                         "the scenario, never drop the run block"
                         if field_name == "scenario" and c.get("run") else "")
                if field_name == "proves":
                    extra = (" — one plain sentence saying what is TRUE of the product when this "
                             "passes, in the owner's terms and not the command's")
                issues.append(f"vet plan check {label!r}: missing `{field_name}`{extra}")
        # As a vet-plan check it runs the suite twice and files the result as the item's own
        # proof. HARD.
        if c.get("run") and is_whole_suite_run(str(c.get("run"))):
            issues.append(
                f"vet plan check {label!r}: `run:` is the project's whole test suite — that is "
                "BUILD's validation, which it runs every cycle and the kernel re-runs to audit. "
                "Drop this check, or narrow the command to the ONE behaviour this item promises "
                "(a single test, a scenario) and say in `proves:` what that green means for the "
                "owner")
        # A check needs a bar that can FAIL: a binary `expect`, or a rubric judged criterion by
        # criterion. Either suffices.
        if not c.get("expect") and not c.get("rubric"):
            issues.append(f"vet plan check {label!r}: needs `expect` (a binary pass condition), "
                          "`rubric` criteria (judged one by one), or both — a check with neither "
                          "has no way to fail")
        for i, crit in enumerate(c.get("rubric") or [], 1):
            if not str(crit).strip():
                issues.append(f"vet plan check {label!r}: rubric criterion {i} is empty")
        mode = c.get("mode", "")
        if mode and mode not in VET_MODES:
            issues.append(f"vet plan check {label!r}: mode must be one of {'/'.join(VET_MODES)} "
                          f"(got {mode!r})")
        if mode == "interaction" and env in ("", "none"):
            issues.append(f"vet plan check {label!r}: mode interaction drives the real thing — "
                          "the plan needs an `env` recipe (not none)")
    return issues


# A check depending on a RETIRED read-only doc can never go green. Match the filename precisely.
_RETIRED_DOC_REF = re.compile(r"\bspec\.md\b", re.I)


def vet_plan_soft_flags(vp: dict) -> list[str]:
    """The judgment flags — surfaced in the gate brief, never blocking: a vague
    `expect`, a mechanism-worded `proves`, a retired-doc target."""
    flags: list[str] = []
    for c in vp.get("checks", []):
        exp, cid = c.get("expect", ""), c.get("id") or "(unnamed)"
        # A check INHERITED from the library is not this planner's prose — flagging it asks the
        # wrong author.
        if c.get("source") == "library":
            continue
        # Retired-doc scan across the whole check (traces/scenario/expect) — a check that can't pass.
        blob = " ".join(str(c.get(f) or "") for f in ("traces", "scenario", "expect"))
        if _RETIRED_DOC_REF.search(blob):
            flags.append(f"{cid}: targets the RETIRED doc spec.md (read-only) — this check can't "
                         "pass through the loop; drop it or migrate the doc's content to "
                         "architecture/decisions (an authorized contract change)")
        proves = c.get("proves", "")   # missing entirely is a HARD issue, not a soft flag
        if proves and (m := _PROVES_MACHINE.search(proves)):
            flags.append(f"{cid}: proves is written in the command's terms ({m.group(0)!r}) — say "
                         "what is true of the PRODUCT when this passes; the owner reads this line "
                         "without the check beside it")
        elif proves and len(proves) < _PROVES_MIN:
            flags.append(f"{cid}: proves is very short ({len(proves)} chars) — one full sentence, "
                         "readable on its own")
        if not exp:
            continue  # missing entirely is a HARD issue, not a soft flag
        if _VET_VAGUE.search(exp):
            flags.append(f"{cid}: expect contains a non-falsifiable word "
                         f"({_VET_VAGUE.search(exp).group(0)!r})")
        elif len(exp) < _VET_EXPECT_MIN:
            flags.append(f"{cid}: expect is very short ({len(exp)} chars) — is it falsifiable?")
    return flags


_VET_DEPTH_RANK = {"none": 0, "checks": 1, "scenarios": 2}


def parse_inner_checks(plan_text: str) -> list[str]:
    """plan.md's `## Inner checks` bullets → the commands build must run green before
    it may exit."""
    body = split_sections(plan_text).get("Inner checks", "")
    cmds: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if not m:
            continue
        cmd = vet_value(m.group(1)).strip("`").strip()
        if cmd:
            cmds.append(cmd)
    return cmds


def _is_legacy_plan(sections: dict) -> bool:
    """A pre-vet-loop plan: has the old `## Validation criteria`, lacks `## Vet plan`."""
    return "Validation criteria" in sections and "Vet plan" not in sections


def _plan_check_ids(item_dir: Path) -> set[str] | None:
    """The CURRENT vet plan's check ids. None when there is nothing to scope by, and the
    caller then reads the whole ledger."""
    plan = Path(item_dir) / "artifacts" / artifact_file("plan")
    if not plan.is_file():
        return None
    ids = {c.get("id") for c in parse_vet_plan(plan.read_text(encoding="utf-8")).get("checks", []) if c.get("id")}
    return ids or None


def plan_vet_depth(item_dir: Path) -> str:
    """The plan's declared vet depth, or `""` when there is no plan to read.

    `none` is the OWNER-APPROVED judgment that nothing here is observable — never vet's call, and
    everything downstream reads it from HERE."""
    plan = Path(item_dir) / "artifacts" / artifact_file("plan")
    if not plan.is_file():
        return ""
    vp = parse_vet_plan(plan.read_text(encoding="utf-8"))
    return str(vp.get("depth") or "") if vp.get("present") else ""
