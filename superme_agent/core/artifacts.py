"""Artifact machinery — one template plus a deterministic scaffolder per kind.

THE STANDARD: agent supplies content, code supplies form. Code owns frontmatter, section order,
ids and timestamps; the agent fills `<fill:…>` slots. A self-check runs at the CONSUMING gate.
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
# An artifact's own frontmatter, and the family stamp inside it. `self_check` judges a file
# against its own template.
_FM_BLOCK = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_FM_RESEARCH_KIND = re.compile(r"(?m)^research_kind:\s*(\S+)\s*$")


# The template FILE is the single source. A `<fill:…>` slot must be FILLED; a comment-only section
# must merely EXIST.

_TEMPLATE_HOMES = {
    "brief":         ("triage", "brief-template.md"),
    "plan":          ("plan", "plan-template.md"),
    "plan-research": ("plan", "plan-research-template.md"),
    "build-vet":     ("build", "build-vet-template.md"),
    # The UNJUDGED shape — a research item whose family nobody named. Adding a section here would
    # retro-fail correct records.
    "investigation": ("investigate", "investigation-template.md"),
    # One shape per family: each answers a different question, so each owes a different record.
    # Read from the REGISTRY.
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
    """The template body for `name`, from its authoring skill's `templates/`. Cached for
    the process lifetime."""
    if name not in _template_cache:
        from ..paths import DEV_PLUGIN_DIR
        skill, fname = _TEMPLATE_HOMES[name]
        _template_cache[name] = (
            DEV_PLUGIN_DIR / "skills" / skill / "templates" / fname).read_text()
    return _template_cache[name]


def template_section_spec(name: str) -> list[tuple[str, bool]]:
    """[(heading, must_be_filled)] per `## ` heading — the template IS the
    required-sections list. Fill detection reads each whole body, since a slot can wrap."""
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

# kind → (template, required sections). `handoff-brief` sections are ALL optional — capture
# friction kills itemizing.
_SPECS: dict[str, dict] = {
    "brief":       {"file": "brief.md",      "required": (), "reader": "agent"},  # derived from template
    "plan":        {"file": "plan.md",       "required": (), "reader": "both"},  # per item-kind, resolved below
    # The research work-segment record — agent-facing, the counterpart of build-vet-<n>.md.
    # Sections derived from its template file.
    "investigation": {"file": "investigation.md", "required": (), "reader": "agent"},
    # Review's own agent-facing record. This holds the record; the report holds the judgment.
    "review":        {"file": "review.md",        "required": (), "reader": "agent"},
    "handoff-brief": {"file": "handoff-brief.md", "required": (), "reader": "agent"},
}
# Legacy plan shapes, READ-ONLY: a plan is judged against the shape it was authored under.
_PLAN_REQUIRED_LEGACY = ("Approach", "Tasks", "Validation criteria")
_PLAN_FEED_SECTIONS = ("Touches", "Behavior preview", "Risks & assumptions")
_PLAN_REQUIRED_V1 = ("Approach", "Tasks", "Inner checks", "Vet plan")
_PLAN_REQUIRED_V2 = ("Approach", "Touches", "Behavior preview", "Tasks",
                     "Risks & assumptions", "Inner checks", "Vet plan")
_PLAN_REQUIRED_RESEARCH_V1 = ("Questions", "Method", "Boundaries", "Done criteria", "Tasks")
ARTIFACT_KINDS = tuple(_SPECS)


def _template_name(artifact: str, item_kind: str | None,
                   research_kind: str | None = None) -> str | None:
    """The skill-template name for a template-backed kind, else None. The family slug IS
    the mapping; an unjudged item gets the base."""
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


# The `reader:` LABEL — who each artifact is designed for. A label, never a constraint.
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
    """Deterministically scaffold one artifact skeleton. NEVER overwrites — filling happens by
    editing. Unknown kinds fail loud.

    The KERNEL attaches the repo's `standing` checks: a copied-by-hand entry is one rewording from a
    different check."""
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
    # `research_kind:` is stamped so the file carries the shape it was authored under. Re-
    # classifying mid-flight cannot turn it red.
    fm = (f"---\nartifact: {artifact}\n"
          + (f"item: {item_id}\n" if item_id else "")
          + f"item_kind: {item_kind}\n"
          + (f"research_kind: {research_kind}\n" if research_kind else "")
          + f"reader: {_SPECS[artifact]['reader']}\n"
          + f"created_at: {date.today().isoformat()}\n---\n")
    tmpl = _template(artifact, item_kind, research_kind)
    heading = title or (item_id or "work-item")
    # The family is named ONCE in the heading, so a naive render doubles it. Read it off the
    # template.
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
            # CONCATENATE a repeated heading: the writer appends to the FIRST, so a reader of the
            # LAST sees nothing.
            out.setdefault(cur, "")
        elif cur is not None:
            out[cur] += line + "\n"
    return out


def _section_filled(body: str) -> bool:
    """Non-empty after dropping fill markers, html comments, and blank lines."""
    cleaned = FILL.sub("", re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL))
    return bool(cleaned.strip())


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

VET_DEPTHS = ("none", "checks", "scenarios")
VET_MODES = ("command", "interaction", "inspection")
_VET_CHECK_ID = re.compile(r"^[a-z0-9-]+$")
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


def _vet_value(raw: str) -> str:
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
            cur = {"id": _vet_value(h.group(1)), "proves": "", "traces": "", "covers": "",
                   "mode": "", "scenario": "", "run": "", "expect": "", "rubric": [], "source": ""}
            out.append(cur)
            continue
        if cur is None:
            continue
        m = _VET_FIELD.match(line.strip())
        if m:
            # `rubric:` holds a LIST — indented bullets, each judged and recorded separately.
            # Everything else is one value.
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
            # A wrapped field — markdown folds it, so we do too. `expect:` is exactly the field
            # long enough to wrap.
            cur[last] = (cur[last] + " " + line.strip()).strip()
    return out


def parse_vet_plan(plan_text: str) -> dict:
    """Parse plan.md's `## Verification plan` → {present, depth, reason, env, checks}.
    Pure text → data; validity is judged separately."""
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
        if not _VET_CHECK_ID.match(cid or ""):
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


def clip(text: str, limit: int) -> str:
    """Trim to `limit` at a WORD boundary, with an ellipsis when anything was cut. A hard slice
    reads as a rendering bug."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0]
    return (head if len(head) >= limit // 2 else text[:limit]).rstrip(" ,;:·-") + "…"


_VET_DEPTH_RANK = {"none": 0, "checks": 1, "scenarios": 2}


def parse_inner_checks(plan_text: str) -> list[str]:
    """plan.md's `## Inner checks` bullets → the commands build must run green before
    it may exit."""
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

# The three plan sections whose STRUCTURED content feeds the gate's answer forms. Absent sections
# return empty.

TOUCH_ACTIONS = ("new", "modify", "read")
_FENCE = re.compile(r"^```[\w-]*\s*$")


def _fenced_blocks(body: str, *, lang: str = "") -> list[str]:
    """The contents of every ``` fenced block in a section body. `lang` keeps only blocks
    opened with that tag."""
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
    """plan.md's `## Touches` fenced yaml → [{component, path, action}]. Tolerant: anything
    unparseable gives []."""
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
    """Gate-blocking structure for a filled `## Touches`: the yaml must parse into ≥1
    complete row with a legal action."""
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
    """plan.md's `## Tasks` → [{id, done, text, detail}], in order. The id is what build's
    commit trailers carry.

    A task is a BLOCK of two parts: `text` is the NAME the board shows, `detail` the indented spec
    under it."""
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
    """plan.md's `## Decisions & clarifications` ledger: one entry per answered question,
    append-only with owner provenance. The deputy never re-litigates one."""
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
    """Put a blank line before every `**Label:**` block that lacks one — markdown folds two
    label lines into one paragraph."""
    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and _LABEL_LINE.match(line) and out and out[-1].strip():
            out.append("")
        out.append(line)
    return "\n".join(out)


# A value meaning the author had nothing to say. Every template says DELETE the block instead.
_DEAD_VALUES = {"", "none", "none.", "n/a", "na", "-", "—", "nothing", "(none)",
                "(first run — n/a)", "(first run - n/a)", "first run — n/a", "first run - n/a"}
_HEADING = re.compile(r"^#{1,6}\s")


def _dead_label(lines: list[str], i: int) -> bool:
    """Is the `**Label:**` at `lines[i]` a block with nothing under it? The next NON-BLANK
    line decides."""
    if lines[i].split(":**", 1)[1].strip().lower() not in _DEAD_VALUES:
        return False
    for nxt in lines[i + 1:]:
        if not nxt.strip():
            continue
        return bool(_LABEL_LINE.match(nxt) or _HEADING.match(nxt))
    return True


def _live_body(lines: list[str]) -> bool:
    """Does a section body hold anything a reader would want? Blank lines, comments and empty
    labels read as nothing."""
    text = re.sub(r"<!--.*?-->", "", "\n".join(lines), flags=re.DOTALL)
    body = text.split("\n")
    return any(ln.strip() and not (_LABEL_LINE.match(ln) and _dead_label(body, k))
               for k, ln in enumerate(body))


def _drop_dead_blocks(text: str) -> str:
    """Delete `**Label:** none` blocks on the READ path — lines that exist only to say
    nothing. Deliberately literal."""
    lines, out = text.split("\n"), []
    i, fenced = 0, False
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and _LABEL_LINE.match(line):
            # Only a same-line value can be dead, and only if the block ends right there.
            if _dead_label(lines, i):
                i += 2                      # drop the label line and its trailing blank
                while out and not out[-1].strip():
                    out.pop()               # …and the blank that preceded it
                out.append("")
                continue
        if not fenced and _HEADING.match(line) and "changed since" in line.lower():
            body = [ln for ln in lines[i + 1:] if not _HEADING.match(ln)]
            joined = "\n".join(body).strip().strip("()").strip().lower()
            # "first run" has a family of phrasings that all mean the same nothing, so it gets a
            # prefix match.
            if joined in _DEAD_VALUES or joined.startswith("first run"):
                break                       # nothing after it but the dead section
        if not fenced and _HEADING.match(line):
            # A bare heading reads as a section that failed to render, so it goes.
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
    """The item's records written since `since`, newest first. Reads MTIMES: every writer
    moves one, and no ledger stays honest."""
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
    """A phase's user-facing report. `contract` points at the full agent-facing artifact,
    read on demand and never pasted in."""
    path = Path(item_dir) / "reports" / f"report-{phase}.md"
    if not path.is_file():
        return None
    contract = {"triage": "artifacts/brief.md", "plan": "artifacts/plan.md",
                "investigate": "artifacts/investigation.md",
                # Review has a record its report could not reach. Close is still None: its report
                # IS the record.
                "review": "artifacts/review.md"}.get(phase)
    if phase in ("build", "vet"):
        # The cycle the report covers is the newest one — build and vet both project the same file.
        reports = cycle_reports(item_dir)
        contract = f"artifacts/{Path(reports[-1]['path']).name}" if reports else None
    # A link to a missing file is worse than none: it renders, the owner clicks, the doc view
    # 404s.
    if contract and not (Path(item_dir) / contract).is_file():
        contract = None
    try:
        st = path.stat()
    except OSError:
        return None
    return {"phase": phase, "name": f"report-{phase}",
            "text": _drop_dead_blocks(_space_labels(path.read_text())),
            "path": str(path), "mtime": st.st_mtime, "contract": contract}


# A `**Label:** value` on ONE line — how every one-line fact in a report is written.
_LABEL_VALUE = re.compile(r"^\*\*(?P<label>[^*\n]+?):\*\*[^\S\n]*(?P<value>.*?)\s*$", re.M)


def label_values(text: str) -> dict[str, str]:
    """Every same-line `**Label:** value` → {label lowercased: value}. Same-line only: a list
    below a label is prose."""
    out: dict[str, str] = {}
    for m in _LABEL_VALUE.finditer(re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)):
        value = m.group("value").strip()
        if value and not FILL.search(value):
            out.setdefault(m.group("label").strip().lower(), value)
    return out


