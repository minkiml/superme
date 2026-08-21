"""Artifact machinery — one template plus a deterministic scaffolder per artifact kind.

THE STANDARD: agent supplies content, code supplies form.

- Code owns frontmatter, section order, ids and timestamps. The agent fills `<fill:…>` slots.
- A light SELF-CHECK runs at the phase gate that CONSUMES the doc, never at write time.
- Reject with instructions and no state change: nothing is persisted on failure.
- Claims are verified against GROUND TRUTH, so a doc cannot acquire a dead pointer at accept.
- Evidence goes STALE on later repo edits. "Validated" is earned, never asserted.
- Append-only, atomic writes for checkpoints.

Inside a work-item folder: `artifacts/` holds the agent-facing spine docs and the per-cycle
build⟷vet reports; `reports/` holds the owner-facing projection of each phase; `checkpoints/`
holds continuity.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

import yaml

from . import kind_profiles as _kp
from .kind_profiles import get_profile

log = logging.getLogger(__name__)

FILL = re.compile(r"<fill:[^>]*>")
# An artifact's own leading frontmatter, and the investigation-family stamp inside it. Read by
# `self_check` so a file is judged against the template that produced it.
_FM_BLOCK = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_FM_RESEARCH_KIND = re.compile(r"(?m)^research_kind:\s*(\S+)\s*$")


# The template FILE under the authoring skill is the single source: body, section order and the
# required-sections check all derive from it. A section with a `<fill:…>` slot must be FILLED;
# a comment-only one must merely EXIST.

_TEMPLATE_HOMES = {
    "brief":         ("triage", "brief-template.md"),
    "plan":          ("plan", "plan-template.md"),
    "plan-research": ("plan", "plan-research-template.md"),
    "build-vet":     ("build", "build-vet-template.md"),
    # The UNJUDGED shape — a research item whose family nobody named (every item minted before the
    # field existed is in that position). Deliberately family-neutral, and deliberately left alone:
    # `self_check` judges a base-shaped file against this spec, so adding a section here would
    # retro-fail records already written correctly.
    "investigation": ("investigate", "investigation-template.md"),
    # One shape per family. NOT variations on a theme: each family answers a different question,
    # so each owes a different record. What they share is `## Follow-up work` — a research item's
    # job is the investigation AND the work it implies.
    #
    # Read from the REGISTRY, never a literal list: a second copy is a family that silently
    # scaffolds the base shape because somebody added a row and missed this line.
    **{_kp.family_template(f.slug): ("investigate", f"{_kp.family_template(f.slug)}-template.md")
       for f in _kp.RESEARCH_FAMILIES},
    "review":          ("review", "review-template.md"),
    "review-research": ("review", "review-research-template.md"),
    "report-plan":          ("plan", "report-plan-template.md"),
    "report-plan-research": ("plan", "report-plan-research-template.md"),
    "report-vet":           ("vet", "report-vet-template.md"),
}
_template_cache: dict[str, str] = {}


def skill_template(name: str) -> str:
    """The template body for `name`, read from its authoring skill's `templates/` folder. Cached
    for the process lifetime (templates change only with a deploy)."""
    if name not in _template_cache:
        from ..paths import DEV_PLUGIN_DIR
        skill, fname = _TEMPLATE_HOMES[name]
        _template_cache[name] = (
            DEV_PLUGIN_DIR / "skills" / skill / "templates" / fname).read_text()
    return _template_cache[name]


def template_section_spec(name: str) -> list[tuple[str, bool]]:
    """[(heading, must_be_filled)] per `## ` heading, derived from the template itself — the
    template IS the required-sections list (no second list anywhere).

    Fill detection runs over each section's WHOLE body, not line by line: a `<fill:…>` slot wrapped
    across two lines matches neither line on its own, and a section whose only slot was wrapped
    would silently read as optional — the template would stop demanding the content it asks for."""
    spec: list[tuple[str, bool]] = []
    cur: str | None = None
    body: list[str] = []
    for line in skill_template(name).splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if cur is not None:
                spec.append((cur, bool(FILL.search("\n".join(body)))))
            cur, body = m.group(1), []
        elif cur is not None:
            body.append(line)
    if cur is not None:
        spec.append((cur, bool(FILL.search("\n".join(body)))))
    return spec





_HANDOFF = """# Handoff brief — {title}

## Background & why raised
<fill:the problem/story — why this came up>

## Discussion summary
<fill:what was discussed and concluded so far>

## Direction & options
<fill:high-level direction or alternatives, with leanings>

## Constraints & notes
<fill:constraints, tried-but-failed, out-of-scope>
"""

# kind → (template, required sections for the self-check). `brief` + `plan` are skill-owned
# templates (sections derived); `handoff-brief` sections are ALL optional (D5: capture friction
# kills itemizing) — its check only demands one non-empty section.
_SPECS: dict[str, dict] = {
    "brief":       {"file": "brief.md",      "required": (), "reader": "agent"},  # derived from template
    "plan":        {"file": "plan.md",       "required": (), "reader": "both"},  # per item-kind, resolved below
    # The research work-segment record — agent-facing, the counterpart of build-vet-<n>.md.
    # Sections derived from its template file.
    "investigation": {"file": "investigation.md", "required": (), "reader": "agent"},
    # Review's own agent-facing record. Without one its owner report had grown five machine-read
    # fields nobody reading it wanted. This holds the record; the report holds the judgment.
    "review":        {"file": "review.md",        "required": (), "reader": "agent"},
    "handoff-brief": {"file": "handoff-brief.md", "required": (), "reader": "agent"},
}
# Legacy plan shapes, READ-ONLY: a plan is judged against the shape it was authored under, and
# these die with their items.
_PLAN_REQUIRED_LEGACY = ("Approach", "Tasks", "Validation criteria")
_PLAN_FEED_SECTIONS = ("Touches", "Behavior preview", "Risks & assumptions")
_PLAN_REQUIRED_V1 = ("Approach", "Tasks", "Inner checks", "Vet plan")
_PLAN_REQUIRED_V2 = ("Approach", "Touches", "Behavior preview", "Tasks",
                     "Risks & assumptions", "Inner checks", "Vet plan")
_PLAN_REQUIRED_RESEARCH_V1 = ("Questions", "Method", "Boundaries", "Done criteria", "Tasks")
ARTIFACT_KINDS = tuple(_SPECS)


def _template_name(artifact: str, item_kind: str | None,
                   research_kind: str | None = None) -> str | None:
    """The skill-template name for a template-file-backed artifact kind, else None (embedded).

    `research_kind` is the investigation family (kind_profiles.RESEARCH_KINDS) and EVERY family has
    its own shape — the mapping is the slug itself, so adding a family is adding a template file and
    an enum entry, never a branch here. An unjudged item passes None and gets the base, which is the
    honest fallback: nobody picked a family, so nobody picked a shape either."""
    if artifact == "plan":
        return "plan-research" if item_kind == "research" else "plan"
    if artifact == "review":
        return "review-research" if item_kind == "research" else "review"
    if artifact == "investigation":
        return (_kp.family_template(research_kind) if research_kind in _kp.RESEARCH_KINDS
                else "investigation")
    return artifact if artifact == "brief" else None


def _template(artifact: str, item_kind: str | None, research_kind: str | None = None) -> str:
    name = _template_name(artifact, item_kind, research_kind)
    if name:
        return skill_template(name)
    return {"handoff-brief": _HANDOFF}[artifact]


def section_spec(artifact: str, item_kind: str | None,
                 research_kind: str | None = None) -> list[tuple[str, bool]]:
    """[(heading, must_be_filled)] the self-check enforces. Template-file kinds derive it from
    their template; embedded legacy kinds require-and-fill their `required` tuple."""
    name = _template_name(artifact, item_kind, research_kind)
    if name:
        return template_section_spec(name)
    return [(h, True) for h in _SPECS[artifact]["required"]]


def required_sections(artifact: str, item_kind: str | None,
                      research_kind: str | None = None) -> tuple[str, ...]:
    return tuple(h for h, _fill in section_spec(artifact, item_kind, research_kind))


def artifact_file(artifact: str) -> str:
    """The on-disk filename for an artifact kind (under the item's artifacts/)."""
    return _SPECS[artifact]["file"]


# The `reader:` LABEL (audit §5): who each artifact is designed for — `user` | `agent` | `both`.
# A label, never a constraint: stamped into scaffolded frontmatter, surfaced as a chip in artifact
# views (user-facing docs prominent, agent plumbing collapsed). Non-scaffolded kinds included so
# routes can label by filename.
ARTIFACT_READERS: dict[str, str] = {
    **{k: s["reader"] for k, s in _SPECS.items()},
    "prd": "both", "vet-report": "agent", "checkpoint": "agent", "notes": "agent",
    "attempts": "agent",
}


def _atomic_write(path: Path, text: str) -> None:
    """tmp + os.replace in the target dir — a reader never sees a half-written artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _inject_checks(body: str, blocks: list[str]) -> str:
    """Append ready-made check blocks to the end of a plan's `## Verification plan` section."""
    m = re.search(r"(?ms)^##\s+Verification plan\s*$.*?(?=^##\s|\Z)", body)
    if not m or not blocks:
        return body
    add = "\n" + "\n".join(b.rstrip() + "\n" for b in blocks)
    return body[:m.end()].rstrip() + "\n" + add + "\n" + body[m.end():]


def scaffold(item_dir: Path, artifact: str, *, title: str = "", item_kind: str | None = None,
             item_id: str | None = None, standing: list[str] | None = None,
             research_kind: str | None = None) -> dict:
    """Deterministically scaffold one artifact skeleton. NEVER overwrites — an existing file
    returns `created: False`, since filling happens by editing. Unknown kinds fail loud.

    `standing` are the repo library's standing checks, pre-written into a plan. The KERNEL attaches
    them: what a repo always owes is not something anyone should have to remember, and a
    copied-by-hand entry is one rewording away from no longer being the same check."""
    if artifact not in _SPECS:
        raise KeyError(f"unknown artifact kind {artifact!r} — known: {sorted(_SPECS)}")
    item_kind = get_profile(item_kind).kind  # validates + resolves null → implementation
    from .kind_profiles import RESEARCH_KINDS
    if research_kind not in RESEARCH_KINDS:
        research_kind = None  # forgiving, like kind_profiles.research_kind — unjudged is a state
    adir = Path(item_dir) / "artifacts"
    path = adir / artifact_file(artifact)
    sections = list(required_sections(artifact, item_kind, research_kind))
    if path.exists():
        return {"path": str(path), "created": False, "sections": sections, "inherited": 0}
    # `research_kind:` is stamped so the file carries the shape it was authored under. `self_check`
    # reads it back from HERE, not from the item — an artifact is judged against the template that
    # produced it (same rule as the legacy plan shapes below), so re-classifying an item mid-flight
    # can never turn its already-written investigation red.
    fm = (f"---\nartifact: {artifact}\n"
          + (f"item: {item_id}\n" if item_id else "")
          + f"item_kind: {item_kind}\n"
          + (f"research_kind: {research_kind}\n" if research_kind else "")
          + f"reader: {_SPECS[artifact]['reader']}\n"
          + f"created_at: {date.today().isoformat()}\n---\n")
    tmpl = _template(artifact, item_kind, research_kind)
    heading = title or (item_id or "work-item")
    # The family is named ONCE in the heading: the template opens with it and a sweep-launched
    # title does too, so a naive render doubles it. Read off the template rather than a table, so
    # a family added later gets this free and a commissioned item keeps its prefix.
    m = re.match(r"#[ \t]+(.+?)[ \t]+—[ \t]+\{title\}", tmpl)
    if m and heading.lower().startswith(m.group(1).lower() + " — "):
        heading = heading[len(m.group(1)) + 3:].lstrip()
    body = tmpl.format(title=heading)
    if artifact == "plan" and standing:
        body = _inject_checks(body, standing)
    _atomic_write(path, fm + body)
    return {"path": str(path), "created": True, "sections": sections,
            "inherited": len(standing or []) if artifact == "plan" else 0}


def _split_sections(text: str) -> dict[str, str]:
    """`## Heading` → body map (frontmatter stripped by the caller or tolerated here)."""
    out: dict[str, str] = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            # CONCATENATE a repeated heading, never reset it. A report carrying two `## Verification`
            # sections read as whatever the LAST held, while the writer appends to the FIRST — so twelve
            # green verdicts read as a ledger of zero and the loop exited `system_fault`.
            out.setdefault(cur, "")
        elif cur is not None:
            out[cur] += line + "\n"
    return out


def _section_filled(body: str) -> bool:
    """Non-empty after dropping fill markers, html comments, and blank lines."""
    cleaned = FILL.sub("", re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL))
    return bool(cleaned.strip())


# The plan-authored contract vet executes: a fresh agent with zero context must run it
# unambiguously. Check ids are the JOIN KEY into the evidence ledger, so plan and ledger meet
# with no new store.
#
# HARD issues block the gate (mechanically decidable); SOFT flags surface for the owner, who
# is present — the one place fail-open is correct.

# Evidence PROVENANCE (design §4): who actually performed the check. `machine` = the kernel ran the
# check's literal `run:` block in the sandbox; `agent` = a vetter did it and attested. Old entries
# carry neither and read as `agent`, which is what they were.
BY_MACHINE = "machine"
BY_AGENT = "agent"

# Ledger entry KINDS. A verdict answers "did this check pass"; a diagnosis answers "where and why
# did it fail". They share the fence and the check id, and nothing else — see `diagnoses`.
KIND_VERDICT = "verdict"
KIND_DIAGNOSIS = "diagnosis"
KIND_LENS = "lens"
KIND_NOMINATION = "nomination"
# The kernel's re-run of a build validation claim (2026-08-07 amendment). A `## Verification`
# entry like the rest, and deliberately NOT a verdict: `evidence_entries` filters on
# KIND_VERDICT, so an audit can never be counted as one of the item's checks.
KIND_AUDIT = "audit"

# The two machine lanes in a cycle report, each a tagged fence inside its own section: `checks` in
# `## Verification` (vet's verdicts) and `runs` in `## Validation` (build's own self-check runs).
# Named, not positional, so a section can hold prose beside its records without either reader
# guessing which fence is theirs.
VALIDATION_FENCE = "runs"
VERIFICATION_FENCE = "checks"

# Read on every cycle, independently of the plan: its checks can only defend what the planner
# thought of. Depth governs EXECUTION, and a lens is a read — so `depth: none` means vet runs
# nothing, not that vet does nothing.
#
# `performance` is not standing: it is meaningful only against a budget the plan named, and a
# lens with no bar produces opinions.
STANDING_LENSES = ("intent", "safety", "robustness")
LENSES = STANDING_LENSES + ("performance",)
SEVERITIES = ("low", "medium", "high")

# Intent and safety have no severity scale: anything found is a gap in what the item is FOR or
# a way it can hurt someone. Only `high` robustness gates, and performance never does.
_LENS_GATES_AT = {"intent": SEVERITIES, "safety": SEVERITIES, "robustness": ("high",)}

VET_DEPTHS = ("none", "checks", "scenarios")
VET_MODES = ("command", "interaction", "inspection")
_VET_CHECK_ID = re.compile(r"^[a-z0-9-]+$")
_VET_HEADER_KEY = re.compile(r"^(depth|reason|env):\s*(.*)$")
_VET_FIELD = re.compile(
    r"^-\s*(proves|traces|covers|mode|scenario|run|rubric|expect|source):\s*(.*)$")
# A rubric criterion: an INDENTED bullet under `- rubric:`. Indentation is what separates it from
# the next field of the check, and the bullet is what separates one criterion from the wrap of the
# one before it — both matter, so both are required here.
_RUBRIC_ITEM = re.compile(r"^\s+[-*]\s+\S")
# The literal command the KERNEL runs. One line, because a check is one exit code — a scenario
# that cannot be said in one line is exactly one that stays agent-attested.
_VET_CHECK_HEAD = re.compile(r"^###\s+(.+?)\s*$")
# Vagueness heuristic for `expect` (soft): non-falsifiable filler words, or too short to pin
# an observable outcome. A banned-word list is too brittle to BLOCK on — hence soft.
_VET_VAGUE = re.compile(r"\b(works|correctly|properly|as expected)\b", re.IGNORECASE)
_VET_EXPECT_MIN = 40
# `proves` is written FOR the owner, and its failure mode is restating the mechanism instead of
# the meaning. "exit code 0" tells nobody whether a green demonstrates the intent. Soft:
# phrasing is a judgment, and a human is at the gate.
_PROVES_MACHINE = re.compile(
    r"\bexit(?:s|ed)?[- ]?(?:code|status)\b|\bexit\s+(?:0|1|zero|non-?zero)\b|\bstdout\b|"
    r"\bstderr\b|\breturns?\s+(?:0|1|zero)\b|"
    r"\b(?:tests?|suite|script|command|check)\s+(?:pass(?:es|ed)?|succeeds?|is green)\b",
    re.IGNORECASE)
_PROVES_MIN = 25


def _vet_value(raw: str) -> str:
    """A field value with unfilled `<fill:…>` markers treated as absent."""
    return FILL.sub("", raw or "").strip()