def report_summary(item_dir: Path, phase: str) -> str:
    """A phase report's `**Summary:**` line — one sentence, what that phase concluded.
    The Quick View card renders it alone."""
    path = Path(item_dir) / "reports" / f"report-{phase}.md"
    return label_values(path.read_text()).get("summary", "") if path.is_file() else ""


def triage_facts(item_dir: Path) -> dict:
    """What triage established, for the `About this work-item` card. Read from the OWNER's
    brief: this is their own framing."""
    path = Path(item_dir) / "reports" / "report-triage.md"
    if not path.is_file():
        return {"category": "", "background": "", "problem": ""}
    v = label_values(path.read_text())
    return {"category": v.get("category", ""), "background": v.get("background", ""),
            "problem": v.get("problem") or v.get("goal", "")}


def report_issues(item_dir: Path, name: str) -> list[str]:
    """Itemized issues on a user-facing report: present, and no template slot unfilled.
    A report is COPIED, not scaffolded."""
    path = Path(item_dir) / "reports" / f"{name}.md"
    if not path.is_file():
        return [f"reports/{name}.md does not exist — write it from its template"]
    text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL)
    left = sorted(set(FILL.findall(text)))
    if left:
        return [f"reports/{name}.md has unfilled slot(s): " + ", ".join(left[:6])]
    return []