def parse_check_blocks(body: str) -> list[dict]:
    """`### <check-id>` blocks → check dicts, in order. The plan's `## Verification plan` and the
    repo's verification library (design §8) both hold checks in this one grammar, so a library entry
    can be inherited into a plan verbatim — no translation step to drift."""
    out: list[dict] = []
    cur: dict | None = None
    last = ""              # the field a wrapped continuation line belongs to
    for line in body.splitlines():
        h = _VET_CHECK_HEAD.match(line)
        if h:
            last = ""
            # `covers` is the Proof view's join key. NOT hard when absent: requiring it would retroactively
            # fail every in-flight plan. An untagged check lands in the item-wide row.
            # `source` says where the check came from: authored here, attached, or cited.
            # `proves` is the one HUMAN field. Every other serves executing or judging, so without it the
            # reports and the vetter each infer what a green means — separately, and drifting.
            cur = {"id": _vet_value(h.group(1)), "proves": "", "traces": "", "covers": "",
                   "mode": "", "scenario": "", "run": "", "expect": "", "rubric": [], "source": ""}
            out.append(cur)
            continue
        if cur is None:
            continue
        m = _VET_FIELD.match(line.strip())
        if m:
            # `rubric:` holds a LIST — its criteria are the indented bullets under it, each one
            # separately judged and separately recorded. Everything else is one value.
            cur[m.group(1)] = [] if m.group(1) == "rubric" else _vet_value(m.group(2))
            last = m.group(1)
        elif last and line.startswith((" ", "\t")) and line.strip():
            if last == "rubric":
                text = _vet_value(re.sub(r"^\s*[-*]\s*", "", line))
                if _RUBRIC_ITEM.match(line):
                    cur["rubric"].append(text)          # a new criterion
                elif cur["rubric"] and text:
                    cur["rubric"][-1] += " " + text     # …or the wrap of the last one
                continue
            # A wrapped field — markdown folds it, so we do too. Without this the value stops
            # mid-sentence, and `expect:` (the falsifiable condition, and the one field the
            # owner reads at the plan gate) is exactly the field long enough to wrap.
            cur[last] = (cur[last] + " " + line.strip()).strip()
    return out


def parse_vet_plan(plan_text: str) -> dict:
    """Parse plan.md's `## Verification plan` section (`## Vet plan` in pre-renovation plans) →
    {present, depth, reason, env, checks}. Pure text → data; validity is judged separately
    (`vet_plan_hard_issues` / `vet_plan_soft_flags`). Header fields are the `key: value` lines
    before the first `### <check-id>`; the checks are read by `parse_check_blocks`."""
    sections = _split_sections(plan_text)
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
            out[m.group(1)] = _vet_value(m.group(2))
    out["checks"] = parse_check_blocks("\n".join(lines[first:]))
    return out


# Whole-suite test runners. A check whose `run:` is one of these UNNARROWED is the project's own
# regression suite — build's validation, not the item's exam (design amendment 2026-08-07). The
# list is invocations, not tools: `pytest tests/test_csv.py::test_note_roundtrip` drives one
# behaviour and is a perfectly good check, so only the bare form is refused.
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
    """Is this `run:` the project's whole test suite, unnarrowed?

    `python3 -m unittest discover -s tests` is → it proves the project still works, which is what
    BUILD's validation already establishes every cycle and what vet now audits. Putting it in the
    vet plan makes the suite run twice and files a validation result as the item's own proof.
    `python3 -m unittest tests.test_ledger -k QuietFlagTest` is NOT — it drives one behaviour, and
    a check is entitled to do that however it likes."""
    cmd = " ".join((cmd or "").split())
    if not cmd or not _SUITE_RUN.match(cmd):
        return False
    # Only the runner's OWN arguments narrow it. A `&&`-joined second command is a different act,
    # and `-q`/`-v`/`--tb=short` are noise controls, not selectors.
    head = re.split(r"&&|\|\||;|\|", cmd)[0]
    tail = head[_SUITE_RUN.match(head).end():] if _SUITE_RUN.match(head) else ""
    return not _SUITE_NARROWERS.search(tail)


def vet_plan_hard_issues(vp: dict) -> list[str]:
    """The gate-blocking structural rules, every one mechanically decidable: legal depth · a
    reason even for none · depth≠none implies ≥1 check and depth=none implies 0 · every check fully
    fielded · `interaction` needs an env recipe · ids unique and slug-shaped.

    `proves` is HARD where `covers` is not, and the difference is who holds the gap. A missing
    `covers` costs a join; a missing `proves` means nobody downstream can say what a green MEANS, so
    the owner's report and the vetter each re-derive it separately."""
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
        if not _VET_CHECK_ID.match(cid or ""):
            issues.append(f"vet plan check {label!r}: id must be a lowercase slug ([a-z0-9-]+) — "
                          "it keys the evidence ledger")
        if cid in seen:
            issues.append(f"vet plan check {label!r}: duplicate id")
        seen.add(cid)
        for field_name in ("proves", "traces", "mode", "scenario"):
            if not c.get(field_name):
                # The remedy travels with the complaint. `run:` ACCOMPANIES `scenario:`, but a bare "missing
                # scenario" reads as "`run:` was the wrong field" — and three kernel-runnable checks lost
                # their run blocks to that reading.
                extra = (" — a check with `run:` still needs the prose scenario BESIDE it; add "
                         "the scenario, never drop the run block"
                         if field_name == "scenario" and c.get("run") else "")
                if field_name == "proves":
                    extra = (" — one plain sentence saying what is TRUE of the product when this "
                             "passes, in the owner's terms and not the command's")
                issues.append(f"vet plan check {label!r}: missing `{field_name}`{extra}")
        # The suite is BUILD's validation, audited by the kernel on vet's pass. As a vet-plan check it
        # runs the suite twice and files the result as the item's own proof. HARD, because a soft flag
        # on a check copied from the library is a row nobody reads twice.
        if c.get("run") and is_whole_suite_run(str(c.get("run"))):
            issues.append(
                f"vet plan check {label!r}: `run:` is the project's whole test suite — that is "
                "BUILD's validation, which it runs every cycle and the kernel re-runs to audit. "
                "Drop this check, or narrow the command to the ONE behaviour this item promises "
                "(a single test, a scenario) and say in `proves:` what that green means for the "
                "owner")
        # A check needs a bar that can FAIL, and there are two shapes of one: a binary `expect`, or
        # a rubric whose criteria are judged one by one. Either satisfies this; both together is a
        # normal check (an exit code AND a judgment about what it printed). Neither is a check that
        # cannot come back red. Every in-flight plan has an `expect`, so nothing is retroactively
        # failed by widening the rule.
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


# A vet check that leans on a RETIRED anchor doc (BV-A2 small-fix): retired docs are read-only, so a
# check whose pass depends on editing/greping one can never go green through the normal loop — it is
# a plan miss (drop the check, or migrate the doc as an authorized contract change). Match the
# filename precisely (`spec.md`) — the bare word "spec" is too common to flag.
_RETIRED_DOC_REF = re.compile(r"\bspec\.md\b", re.I)


def vet_plan_soft_flags(vp: dict) -> list[str]:
    """The judgment flags (§3.4 SOFT) — surfaced in the pre-main gate brief, never blocking:
    an `expect` that pattern-matches vagueness or is too short to pin an observable outcome, a
    `proves` written in the command's terms rather than the product's, or a check that targets a
    retired (read-only) anchor doc."""
    flags: list[str] = []
    for c in vp.get("checks", []):
        exp, cid = c.get("expect", ""), c.get("id") or "(unnamed)"
        # A check INHERITED from the library is not this planner's prose. Its wording was settled
        # when the repo adopted it, the planner cannot rewrite it without breaking the inheritance,
        # and the owner curates the library in its own surface. Flagging it asks the wrong author a
        # question they cannot answer, on every item that cites the entry — which is how a soft
        # flag stops being read at all (owner, 2026-08-07).
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


def clip(text: str, limit: int) -> str:
    """Trim to `limit` at a WORD boundary, with an ellipsis when anything was cut.

    A hard slice reads as a rendering bug rather than as a trim — the owner sees
    "…to the `list` and `sum` subparsers i" and wonders what broke. Cut at the last space
    instead, and say so with the ellipsis. Falls back to the hard slice for a single long token
    (a path, a URL) that has no space to cut at."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0]
    return (head if len(head) >= limit // 2 else text[:limit]).rstrip(" ,;:·-") + "…"


_VET_DEPTH_RANK = {"none": 0, "checks": 1, "scenarios": 2}


def parse_inner_checks(plan_text: str) -> list[str]:
    """plan.md's `## Inner checks` bullets → the command list build must run green before it may
    exit (§2.1: every line is a command whose exit code decides it). Backticks stripped; unfilled
    slots skipped."""
    body = _split_sections(plan_text).get("Inner checks", "")
    cmds: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if not m:
            continue
        cmd = _vet_value(m.group(1)).strip("`").strip()
        if cmd:
            cmds.append(cmd)
    return cmds


def _is_legacy_plan(sections: dict) -> bool:
    """A pre-vet-loop plan: has the old `## Validation criteria`, lacks `## Vet plan`."""
    return "Validation criteria" in sections and "Vet plan" not in sections


# ------------------------------------------------------------------- gate feeds (renovation §2)
# The three plan sections whose STRUCTURED content feeds the plan gate's answer forms (design doc
# Q→FORM→FEED): `## Touches` (yaml → change map) · `## Behavior preview` (fenced before/after →
# predicted panes) · `## Risks & assumptions` (bullets → confirm/adjust cards). Parsers are pure
# text→data; absent sections return empty — the surface falls back to prose rows (design §2b).

TOUCH_ACTIONS = ("new", "modify", "read")
_FENCE = re.compile(r"^```[\w-]*\s*$")


def _fenced_blocks(body: str, *, lang: str = "") -> list[str]:
    """The contents of every ``` fenced block in a section body, in order. `lang` keeps only the
    blocks opened with that tag — needed once a section carries more than one machine lane."""
    blocks, cur, keep = [], None, True
    for line in body.splitlines():
        s = line.strip()
        if _FENCE.match(s):
            if cur is None:
                cur, keep = [], (not lang or s == f"```{lang}")
            else:
                if keep:
                    blocks.append("\n".join(cur))
                cur = None
        elif cur is not None:
            cur.append(line)
    return blocks


def parse_touches(plan_text: str) -> list[dict]:
    """plan.md's `## Touches` fenced yaml → [{component, path, action}]. Tolerant: absent
    section / unparseable yaml / unfilled slots → []; validity is judged in touches_hard_issues."""
    body = _split_sections(plan_text).get("Touches", "")
    blocks = _fenced_blocks(body)
    raw = blocks[0] if blocks else body
    if FILL.search(raw):
        return []
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return []
    if not isinstance(data, list):
        return []
    return [{"component": str(e.get("component") or "").strip(),
             "path": str(e.get("path") or "").strip(),
             "action": str(e.get("action") or "").strip()}
            for e in data if isinstance(e, dict)]


def touches_hard_issues(plan_text: str) -> list[str]:
    """Gate-blocking structure for a filled `## Touches` section: the yaml must parse into ≥1
    complete row with a legal action. (Only called for plans that carry the section.)"""
    rows = parse_touches(plan_text)
    if not rows:
        return ["touches: the fenced yaml must parse into at least one "
                "{component, path, action} row"]
    issues = []
    for r in rows:
        label = r["component"] or r["path"] or "(unnamed)"
        for f in ("component", "path", "action"):
            if not r[f]:
                issues.append(f"touches row {label!r}: missing `{f}`")
        if r["action"] and r["action"] not in TOUCH_ACTIONS:
            issues.append(f"touches row {label!r}: action must be one of "
                          f"{'/'.join(TOUCH_ACTIONS)} (got {r['action']!r})")
    return issues


_TASK_LINE = re.compile(r"^\s*-\s*\[(?P<tick>[ xX])\]\s*(?P<id>t\d+)\b[\s—:-]*(?P<text>.*)$")


def parse_tasks(plan_text: str) -> list[dict]:
    """plan.md's `## Tasks` → [{id, done, text, detail}], in plan order. The id is what build's
    commits carry in their trailer, so this is the join that titles a walkthrough group. Tolerant: an
    unparseable line is skipped, never raised.

    A task is a BLOCK of two parts, and the split is the point. `text` is the HEAD line — the task's
    NAME, what the board shows. `detail` is the indented specification under it.

    Reading the head alone cut a wrapped task off mid-clause; merging both produced a 340-character
    paragraph. Keeping them separate is what lets the name be a name."""
    body = _split_sections(plan_text).get("Tasks", "")
    out: list[dict] = []
    cur: dict | None = None
    for line in body.splitlines():
        if (m := _TASK_LINE.match(line)):
            cur = {"id": m.group("id"), "done": m.group("tick").lower() == "x",
                   "text": m.group("text").strip(), "detail": ""}
            out.append(cur)
        elif not line.strip():                      # a blank line ends the block
            cur = None
        elif line[:1].isspace() and cur is not None:
            cur["detail"] = (cur["detail"] + " " + line.strip()).strip()
        else:                                       # any unindented line starts something else
            cur = None
    return out


_DECISION_HEAD = re.compile(r"^### (?P<ts>\S+) — (?P<q>.+)$", re.M)


def parse_decisions(plan_text: str) -> list[dict]:
    """plan.md's `## Decisions & clarifications` ledger (the grill's record): one entry per
    answered question — `### <ts> — <question>` + `- answer:` + `- changed:`. Append-only with
    owner provenance; the plan gate surfaces the newest few, and the deputy treats an entry as
    settled ("owner said X"), never re-litigated. The template's authoring comment is stripped
    first so its example entry can't parse as a real one."""
    body = _split_sections(plan_text).get("Decisions & clarifications", "")
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


_LABEL_LINE = re.compile(r"^\*\*[^*]+:\*\*")


def _space_labels(text: str) -> str:
    """Put a blank line before every `**Label:**` block that lacks one.

    Reports are agent-copied from a template, so their spacing is prose, not structure — and markdown
    is unforgiving: two label lines in a row fold into one paragraph. A template is a suggestion to a
    model, and this is the read path both the owner and the deputy go through. Fences are left alone."""
    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and _LABEL_LINE.match(line) and out and out[-1].strip():
            out.append("")
        out.append(line)
    return "\n".join(out)


# A value that means the author had nothing to say. Every template tells them to DELETE the block
# instead; this is what happens when they don't.
_DEAD_VALUES = {"", "none", "none.", "n/a", "na", "-", "—", "nothing", "(none)",
                "(first run — n/a)", "(first run - n/a)", "first run — n/a", "first run - n/a"}
_HEADING = re.compile(r"^#{1,6}\s")


def _dead_label(lines: list[str], i: int) -> bool:
    """Is the `**Label:**` at `lines[i]` a block with nothing under it?

    The next NON-BLANK line decides. Reading `lines[i + 1]` called every list-under-a-label dead, and
    stripped the labels that said whose the paragraphs were."""
    if lines[i].split(":**", 1)[1].strip().lower() not in _DEAD_VALUES:
        return False
    for nxt in lines[i + 1:]:
        if not nxt.strip():
            continue
        return bool(_LABEL_LINE.match(nxt) or _HEADING.match(nxt))
    return True


def _live_body(lines: list[str]) -> bool:
    """Does a section body hold anything a reader would want? Blank lines, authoring comments and
    labels with nothing under them all read as nothing."""
    text = re.sub(r"<!--.*?-->", "", "\n".join(lines), flags=re.DOTALL)
    body = text.split("\n")
    return any(ln.strip() and not (_LABEL_LINE.match(ln) and _dead_label(body, k))
               for k, ln in enumerate(body))


def _drop_dead_blocks(text: str) -> str:
    """Delete `**Label:** none` blocks and an empty `## Changed since` on the READ path.

    Every template says to delete a block it has nothing to put under, and two survived into the very
    first report the owner read — lines that exist only to say nothing, in a document whose budget is
    half a screen.

    Deliberately literal: a block goes only when its value is one of a few dead tokens. Anything with
    real content is left exactly as written."""
    lines, out = text.split("\n"), []
    i, fenced = 0, False
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and _LABEL_LINE.match(line):
            # A label whose value is on the FOLLOWING lines (bullets etc.) is never dead — only a
            # same-line value can be, and only if the block ends right there.
            if _dead_label(lines, i):
                i += 2                      # drop the label line and its trailing blank
                while out and not out[-1].strip():
                    out.pop()               # …and the blank that preceded it
                out.append("")
                continue
        if not fenced and _HEADING.match(line) and "changed since" in line.lower():
            body = [ln for ln in lines[i + 1:] if not _HEADING.match(ln)]
            joined = "\n".join(body).strip().strip("()").strip().lower()
            # "first run" is its own family of phrasings ("(first run)", "first run — n/a", …) and
            # they all mean the same nothing, so this one gets a prefix match rather than another
            # entry in the token list every time an author words it differently.
            if joined in _DEAD_VALUES or joined.startswith("first run"):
                break                       # nothing after it but the dead section
        if not fenced and _HEADING.match(line):
            # A heading with nothing under it. `## From you` is the standing case — it exists in
            # every triage brief because the owner has to be able to find it, and until they write
            # in it there is nothing to read. A bare heading reads as a section that failed to
            # render, so it goes; the drilldown's editor is what tells the owner it is there.
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
    """The item's records written since `since`, newest first. Empty when `since` is unparseable
    or nothing moved.

    Reads mtimes, not a change log: every writer moves an mtime, and none can be persuaded to keep a
    ledger honest. Scope is the two folders a phase FORMS A JUDGMENT FROM — not `checkpoints/`, which
    every run writes, and not the frozen `preliminary/`."""
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
    """A phase's user-facing report, for the drilldown's Reports tab and the deputy's prompt.

    `contract` is the relative path to the phase's full agent-facing artifact, read on demand. The
    report is the compact projection; the contract is the whole thing, never pasted into it. Review
    and close have no separate contract — their report IS the record."""
    path = Path(item_dir) / "reports" / f"report-{phase}.md"
    if not path.is_file():
        return None
    contract = {"triage": "artifacts/brief.md", "plan": "artifacts/plan.md",
                "investigate": "artifacts/investigation.md",
                # Review HAS an agent-facing record (`artifacts/review.md`) and was the one phase
                # whose report offered no way to reach it — the owner read the judgment at the
                # merge gate with the record behind it unreachable (owner, 2026-08-08). Close is
                # still None: its report IS the record.
                "review": "artifacts/review.md"}.get(phase)
    if phase in ("build", "vet"):
        # The cycle the report covers is the newest one — build and vet both project the same file.
        reports = cycle_reports(item_dir)
        contract = f"artifacts/{Path(reports[-1]['path']).name}" if reports else None
    # A link to a file that isn't there is worse than no link: the surface renders it, the owner
    # clicks it, and the doc view 404s. Older items predate several of these artifacts.
    if contract and not (Path(item_dir) / contract).is_file():
        contract = None
    try:
        st = path.stat()
    except OSError:
        return None
    return {"phase": phase, "name": f"report-{phase}",
            "text": _drop_dead_blocks(_space_labels(path.read_text())),
            "path": str(path), "mtime": st.st_mtime, "contract": contract}


# A `**Label:** value` with the value ON THE SAME LINE. Every one-line fact in a user-facing report
# is written this way (`**Summary:**`, `**Category:**`, `**Problem:**`), which is what makes them
# readable by code without parsing the prose around them.
_LABEL_VALUE = re.compile(r"^\*\*(?P<label>[^*\n]+?):\*\*[^\S\n]*(?P<value>.*?)\s*$", re.M)


def label_values(text: str) -> dict[str, str]:
    """Every same-line `**Label:** value` in a document → {label lowercased: value}.

    Same-line only, deliberately: a label whose content is a list below it is prose for the owner,
    and a reader that flattened it would put a paragraph in a card row. First occurrence wins."""
    out: dict[str, str] = {}
    for m in _LABEL_VALUE.finditer(re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)):
        value = m.group("value").strip()
        if value and not FILL.search(value):
            out.setdefault(m.group("label").strip().lower(), value)
    return out


def report_summary(item_dir: Path, phase: str) -> str:
    """A phase report's `**Summary:**` line — one sentence, what that phase concluded.

    The Quick View phase card renders this alone, which is the whole reason every user-facing report
    opens with it. Empty when the phase has written no report yet, or wrote one without the line."""
    path = Path(item_dir) / "reports" / f"report-{phase}.md"
    return label_values(path.read_text()).get("summary", "") if path.is_file() else ""


def triage_facts(item_dir: Path) -> dict:
    """What triage established about the item, for the drilldown's `About this work-item` card:
    {category, background, problem}.

    Read from the OWNER's brief rather than from `brief.md`, because these are the owner's own
    framing of their own request — the agent-facing record states the same things in the vocabulary
    of the work. `**Goal:**` is the template's alternative to `**Problem:**` for an item where
    nothing is broken; both land in `problem`, since the card asks one question: what is this for."""
    path = Path(item_dir) / "reports" / "report-triage.md"
    if not path.is_file():
        return {"category": "", "background": "", "problem": ""}
    v = label_values(path.read_text())
    return {"category": v.get("category", ""), "background": v.get("background", ""),
            "problem": v.get("problem") or v.get("goal", "")}


def report_issues(item_dir: Path, name: str) -> list[str]:
    """Itemized issues on a user-facing report in `reports/` — present, and no template slot left
    unfilled. A report is COPIED from its template by the authoring agent (not scaffolded by code),
    so this is the only mechanical hold on it. HTML comments are stripped first: a template
    documents its own slots in a comment, and documentation is not an unfilled slot."""
    path = Path(item_dir) / "reports" / f"{name}.md"
    if not path.is_file():
        return [f"reports/{name}.md does not exist — write it from its template"]
    text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL)
    left = sorted(set(FILL.findall(text)))
    if left:
        return [f"reports/{name}.md has unfilled slot(s): " + ", ".join(left[:6])]
    return []


# `[^\S\n]*`, not `\s*`: in MULTILINE `\s` matches newlines, so a decision line left EMPTY (the
# template's comment stripped away) would skip the blank line and capture whatever heading came
# next — reporting a decision the owner never made, at the gate that exists to catch exactly that.
# Invisible until the line stopped being the last one in its file.
_OWNER_DECISION = re.compile(r"^\*\*Owner's decision:\*\*[^\S\n]*(.+?)\s*$", re.M)


def owner_decision(item_dir: Path) -> str:
    """The itemization outcome `itemize` recorded into review.md: what it filed, what it skipped
    as a duplicate, or that there was nothing. Empty when absent or still an unfilled slot — either
    way itemization never ran, and the proposals went nowhere."""
    path = Path(item_dir) / "artifacts" / artifact_file("review")
    if not path.is_file():
        return ""
    text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL)
    m = _OWNER_DECISION.search(text)
    if not m:
        return ""
    value = m.group(1).strip()
    return "" if FILL.search(value) else value


def proposed_work(item_dir: Path) -> str:
    """A research review record's `## Proposed work` body — the work its findings imply, which is
    half of what a research item owes. Empty string when the section is missing, unfilled, or says
    nothing.

    This is what the REVIEW gate can actually ask. `itemize` — which turns these into inbox items —
    fires on review APPROVE, so at the moment the gate is read it has not run yet and its record is
    always empty. Asking there whether the proposals were filed is a question no first approval can
    answer; asking whether they were STATED is answerable, and it is answerable while the owner can
    still send the item back."""
    path = Path(item_dir) / "artifacts" / artifact_file("review")
    if not path.is_file():
        return ""
    text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL)
    body = _split_sections(text).get("Proposed work", "")
    if FILL.search(body) or not _live_body(body.splitlines()):
        return ""
    return " ".join(body.split())


# ----------------------------------------------------- research proposals (the typed `## Proposed work`)
# A research item's findings imply work, but research may not DECIDE — so each proposal carries how
# its open call was handled. Three shapes, and only the third can withhold a proposal:
#
#   no ruling fields          — nothing to decide; files as-is.
#   `Default applied`         — a preference with a safe, cheaply reversed default. Files WITH the
#                               default stated; the owner adjusts it at the gate if it is wrong.
#   `Question` (+ reason)     — a preference whose default is destructive or expensive to reverse.
#                               Files ONLY once `Answer` carries the owner's ruling.
#
# The withheld case is the whole point: a brief that says "do X, pending a ruling nobody gave" reads
# as actionable, and a build agent asked to finish it will pick an option — which is the one act
# research is not allowed to perform. Nothing stores an unanswered question; the next sweep re-reads
# the same code and raises it again, which is the reminder a parked question could never be.
_PROPOSAL_FIELDS = {
    "Title": "title", "Kind": "kind", "Why now": "why_now", "Delivers": "delivers",
    "Default applied": "default_applied", "Question": "question",
    "Reserved because": "reserved_because", "Suggested": "suggested", "Answer": "answer",
    # The generalization the answer establishes, if it establishes one. Written WITH `Answer` and
    # never before it: a rule follows from the ruling, so one written beside the question would only
    # be true if the owner took the suggestion. Empty is the normal case — see `promotable`.
    "Rule": "rule",
    # The free-prose predecessor of `Question`. Kept as a field so an older review's line lands in
    # its own key instead of running on into `Why now` — it gates nothing (it never could: no
    # reader ever consumed it), and reading it as a question would retroactively withhold work on
    # items written before the field existed.
    "Depends-on": "legacy_depends_on",
}
_PROPOSAL_FIELD = re.compile(r"^\s*\*\*(" + "|".join(_PROPOSAL_FIELDS) + r"):\*\*\s*(.*)$")
# Closed set, and the reason the set is closed: an agent that must name which limb a question passes
# writes fewer questions than one that may simply assert the owner should decide.
RESERVED_REASONS = ("destructive", "expensive_to_reverse")
# `Becomes work` is a yes/no, and absent means yes: the ordinary proposal is work, and only a ruling
# that emptied it has to say so.
BECOMES_WORK = ("yes", "no")


def research_proposals(item_dir: Path) -> list[dict]:
    """`## Proposed work` → one dict per proposal, keyed by `_PROPOSAL_FIELDS`. A block opens at
    `**Title:**`; a line that matches no field header continues the field above it (values wrap).
    Unfilled `<fill:…>` slots read as absent. Returns [] when the section is missing or unfilled."""
    path = Path(item_dir) / "artifacts" / artifact_file("review")
    if not path.is_file():
        return []
    text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL)
    body = _split_sections(text).get("Proposed work", "")
    out: list[dict] = []
    cur: dict | None = None
    field: str | None = None
    for line in body.splitlines():
        m = _PROPOSAL_FIELD.match(line)
        if m:
            key = _PROPOSAL_FIELDS[m.group(1)]
            if key == "title":
                cur = {v: "" for v in _PROPOSAL_FIELDS.values()}
                out.append(cur)
            if cur is None:      # a stray field before any title — nothing to attach it to
                continue
            field = key
            cur[key] = _vet_value(m.group(2))
        elif cur is not None and field and line.strip():
            cur[field] = (cur[field] + " " + _vet_value(line)).strip()
    return [p for p in out if p["title"]]


def proposal_is_withheld(prop: dict) -> bool:
    """A proposal the owner has not ruled on: it asks a question and carries no answer."""
    return bool(prop.get("question")) and not str(prop.get("answer") or "").strip()


def filed_and_withheld(props: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split proposals into (files now, waits for the owner). Per-proposal, never per-review — one
    open question must not hold settled siblings, or a busy week turns into a stuck board."""
    filed = [p for p in props if not proposal_is_withheld(p)]
    return filed, [p for p in props if proposal_is_withheld(p)]


def proposal_promotable(prop: dict) -> bool:
    """Does this ruling establish something a LATER reader can use? Only then is it project memory.

    The test is `Rule`, not `Reserved because`. Those answer different questions: the reserved reason
    says the call was the owner's to make (the action is destructive or expensive to reverse), which
    is a property of the ACTION and says nothing about whether the answer generalizes. A one-off
    destructive act produces a one-off answer — "delete this file" is an instruction that dies with
    the file, not a rule anybody can apply. So the common case is EMPTY: an answered question is
    remembered only when the answer was written down as a rule that binds work outside this item."""
    return bool(str(prop.get("rule") or "").strip()) and bool(str(prop.get("answer") or "").strip())


def proposal_becomes_work(prop: dict) -> bool:
    """Does this proposal still describe WORK once the owner has ruled on it?

    An inbox item is a thing that becomes a work item when pushed. That is the whole definition, and
    a proposal that fails it was never an item — it is an answer. Half of every keep-or-delete
    question lands on "keep", which empties the deliverable: nothing to plan, nothing to build,
    nothing to verify. Filing it anyway puts a ticket on the owner's board whose own body says there
    is nothing to do, and hands them a Push button that would cut a branch for a no-op.

    Absent reads as YES. The ordinary proposal is work; only a ruling that emptied one declares it."""
    return str(prop.get("becomes_work") or "yes").strip().lower() != "no"


def filed_and_settled(props: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the FILE-ABLE proposals into (become work, settled with nothing to do). The second list
    is reported, never filed: the owner's ruling is on the record, and no ticket carries it."""
    filed, _ = filed_and_withheld(props)
    return ([p for p in filed if proposal_becomes_work(p)],
            [p for p in filed if not proposal_becomes_work(p)])


def research_proposal_issues(props: list[dict]) -> list[str]:
    """Structural faults in the proposal blocks — read at the review gate, where the owner can still
    send the item back. A malformed ruling field is worse than none: it decides whether a proposal
    files, so a typo silently changes what reaches the queue."""
    issues: list[str] = []
    for p in props:
        label = clip(p.get("title") or "(untitled)", 60)
        if p.get("question"):
            reason = p.get("reserved_because", "")
            if not reason:
                issues.append(f"proposal {label!r}: asks the owner a question with no "
                              "`Reserved because` — name the limb it passes "
                              f"({' or '.join(RESERVED_REASONS)}) or decide it yourself")
            elif reason not in RESERVED_REASONS:
                # The commonest way to fail this is to answer the field's own grammar: "Reserved
                # BECAUSE" invites a reason, and the value that follows the right word is prose.
                # So the message names where the prose belongs rather than only what is wrong.
                first = reason.split()[0].rstrip(":,;.").strip("`*")
                hint = (f" — the word {first!r} is right; delete everything after it and put the "
                        "reasoning in `Suggested`" if first in RESERVED_REASONS else "")
                issues.append(f"proposal {label!r}: `Reserved because` must be one of "
                              f"{'/'.join(RESERVED_REASONS)} and nothing else "
                              f"(got {reason!r}){hint}")
            if not p.get("suggested"):
                issues.append(f"proposal {label!r}: a question with no `Suggested` makes the owner "
                              "do the research again — state the answer you would give")
        if p.get("answer") and not p.get("question"):
            issues.append(f"proposal {label!r}: carries an `Answer` with no `Question` — a ruling "
                          "with no question recorded cannot be read back")
        becomes = str(p.get("becomes_work") or "").strip().lower()
        if becomes and becomes not in BECOMES_WORK:
            issues.append(f"proposal {label!r}: `Becomes work` must be "
                          f"{' or '.join(BECOMES_WORK)} (got {p['becomes_work']!r})")
        elif becomes == "no" and not str(p.get("answer") or "").strip():
            issues.append(f"proposal {label!r}: says `Becomes work: no` with no `Answer` — a "
                          "proposal is only emptied by a ruling, so with no ruling it is still work")
        if str(p.get("rule") or "").strip() and not str(p.get("answer") or "").strip():
            issues.append(f"proposal {label!r}: carries a `Rule` with no `Answer` — a rule is what "
                          "the owner's ruling established, so it cannot be written before there is "
                          "a ruling to establish it")
        if p.get("question") and p.get("default_applied"):
            issues.append(f"proposal {label!r}: carries both `Default applied` and `Question` — a "
                          "call is either yours to make or the owner's, never both")
    return issues


# --------------------------------------------------------------- `## From you` (the owner's input)
# The one section of any report the OWNER writes, and the only place in the item where their own
# words arrive as instruction rather than as chat. It lives in the triage brief because that is the
# document the plan phase cold-starts from — an owner note filed anywhere else reaches nobody.
#
# CODE owns the section from here on. Triage copies its template through with the two labels empty
# and never fills them; the drilldown's editor is the only writer. That is why the template carries
# no `<fill:…>` slot under this heading: a slot would invite the triage agent to invent the owner's
# references, and an invented authority is worse than an empty one.
#
# SLOTS, not prose (owner, 2026-08-08). Both blocks used to be one free textarea each, which made
# the section a wall the owner had to punctuate themselves and gave the reader nothing to delete.
# Each entry is now ONE bullet: a reference is `- **<source>** — <description>`, a note is `- <text>`.
# The surface adds one slot at a time and removes one at a time, and the plan phase's contract
# ("one note, one check") finally matches what is on disk.
FROM_YOU = "From you"
_OWNER_BLOCKS = (("references", "Useful imported references"), ("notes", "Verification notes"))
# `- **source** — description`. The bold source and the em-dash are optional so an older section's
# free prose still reads as slots rather than vanishing: a bullet with no source is all description.
_OWNER_BULLET = re.compile(r"^\s*[-*]\s+(?:\*\*(?P<source>[^*]+?)\*\*\s*[—-]\s*)?(?P<rest>.+?)\s*$")


def _owner_blocks(body: str) -> dict[str, str]:
    """`## From you`'s body → {references, notes} as RAW text. Label-driven, so an owner's own blank
    lines and bullets survive round-tripping and only the two headings are structural."""
    keyed = {label.lower(): key for key, label in _OWNER_BLOCKS}
    out: dict[str, list[str]] = {key: [] for key, _ in _OWNER_BLOCKS}
    cur: str | None = None
    for line in re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).splitlines():
        if _LABEL_LINE.match(line):
            name, _, rest = line.partition(":**")
            key = keyed.get(name.strip("*").strip().lower())
            # ONLY our two labels are structural. An owner who bolds a label of their own inside a
            # block is writing content, and treating it as a delimiter would silently swallow
            # everything they typed after it — the one failure a round-trip editor must not have.
            if key:
                cur = key
                if rest.strip():
                    out[cur].append(rest.strip())
                continue
        if cur:
            out[cur].append(line)
    return {k: FILL.sub("", "\n".join(v)).strip() for k, v in out.items()}