# `[^\S\n]*`, not `\s*`: in MULTILINE `\s` matches newlines, so an empty line would capture the
# next heading.
_OWNER_DECISION = re.compile(r"^\*\*Owner's decision:\*\*[^\S\n]*(.+?)\s*$", re.M)


def owner_decision(item_dir: Path) -> str:
    """The itemization outcome `itemize` recorded into review.md. Empty means itemization
    never ran."""
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
    """A research review record's `## Proposed work` body — the work its findings imply.
    Empty when missing, unfilled, or saying nothing."""
    path = Path(item_dir) / "artifacts" / artifact_file("review")
    if not path.is_file():
        return ""
    text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL)
    body = _split_sections(text).get("Proposed work", "")
    if FILL.search(body) or not _live_body(body.splitlines()):
        return ""
    return " ".join(body.split())


# ----------------------------------------------------- research proposals (the typed `## Proposed work`)

# A research item's findings imply work, but research may not DECIDE — each proposal carries its
# ruling.
_PROPOSAL_FIELDS = {
    "Title": "title", "Kind": "kind", "Why now": "why_now", "Delivers": "delivers",
    "Default applied": "default_applied", "Question": "question",
    "Reserved because": "reserved_because", "Suggested": "suggested", "Answer": "answer",
    # The rule the answer establishes, if any. Written WITH `Answer`, never before it. Empty is
    # the normal case.
    "Rule": "rule",
    # The free-prose predecessor of `Question`, kept so an older line lands in its own key. It
    # gates nothing.
    "Depends-on": "legacy_depends_on",
}
_PROPOSAL_FIELD = re.compile(r"^\s*\*\*(" + "|".join(_PROPOSAL_FIELDS) + r"):\*\*\s*(.*)$")
# Closed set: an agent that must name which limb a question passes writes fewer questions.
RESERVED_REASONS = ("destructive", "expensive_to_reverse")
# `Becomes work` is a yes/no, and absent means yes — only a ruling that emptied a proposal must
# say so.
BECOMES_WORK = ("yes", "no")