def _owner_slots(raw: str, *, sourced: bool) -> list[dict]:
    """One block's raw text → its slots. A `sourced` block (references) splits each bullet into
    {source, description}; a plain one (notes) keeps {description} alone.

    A non-bullet line is still a slot. Older sections hold whatever the owner typed into a
    textarea, and reading their words as one description each keeps them addressable instead of
    silently dropping them on the first save."""
    out: list[dict] = []
    for line in raw.splitlines():
        if not (text := line.strip()):
            continue
        m = _OWNER_BULLET.match(text)
        source, desc = (m.group("source") or "", m.group("rest")) if m else ("", text)
        out.append({"source": source.strip(), "description": desc.strip()} if sourced
                   else {"description": desc.strip()})
    return out


def owner_input(item_dir: Path) -> dict:
    """What the owner has written into `reports/report-triage.md` § From you, as slots.

    `exists` is whether the triage brief is on disk at all — the editor has nothing to write into
    before triage runs, and the surface says so rather than creating a report no phase authored."""
    path = Path(item_dir) / "reports" / "report-triage.md"
    if not path.is_file():
        return {"exists": False, "references": [], "notes": []}
    blocks = _owner_blocks(_split_sections(path.read_text()).get(FROM_YOU, ""))
    return {"exists": True,
            "references": _owner_slots(blocks["references"], sourced=True),
            "notes": _owner_slots(blocks["notes"], sourced=False)}


def _one_line(s: str) -> str:
    """A slot is one bullet, so it is one line — newlines pasted into a field would otherwise split
    it into slots nobody added."""
    return " ".join(str(s or "").split())


# The owner's standing input, carried to EVERY phase (owner, 2026-08-09).
#
# Each intake phase runs in its OWN session (see kind_profiles' role block): what the owner typed
# during triage is gone by the time plan runs, and gone again by review. The two places their words
# are DURABLE are `## From you` in the triage report and `## Decisions & clarifications` in the
# plan. Both were reaching later phases only because a template told the agent to go and read them
# — an instruction, which a phase can skip, misread, or simply not have (the build and vet skills
# never mentioned either). This makes it MECHANICAL: the kernel puts the owner's own words in front
# of every turn, so no phase can miss them by not looking.
#
# A POINTER WITH THE TEXT IN IT, not a substitute for the artifacts: capped hard, because this
# rides every turn's system prompt and an owner who writes an essay must not push the rest of the
# contract out. Over the cap it says so and names the file.
_CARRY_CAP = 1200
_DECISIONS = "Decisions & clarifications"


def carry_owner_input(item_dir: Path, *, cap: int = _CARRY_CAP) -> str | None:
    """The owner's durable words for this item as one preamble block, or None when they have said
    nothing. Read-only and failure-tolerant: a missing or malformed artifact yields None rather
    than breaking a turn that was going to run anyway."""
    lines: list[str] = []
    try:
        own = owner_input(item_dir)
        for r in own.get("references") or []:
            src, desc = _one_line(r.get("source")), _one_line(r.get("description"))
            if desc:
                lines.append(f"- reference — {f'**{src}**: ' if src else ''}{desc}")
        for n in own.get("notes") or []:
            if desc := _one_line(n.get("description")):
                lines.append(f"- verification note — {desc}")
    except Exception:
        pass
    try:
        plan = Path(item_dir) / "artifacts" / artifact_file("plan")
        if plan.is_file():
            body = _split_sections(plan.read_text()).get(_DECISIONS, "")
            # Comment-only bodies are the SCAFFOLD, not an answer — the template ships this section
            # with its instructions inside `<!-- -->` and the agent appends beneath them.
            body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
            for ln in (l.strip() for l in body.splitlines()):
                if ln and not ln.startswith("#"):
                    lines.append(f"- decision — {ln.lstrip('-* ')}" if not ln.startswith("-")
                                 else f"- decision — {ln.lstrip('-* ')}")
    except Exception:
        pass
    if not lines:
        return None
    block = "\n".join(lines)
    over = len(block) > cap
    if over:
        block = block[:cap].rsplit("\n", 1)[0]
    return (
        "\n**From the owner (carried forward — their words, not a summary):**\n" + block
        + ("\n- …truncated. The rest is in `reports/report-triage.md` § From you and "
           "`artifacts/plan.md` § Decisions & clarifications." if over else "")
        + "\n→ These are STANDING instructions for this item. They outrank your own reading of the "
          "task; if one conflicts with what you were about to do, follow it or say why you cannot."
    )


def _render_from_you(references: list[dict], notes: list[dict]) -> str:
    """The section, rebuilt whole. Both labels stay even when empty: they are how the owner knows
    the section is theirs to write in, and the read path drops an empty block on its own."""
    out = [f"## {FROM_YOU}", ""]
    for key, label in _OWNER_BLOCKS:
        lines = []
        for slot in (references if key == "references" else notes):
            desc = _one_line(slot.get("description"))
            if not desc:
                continue    # an empty slot is not a slot — never render a bare bullet
            src = _one_line(slot.get("source"))
            lines.append(f"- **{src}** — {desc}" if src else f"- {desc}")
        out += [f"**{label}:**", ""] + (lines + [""] if lines else [""])
    return "\n".join(out).rstrip() + "\n"


def write_owner_input(item_dir: Path, *, references: list[dict],
                      notes: list[dict]) -> dict:
    """Replace `## From you` in the triage brief with the owner's slots, leaving every other byte of
    the report alone. Appends the section when an older brief has none.

    The caller sends the WHOLE list, not a delta: adding and deleting are both a rewrite of two
    short lists, the owner is the section's only writer, and a whole-section replacement is the one
    shape that keeps what is on disk equal to what the surface showed."""
    path = Path(item_dir) / "reports" / "report-triage.md"
    if not path.is_file():
        raise FileNotFoundError("reports/report-triage.md does not exist — triage writes it first")
    text = path.read_text()
    section = _render_from_you(references, notes)
    pattern = re.compile(rf"^##[^\S\n]+{re.escape(FROM_YOU)}[^\S\n]*$.*?(?=^##[^\S\n]|\Z)",
                         re.M | re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _m: section + "\n", text, count=1)
    else:
        text = text.rstrip() + "\n\n" + section
    _atomic_write(path, text.rstrip() + "\n")
    return owner_input(item_dir)


def self_check(item_dir: Path, artifact: str, *, item_kind: str | None = None,
               path: Path | None = None) -> list[str]:
    """The gate-time validator: itemized issues (empty list = pass). Checks: file exists ·
    every required section present AND filled · no `<fill:…>` slot left anywhere.
    Read-only — never mutates state. `path` overrides
    the default artifacts/ location (the handoff-brief lives in inbox/<id>/ then preliminary/)."""
    if artifact not in _SPECS:
        raise KeyError(f"unknown artifact kind {artifact!r} — known: {sorted(_SPECS)}")
    path = Path(path) if path else Path(item_dir) / "artifacts" / artifact_file(artifact)
    if not path.exists():
        return [f"{artifact_file(artifact)} does not exist — scaffold it first"]
    text = path.read_text()
    issues: list[str] = []
    fills = FILL.findall(text)
    # handoff-brief sections are ALL optional (D5: capture friction kills itemizing) — a leftover
    # slot just marks an unfilled optional section; every other kind must clear its slots.
    if fills and artifact != "handoff-brief":
        issues.append(f"{len(fills)} unfilled <fill:…> slot(s) remain — fill or remove them")
    sections = _split_sections(text)
    # The shape the file was AUTHORED under, read from its own frontmatter — never from the item's
    # current field. A family re-classified after the artifact was written must not retro-fail the
    # text already on disk (same principle as the legacy plan shapes below); and it keeps this
    # signature free of a parameter every one of the five call sites would otherwise have to thread.
    head = _FM_BLOCK.match(text)
    fam = _FM_RESEARCH_KIND.search(head.group(1)) if head else None
    spec = section_spec(artifact, item_kind, fam.group(1) if fam else None)
    is_impl_plan = (artifact == "plan"
                    and get_profile(item_kind).kind == "implementation")
    is_new_plan = artifact == "plan" and any(
        h in sections for h in ("Intent", "Verification plan", "Decisions & clarifications"))
    # Pre-renovation plans stay valid READ-ONLY — each is judged against the shape it was
    # authored under, so mid-flight items don't go red retroactively. Newest first.
    if artifact == "plan" and not is_new_plan:
        if is_impl_plan and _is_legacy_plan(sections):
            spec = [(h, True) for h in _PLAN_REQUIRED_LEGACY]
            is_impl_plan = False  # legacy shape: no vet-plan rules to enforce
        elif is_impl_plan and any(s in sections for s in _PLAN_FEED_SECTIONS):
            spec = [(h, True) for h in _PLAN_REQUIRED_V2]
        elif is_impl_plan:
            spec = [(h, True) for h in _PLAN_REQUIRED_V1]
        else:
            spec = [(h, True) for h in _PLAN_REQUIRED_RESEARCH_V1]
    for req, needs_fill in spec:
        if req not in sections:
            issues.append(f"missing required section '## {req}'")
        elif needs_fill and not _section_filled(sections[req]):
            issues.append(f"section '## {req}' is empty")
    # The verification-plan structural gate (§3.4 HARD) — the pre-main gate consumes plan.md, so
    # a plan whose checks a fresh agent couldn't execute is not gate-ready. Skip the duplicate
    # "missing section" (already reported above); soft flags go to the gate brief, never here.
    if is_impl_plan and ("Verification plan" in sections or "Vet plan" in sections):
        issues.extend(vet_plan_hard_issues(parse_vet_plan(text)))
    # The change-map feed (old v2 shape only): a plan CARRYING `## Touches` owes parseable rows.
    if is_impl_plan and "Touches" in sections and _section_filled(sections["Touches"]):
        issues.extend(touches_hard_issues(text))
    if artifact == "handoff-brief" and not issues:
        if not any(_section_filled(b) for b in sections.values()):
            issues.append("every section is empty — a brief needs at least one filled section")
    return issues


# --- owner edits (2026-08-09) ------------------------------------------------------------------
# THE OWNER MAY HAND-EDIT EXACTLY TWO ARTIFACTS: the brief and the plan.
#
# Both state INTENT — what this item is for and how it will be done — and intent is the owner's to
# state. A stale or wrong one is what strands an item, and until now nothing could repair it: there
# is no write route for a work-item artifact, so a plan written under an older contract, or one that
# read the request wrong, could only be regenerated by re-running the phase that wrote it.
#
# Every other artifact stays read-only, and not because it is harder: `build-vet-<n>.md`, `review.md`
# and `execution.md` are RECORDS OF WHAT A RUN DID. Editing one is not repairing a contract, it is
# changing the evidence — and the verification model is worth exactly what that evidence is worth.
OWNER_EDITABLE: tuple[str, ...] = ("brief", "plan")

_EDITED_LINE = re.compile(r"(?m)^edited_by_owner:.*\n?")


def owner_edited_at(text: str) -> str | None:
    """The `edited_by_owner` stamp in an artifact's frontmatter, or None if the owner never touched
    it. Readers use this to know the document in front of them is not what the agent last wrote."""
    m = _FM_BLOCK.match(text or "")
    if not m:
        return None
    got = re.search(r"(?m)^edited_by_owner:\s*(\S+)\s*$", m.group(1))
    return got.group(1) if got else None


def owner_edit(item_dir: Path, artifact: str, text: str, *,
               item_kind: str | None = None) -> list[str]:
    """Replace an owner-editable artifact with `text`, stamping `edited_by_owner` into its
    frontmatter. Returns the self-check issues and WRITES NOTHING when the new text breaks the
    artifact's contract — the same validator the gate runs, so a save can never leave an item in a
    state the gate will refuse and the owner cannot see. Empty list = written.

    The stamp is the point, not bookkeeping: an agent re-reading this plan is reading the OWNER's
    words, not its own, and the trail has to say so. Raises ValueError for a non-editable kind."""
    if artifact not in OWNER_EDITABLE:
        raise ValueError(f"{artifact!r} is not owner-editable — only {', '.join(OWNER_EDITABLE)} "
                         "state intent; the rest are records of what a run did")
    path = Path(item_dir) / "artifacts" / artifact_file(artifact)
    if not path.exists():
        return [f"{artifact_file(artifact)} does not exist — nothing to edit"]
    body = (text or "").replace("\r\n", "\n")
    stamp = datetime.now().isoformat(timespec="seconds")
    if (m := _FM_BLOCK.match(body)):
        fm = _EDITED_LINE.sub("", m.group(1)).rstrip()
        body = f"---\n{fm}\nedited_by_owner: {stamp}\n---\n" + body[m.end():]
    else:
        # An edit that dropped the frontmatter gets it back — the block carries `artifact:` and
        # `item_kind:`, which downstream readers key on. Losing it silently would be the edit
        # breaking the file in a way the section checks below cannot see.
        head = _FM_BLOCK.match(path.read_text())
        keep = _EDITED_LINE.sub("", head.group(1)).rstrip() if head else f"artifact: {artifact}"
        body = f"---\n{keep}\nedited_by_owner: {stamp}\n---\n" + body.lstrip("\n")
    # Judge the CANDIDATE, never the file: validating after the write would mean the artifact holds
    # a rejected version for as long as it takes to report the failure, and a phase agent reading it
    # in that window would work against text the owner was told did not save. The probe lives
    # outside artifacts/ so nothing that lists the folder can ever see it.
    probe = Path(tempfile.mkdtemp(prefix="superme-edit-")) / path.name
    try:
        probe.write_text(body)
        if (issues := self_check(item_dir, artifact, item_kind=item_kind, path=probe)):
            return issues
    finally:
        probe.unlink(missing_ok=True)
        probe.parent.rmdir()
    _atomic_write(path, body)
    return []


# --------------------------------------------------------------------------- handoff brief (D5)

_BRIEF_SECTIONS = (("Background & why raised", "background"),
                   ("Discussion summary", "discussion"),
                   ("Direction & options", "direction"),
                   ("Constraints & notes", "constraints"))


def write_handoff_brief(folder: Path, title: str, *, background: str = "", discussion: str = "",
                        direction: str = "", constraints: str = "") -> str:
    """Deterministically render an inbox item's `handoff-brief.md` (D5): code owns frontmatter +
    section order; the caller (the itemizing agent, while context is hot) supplies the prose per
    section AS ARGS — all optional; an empty section keeps its `<fill:…>` slot for triage to
    backfill. If the brief already EXISTS, the new content is APPENDED under a divider (D5: the
    topic resurfaced — never rewrite). Content contract: high-level only — no plans, no
    implementation detail, no research findings. Returns the file path."""
    folder = Path(folder)
    path = folder / "handoff-brief.md"
    provided = {"background": (background or "").strip(), "discussion": (discussion or "").strip(),
                "direction": (direction or "").strip(), "constraints": (constraints or "").strip()}
    if path.exists():
        add = "\n".join(f"**{h}:** {provided[k]}" for h, k in _BRIEF_SECTIONS if provided[k])
        if add:
            _atomic_write(path, path.read_text().rstrip() + "\n\n---\n"
                          f"*(appended {date.today().isoformat()})*\n\n" + add + "\n")
        return str(path)
    fm = (f"---\nartifact: handoff-brief\ntitle: {title!r}\nreader: agent\n"
          f"created_at: {date.today().isoformat()}\n---\n")
    body = f"# Handoff brief — {title}\n"
    for heading, key in _BRIEF_SECTIONS:
        body += f"\n## {heading}\n{provided[key] or f'<fill:{key}>'}\n"
    _atomic_write(path, fm + body)
    return str(path)


# --------------------------------------------------------------------------- evidence ledger

def repo_fingerprint(repo_dir: Path | None) -> str:
    """A cheap fingerprint of the repo's CODE STATE: HEAD sha + the content of every uncommitted
    change to a TRACKED file (`git diff HEAD`). Any commit or tracked edit changes it — the D6
    stale-on-edit trigger. The diff CONTENT is what's hashed, not a status summary: a summary is
    byte-identical while an already-dirty file keeps changing, which would let evidence recorded
    mid-build stay "fresh" through further edits to the same file.

    UNTRACKED files are deliberately excluded (2026-07-30). They were counted via
    `git status --porcelain`, which made a vet run stale its own evidence: running the project's
    tests drops coverage files, temp databases and logs into the worktree, the fingerprint moved,
    and green evidence read as `stale` — a false positive of exactly the kind that wedged the close
    gate (dogfood D5). Build's implementation is always tracked (the commit gate sees to that), so
    nothing this rule exists to catch lives in an untracked file.

    Non-git / missing dir → 'no-git' (evidence there can't be freshness-tracked, only recorded)."""
    if not repo_dir or not Path(repo_dir).is_dir():
        return "no-git"
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True,
                              text=True, timeout=10)
        if head.returncode != 0:
            return "no-git"
        diff = subprocess.run(["git", "diff", "HEAD"], cwd=repo_dir,
                              capture_output=True, text=True, timeout=15)
        return hashlib.sha1((head.stdout.strip() + "\n" + diff.stdout).encode()).hexdigest()[:16]
    except (OSError, subprocess.SubprocessError):
        return "no-git"


_EVIDENCE_HEAD = re.compile(r"^### (?P<ts>\S+) — (?P<check>.*)$")


def _resolve_evidence_check(check: str, valid_ids: list[str]) -> str:
    """The evidence ledger's check field IS its join key — `evidence_status` derives each check's
    verdict from the LATEST entry bearing that exact key. So a glued key (`stats-top: leaderboard,
    no TOTAL`) is a DIFFERENT key from the vet-plan id (`stats-top`): its stale `failed` entry is
    never superseded by the clean id's later `pass`, and the loop halts on a phantom permanent
    failure (B4). Single source of truth: the ledger key space == the vet-plan id space, enforced
    at write. Returns the exact id, or raises with a targeted hint. `valid_ids` empty → no vet plan
    to check against (depth=none / non-vetted kinds) → caller records the check verbatim."""
    c = check.strip()
    if c in valid_ids:
        return c
    # Glued key? — a real id with a description welded on. Name it so the fix is one edit, not a hunt.
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
    """Append one BUILD validation run to the current cycle report's `## Validation` ```runs fence:
    the command build ran, what came back, whether it passed, and the tree it ran against.

    Validation is the builder's self-check (design §Further Notes) and it stays build's to run —
    but a self-check written as prose (*"full suite: 106/106 pass"*) cannot be checked by anything.
    Build is both the runner and the only witness, and the sentence reads identically whether the
    suite passed, failed, or never ran. Recording the run as DATA is what lets vet audit the claim
    on its own pass and send a false green back into the loop, which is the whole point: the proof
    of build's work belongs to verification, and verification needs something to hold.

    The prose bullets in `## Validation` are unaffected — they are the per-task narrative a vetter
    reads. This is the machine lane beside them, same grammar as `## Verification`'s ```checks."""
    result = " ".join((result or "").split())
    raw = (command or "").strip()
    if not raw:
        raise ValueError("record_validation needs the COMMAND you ran — a claim with no command "
                         "is the prose this record exists to replace")
    # A newline INSIDE A QUOTE is refused, not silently flattened. This ledger stores the command on
    # its `###` heading line and vet's audit re-runs THE STORED TEXT verbatim, so every newline
    # becomes a space. Between shell words that is harmless — a wrapped `pytest -q\n tests/` is the
    # same command. Inside a quoted string it is not: the newlines are the program's statement
    # separators, so `python3 -c "import os\nprint(1)"` flattens into an IndentationError, the audit
    # reports "claim did not reproduce", and the loop spends a whole build⟷vet cycle on a storage
    # round-trip while the code was always correct (observed live 2026-08-14, 223k tokens).
    # Refusing costs one tool call instead. Quote tracking is deliberately naive — it answers "was a
    # quote open at this newline", which is the question, and over-refusing a pathological quoting
    # style is cheaper than shipping a command that no longer runs.
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
    """Every recorded build validation run, in record order: [{ts, command, task, result, passed,
    fingerprint, cycle}]. `cycle` scopes to one pass — what vet's audit re-runs.

    The head slot holds the COMMAND (the ledger parser reads it as `check`), because a validation
    run is identified by what was executed, not by a name someone chose for it."""
    out: list[dict] = []
    for r in cycle_reports(item_dir):
        if cycle is not None and r["cycle"] != cycle:
            continue
        body = _split_sections(Path(r["path"]).read_text()).get("Validation", "")
        for block in _fenced_blocks(body, lang=VALIDATION_FENCE):
            for e in _parse_ledger_entries(block):
                out.append({"ts": e.get("ts", ""), "command": e.get("check", ""),
                            "task": e.get("task", ""), "result": e.get("result", ""),
                            "passed": bool(e.get("passed")),
                            "fingerprint": e.get("fingerprint", ""), "cycle": r["cycle"]})
    return out


def record_validation_audit(item_dir: Path, repo_dir: Path | None, *, command: str,
                            claimed: bool, actual: bool, result: str) -> dict:
    """Record the kernel's AUDIT of one build validation claim — what build said the command did,
    and what the command actually does now.

    It lands in the `## Verification` fence as `kind: audit`, beside vet's verdicts and diagnoses:
    same append-only grammar, same freshness stamp, and `evidence_entries` filters it out, so an
    audit can never be counted as a check. That matters — the whole point of moving the test suite
    out of the vet plan is that unit tests are not the item's exam. This is not an exam question;
    it is the answer to *"is build's claim true?"*, which is verification's own business."""
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
    """The audit rows recorded so far: [{command, claimed, actual, agrees, result, cycle}].
    `cycle` scopes to one pass — the loop only acts on the pass it is deciding."""
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
    """Audit rows where build's claim and the machine disagree — the findings that send a cycle
    back. The LAST audit of a command wins: a re-audit after build fixed something is the current
    answer, and carrying the superseded row would keep a resolved discrepancy alive forever."""
    latest: dict[str, dict] = {}
    for r in validation_audit(item_dir, cycle=cycle):
        latest[r["command"]] = r
    return [r for r in latest.values() if not r["agrees"]]


def _parse_ledger_entries(text: str) -> list[dict]:
    """Line-oriented ledger text → [{ts, check, how, result, note?, passed, deferred?,
    fingerprint}] in order. Shared by the legacy validation.md reader and the cycle-file
    §Verification fence reader — one format, one parser."""
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
                    # `<severity>: <text>` — the severity vocabulary is fixed and three words long,
                    # so the first colon splits it unambiguously even when the text has its own.
                    f = re.match(r"^(low|medium|high):\s*(.*)$", v)
                    cur.setdefault("findings", []).append(
                        {"severity": f.group(1), "text": f.group(2)} if f
                        else {"severity": "medium", "text": v})
                    continue
                if k in ("met", "missed"):
                    # One rubric criterion, judged. Repeated lines accumulate rather than
                    # overwrite — the whole point is that each criterion stands on its own.
                    cur.setdefault("criteria", []).append({"text": v, "met": k == "met"})
                    continue
                if k == "probed":
                    # One probe per line, accumulating. A lens read is a LIST of things tried, and
                    # a reader asking "what did it actually check" wants them separable — the old
                    # single line turned four distinct probes into one unreadable paragraph. Rows
                    # written before this still parse: one line simply gives a one-item list.
                    cur.setdefault("probed", []).append(v)
                    continue
                cur[k] = (v == "true") if k in ("passed", "deferred") else v
    return entries