def research_proposals(item_dir: Path) -> list[dict]:
    """`## Proposed work` → one dict per proposal. A block opens at `**Title:**`; a line
    matching no field header continues the one above."""
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
    """Split proposals into (files now, waits for the owner). Per-proposal: one open
    question must not hold settled siblings."""
    filed = [p for p in props if not proposal_is_withheld(p)]
    return filed, [p for p in props if proposal_is_withheld(p)]


def proposal_promotable(prop: dict) -> bool:
    """Does this ruling establish something a LATER reader can use? The test is `Rule`,
    not `Reserved because`.

    Those answer different questions: a one-off destructive act produces a one-off answer. The common
    case is EMPTY."""
    return bool(str(prop.get("rule") or "").strip()) and bool(str(prop.get("answer") or "").strip())


def proposal_becomes_work(prop: dict) -> bool:
    """Does this proposal still describe WORK once the owner ruled on it? A ruling that
    emptied it leaves nothing to plan.

    Absent reads as YES: the ordinary proposal is work, and only an emptied one declares otherwise."""
    return str(prop.get("becomes_work") or "yes").strip().lower() != "no"


def filed_and_settled(props: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the FILE-ABLE proposals into (become work, settled with nothing to do).
    The second list is reported, never filed."""
    filed, _ = filed_and_withheld(props)
    return ([p for p in filed if proposal_becomes_work(p)],
            [p for p in filed if not proposal_becomes_work(p)])


def research_proposal_issues(props: list[dict]) -> list[str]:
    """Structural faults in the proposal blocks, read at the review gate.
    A malformed ruling field is worse than none."""
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
                # "Reserved BECAUSE" invites a reason, so the message names where the prose
                # belongs, not just what is wrong.
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

# The one section the OWNER writes. It lives in the triage brief, which the plan phase cold-starts
# from.
FROM_YOU = "From you"
_OWNER_BLOCKS = (("references", "Useful imported references"), ("notes", "Verification notes"))
# The bold source and em-dash are optional, so an older section's free prose still reads as slots.
_OWNER_BULLET = re.compile(r"^\s*[-*]\s+(?:\*\*(?P<source>[^*]+?)\*\*\s*[—-]\s*)?(?P<rest>.+?)\s*$")


def _owner_blocks(body: str) -> dict[str, str]:
    """`## From you`'s body → {references, notes} as RAW text. Only the two headings are
    structural."""
    keyed = {label.lower(): key for key, label in _OWNER_BLOCKS}
    out: dict[str, list[str]] = {key: [] for key, _ in _OWNER_BLOCKS}
    cur: str | None = None
    for line in re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).splitlines():
        if _LABEL_LINE.match(line):
            name, _, rest = line.partition(":**")
            key = keyed.get(name.strip("*").strip().lower())
            # ONLY our two labels are structural: treating an owner's own bold as a delimiter
            # would swallow what they typed.
            if key:
                cur = key
                if rest.strip():
                    out[cur].append(rest.strip())
                continue
        if cur:
            out[cur].append(line)
    return {k: FILL.sub("", "\n".join(v)).strip() for k, v in out.items()}


def _owner_slots(raw: str, *, sourced: bool) -> list[dict]:
    """One block's raw text → its slots. A non-bullet line is still a slot, so older free text
    stays addressable."""
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
    """What the owner wrote into `reports/report-triage.md` § From you. `exists` says whether
    the triage brief is on disk."""
    path = Path(item_dir) / "reports" / "report-triage.md"
    if not path.is_file():
        return {"exists": False, "references": [], "notes": []}
    blocks = _owner_blocks(_split_sections(path.read_text()).get(FROM_YOU, ""))
    return {"exists": True,
            "references": _owner_slots(blocks["references"], sourced=True),
            "notes": _owner_slots(blocks["notes"], sourced=False)}


def _one_line(s: str) -> str:
    """A slot is one bullet, so it is one line — a pasted newline would split it into slots
    nobody added."""
    return " ".join(str(s or "").split())


# The owner's standing input, carried to EVERY phase: each intake phase has its own session, so
# words are otherwise lost.
_CARRY_CAP = 1200
_DECISIONS = "Decisions & clarifications"


def carry_owner_input(item_dir: Path, *, cap: int = _CARRY_CAP) -> str | None:
    """The owner's durable words as one preamble block, or None. Read-only and
    failure-tolerant: never breaks a turn."""
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
            # Comment-only bodies are the SCAFFOLD, not an answer — the template ships its
            # instructions inside `<!-- -->`.
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
    """The section, rebuilt whole. Both labels stay even when empty — they tell the owner
    the section is theirs."""
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
    """Replace `## From you` in the triage brief, leaving every other byte alone. The
    caller sends the WHOLE list."""
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
    """The gate-time validator: itemized issues, empty list means pass. Read-only. `path`
    overrides the default `artifacts/` location."""
    if artifact not in _SPECS:
        raise KeyError(f"unknown artifact kind {artifact!r} — known: {sorted(_SPECS)}")
    path = Path(path) if path else Path(item_dir) / "artifacts" / artifact_file(artifact)
    if not path.exists():
        return [f"{artifact_file(artifact)} does not exist — scaffold it first"]
    text = path.read_text()
    issues: list[str] = []
    fills = FILL.findall(text)
    # A leftover slot in a handoff-brief marks an unfilled optional section; every other kind must
    # clear its slots.
    if fills and artifact != "handoff-brief":
        issues.append(f"{len(fills)} unfilled <fill:…> slot(s) remain — fill or remove them")
    sections = _split_sections(text)
    # The shape the file was AUTHORED under, read from its own frontmatter — never the item's
    # current field.
    head = _FM_BLOCK.match(text)
    fam = _FM_RESEARCH_KIND.search(head.group(1)) if head else None
    spec = section_spec(artifact, item_kind, fam.group(1) if fam else None)
    is_impl_plan = (artifact == "plan"
                    and get_profile(item_kind).kind == "implementation")
    is_new_plan = artifact == "plan" and any(
        h in sections for h in ("Intent", "Verification plan", "Decisions & clarifications"))
    # Pre-renovation plans stay valid READ-ONLY, judged against the shape they were authored
    # under. Newest first.
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
    # The pre-main gate consumes plan.md, so a plan whose checks a fresh agent could not execute
    # is not gate-ready.
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

# THE OWNER MAY HAND-EDIT EXACTLY TWO ARTIFACTS: the brief and the plan. Both state INTENT, which
# is theirs to state.
OWNER_EDITABLE: tuple[str, ...] = ("brief", "plan")

_EDITED_LINE = re.compile(r"(?m)^edited_by_owner:.*\n?")


def owner_edited_at(text: str) -> str | None:
    """The `edited_by_owner` stamp, or None. Readers use it to know the document is not what
    the agent last wrote."""
    m = _FM_BLOCK.match(text or "")
    if not m:
        return None
    got = re.search(r"(?m)^edited_by_owner:\s*(\S+)\s*$", m.group(1))
    return got.group(1) if got else None


def owner_edit(item_dir: Path, artifact: str, text: str, *,
               item_kind: str | None = None) -> list[str]:
    """Replace an owner-editable artifact, stamping `edited_by_owner`. WRITES NOTHING when the
    text breaks the contract — the same validator the gate runs.

    The stamp is the point: an agent re-reading this plan is reading the OWNER's words."""
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
        # An edit that dropped the frontmatter gets it back: downstream readers key on `artifact:`
        # and `item_kind:`.
        head = _FM_BLOCK.match(path.read_text())
        keep = _EDITED_LINE.sub("", head.group(1)).rstrip() if head else f"artifact: {artifact}"
        body = f"---\n{keep}\nedited_by_owner: {stamp}\n---\n" + body.lstrip("\n")
    # Judge the CANDIDATE, never the file: validating after the write leaves a rejected version
    # readable meanwhile.
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
    """Render an inbox item's `handoff-brief.md`: code owns form, the caller supplies
    prose. An existing brief is APPENDED to."""
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
    """A cheap fingerprint of the repo's CODE STATE: HEAD sha plus `git diff HEAD` content.
    Any commit or tracked edit moves it.

    UNTRACKED files are excluded: test runs drop coverage files and logs, which staled green evidence.
    Non-git → 'no-git'."""
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
    """The ledger's check field IS its join key, so a glued key is a DIFFERENT key
    whose stale `failed` never gets superseded.

    Returns the exact id, or raises with a targeted hint. Empty `valid_ids` means record verbatim."""
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
    """Append one BUILD validation run to the cycle report's `## Validation` ```runs
    fence.

    A self-check written as prose cannot be checked: build is both runner and only witness. Recording
    the run as DATA is what lets vet audit the claim."""
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
    """Record the kernel's AUDIT of one build validation claim — what build said, and
    what the command does now.

    It lands in `## Verification` as `kind: audit`, so `evidence_entries` filters it out and it never
    counts as a check."""
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
    """Append one entry to the cycle report's `§Verification` check fence. Append-only,
    so 'verified' is derived.

    `by` is PROVENANCE: `machine` beats `agent` and is FINAL for the cycle. A missed rubric criterion
    FAILS the check; `deferred` is neither pass nor fail."""
    # Single-line coerce: the ledger is line-oriented, so an embedded newline corrupts every entry
    # after it.
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    check, how, result, note = _one_line(check), _one_line(how), _one_line(result), _one_line(note)
    if not (check and how and result):
        raise ValueError("evidence needs non-empty check, how, and result")
    # Single source of truth for check state: the ledger key MUST be a plan check id (when one exists).
    plan_path = Path(item_dir) / "artifacts" / artifact_file("plan")
    valid_ids = [c["id"] for c in parse_vet_plan(plan_path.read_text()).get("checks", [])] \
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

    Refused unless the check's latest verdict is an actual failure. `unknown` is load-bearing: a
    confident guess sends build somewhere nobody looked."""
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
    """Record one standing lens's read of this cycle: what was PROBED, and what it found.
    No findings is a complete record.

    `probed` is a LIST, one probe per entry. No quotas anywhere: a quota manufactures a finding when
    the code is fine."""
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
    """Nominate one of this item's checks for the repo's VERIFICATION LIBRARY. `general`
    says what property of THIS REPO it defends.

    Vet nominates; CLOSE writes. Refused unless the check has PASSED here — untested hypotheses cost
    the next item a cycle."""
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
    """Every recorded VERDICT, in cycle order — one derived view, so no caller needs to know
    where an entry lives.

    Diagnosis entries share the fence but are filtered out: one leaking in would read as a second,
    failing entry."""
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
    """The latest diagnosis per check → {check: {where, why, unknown, cycle}}.

    A diagnosis is a separate act from the verdict: for a kernel-run check they even have different
    authors, so merging them would let an agent rewrite a machine verdict."""
    out: dict[str, dict] = {}
    for e in _ledger(item_dir):
        if e.get("kind") == KIND_DIAGNOSIS:
            out[e["check"]] = {"where": str(e.get("where") or ""),
                               "why": str(e.get("why") or ""),
                               "unknown": str(e.get("unknown") or ""),
                               "cycle": e.get("cycle")}
    return out


# --- Proof: the connected view (renovation v2 §4.2) ------------------------------------------

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
    """The Proof view's rows — the plan's tasks in order, then one item-wide row for the rest.

    `verified` entries are the PLANNED checks with any verdict joined on. A check the loop has not
    reached is still a row: the exam is decided at plan."""
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
    """The LATEST verdict per check, in first-seen order — the vet's actual findings.

    Latest-per-check, not every entry: a check that failed in c1 and passed in c3 IS passing, and two
    rows invite averaging contradictory facts."""
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


# --- assumptions: RETIRED (workflow-renovation-v2 §3.1 demolition, 2026-07-27) --------------

# `assumptions.md` and its tools are gone. The signal survives as a `## Assumptions` section in
# the phase's own record.


# --- the authorization ledger (BV-A2) -------------------------------------------

# A work-item may PROPOSE a contract change, but changes that DEFINE intent are owner-reserved.
# Build requests and DEFERS.
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

# The DEFAULT sync-to-reality set. The live delegated set is a per-system setting; this only
# informs the review brief.
DELEGABLE_SCOPES = ("doc-sync", "rename-to-shipped", "roadmap-mark-done")

_AUTHORIZATION_FILE = "authorizations.md"
_AUTHORIZATION_HEAD = re.compile(r"^### (?P<id>\S+) — (?P<what>.*)$")


# Which STAGED OPS make a declared scope a lie: the split is declared by the agent it constrains.
_INTENT_SECTIONS = {
    "project-prd": ("deliverables", "success signals", "non-goals", "users", "problem"),
    "roadmap":     ("wave", "deliverable"),
}


def intent_ops(ops: list) -> list[str]:
    """The staged ops that DEFINE intent rather than record what shipped. Empty when nothing
    intent-defining is staged."""
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
    """'' when the declared scope matches the staged ops, else the refusal message. Only
    DELEGABLE scopes are checked — a reserved one already goes to the owner."""
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
    """Append one PENDING authorization request; the id is its timestamp. Append-only:
    a later decision rewrites only `status` and `by`."""
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
    """Grant or deny ONE pending request. Returns the updated entry, or None if the id
    is unknown or no longer pending."""
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
    """The CURRENT vet plan's check ids. None when there is nothing to scope by, and the
    caller then reads the whole ledger."""
    plan = Path(item_dir) / "artifacts" / artifact_file("plan")
    if not plan.is_file():
        return None
    ids = {c.get("id") for c in parse_vet_plan(plan.read_text()).get("checks", []) if c.get("id")}
    return ids or None


def plan_vet_depth(item_dir: Path) -> str:
    """The plan's declared vet depth, or `""` when there is no plan to read.

    `none` is the OWNER-APPROVED judgment that nothing here is observable — never vet's call, and
    everything downstream reads it from HERE."""
    plan = Path(item_dir) / "artifacts" / artifact_file("plan")
    if not plan.is_file():
        return ""
    vp = parse_vet_plan(plan.read_text())
    return str(vp.get("depth") or "") if vp.get("present") else ""


_NO_VET_LINE = "**Nothing to verify.**"


def note_no_verification(item_dir: Path) -> str | None:
    """Write the `depth: none` cycle's §Verification content, CODE-WRITTEN and quoting
    the plan's own `reason`.

    Derived, so an empty §Verification can never be mistaken for a vet that gave up. Idempotent."""
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
    """The derived verdict over the ledger: `unverified` · `failed` · `stale` · `passed`.

    Scoped to the CURRENT plan's checks, so a renamed check's ORPHAN cannot pin the loop red forever.
    `deferred` sits between passed and failed; the authorization ledger is the AUTHORITY."""
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


# `author_readiness` and `readiness.md` are RETIRED: a mechanical user doc saying what `report-
# review.md` already said.


# --------------------------------------------------------------------------- cycle reports (renovation §3.1)

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
    from .plan_revision import current_revision   # local: plan_revision imports this module
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


# --- §Cycle outcome — the driver's trail (replaces the retired attempts.md ledger) --------------

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


def _how_checked(c: dict) -> str:
    """One check → how the owner will know it held, in their words. Derived from the plan,
    so the owner sees which rows are machine-decided."""
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
    Structure is code's to own, so code drops the echo."""
    body = (text or "").strip()
    first, _, rest = body.partition("\n")
    if first.startswith("#") and first.lstrip("#").strip().lower() == heading.strip().lower():
        return rest.strip()
    return body


_LENS_LINE = re.compile(r"(?mi)^(\s*[-*]\s+)(" + "|".join(LENSES) + r")(\s*:)")


def _bold_lenses(text: str) -> str:
    """Bold the lens name that OPENS a `## What else was looked at` bullet. Here rather than
    in CSS, so ONE rule says what a label looks like."""
    return _LENS_LINE.sub(lambda m: f"{m.group(1)}**{m.group(2)}{m.group(3).strip()}**", text)


def write_plan_user_report(item_dir: Path, *, summary: str, approach: str = "",
                           confirm: str = "", decisions: str = "", assumptions: str = "",
                           item_kind: str | None = None) -> dict:
    """Write the owner's answer to *what is being built, and what will prove it*.

    The prose slots are the planner's; everything factual is DERIVED from plan.md, because a
    hand-copied claim is a claim ABOUT the plan."""
    # An OMITTED optional slot arrives as None, not "". Normalize once, here, where the type is
    # declared.
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
    # One row per CHECK: the owner is approving an exam, and `proves:` IS the row, so it is never
    # clipped.
    rows = [f"| {c.get('proves') or '—'} | {_how_checked(c)} |" for c in vp.get("checks", [])]
    # A research item declares no checks BY DESIGN, so the gap call-out is about implementation
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
    _atomic_write(path, body)
    return {"path": str(path), "tasks": len(tasks), "checks": len(vp.get("checks", [])),
            "uncovered": uncovered}


def write_vet_user_report(item_dir: Path, repo_dir: Path | None, *, summary: str = "",
                          confirms: str = "", looked_at: str = "", unknown: str = "") -> dict:
    """Write the vet report: vet writes the narrative, code writes `## What didn't
    hold` off the ledger.

    ONE-WRITER with the ledger, so vet cannot write around a red check. No report while a check has
    no entry, a lens no read, or a failure no diagnosis."""
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
    # The lenses run on EVERY cycle: depth governs what is executed, not whether the work is read.
    if (missing := missing_lenses(item_dir)):
        raise ValueError("; ".join(
            f"the {ln} lens has no read this cycle — call record_lens with what you probed (no "
            "findings is a fine answer, and saying what you probed is what makes it one)"
            for ln in missing))
    # The diagnosis duty has its teeth here: "3 checks failing" with no WHERE sends the next cycle
    # hunting.
    if (undiag := undiagnosed_failures(item_dir)):
        raise ValueError("; ".join(
            f"check {c!r} is failing with no diagnosis this cycle — call record_diagnosis with "
            "`where` it broke and `why`, so the next build cycle starts at the cause instead of "
            "the symptom (never the fix: that is build's to reason out)" for c in undiag))
    ev = evidence_status(item_dir, repo_dir)
    checks = plan_ids + [c for c in by_check if c not in plan_ids]
    deferred_auth = {a["check"] for a in pending_authorizations(item_dir) if a.get("check")}

    # Each check's `proves:`, so a red row says what STOPPED being true instead of naming an id
    # nobody remembers.
    proves_of = {c["id"]: str(c.get("proves") or "")
                 for c in (parse_vet_plan(plan_path.read_text()).get("checks", [])
                           if plan_path.is_file() else [])}
    failed = [c for c in checks
              if (h := by_check.get(c)) and not h[-1].get("passed") and not h[-1].get("deferred")]
    deferred_all = sorted(deferred_auth | {c for c, h in by_check.items()
                                           if h and h[-1].get("deferred")})

    # `## What didn't hold` is authored HERE, off the ledger, so a red check reaches the owner
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
    # A lens finding that GATES belongs here for the same reason a failed check does: it sends the
    # item back.
    for g in lens_gaps(item_dir):
        lines.append(f"- **{g['text']}** — raised by the {g['lens']} reading ({g['severity']}).")
    # A build validation claim the kernel could not reproduce — the one record that must not
    # depend on being mentioned.
    for a in validation_discrepancies(item_dir, cycle=(cycle_reports(item_dir) or [{}])[-1].get("cycle")):
        lines.append(
            f"- **The build reported `{a['command']}` as "
            f"{'passing' if a['claimed'] else 'failing'}, and re-running it here "
            f"{'passes' if a['actual'] else 'does not'}** — its own validation does not reproduce. "
            f"({a['result']})")
    machine = ("## What didn't hold\n" + "\n".join(lines) + "\n\n") if lines else ""
    # A `depth: none` item still gets a reading, and that reading can GATE — so this note PRECEDES
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
    _atomic_write(path, body)
    return {"path": str(path), "verdict": ev.get("status", ""), "failed": failed}


# --------------------------------------------------------------------------- PR review notes

# What the PR page shows BESIDE each task's commits. Not a second opinion: the review report
# answers whether to land.

def delivered_line(item_dir: Path) -> str:
    """`artifacts/review.md`'s **Delivered** field, read by the landing commit's body.
    Reads the whole PARAGRAPH: the file is prose wrapped for reading."""
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
            # The field ends where its paragraph does — a blank line, or the next bold field.
            if not line.strip() or line.strip().startswith("**"):
                break
            parts.append(line.strip())
        return " ".join(p for p in parts if p).strip()
    except OSError:
        log.warning("delivered line: could not read %s", path)
    return ""


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
    """`look: … · deviated: …` → its labelled parts. Split on the separator FIRST, so a `·`
    in prose cannot start a phantom field.

    A value whose FIRST SENTENCE is `none` is nothing, however much follows."""
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
    """Everything the PR page shows per task. `needed` is the covering check's `proves:` —
    never the task spec, which is build instructions."""
    out: dict[str, dict] = {}
    notes = pr_task_notes(item_dir)
    plan_path = Path(item_dir) / "artifacts" / artifact_file("plan")
    # How many tasks each check defends. One covering `t1, t2` answers "what did THIS task make
    # true" poorly.
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

# Normalize the failure signature so incidental variation — case, punctuation, timestamps, ids —
# cannot hide a no-progress cycle.
_SIG_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{7,40}\b")
_SIG_NUM = re.compile(r"\d{2,}")
_SIG_JUNK = re.compile(r"[^a-z0-9 ]+")


def _normalize_signature(s: str) -> str:
    s = _SIG_HEX.sub("", (s or "").lower())
    s = _SIG_NUM.sub("", s)
    s = _SIG_JUNK.sub(" ", s)
    return " ".join(s.split())


def convergence_fingerprint(item_dir: Path, *, extra: list[str] | None = None) -> str:
    """The current cycle's failure fingerprint: sha1 over the sorted (check,
    normalized result) pairs. Empty when nothing is failing.

    `extra` carries failure signatures that are not ledger checks — a wall the loop keeps hitting
    should exit `not_converging`, not burn the budget."""
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
    """Bank one continuity checkpoint. APPEND-ONLY and atomic; the filename IS the order.
    Reference artifacts BY PATH.

    `role` is the SESSION ROLE that banked it: unstamped, a compacted intake thread gets the build
    thread's checkpoint and reads it as its own."""
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
    """The drilldown's continuity feed: newest-first checkpoint stubs. Full text stays
    behind the path, one click deeper."""
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
    """The newest checkpoint by filename, char-capped. None when none exist.

    `role=None` answers "what is this ITEM's latest state"; a named role answers "what was THIS
    THREAD doing", and also takes unstamped checkpoints, which are role-agnostic."""
    cdir = Path(item_dir) / "checkpoints"
    if not cdir.is_dir():
        return None
    # Sort by STEM: with `.md` attached, `-` < `.` would put a collision file `<ts>-1` before
    # `<ts>`.
    files = sorted(cdir.glob("*.md"), key=lambda p: p.stem)
    if not files:
        return None
    if role:
        # Two other stamps match: an UNSTAMPED checkpoint, and a legacy `intake` one. Both widen,
        # never narrow.
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

# A general session has no item folder, so a compaction loses the conversation outright. ONE file
# per session, overwritten.

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
    """The COMPUTED per-artifact status map: {kind → {required, present, issues, status}},
    derived and never stored. The `plan` row also carries the evidence verdict."""
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
        # The derived check verdict rides the `plan` row — the plan owns the vet checks.
        if kind == "plan" and cycle_reports(item_dir):
            row["evidence"] = evidence_status(item_dir, repo_dir)
        out[kind] = row
    return out