def record_verification(item_dir: Path, repo_dir: Path | None, *, check: str, how: str,
                        result: str, passed: bool, deferred: bool = False, note: str = "",
                        title: str = "", by: str = BY_AGENT,
                        met: list[str] | None = None, missed: list[str] | None = None) -> dict:
    """Append one machine entry to the current cycle report's `§Verification` check fence
    (renovation §3.1 — the fence replaces the retired validation.md ledger; scaffolds the cycle
    file first if none exists): check + how it ran + the machine result + pass/fail + the repo
    fingerprint at record time. Entries are APPEND-ONLY; 'verified' is derived from them, never
    asserted. The `check` MUST be an exact verification-plan id when the plan has one (B4) — see
    _resolve_evidence_check. `note` is the one-line expected-vs-actual context for a failure.

    `by` is the entry's PROVENANCE (design §4): `machine` when the kernel executed the check's
    literal `run:` block itself, `agent` when a vetter performed it and attested to the result.
    Both are legitimate evidence; they are not equally strong, and a reader who cannot tell them
    apart is reading a weaker record than they think. A machine entry already written this cycle
    is FINAL — a later agent record against the same check is refused, because the one property
    that makes kernel execution worth having is that nothing downstream can revise it.

    `met` / `missed` carry a rubric check's per-criterion judgment (design §2). Every criterion the
    plan listed must be accounted for, and a missed one means the check FAILED — "three of four,
    close enough" is exactly the soft pass a rubric exists to prevent. Both rules are enforced
    below rather than asked for, because a partial record and a partial pass look identical from
    the outside.

    `deferred=True` (BV-A2) records a check the build could NOT satisfy because it needs an
    authorization it lacks: it is neither pass nor fail — it advances the item to review with the
    authorization request, rather than failing the loop closed. A deferred entry rides
    `passed: false` for legacy readers but carries `deferred: true`, and evidence_status buckets
    it separately."""
    # Single-line coerce: the ledger is line-oriented (one `- key: value` per line) — an embedded
    # newline in any field would corrupt parsing for every entry after it.
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    check, how, result, note = _one_line(check), _one_line(how), _one_line(result), _one_line(note)
    if not (check and how and result):
        raise ValueError("evidence needs non-empty check, how, and result")
    # Single source of truth for check state: the ledger key MUST be a plan check id (when one exists).
    plan_path = Path(item_dir) / "artifacts" / artifact_file("plan")
    valid_ids = [c["id"] for c in parse_vet_plan(plan_path.read_text()).get("checks", [])] \
        if plan_path.is_file() else []
    # `depth: none` has no key space at all, so a recorded entry here could only be one vet
    # invented — and it would then drive the loop. Refuse, and name where the observation goes.
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
    rubric = next((c.get("rubric") or [] for c in parse_vet_plan(plan_path.read_text())["checks"]
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
    # Target the LATEST cycle report even when the driver already closed it (a re-vet after stale
    # re-verifies the SAME cycle); scaffold only when no cycle exists at all.
    reports = cycle_reports(item_dir)
    cy = ({"cycle": reports[-1]["cycle"], "path": reports[-1]["path"]}
          if reports else scaffold_cycle(item_dir, title=title))
    # A machine entry is the cycle's final word on that check. Without this an agent could re-record
    # over a red kernel result and the loop would advance on the agent's version — which would make
    # kernel execution decorative rather than load-bearing.
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
    """Append vet's DIAGNOSIS of a failed check (design §5): where it broke, why, and what could
    not be determined. Never the fix — build reasons that out inside the same plan, because a
    remedy named here is a remedy vet then grades itself on.

    Refused unless the check's latest verdict is an actual failure: a diagnosis of a passing check
    is noise in the one place the next build cycle is told to look, and a diagnosis of a deferral
    invents a cause for work nobody did.

    `unknown` is optional and load-bearing when present — "the request never reached the handler,
    and I could not tell whether the router or the middleware dropped it" is a better handoff than
    a confident guess. Silence about the limits of the evidence is how a build cycle gets sent
    somewhere the vetter never actually looked."""
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
    """Record one standing lens's read of this cycle (design §3): what was PROBED, and whatever it
    found. No findings is the expected outcome and a complete record — `probed` is what makes a
    clean pass mean something instead of reading as a lens nobody ran.

    `probed` is a LIST — one probe per entry (owner, 2026-08-06). A lens read is several separable
    things tried, and the owner reads this to answer "what did it actually check": as one paragraph
    that answer has to be extracted by eye, as four lines it is scannable. A bare string is still
    accepted and becomes a one-item list, so every stored read keeps parsing.

    Deliberately no quotas anywhere in this path. "Find at least two unhandled inputs" manufactures
    the second one when the code is fine, so the obligation is to probe and say what was probed.

    A lens is not a plan check: it has no id in `## Verification plan`, it runs whatever the depth
    says, and it is recorded here rather than through `record_verification` — which would refuse it,
    correctly, for not naming a planned check."""
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
    """Nominate one of this item's checks for the repo's VERIFICATION LIBRARY (design §8) — the
    growing catalogue the next item's plan inherits from. `general` says what makes it general: the
    property of THIS REPO it defends, stated without reference to this item.

    Vet nominates; CLOSE writes. Vet has no writes into `general/`, and the nomination changing that
    would put a repo-wide commitment on the far side of a gate the owner has not reached yet.

    Refused unless the check has PASSED at least once here. A library of untested hypotheses is
    worse than no library: the next plan inherits one, it does not work, and that item spends a
    cycle discovering what this one could have told it."""
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
    """Every check nominated for the library across this item's cycles → {check: {general, cycle}}.
    Not this-cycle-only, unlike the lenses and diagnoses: a nomination is a claim about the REPO,
    which does not expire when the item's code moves, and close reads the whole item at the end."""
    out: dict[str, dict] = {}
    for e in _ledger(item_dir):
        if e.get("kind") == KIND_NOMINATION:
            out[e["check"]] = {"general": str(e.get("general") or ""), "cycle": e.get("cycle")}
    return out


def lens_reads(item_dir: Path) -> dict[str, dict]:
    """This cycle's lens reads → {lens: {probed, findings, cycle}}. THIS cycle only: a finding the
    last cycle raised describes code that has since moved, and a lens read is cheap to redo."""
    reports = cycle_reports(item_dir)
    if not reports:
        return {}
    cycle = reports[-1]["cycle"]
    out: dict[str, dict] = {}
    for e in _ledger(item_dir):
        if e.get("kind") == KIND_LENS and e.get("cycle") == cycle:
            # `probed` is a list of probes. A read stored before that (one `- probed:` line) parses
            # to a one-item list, so no reader needs to know which era wrote it.
            raw = e.get("probed") or []
            out[e["check"]] = {"probed": [raw] if isinstance(raw, str) else list(raw),
                               "findings": list(e.get("findings") or []),
                               "cycle": e.get("cycle")}
    return out


def _gating(lens: str, findings: list[dict]) -> list[dict]:
    at = _LENS_GATES_AT.get(lens, ())
    return [f for f in findings if f.get("severity") in at]


def lens_gaps(item_dir: Path) -> list[dict]:
    """The lens findings that send this item back to build → [{lens, severity, text}], in lens
    order. A failing lens routes like any other failed check; there is no separate exit for it."""
    reads = lens_reads(item_dir)
    return [{"lens": ln, **f} for ln in LENSES
            for f in _gating(ln, reads.get(ln, {}).get("findings") or [])]


def missing_lenses(item_dir: Path) -> list[str]:
    """Standing lenses with no read this cycle. `performance` is never here — it is only meaningful
    against a budget the plan named, and demanding it would buy opinions."""
    reads = lens_reads(item_dir)
    return [ln for ln in STANDING_LENSES if ln not in reads]


def undiagnosed_failures(item_dir: Path) -> list[str]:
    """Checks whose latest verdict is a failure with no diagnosis in that same cycle — the vet
    report's closing bar (design §5). Same-cycle, deliberately: a check that failed in c1 and fails
    again in c3 needs c3's reading, because the code moved in between and last cycle's cause may
    have been fixed and replaced by another."""
    latest = {e["check"]: e for e in evidence_entries(item_dir)}
    diag = diagnoses(item_dir)
    return [c for c, e in latest.items()
            if not e.get("passed") and not e.get("deferred")
            and diag.get(c, {}).get("cycle") != e.get("cycle")]


def evidence_entries(item_dir: Path) -> list[dict]:
    """Every recorded VERDICT, in record order: each cycle report's §Verification fence, in cycle
    order. One derived view, so evidence_status and the briefs never care where an entry lives.
    (`validation.md` — the pre-renovation ledger — is retired; a still-open item carrying one
    predates the loop entirely and has no live cycle to gate.)

    Diagnosis entries share the fence but are NOT verdicts and are filtered out here — every caller
    (freshness, the loop's convergence fingerprint, the check table) counts verdicts, and a
    diagnosis leaking into that count would read as a second, failing entry."""
    return [e for e in _ledger(item_dir) if e.get("kind", KIND_VERDICT) == KIND_VERDICT]


def _ledger(item_dir: Path) -> list[dict]:
    """Every entry in the §Verification fences, verdicts and diagnoses alike, in record order."""
    entries: list[dict] = []
    for r in cycle_reports(item_dir):
        body = _split_sections(Path(r["path"]).read_text()).get("Verification", "")
        for block in _fenced_blocks(body):
            for e in _parse_ledger_entries(block):
                entries.append({**e, "cycle": r["cycle"]})
    return entries


def diagnoses(item_dir: Path) -> dict[str, dict]:
    """The latest diagnosis per check → {check: {where, why, unknown, cycle}} (design §5).

    A diagnosis is a separate act from the verdict, deliberately: the verdict says a check failed,
    the diagnosis says where and why — and for a kernel-run check the two even have different
    authors (the daemon owns the exit code, vet owns the reading of it). Keeping them as one record
    would mean either letting an agent rewrite a machine verdict to attach its reading, or leaving
    machine failures undiagnosed. Both are worse than a second entry."""
    out: dict[str, dict] = {}
    for e in _ledger(item_dir):
        if e.get("kind") == KIND_DIAGNOSIS:
            out[e["check"]] = {"where": str(e.get("where") or ""),
                               "why": str(e.get("why") or ""),
                               "unknown": str(e.get("unknown") or ""),
                               "cycle": e.get("cycle")}
    return out


# --- Proof: the connected view (renovation v2 §4.2) ------------------------------------------
# One row per BUILT THING, each carrying its own validation → verification. A bare grid of check ids
# tells the owner nothing; "this feature, proven this way" does. The join key is the plan's `## Tasks`
# id: cycle §Built / §Validation bullets lead with `t<n> —`, and vet-plan checks name `covers:`.
# Assembled mechanically — no LLM anywhere in this path.
#
# TOLERANT BY DESIGN. Untagged content is not dropped and not guessed at: it lands in the item-wide
# row. Requiring the tags would fail every in-flight item, and a Proof view that silently omits work
# is worse than one that admits it couldn't attribute it.
# The id may be EMPHASIZED. Live finding (2026-07-30): every real cycle report on the playground
# writes `- **t1** (\`file.py:49\`): …`, so a bare `t\d+` match found nothing and every bullet fell
# into the item-wide row — the tolerant path hid the bug instead of failing it. Agents bold the id
# because it reads better, and the format they actually produce is the one to parse.
# `\b` sits BEFORE the closing emphasis, not after it: `t1**` followed by a space has no word
# boundary after the asterisks, so a trailing `\b` makes the regex backtrack to consuming zero of
# them and the `**` survives into the text.
_TAGGED_BULLET = re.compile(r"^\s*[-*]\s*[*`_]{0,2}(t\d+)\b[*`_]{0,2}[\s—:.\-]*(.*)$")


def _tagged_bullets(body: str) -> tuple[dict[str, list[str]], list[str]]:
    """A report section's bullets split by leading task id → ({task: [text, …]}, [untagged, …]).
    A bullet is a BLOCK: continuation lines belong to the bullet above (the template wraps long
    ones, and the line-wise reading of exactly this shape is what corrupted a plan on 2026-07-28)."""
    by_task: dict[str, list[str]] = {}
    loose: list[str] = []
    cur: list[str] | None = None
    fenced = False
    for line in (body or "").splitlines():
        # A fenced block is the section's MACHINE lane (`## Validation`'s ```runs), not prose. Read
        # line-wise it would spill `- result:` / `- passed:` lines into the untagged bullets and put
        # raw ledger fields on the Task tab beside the narrative they were extracted from.
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
    """The Proof view's rows → [{task, text, done, built[], validated[], verified[]}], the plan's
    tasks in order, then one `task: ""` item-wide row for everything that named no task.

    `verified` entries are the PLANNED checks — the plan's `## Verification plan` is the list, and a
    recorded verdict is joined onto it. A check the loop hasn't reached yet is still a row (`ran:
    False`), because the exam is decided at plan and the owner reads it there: rows that only appear
    once vet has run left the Task tab empty at exactly the gate where the owner is asked whether
    this proof is enough. A check is attached to a task when its `covers:` names it; one covering two
    tasks appears under both — it genuinely proves both. The item-wide row also carries the
    whole-item validation lines (a suite run isn't per-task) and any check that named nothing."""
    item_dir = Path(item_dir)
    plan_path = item_dir / "artifacts" / artifact_file("plan")
    plan = plan_path.read_text() if plan_path.is_file() else ""
    tasks = parse_tasks(plan)
    checks = parse_vet_plan(plan).get("checks", [])
    # check id → the task ids it defends, straight off the approved plan.
    covers_of = {c["id"]: str(c.get("covers") or "") for c in checks}
    built: dict[str, list[str]] = {}
    validated: dict[str, list[str]] = {}
    built_loose: list[str] = []
    valid_loose: list[str] = []
    for r in cycle_reports(item_dir):
        sections = _split_sections(Path(r["path"]).read_text())
        b, bl = _tagged_bullets(sections.get("Built", ""))
        v, vl = _tagged_bullets(sections.get("Validation", ""))
        for src, dst in ((b, built), (v, validated)):
            for k, vals in src.items():
                dst.setdefault(k, []).extend(vals)
        built_loose += bl
        valid_loose += vl

    # Each check's pass/fail sequence by cycle, so the surface can render `c3 ✗→✓` — the one thing
    # latest-per-check loses, and the whole point of showing a LOOP's proof rather than a snapshot.
    history: dict[str, list[dict]] = {}
    for e in evidence_entries(item_dir):
        history.setdefault(e["check"], []).append(
            {"cycle": e.get("cycle"), "passed": bool(e.get("passed"))})

    # The planned exam, joined with whatever the loop has recorded. Planned order first; a recorded
    # check the plan no longer declares still shows (a revision dropped it — the verdict is real and
    # silently losing it would hide a result the owner may have already read).
    verdicts = {r["check"]: r for r in verdict_rows(item_dir)}
    # `by` on a PLANNED row is the promise, not the record: a check carrying a `run:` block will be
    # executed by the kernel, and the owner reading the plan gate should see which parts of the exam
    # are machine-decided before approving it. Once it runs, the ledger's own `by` overwrites this.
    planned = [{"check": c["id"],
                # The plan's own sentence for what a green MEANS — the line the Task tab leads
                # with, so the owner reads the proof before the mechanism. Never re-derived here:
                # deriving it from `run:` is exactly the drift `proves:` exists to end.
                "proves": str(c.get("proves") or ""),
                "expect": str(c.get("expect") or ""),
                "mode": str(c.get("mode") or ""), "ran": False,
                # A rubric check is judged, so the kernel never runs it (see services/checks.py).
                "by": BY_MACHINE if (c.get("run") and not c.get("rubric")) else BY_AGENT,
                # Where the check came from: "" = authored for this item, `standing`/`library` =
                # inherited from the repo's verification library (design §8).
                "source": str(c.get("source") or ""),
                # The criteria the plan set, readable at the plan gate — the recorded judgment
                # lands beside them in `criteria` once the check runs.
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
        # Only tasks the plan actually DECLARES can hold a check. A `covers: t7` with no `t7` in
        # `## Tasks` used to route the row to a bucket no task ever read, so the check disappeared
        # from the owner's Proof view — the silent omission this function's contract refuses. It is
        # a plan miss (the skill says fix one or the other), but the surface's job is to admit it
        # couldn't attribute the check, not to drop it: unattributed lands item-wide, like any
        # untagged content.
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
    """The LATEST verdict per check, in first-seen order → [{check, passed, deferred, cycle, how,
    result}]. The vet's actual findings, which is what a review reader needs and what a count of
    ledger entries can't say. Read by the review deputy's prompt (slice 6b) and the Proof view.

    Latest-per-check, not every entry: a check that failed in c1 and passed in c3 IS passing, and
    showing both rows invites the reader to average two contradictory facts. Same rule
    `evidence_status` uses to decide freshness — one definition of "where this check stands"."""
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
             # The located cause, joined from this cycle's diagnosis (empty on a passing check, and
             # on a stale one from an earlier cycle — a cause the code has moved past misleads).
             **{k: (diag.get(c, {}).get(k, "") if diag.get(c, {}).get("cycle") == e.get("cycle")
                    else "") for k in ("where", "why", "unknown")}}
            for c, e in latest.items()]


# --- assumptions: RETIRED (workflow-renovation-v2 §3.1 demolition, 2026-07-27) --------------
# `assumptions.md` + `record_assumption` / `ratify_assumptions` / `assumptions_ratified` are gone.
# The ledger was a file nobody opened whose only teeth were a close criterion an autopilot item
# could never satisfy — ratification is owner-only, so autonomy ended at a blocked gate. The
# signal it carried survives where it is actually read: a `## Assumptions` section in the phase's
# own record (cycle report / investigation.md), surfaced in that phase's user report, picked up on
# demand. Do not reintroduce a standalone ledger for it.


# --- the authorization ledger (BV-A2) -------------------------------------------
# A work-item may PROPOSE a contract change (an anchor-doc op) but not every such change is
# self-authorizable: the ones that DEFINE or alter intent are owner-reserved. When build hits one it
# can't self-authorize, it records an AUTHORIZATION REQUEST here (what · why · which doc · a SCOPE
# the deputy's delegated authority is matched against · the vet check it blocks) and lets that check
# DEFER — instead of stalling, or (worse) re-pointing its own exam to dodge the wall. The request
# rides every gate brief; at review the owner — or the deputy, when the scope is delegated — grants
# or denies. A grant routes back through build⟷vet (grant-as-send_back); a denial accepts the
# deferral (the work is skipped, on the record). Close refuses while any request is still PENDING.
#
# The SCOPE vocabulary encodes the owner's line (build-vet-autonomy-design.md): the deputy may
# authorize changes that SYNC the contract to shipped reality; the owner reserves changes that
# DEFINE or alter intent. Which scopes are delegated is a per-system setting (BV-A2.2) matched
# against this vocabulary — the default set lives with the deputy mandate, not here.
AUTH_SCOPES = (
    # DELEGABLE by default — "sync the contract to shipped reality":
    "doc-sync",           # reconcile a descriptive doc (architecture/capabilities/resources) to merged reality
    "rename-to-shipped",  # rename doc references to match already-shipped code
    "roadmap-mark-done",  # mark a roadmap item done that IS done
    # RESERVED by default — "define or alter intent" (the floor holds; the deputy escalates):
    "prd-identity",       # project-prd identity / goals / deliverables
    "roadmap-scope",      # add / remove / re-scope a roadmap deliverable
    "new-decision",       # a decision that sets direction (incl. the public-contract naming choice)
    "doc-delete",         # delete / retire a doc
)

# The DEFAULT sync-to-reality set — scopes the deputy may grant out of the box (BV-A2.2's
# spine.DEFAULT_DELEGATED_AUTHORITY mirrors this). The actual delegated set is a per-system setting;
# this static split only informs the owner's review brief ("was this delegable, or is it reserved —
# which is why it reached you"). Keep in sync with AUTH_SCOPES' delegable half above.
DELEGABLE_SCOPES = ("doc-sync", "rename-to-shipped", "roadmap-mark-done")

_AUTHORIZATION_FILE = "authorizations.md"
_AUTHORIZATION_HEAD = re.compile(r"^### (?P<id>\S+) — (?P<what>.*)$")


# Which STAGED OPS make a declared scope a lie. The reserved/delegable split (above) is declared
# by the agent it constrains, so on its own it is an honour system — live evidence 2026-07-27
# (item b229793bcf9a): ops that dropped `--csv` from the `d-reporting` deliverable line AND
# rewrote its success-signal row were filed as `doc-sync`, the DELEGABLE scope, with the reason
# "the docs-updated check inspects the live doc". A deputy would have granted it. Code, not prose,
# has to say that touching what a project IS is not a sync.
#
# Deliberately narrow: it matches the two anchor docs + the sections that DEFINE intent, and it
# only fires when a delegable scope is claimed. It cannot catch every mislabel (an op can reword a
# deliverable from inside a neighbouring section) — the guarantee is "the obvious lie is refused",
# not "the scope is proven". Reserved scopes pass through untouched: they already reach the owner.
_INTENT_SECTIONS = {
    "project-prd": ("deliverables", "success signals", "non-goals", "users", "problem"),
    "roadmap":     ("wave", "deliverable"),
}


def intent_ops(ops: list) -> list[str]:
    """The staged ops that DEFINE intent rather than record what shipped → ['<doc> § <section>'].
    Empty when nothing intent-defining is staged."""
    out: list[str] = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        doc = str(op.get("doc") or "").strip().lower()
        section = str(op.get("section") or "").strip().lower()
        for marker in _INTENT_SECTIONS.get(doc, ()):
            if marker in section:
                out.append(f"{doc} § {op.get('section')}")
                break
    return out


def scope_mismatch(scope: str, ops: list) -> str:
    """'' when the declared scope is consistent with the staged ops, else the refusal message.
    Only DELEGABLE scopes are checked — a reserved one already goes to the owner, so mislabelling
    upward costs nothing and blocking it would only add friction."""
    if scope not in DELEGABLE_SCOPES:
        return ""
    hits = intent_ops(ops)
    if not hits:
        return ""
    return (f"scope {scope!r} is the delegable 'sync the docs to shipped reality' kind, but the "
            f"staged ops change what the project IS: {', '.join(sorted(set(hits))[:3])}. That is "
            f"an intent change — use `roadmap-scope` (add/remove/re-scope a deliverable), "
            f"`prd-identity` (identity/goals) or `new-decision`. Those are owner-reserved: they "
            f"reach the owner instead of a delegated deputy, which is the point.")


def record_authorization(item_dir: Path, *, what: str, why: str, doc: str, scope: str,
                         check: str = "", phase: str = "", cycle: int | None = None) -> dict:
    """Append one PENDING authorization request. id = the record timestamp (unique per request).
    Append-only: the grant/deny decision rewrites only this entry's `status`/`by` lines later."""
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    what, why, doc, scope, check = (_one_line(x) for x in (what, why, doc, scope, check))
    if not (what and why and scope):
        raise ValueError("an authorization request needs what, why, and a scope")
    if scope not in AUTH_SCOPES:
        raise ValueError(f"unknown authorization scope {scope!r} — one of: {', '.join(AUTH_SCOPES)}")
    path = Path(item_dir) / "artifacts" / _AUTHORIZATION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "" if path.exists() else (
        "# Authorizations\n\nContract changes a work-item cannot self-authorize — each awaiting a "
        "grant or deny at review.\n")
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry = (f"\n### {ts} — {what}\n"
             f"- why: {why}\n"
             f"- doc: {doc}\n"
             f"- scope: {scope}\n"
             f"- check: {check}\n"
             f"- phase: {phase or 'unknown'}\n"
             f"- cycle: {cycle if cycle is not None else ''}\n"
             f"- status: pending\n"
             f"- by: \n")
    _atomic_write(path, (path.read_text() if path.exists() else head) + entry)
    return {"id": ts, "what": what, "scope": scope, "check": check, "status": "pending"}


def authorization_entries(item_dir: Path) -> list[dict]:
    """Parse the ledger: [{id, what, why, doc, scope, check, phase, cycle, status, by}] in order."""
    path = Path(item_dir) / "artifacts" / _AUTHORIZATION_FILE
    if not path.exists():
        return []
    entries: list[dict] = []
    cur: dict | None = None
    for line in path.read_text().splitlines():
        m = _AUTHORIZATION_HEAD.match(line)
        if m:
            cur = {"id": m.group("id"), "what": m.group("what"), "status": "pending"}
            entries.append(cur)
        elif cur is not None:
            kv = re.match(r"^- (why|doc|scope|check|phase|cycle|status|by): (.*)$", line)
            if kv:
                cur[kv.group(1)] = kv.group(2).strip()
    return entries


def pending_authorizations(item_dir: Path) -> list[dict]:
    """Requests still owed a grant/deny — what the gate brief shows and the close gate refuses on."""
    return [a for a in authorization_entries(item_dir) if a.get("status") == "pending"]


def resolve_authorization(item_dir: Path, auth_id: str, *, decision: str, by: str) -> dict | None:
    """Grant or deny ONE pending request (append-only in substance: rewrite its status+by lines).
    Returns the updated entry, or None if the id isn't found or isn't pending."""
    if decision not in ("granted", "denied"):
        raise ValueError("decision must be granted or denied")
    path = Path(item_dir) / "artifacts" / _AUTHORIZATION_FILE
    if not path.exists():
        return None
    out: list[str] = []
    in_block = False
    changed = False
    for line in path.read_text().splitlines(keepends=True):
        m = _AUTHORIZATION_HEAD.match(line.rstrip("\n"))
        if m:
            in_block = (m.group("id") == auth_id)
        elif in_block and line.startswith("- status: pending"):
            line = f"- status: {decision}\n"
            changed = True
        elif in_block and line.startswith("- by:"):
            line = f"- by: {by}\n"
        out.append(line)
    if not changed:
        return None
    _atomic_write(path, "".join(out))
    return next((a for a in authorization_entries(item_dir) if a["id"] == auth_id), None)


def _plan_check_ids(item_dir: Path) -> set[str] | None:
    """The CURRENT vet plan's check ids — the authoritative set. None when there is nothing to
    scope by (no plan file, no `## Vet plan`, or a plan with zero checks): the caller then reads
    the whole ledger, the pre-scoping behaviour."""
    plan = Path(item_dir) / "artifacts" / artifact_file("plan")
    if not plan.is_file():
        return None
    ids = {c.get("id") for c in parse_vet_plan(plan.read_text()).get("checks", []) if c.get("id")}
    return ids or None


def plan_vet_depth(item_dir: Path) -> str:
    """The plan's declared vet depth (`none` | `checks` | `scenarios`), or `""` when there is no
    plan / no `## Verification plan` to read.

    `none` is the OWNER-APPROVED judgment that this item has no observable surface worth checking
    — declared by the plan agent, shown at the plan gate, and approved with it. It is NOT vet's
    call to make: an agent that could declare "nothing to verify" for itself would reach for the
    phrase whenever checks were inconvenient. Everything downstream that honours `none` reads it
    from HERE, so the authority has exactly one source (slice 5b, 2026-07-30)."""
    plan = Path(item_dir) / "artifacts" / artifact_file("plan")
    if not plan.is_file():
        return ""
    vp = parse_vet_plan(plan.read_text())
    return str(vp.get("depth") or "") if vp.get("present") else ""


_NO_VET_LINE = "**Nothing to verify.**"


def note_no_verification(item_dir: Path) -> str | None:
    """Write the `depth: none` cycle's §Verification content — CODE-WRITTEN, like every other fact
    nobody should be able to type, and quoting the plan's own `reason` so the justification on the
    page is the one the owner approved. The vet agent narrates this in its report; what lands in
    the cycle file is derived, so an empty §Verification can never be mistaken for a vet that gave
    up (the failure mode the build skill already hit with unfilled slots).

    Idempotent: a re-vet of the same cycle adds nothing. Returns the cycle path, or None when there
    is no cycle report yet or the line is already there."""
    item_dir = Path(item_dir)
    reports = cycle_reports(item_dir)
    if not reports:
        return None
    path = Path(reports[-1]["path"])
    if _NO_VET_LINE in path.read_text():
        return None
    plan = item_dir / "artifacts" / artifact_file("plan")
    reason = " ".join(str(parse_vet_plan(plan.read_text()).get("reason") or "").split()) \
        if plan.is_file() else ""
    _append_to_section(path, "Verification",
                       f"{_NO_VET_LINE} The approved plan declares `depth: none`"
                       + (f" — {reason}" if reason else "")
                       + ". No check was owed, so none was run.\n")
    return str(path)


def evidence_status(item_dir: Path, repo_dir: Path | None, *, scope_to_plan: bool = True) -> dict:
    """The derived verdict over the ledger (D6 §2, hermes stale-on-edit): `unverified` (no
    entries) · `failed` (latest entry of any check failed) · `stale` (all latest entries passed
    but the repo fingerprint moved since) · `passed` (green AND fresh).

    Scoped to the CURRENT vet plan's checks by default: the ledger is append-only, so a check that
    was renamed or dropped (a re-plan, or a build re-pointing its checks) leaves a stale entry
    behind. Unscoped, that ORPHAN's last verdict — often a FAIL — pins the loop red forever, since
    nothing ever writes a fresh entry under a name no longer in the plan. Scoping drops orphans
    from the derived verdict while keeping them in the ledger for audit (they ride the result as
    `orphaned`). Pass scope_to_plan=False for the raw whole-ledger view.

    A `deferred` status (BV-A2) sits between passed and failed: a check awaiting an authorization
    grant. The authorization ledger is the AUTHORITY — a check named by a PENDING request is
    deferred whether or not the vetter recorded it that way — so a genuine wall can't masquerade as
    a fail-closed loop, and the build never has to re-point its own exam to dodge it. A real FAIL
    still dominates; deferred routes the item to review carrying the request."""
    entries = evidence_entries(item_dir)
    ids = _plan_check_ids(item_dir) if scope_to_plan else None
    auths = authorization_entries(item_dir)
    deferred_by_auth = {a["check"] for a in auths if a.get("status") == "pending" and a.get("check")}
    # A DENIED request WAIVES its check (BV-A2.3): the owner decided the change won't happen, so the
    # check that required it is excused — the item can close with the gap on the record. A check that
    # is denied-then-re-requested is pending again (deferred wins over waived).
    waived_by_auth = {a["check"] for a in auths if a.get("status") == "denied" and a.get("check")} \
        - deferred_by_auth
    if ids is not None:
        deferred_by_auth &= ids
        waived_by_auth &= ids
    if not entries and not deferred_by_auth:
        # `depth: none` — the plan's owner-approved judgment that nothing here is observable. An
        # empty ledger is then the CORRECT ledger, so it reports `passed`, not `unverified`: the
        # loop's fail-closed rule ("an unrecorded vet is not a pass") exists to catch a vet that
        # skipped its work, and there was no work to skip. It stays `passed` rather than a fourth
        # status so no consumer's `== "passed"` branch can silently halt an item that is fine;
        # `not_required` is the flag the user-facing surfaces read to say the honest thing.
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


# `author_readiness` / `readiness.md`: RETIRED (workflow-renovation-v2 §3.1 demolition,
# 2026-07-27). A mechanically-authored user doc that said what `report-review.md` already says,
# written at advance-to-review into a slot the owner had no reason to open twice.


# --------------------------------------------------------------------------- cycle reports (renovation §3.1)
# ONE report per build⟷vet cycle: `artifacts/build-vet-<n>.md`, scaffolded from the build skill's
# template at cycle start. Strictly sequential writers — build fills §Built/§Validation, the vet
# pen APPENDS the §Verification check fence (record_verification above), the loop driver APPENDS
# §Cycle outcome, which CLOSES the cycle. The file is both the build→vet handover (vet reads
# §Built/§Validation instead of re-deriving from a raw diff) and the cycle narrative for review.

_CYCLE_FILE = re.compile(r"^build-vet-(\d+)\.md$")
_VET_REPORT_FILE = re.compile(r"^vet-report-(\d+)\.md$")   # legacy files — reader labeling only


_CYCLE_REVISION = re.compile(r"(?m)^plan_revision:\s*(r\d+)\s*$")


def cycle_reports(item_dir: Path) -> list[dict]:
    """All cycle reports in cycle order: [{cycle, path, revision}] — `revision` is the plan
    revision the report was scaffolded under (`''` for the original plan), which is what scopes the
    loop's guards to the current generation (§3-bis.4)."""
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
    """The newest cycle report {cycle, path, text, truncated} — the loop's handover payload for
    the next build cycle (capped: hermes build_worker_context precedent, §8·O10)."""
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
    """Scaffold the current OPEN cycle's report from the build skill's template (no-op when the
    open cycle's file already exists). The open cycle = the last file while its §Cycle outcome is
    empty, else last+1 (1 when none) — the driver's outcome append is what closes a cycle.
    Returns {cycle, path, created}."""
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
    # The plan revision this cycle implements (§2.1): `build-vet-3` under a rewritten design is
    # legible as such instead of reading like a third attempt at the same one. Empty for a plan
    # that was never revised.
    from .plan_revision import current_revision   # local: plan_revision imports this module
    rev = current_revision(item_dir)
    fm = (f"---\nartifact: build-vet\ncycle: {cycle}\nreader: agent\n"
          + (f"plan_revision: {rev}\n" if rev else "")
          + f"created_at: {date.today().isoformat()}\n---\n")
    # An HTML comment in a template is a note to whoever AUTHORS from it, not a line of the document
    # it produces — the same call `write_plan_user_report` / `write_vet_user_report` already make.
    # Instantiating one verbatim published scaffolding as content: the owner read "appended by vet's
    # recording tool — never hand-edit" as a paragraph of their own cycle report (2026-08-03). The
    # rule those two comments carried now lives where a rule belongs, in the build and vet skills.
    body = re.sub(r"[ \t]*<!--.*?-->\n?", "", skill_template("build-vet"), flags=re.DOTALL).format(
        cycle=cycle, title=title or Path(item_dir).name)
    _atomic_write(path, fm + body)
    return {"cycle": cycle, "path": str(path), "created": True}


def _append_to_section(path: Path, heading: str, entry: str, *, fence: str = "") -> None:
    """Append `entry` inside the `## {heading}` section of a cycle report — at the section's end,
    or (fence) inside its ```<fence> block, creating the block when missing. Line-based and atomic;
    raises ValueError when the section heading is absent (a hand-mangled file must fail loud, not
    scatter entries).

    The fence is NAMED rather than assumed: `## Verification` keeps its ```checks block, and
    `## Validation` gains a ```runs one for build's own machine records — same grammar, so one
    appender and one parser serve both, and a section can carry prose beside its machine lane."""
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
            # A BLANK LINE between entries (owner, 2026-08-08). Packed back to back, a fence of six
            # `### ts — name` records reads as one wall of text with no place for the eye to start.
            # The parser is line-oriented on `### ` heads and `- key: value` lines, so a blank line
            # is invisible to it — this is presentation only, and it costs one line per entry.
            has_entries = any(lines[k].strip() for k in range(opened + 1, close))
            lines[close:close] = ([""] if has_entries else []) + entry_lines
        else:
            lines[end:end] = ["", f"```{fence}", *entry_lines, "```"]
    else:
        lines[end:end] = ["", *entry_lines]
    _atomic_write(path, "\n".join(lines) + "\n")


# --- §Cycle outcome — the driver's trail (replaces the retired attempts.md ledger) --------------

_OUTCOME_HEAD = re.compile(r"^### (?P<ts>\S+) — (?P<decision>\S+)$", re.MULTILINE)


def append_cycle_outcome(item_dir: Path, *, evidence: str, decision: str, reason: str,
                         fingerprint: str = "", failed: list[str] | tuple = (),
                         tokens: int | None = None, budget: int | None = None,
                         loop_exit: str = "") -> dict | None:
    """Append one driver decision to the LATEST cycle report's §Cycle outcome (closing the cycle).
    `evidence` is the evidence_status verdict; `decision` what the driver did (review|build|revet|
    halt); `loop_exit` the TYPED loop exit when this decision ended the loop (converged | budget |
    not_converging | no_progress | system_fault) — the record a revision reads its `concerns` off
    (§3-bis.3), so the tag is never guessed; `fingerprint` the failure fingerprint (convergence-guard
    input); `tokens`/`budget` the meter reading. Returns None (nothing recorded) when no cycle report
    exists yet — the DB loop.decision event still carries the decision."""
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    reports = cycle_reports(item_dir)
    if not reports:
        return None
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    # Name the cycle this decision closed. It goes in a FIELD, never in the heading: the heading is
    # parsed (`^### <ts> — <decision>$`) and `read_cycle_outcomes` feeds the convergence and stale
    # guards off it, so decorating it turns "review" into "cycle 1 — review" and the loop's own
    # breakers stop recognising their decisions. The parser keeps taking `cycle` from the FILE,
    # which is authoritative; this line exists so the owner can read which loop pass each of three
    # stacked outcome entries ended without counting `###` blocks.
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
    """Every driver decision across the cycle reports, in order: [{ts, cycle, decision, evidence,
    reason, exit?, fingerprint?, failed?, tokens?}]. The convergence guard reads the last entry's
    fingerprint; the stale guard the last entry's decision.

    `revision` scopes the read to ONE generation (§3-bis.4): pass the plan's current revision and
    only cycles scaffolded under it count, so three identical failures recorded before a redesign
    no longer trip the recurrence guard after it. `None` reads the item's whole life."""
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


def _how_checked(c: dict) -> str:
    """One check → how the owner will know it held, in their words. Derived, never asserted: the
    plan already fixed the mode and whether a `run:` block makes it kernel-executed, and the owner
    is entitled to know which rows are machine-decided BEFORE they approve the exam."""
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
    """A prose slot's body, with the section heading stripped when the author repeated it.

    The template owns the headings and the agent owns the words, but an agent naturally opens a
    section with its own title and the render then carries it twice. Structure is code's to own, so
    code drops the echo instead of asking every author to remember."""
    body = (text or "").strip()
    first, _, rest = body.partition("\n")
    if first.startswith("#") and first.lstrip("#").strip().lower() == heading.strip().lower():
        return rest.strip()
    return body


_LENS_LINE = re.compile(r"(?mi)^(\s*[-*]\s+)(" + "|".join(LENSES) + r")(\s*:)")


def _bold_lenses(text: str) -> str:
    """Bold the lens name that OPENS a `## What else was looked at` bullet.

    The lens vocabulary is code's, not prose: each bullet is one standing reading, and the name is its
    label. Bolding here rather than in CSS keeps ONE rule for what a label looks like."""
    return _LENS_LINE.sub(lambda m: f"{m.group(1)}**{m.group(2)}{m.group(3).strip()}**", text)


def write_plan_user_report(item_dir: Path, *, summary: str, approach: str = "",
                           confirm: str = "", decisions: str = "", assumptions: str = "",
                           item_kind: str | None = None) -> dict:
    """Write the owner's answer to *what is being built, and what will prove it*.

    The prose slots are the planner's; everything factual is DERIVED from plan.md, because a
    hand-copied claim is a claim ABOUT the plan rather than a reading of it, and the gap is the one
    thing this report exists to make visible.

    The centrepiece is the confirmation table: each check's `proves:` line verbatim, and how the
    owner will know. A task nothing defends is named under the table, because a hole is worth seeing
    at the gate rather than three cycles later.

    Refuses on a plan with no tasks — an empty table would read as "nothing needs proving"."""
    # An OMITTED optional slot arrives as None, not "" — the tool layer's `_s` returns None for an
    # absent arg, and this report's own contract tells the planner to omit `decisions`/`assumptions`
    # when there were none. Taking the parameter at its type and calling `.strip()` on it turned
    # doing-as-told into `'NoneType' object has no attribute 'strip'`, an error naming neither the
    # field nor the tool (live, 2026-08-07). Normalize once, here, where the type is declared.
    approach, confirm = approach or "", confirm or ""
    decisions, assumptions = decisions or "", assumptions or ""
    item_dir = Path(item_dir)
    plan_path = item_dir / "artifacts" / artifact_file("plan")
    plan = plan_path.read_text() if plan_path.is_file() else ""
    vp = parse_vet_plan(plan)
    tasks = parse_tasks(plan)
    research = get_profile(item_kind).kind == "research"
    if not tasks:
        raise ValueError("plan.md declares no `## Tasks` — scaffold and fill the plan first; a "
                         "report over nothing would read as 'nothing needs proving'")
    # One row per CHECK, not per task: the owner is approving an exam, and the exam's unit is the
    # check. Task coverage is still computed — it just surfaces as the named gap below the table,
    # where a hole reads as a sentence instead of as an empty cell.
    # NOT clipped. `proves:` is one bounded sentence by contract, and it IS the row — clipping it
    # to keep the column tidy cut the meaning mid-word ("prints only the groceries entries, none
    # from other…") on the first real item to reach this table, which is the exact failure the field
    # was added to prevent. A markdown cell wraps; a truncated sentence does not recover.
    rows = [f"| {c.get('proves') or '—'} | {_how_checked(c)} |" for c in vp.get("checks", [])]
    # A research item declares no checks BY DESIGN, so every one of its tasks would read as a hole.
    # The gap call-out is about an implementation plan that forgot to defend something.
    uncovered = [] if research else [r["task"] for r in proof_rows(item_dir)
                                     if r["task"] and not r["verified"]]
    gap_text = ", ".join(
        clip(t["text"], 60) for t in tasks if t["id"] in set(uncovered))
    gaps = (f"\n\n**Nothing will prove:** {gap_text} — either a check is missing, or that work "
            "genuinely needs no proof and the gate is where to say so." if uncovered else "")
    # Assembled here rather than as separate template slots, so a block with nothing real in it
    # leaves no blank line behind — the reader-side hygiene the other reports already have.
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
    _atomic_write(path, body)
    return {"path": str(path), "tasks": len(tasks), "checks": len(vp.get("checks", [])),
            "uncovered": uncovered}


def write_vet_user_report(item_dir: Path, repo_dir: Path | None, *, summary: str = "",
                          confirms: str = "", looked_at: str = "", unknown: str = "") -> dict:
    """Write the vet report. HYBRID: vet writes the narrative, code writes `## What didn't hold`
    off the evidence ledger and owns the refusals.

    Vet is not suspected of lying — it runs the checks. What code guarantees is narrower:

    - ONE-WRITER with the ledger. The driver decides on the recorded entries, so a failure reaches
      the owner whatever the prose says. Vet cannot omit a red check by writing around it.
    - COMPLETENESS. No report while a plan check has no entry, a standing lens has no read, or a
      failing check has no diagnosis. The last is the diagnosis duty's teeth: a cycle that says
      "3 failing" and nothing about WHERE sends the next build hunting.

    The per-check table is gone: the Task tab carries evidence per check, and a table here made
    build's self-report and vet's independent pass read as the same list."""
    item_dir = Path(item_dir)
    plan_path = item_dir / "artifacts" / artifact_file("plan")
    plan_ids = [c["id"] for c in parse_vet_plan(plan_path.read_text()).get("checks", [])] \
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
    # The lenses run on EVERY cycle, including one whose plan declared `depth: none` — depth governs
    # what is executed, not whether the work is read. This refusal is why "there was nothing to run"
    # can no longer produce a cycle with nothing on the record.
    if (missing := missing_lenses(item_dir)):
        raise ValueError("; ".join(
            f"the {ln} lens has no read this cycle — call record_lens with what you probed (no "
            "findings is a fine answer, and saying what you probed is what makes it one)"
            for ln in missing))
    # The diagnosis duty has its teeth here (design §5). A cycle that reports "3 checks failing"
    # and nothing about WHERE sends the next build cycle hunting, which is the tax this whole
    # stage exists to remove — and the report is the last moment anyone can still be asked.
    if (undiag := undiagnosed_failures(item_dir)):
        raise ValueError("; ".join(
            f"check {c!r} is failing with no diagnosis this cycle — call record_diagnosis with "
            "`where` it broke and `why`, so the next build cycle starts at the cause instead of "
            "the symptom (never the fix: that is build's to reason out)" for c in undiag))
    ev = evidence_status(item_dir, repo_dir)
    checks = plan_ids + [c for c in by_check if c not in plan_ids]
    deferred_auth = {a["check"] for a in pending_authorizations(item_dir) if a.get("check")}

    # Each check's `proves:` — what a green MEANS, in the owner's terms. The machine block leads
    # a failure with it, so a red row says what STOPPED being true rather than naming a check id
    # the owner has no memory of.
    proves_of = {c["id"]: str(c.get("proves") or "")
                 for c in (parse_vet_plan(plan_path.read_text()).get("checks", [])
                           if plan_path.is_file() else [])}
    failed = [c for c in checks
              if (h := by_check.get(c)) and not h[-1].get("passed") and not h[-1].get("deferred")]
    deferred_all = sorted(deferred_auth | {c for c, h in by_check.items()
                                           if h and h[-1].get("deferred")})

    # --- the machine block: everything vet must not be able to write around ----------------------
    # `## What didn't hold` is authored HERE, off the ledger, so a red check reaches the owner
    # whatever the Summary says. It leads with what stopped being true and follows with the
    # recorded diagnosis — where it broke and why — because "what do I look at" is the reader's
    # actual question and the `where` alone answers it.
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
    # A lens finding that GATES belongs here for the same reason a failed check does: it is what
    # sends the item back, and the owner should not have to take vet's word that it was mentioned.
    for g in lens_gaps(item_dir):
        lines.append(f"- **{g['text']}** — raised by the {g['lens']} reading ({g['severity']}).")
    # A build validation claim the kernel could not reproduce. Machine-authored for the same reason
    # as everything else here: it is a finding ABOUT the phase writing this report's neighbour, and
    # the one record that must not depend on anyone choosing to mention it.
    for a in validation_discrepancies(item_dir, cycle=(cycle_reports(item_dir) or [{}])[-1].get("cycle")):
        lines.append(
            f"- **The build reported `{a['command']}` as "
            f"{'passing' if a['claimed'] else 'failing'}, and re-running it here "
            f"{'passes' if a['actual'] else 'does not'}** — its own validation does not reproduce. "
            f"({a['result']})")
    machine = ("## What didn't hold\n" + "\n".join(lines) + "\n\n") if lines else ""
    # A `depth: none` item still gets a reading, and that reading can still GATE — so this note
    # PRECEDES the didn't-hold block, it does not replace it. It replaced it for about ten minutes,
    # and in that state a high robustness finding on a no-checks item reached nobody.
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
    _atomic_write(path, body)
    return {"path": str(path), "verdict": ev.get("status", ""), "failed": failed}


# --------------------------------------------------------------------------- PR review notes
# What the PR page shows BESIDE each task's own commits (owner, 2026-08-09).
#
# NOT A DOCUMENT, and not a second opinion on the merge. The review report answers *should this
# land* — what to push back on, how much to trust it, what it leaves behind — and repeating any of
# that here would give the owner two panes saying the same sentences. These notes answer a different
# question, the one you have while looking at a diff: *what do I need to know about THIS task?*
#
# PER TASK, never per file. The walkthrough is already grouped by task, so a note travels with the
# commits it describes; a file column would only restate what is on screen. Two tasks touching one
# module get two notes, which is exactly what a file-keyed view cannot do.
#
# WRITTEN BY BUILD, not review. Build wrote the code and is the only phase that knows what a reader
# should look at and where it left the plan; review, by the time it writes, is summarizing artifacts
# it did not produce. Everything else is derived — the requirement from the check's `proves:`, the
# proof from the ledger. LATEST CYCLE WINS: a note cycle 1 wrote about a line cycle 3 rewrote is
# worse than no note at all.

def delivered_line(item_dir: Path) -> str:
    """`artifacts/review.md`'s **Delivered** field — what actually shipped, as one line.

    Read by the landing commit's body. Reads the whole PARAGRAPH, not the first physical line: the
    file is hand-written prose wrapped for reading, so a two-sentence Delivered routinely spans
    three lines."""
    path = Path(item_dir) / "artifacts" / "review.md"
    try:
        if not path.is_file():
            return ""
        parts: list[str] = []
        for line in path.read_text().splitlines():
            if not parts:
                if line.strip().startswith("**Delivered:**"):
                    parts.append(line.split("**Delivered:**", 1)[1].strip())
                continue
            # The field ends where its paragraph does — a blank line, or the next bold field for a
            # report whose author forgot the blank.
            if not line.strip() or line.strip().startswith("**"):
                break
            parts.append(line.strip())
        return " ".join(p for p in parts if p).strip()
    except OSError:
        log.warning("delivered line: could not read %s", path)
    return ""


# `- t1 — look: … · deviated: …`. One line, and the whole grammar build has to hold. A `look` or
# `deviated` reading `none` is a real answer, not a missing one, and renders as nothing.
_NOTE = re.compile(r"^-\s*(?P<task>t\d+)\s*[—-]\s*(?P<body>.+)$")
_NONE = {"none", "none.", "n/a", "-", "—"}


def _bullets(body: str) -> list[str]:
    """A section's `- ` bullets, each folded back into ONE line with its indented continuations.
    Markdown wraps for reading; the grammar reads a bullet, not a physical line."""
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
    """`look: … · deviated: …` → its labelled parts. Split on the separator FIRST and the label
    second, so a `·` in prose cannot start a phantom field.

    A value whose FIRST SENTENCE is `none` is nothing, however much follows: build wrote "none" and
    then justified it, which put a restatement of the diff under a heading promising what the diff
    cannot show. The declaration is the answer; the justification is for the record."""
    out: dict = {}
    for part in re.split(r"\s+·\s+", body):
        if m := re.match(r"^(look|deviated)\s*:\s*(.*)$", part.strip(), re.I):
            val = m.group(2).strip()
            head = re.split(r"[.;]", val, maxsplit=1)[0].strip().lower()
            out[m.group(1).lower()] = "" if (val.lower() in _NONE or head in _NONE) else val
    return out


def pr_task_notes(item_dir: Path) -> dict:
    """`{task_id: {look, deviated, cycle}}` from the cycle reports' `## For the reviewer`.

    Oldest cycle first so the newest overwrites: a task rebuilt in cycle 3 carries cycle 3's note,
    and the superseded one is left in its own cycle report where it belongs."""
    notes: dict[str, dict] = {}
    for r in cycle_reports(item_dir):
        try:
            section = _split_sections(Path(r["path"]).read_text()).get("For the reviewer", "")
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
    """Everything the PR page shows per task.

    `needed` is the covering check's `proves:` line — what must be TRUE, stated for a person. Never
    the plan's task spec: that is build instructions, and handing it to a reviewer is giving them the
    recipe when they asked what the dish is.

    `checks` is the proof, joined at read time, never re-transcribed."""
    out: dict[str, dict] = {}
    notes = pr_task_notes(item_dir)
    plan_path = Path(item_dir) / "artifacts" / artifact_file("plan")
    # How many tasks each check defends. A check covering `t1, t2` states something true of BOTH,
    # so it is a poor answer to "what did THIS task have to make true" — on the first live run t2
    # ("wire the subcommand") showed the month-filter requirement, purely because that shared check
    # was declared first in the plan. Prefer a check that covers this task ALONE.
    breadth: dict[str, int] = {}
    if plan_path.is_file():
        for c in parse_vet_plan(plan_path.read_text()).get("checks", []):
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


# --------------------------------------------------------------------------- convergence guard (build-vet-loop §5)
# Convergence-fingerprint normalization (§8·O3 resolved): the signature is the ledger's latest
# `result` line per failed check — model-authored one-line failure prose. Normalize it so
# incidental variation (case, punctuation, timestamps, addresses, durations) doesn't hide a
# no-progress cycle: lowercase → drop hex runs (addresses/shas) → drop every digit-run of ≥2
# digits (timestamp/date fragments, ports, ids; single digits like "exit 1" / "2 failed" stay —
# they're signal) → strip non-alphanumerics → collapse whitespace. Deliberately biased toward
# STABILITY over granularity: a fingerprint that flickers on a timestamp defeats the guard, while
# one that misses "13 passed → 14 passed" only delays escalation by a cycle.
_SIG_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{7,40}\b")
_SIG_NUM = re.compile(r"\d{2,}")
_SIG_JUNK = re.compile(r"[^a-z0-9 ]+")


def _normalize_signature(s: str) -> str:
    s = _SIG_HEX.sub("", (s or "").lower())
    s = _SIG_NUM.sub("", s)
    s = _SIG_JUNK.sub(" ", s)
    return " ".join(s.split())


def convergence_fingerprint(item_dir: Path, *, extra: list[str] | None = None) -> str:
    """The current cycle's failure fingerprint: sha1 over the sorted (check, normalized latest
    failing result) pairs from the evidence ledger. Empty string when nothing is failing —
    an empty fingerprint never trips the guard.

    `extra` carries failure signatures that are not ledger checks — today the gating lens findings
    (design §3). They belong here for the same reason the checks do: a wall the loop keeps hitting
    should exit as `not_converging` rather than burn the whole budget rediscovering it."""
    latest: dict[str, dict] = {}
    for e in evidence_entries(item_dir):
        latest[e["check"]] = e
    failing = sorted((c, _normalize_signature(str(e.get("result") or "")))
                     for c, e in latest.items() if not e.get("passed"))
    failing += sorted(("lens", _normalize_signature(t)) for t in (extra or []) if t)
    if not failing:
        return ""
    return hashlib.sha1("\n".join(f"{c}|{sig}" for c, sig in failing).encode()).hexdigest()[:12]


# --------------------------------------------------------------------------- checkpoints

def write_checkpoint(item_dir: Path, repo_dir: Path | None, *, working_on: str, decisions: str,
                     remaining: str, notes: str = "", role: str | None = None) -> str:
    """Bank one continuity checkpoint. APPEND-ONLY and atomic — a new timestamped file every
    time, and the filename IS the canonical order. Reference artifacts BY PATH, never duplicate them.

    `role` is the SESSION ROLE that banked it. An item's three threads all bank into one folder, so
    without the stamp "the latest checkpoint" is whichever wrote last. Harmless for item-state
    readers, WRONG for continuity: handing a compacted intake thread the build thread's checkpoint
    tells it "this is what you were doing" about work it never did."""
    if not (working_on.strip() and remaining.strip()):
        raise ValueError("a checkpoint needs at least working_on and remaining")
    cdir = Path(item_dir) / "checkpoints"
    cdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = cdir / f"{ts}.md"
    n = 1
    while path.exists():            # same-second collision → suffix, never overwrite
        path = cdir / f"{ts}-{n}.md"
        n += 1
    git_line = "(no git state)"
    if repo_dir and Path(repo_dir).is_dir():
        try:
            r = subprocess.run(["git", "log", "-1", "--format=%h %s", "HEAD"], cwd=repo_dir,
                               capture_output=True, text=True, timeout=10)
            b = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir,
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                git_line = f"{b.stdout.strip()} @ {r.stdout.strip()}"
        except (OSError, subprocess.SubprocessError):
            pass
    text = (f"---\ncheckpoint: {ts}\ngit: {git_line}\nreader: agent\n"
            + (f"role: {role}\n" if role else "")
            + f"---\n"
            f"## Working on\n{working_on.strip()}\n\n"
            f"## Decisions\n{(decisions or '').strip() or '—'}\n\n"
            f"## Remaining\n{remaining.strip()}\n\n"
            f"## Notes\n{(notes or '').strip() or '—'}\n")
    _atomic_write(path, text)
    return str(path)


def checkpoint_feed(item_dir: Path, *, limit: int = 30) -> list[dict]:
    """The drilldown's continuity feed (S7): newest-first checkpoint stubs [{ts, path, headline,
    git}] — headline = the first content line (what the session was working on), git = the
    frontmatter git-state line. Full text stays behind the path (one click deeper)."""
    cdir = Path(item_dir) / "checkpoints"
    if not cdir.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(cdir.glob("*.md"), key=lambda p: p.stem, reverse=True)[:limit]:
        text = p.read_text()
        git = None
        m = re.search(r"(?m)^git: (.+)$", text)
        if m:
            git = m.group(1).strip()
        body = re.sub(r"(?s)\A---\n.*?\n---\n", "", text)
        headline = next((ln.strip() for ln in body.splitlines()
                         if ln.strip() and not ln.startswith("#")), "")
        out.append({"ts": p.stem, "path": str(p), "headline": headline[:200], "git": git})
    return out


def latest_checkpoint(item_dir: Path, *, char_cap: int = 6000,
                      role: str | None = None) -> dict | None:
    """The newest checkpoint (by filename — canonical order), char-capped. None when none exist.

    Two different questions, one function:
    - **"What is this ITEM's latest state?"** — `role=None`, newest from any thread. What the
      item-state readers want.
    - **"What was THIS THREAD doing before it lost its memory?"** — `role='intake'|'build'|'vet'`,
      the continuity read. Restricts to checkpoints that thread banked, PLUS unstamped ones
      (written before the stamp existed — role-agnostic by definition, and dropping them would
      blind every pre-existing item).
    """
    cdir = Path(item_dir) / "checkpoints"
    if not cdir.is_dir():
        return None
    # Sort by STEM, not filename: a same-second collision file `<ts>-1` must sort AFTER `<ts>`,
    # but with the `.md` suffix attached '-' < '.' would order it before.
    files = sorted(cdir.glob("*.md"), key=lambda p: p.stem)
    if not files:
        return None
    if role:
        # `role` is now a per-phase SLOT. Two stamps still match besides the exact one: an UNSTAMPED
        # checkpoint (written before the stamp existed — role-agnostic by definition), and a legacy
        # `intake` stamp from before sessions went per-phase, which any intake phase may claim. Both
        # are widenings, never a narrowing: dropping them would blind an in-flight item to the
        # continuity it actually banked.
        from .kind_profiles import INTAKE_PHASES, LEGACY_INTAKE_SLOT
        wants = [f"\nrole: {role}\n"]
        if role in INTAKE_PHASES:
            wants.append(f"\nrole: {LEGACY_INTAKE_SLOT}\n")
        files = [p for p in files
                 if (t := p.read_text())
                 and (any(w in t for w in wants) or "\nrole: " not in t)]
        if not files:
            return None
    text = files[-1].read_text()
    return {"path": str(files[-1]), "text": text[:char_cap],
            "truncated": len(text) > char_cap}


# --- session memory: the non-work-item thread's checkpoint (compaction-redesign §13.4 / T5) ---
#
# A general (unbound) session has no item folder, no phase and no artifacts — so it has NO disk
# copy of anything, and a compaction there loses the conversation outright. `session-memory/` is
# its equivalent of `checkpoints/`, with two deliberate differences:
#   - ONE file per session, overwritten. A work-item's checkpoints are append-only because three
#     threads share the folder and the item's HISTORY is a surface (the drilldown feed). A session
#     has one thread and no history surface; the only question ever asked is "what did this thread
#     know before it lost its memory", so keeping older revisions would just be growth.
#   - Mode-scoped root (`<internal_root>/dev` or `/core`), passed in by the caller — a general
#     session can be either, and the knowledge tree is split that way already (owner, 2026-07-28).
#   - There is no WRITER here. A work-item checkpoint goes through `write_checkpoint` because the
#     agent reaches it as an MCP tool; a general session has no item tools, so the `checkpoint`
#     skill writes this file directly and the four `## ` headings are the whole format. A
#     kernel-side writer would have no caller — the derived fallback that justifies one for
#     work-items cannot exist here, because a general session has no artifacts to derive from.
# The spine holds NO pointer to this file: the path is `<root>/session-memory/<session-id>.md`,
# fully derivable from the session id, and "is one owed" is already derivable from the run table
# (`spine.session_compacted_pending`). Adding a column would store what we can compute.

def session_memory_path(root_dir: Path, session_id: str) -> Path:
    """Where this session's memory lives. `root_dir` is the MODE root (`…/dev` or `…/core`)."""
    return Path(root_dir) / "session-memory" / f"{session_id}.md"


def read_session_memory(root_dir: Path, session_id: str, *,
                        char_cap: int = 6000) -> dict | None:
    """This session's banked memory, char-capped. None when it has never banked one."""
    path = session_memory_path(root_dir, session_id)
    if not path.is_file():
        return None
    text = path.read_text()
    return {"path": str(path), "text": text[:char_cap], "truncated": len(text) > char_cap}


def artifact_status(item: dict, item_dir: Path, repo_dir: Path | None = None) -> dict:
    """The COMPUTED per-artifact status map (D6 §4 — derived from file existence + self-check +
    evidence freshness; never stored in any doc): {kind → {required, present, issues, status}}.
    The `plan` row additionally carries the evidence verdict. Feeds the S7 drilldown."""
    profile = get_profile(item.get("kind"))
    out: dict[str, dict] = {}
    for kind in ARTIFACT_KINDS:
        if kind == "handoff-brief":
            continue  # lives in preliminary/ (S3), not artifacts/
        present = (Path(item_dir) / "artifacts" / artifact_file(kind)).exists()
        row: dict = {"required": kind in profile.required_artifacts, "present": present}
        if present:
            issues = self_check(item_dir, kind, item_kind=profile.kind)
            row["issues"] = issues
            row["status"] = "ok" if not issues else "incomplete"
        else:
            row["status"] = "missing"
        # The derived check verdict rides the `plan` row — the plan owns the vet checks, and the
        # entries proving them live in the cycle reports' §Verification fences.
        if kind == "plan" and cycle_reports(item_dir):
            row["evidence"] = evidence_status(item_dir, repo_dir)
        out[kind] = row
    return out
