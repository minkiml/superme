"""Artifact machinery — the D5 playbook generalized to every work-item artifact kind (workspace-
workflow PRD stage S2).

The convergent authoring standard (decision doc Research §5, all four benchmark codebases agree):
**agent supplies content, code supplies form.**
- One TEMPLATE + deterministic SCAFFOLDER per artifact kind — code owns frontmatter, section
  order, ids, timestamps. The agent only fills `<fill:…>` prose slots.
- A light SELF-CHECK validator (required sections present, no placeholder slots left) runs at the
  phase gate that CONSUMES the doc — never at write time.
- Reject-with-instructions, no state change: every validation failure returns an itemized issue
  list; nothing is persisted on failure.
- Claims verified against GROUND TRUTH where an artifact asserts facts (a staged knowledge op's
  file references must exist) — a doc cannot acquire a dead pointer at accept time.
- Evidence goes STALE on subsequent repo edits (repo fingerprint at record time vs now);
  "validated" is earned, never asserted.
- Append-only + atomic writes for the continuity channel (checkpoints).

Layout inside a work-item folder (`work-items/<id>/`):
    artifacts/{brief,plan}.md                — the agent-facing spine docs (renovation §3.1)
    artifacts/build-vet-<n>.md               — ONE report per build⟷vet cycle, staged writers:
                                               build (§Built/§Validation) → vet's pen appends the
                                               §Verification check fence → the loop driver appends
                                               §Cycle outcome
    artifacts/investigation.md               — research's work-segment record (the cycle report's
                                               counterpart; findings.md is RETIRED — its verdicts
                                               live in reports/report-review.md)
    reports/                                 — user-facing reports (projections; §3.3)
    checkpoints/<YYYYMMDD-HHMMSS>.md         — session continuity (append-only)
    preliminary/                             — the pushed inbox folder (S3)

Scaffold ownership (renovation §3.3, option 1): every scaffold with an authoring SKILL lives as one
template file under `skills/<skill>/templates/` — this module READS those files (never embeds a
copy) and derives the required-sections self-check from the template's own headings.

Everything here is deterministic, file-based, and spine-free — unit-testable without a daemon.
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

from .kind_profiles import get_profile

log = logging.getLogger(__name__)

FILL = re.compile(r"<fill:[^>]*>")


# --------------------------------------------------------------------------- templates
# Skill-owned scaffolds (renovation §3.3 option 1): the template FILE under the authoring skill is
# the single source — body, section order, and the required-sections check are all derived from it.
# A section whose template body carries a `<fill:…>` slot must be FILLED; a comment-only section
# (a pen's, the driver's, or a revision log) must merely EXIST. `handoff-brief` keeps an embedded
# skeleton — it has no authoring skill of its own.

_TEMPLATE_HOMES = {
    "brief":         ("triage", "brief-template.md"),
    "plan":          ("plan", "plan-template.md"),
    "plan-research": ("plan", "plan-research-template.md"),
    "build-vet":     ("build", "build-vet-template.md"),
    "investigation": ("investigate", "investigation-template.md"),
    "report-vet":    ("vet", "report-vet-template.md"),
}
_template_cache: dict[str, str] = {}


def skill_template(name: str) -> str:
    """The template body for `name`, read from its authoring skill's `templates/` folder. Cached
    for the process lifetime (templates change only with a deploy)."""
    if name not in _template_cache:
        from ..runtime.config import DEV_PLUGIN_DIR
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
    "handoff-brief": {"file": "handoff-brief.md", "required": (), "reader": "agent"},
}
# Pre-renovation plan shapes, kept for READ-ONLY tolerance (a plan is judged against the shape it
# was authored under; these die with their items). Oldest → newest: `## Validation criteria` (pre
# vet-loop) · v1 vet-loop (Inner checks + Vet plan) · v2 gate-feed (adds Touches / Behavior
# preview / Risks & assumptions) · old research shape (no Decisions & clarifications).
_PLAN_REQUIRED_LEGACY = ("Approach", "Tasks", "Validation criteria")
_PLAN_FEED_SECTIONS = ("Touches", "Behavior preview", "Risks & assumptions")
_PLAN_REQUIRED_V1 = ("Approach", "Tasks", "Inner checks", "Vet plan")
_PLAN_REQUIRED_V2 = ("Approach", "Touches", "Behavior preview", "Tasks",
                     "Risks & assumptions", "Inner checks", "Vet plan")
_PLAN_REQUIRED_RESEARCH_V1 = ("Questions", "Method", "Boundaries", "Done criteria", "Tasks")
ARTIFACT_KINDS = tuple(_SPECS)


def _template_name(artifact: str, item_kind: str | None) -> str | None:
    """The skill-template name for a template-file-backed artifact kind, else None (embedded)."""
    if artifact == "plan":
        return "plan-research" if item_kind == "research" else "plan"
    return artifact if artifact in ("brief", "investigation") else None


def _template(artifact: str, item_kind: str | None) -> str:
    name = _template_name(artifact, item_kind)
    if name:
        return skill_template(name)
    return {"handoff-brief": _HANDOFF}[artifact]


def section_spec(artifact: str, item_kind: str | None) -> list[tuple[str, bool]]:
    """[(heading, must_be_filled)] the self-check enforces. Template-file kinds derive it from
    their template; embedded legacy kinds require-and-fill their `required` tuple."""
    name = _template_name(artifact, item_kind)
    if name:
        return template_section_spec(name)
    return [(h, True) for h in _SPECS[artifact]["required"]]


def required_sections(artifact: str, item_kind: str | None) -> tuple[str, ...]:
    return tuple(h for h, _fill in section_spec(artifact, item_kind))


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


def scaffold(item_dir: Path, artifact: str, *, title: str = "", item_kind: str | None = None,
             item_id: str | None = None) -> dict:
    """Deterministically scaffold one artifact skeleton into `item_dir/artifacts/`. Code owns
    frontmatter + section order; the agent fills the `<fill:…>` slots only. NEVER overwrites —
    an existing file returns {created: False} (fill happens by editing, re-scaffold is a no-op).
    Unknown artifact kind fails loud (KeyError). Returns {path, created, sections}."""
    if artifact not in _SPECS:
        raise KeyError(f"unknown artifact kind {artifact!r} — known: {sorted(_SPECS)}")
    item_kind = get_profile(item_kind).kind  # validates + resolves null → implementation
    adir = Path(item_dir) / "artifacts"
    path = adir / artifact_file(artifact)
    sections = list(required_sections(artifact, item_kind))
    if path.exists():
        return {"path": str(path), "created": False, "sections": sections}
    fm = (f"---\nartifact: {artifact}\n"
          + (f"item: {item_id}\n" if item_id else "")
          + f"item_kind: {item_kind}\nreader: {_SPECS[artifact]['reader']}\n"
          + f"created_at: {date.today().isoformat()}\n---\n")
    body = _template(artifact, item_kind).format(title=title or (item_id or "work-item"))
    _atomic_write(path, fm + body)
    return {"path": str(path), "created": True, "sections": sections}


def _split_sections(text: str) -> dict[str, str]:
    """`## Heading` → body map (frontmatter stripped by the caller or tolerated here)."""
    out: dict[str, str] = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            out[cur] = ""
        elif cur is not None:
            out[cur] += line + "\n"
    return out


def _section_filled(body: str) -> bool:
    """Non-empty after dropping fill markers, html comments, and blank lines."""
    cleaned = FILL.sub("", re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL))
    return bool(cleaned.strip())


# --------------------------------------------------------------------------- vet plan (build⟷vet §3)
# The plan-authored contract the vet phase executes: a fresh agent with zero context must be able
# to run it unambiguously. Line-oriented on purpose — check ids are the JOIN KEY into the evidence
# ledger (`record_verification(check=…)` / `evidence_status()` already key on `check`), so plan and
# ledger meet with no new store. HARD issues block the pre-main gate (mechanically decidable
# structure); SOFT flags surface in the gate brief for the owner (judgment — a human is present,
# the one place fail-open is correct).

# Evidence PROVENANCE (design §4): who actually performed the check. `machine` = the kernel ran the
# check's literal `run:` block in the sandbox; `agent` = a vetter did it and attested. Old entries
# carry neither and read as `agent`, which is what they were.
BY_MACHINE = "machine"
BY_AGENT = "agent"

# Ledger entry KINDS. A verdict answers "did this check pass"; a diagnosis answers "where and why
# did it fail". They share the fence and the check id, and nothing else — see `diagnoses`.
KIND_VERDICT = "verdict"
KIND_DIAGNOSIS = "diagnosis"

VET_DEPTHS = ("none", "checks", "scenarios")
VET_MODES = ("command", "interaction", "inspection")
_VET_CHECK_ID = re.compile(r"^[a-z0-9-]+$")
_VET_HEADER_KEY = re.compile(r"^(depth|reason|env):\s*(.*)$")
_VET_FIELD = re.compile(r"^-\s*(traces|covers|mode|scenario|run|expect):\s*(.*)$")
# `run:` — the optional literal command the KERNEL executes for this check (design §4). One line,
# because a check is one exit code: several steps join with `&&`. A scenario that cannot be said in
# one line is exactly the scenario that stays agent-attested, so there is nothing to bend here.
# The same grammar `## Inner checks` already uses — a command whose exit status decides it.
_VET_CHECK_HEAD = re.compile(r"^###\s+(.+?)\s*$")
# Vagueness heuristic for `expect` (soft): non-falsifiable filler words, or too short to pin
# an observable outcome. A banned-word list is too brittle to BLOCK on — hence soft.
_VET_VAGUE = re.compile(r"\b(works|correctly|properly|as expected)\b", re.IGNORECASE)
_VET_EXPECT_MIN = 40


def _vet_value(raw: str) -> str:
    """A field value with unfilled `<fill:…>` markers treated as absent."""
    return FILL.sub("", raw or "").strip()


def parse_vet_plan(plan_text: str) -> dict:
    """Parse plan.md's `## Verification plan` section (`## Vet plan` in pre-renovation plans) →
    {present, depth, reason, env, checks}. Pure text → data; validity is judged separately
    (`vet_plan_hard_issues` / `vet_plan_soft_flags`). Header fields are the `key: value` lines
    before the first `### <check-id>`; each check's fields are its `- key: value` lines. Unknown
    lines are ignored (prose between fields is tolerated)."""
    sections = _split_sections(plan_text)
    body = sections.get("Verification plan")
    if body is None:
        body = sections.get("Vet plan")
    if body is None:
        return {"present": False, "depth": "", "reason": "", "env": "", "checks": []}
    out: dict = {"present": True, "depth": "", "reason": "", "env": "", "checks": []}
    cur: dict | None = None
    last = ""              # the field a wrapped continuation line belongs to
    for line in body.splitlines():
        h = _VET_CHECK_HEAD.match(line)
        if h:
            last = ""
            # `covers` = the plan task id(s) this check defends — the Proof view's join key (§4.2).
            # NOT a hard issue when absent: requiring it would retroactively fail every in-flight
            # plan's self-check, the same trap `## Revisions`-in-the-template was. An untagged check
            # lands in Proof's item-wide row.
            cur = {"id": _vet_value(h.group(1)), "traces": "", "covers": "", "mode": "",
                   "scenario": "", "run": "", "expect": ""}
            out["checks"].append(cur)
            continue
        if cur is None:
            m = _VET_HEADER_KEY.match(line.strip())
            if m:
                out[m.group(1)] = _vet_value(m.group(2))
        else:
            m = _VET_FIELD.match(line.strip())
            if m:
                cur[m.group(1)] = _vet_value(m.group(2))
                last = m.group(1)
            elif last and line.startswith((" ", "\t")) and line.strip():
                # A wrapped field — markdown folds it, so we do too. Without this the value stops
                # mid-sentence, and `expect:` (the falsifiable condition, and the one field the
                # owner reads at the plan gate) is exactly the field long enough to wrap.
                cur[last] = (cur[last] + " " + line.strip()).strip()
    return out


def vet_plan_hard_issues(vp: dict) -> list[str]:
    """The gate-blocking structural rules (§3.4 HARD) — every one mechanically decidable:
    depth legal · reason present (even for none — the owner can veto the judgment) · depth≠none ⇒
    ≥1 check and depth=none ⇒ 0 checks (a "no vet" plan listing checks is exactly the ambiguity
    fail-closed exists to kill) · every check fully fielded with a legal mode · `interaction`
    drives the real thing so it needs an env recipe (the mechanical form of §3.4's "scenario
    naming a runnable app"; `command`/`inspection` may run env-free) · ids unique + slug-shaped
    (they are ledger join keys and fingerprint keys)."""
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
        for field_name in ("traces", "mode", "scenario", "expect"):
            if not c.get(field_name):
                issues.append(f"vet plan check {label!r}: missing `{field_name}`")
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
    an `expect` that pattern-matches vagueness or is too short to pin an observable outcome, or a
    check that targets a retired (read-only) anchor doc."""
    flags: list[str] = []
    for c in vp.get("checks", []):
        exp, cid = c.get("expect", ""), c.get("id") or "(unnamed)"
        # Retired-doc scan across the whole check (traces/scenario/expect) — a check that can't pass.
        blob = " ".join(str(c.get(f) or "") for f in ("traces", "scenario", "expect"))
        if _RETIRED_DOC_REF.search(blob):
            flags.append(f"{cid}: targets the RETIRED doc spec.md (read-only) — this check can't "
                         "pass through the loop; drop it or migrate the doc's content to "
                         "architecture/decisions (an authorized contract change)")
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


def _fenced_blocks(body: str) -> list[str]:
    """The contents of every ``` fenced block in a section body, in order."""
    blocks, cur = [], None
    for line in body.splitlines():
        if _FENCE.match(line.strip()):
            if cur is None:
                cur = []
            else:
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


_TASK_LINE = re.compile(r"^\s*-\s*\[(?P<tick>[ xX])\]\s*(?P<id>t\d+)\b[\s—:-]*(?P<text>.*)$", re.M)


def parse_tasks(plan_text: str) -> list[dict]:
    """plan.md's `## Tasks` → [{id, done, text}], in plan order. The id is what the build's commits
    carry in their `SuperMe-Task` trailer, so this is the join that lets the PR walkthrough title a
    group with the task it implements instead of a bare `t3`. Tolerant by design: an unparseable
    line is skipped, never raised — a walkthrough is a view, not a gate."""
    body = _split_sections(plan_text).get("Tasks", "")
    return [{"id": m.group("id"), "done": m.group("tick").lower() == "x",
             "text": m.group("text").strip()}
            for m in _TASK_LINE.finditer(body)]


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
    """Put a blank line before every `**Label:**` block that doesn't already have one.

    Reports are agent-COPIED from a template, so their line spacing is prose, not structure — and
    markdown is unforgiving about it: two label lines in a row fold into one paragraph ("…the print
    path. **Key points:**"), and a label line under a bullet becomes a lazy continuation OF that
    bullet. The templates now carry the blank lines, but a template is a suggestion to a model and
    this is the read path both the owner's Reports tab and the deputy go through, so normalize here
    rather than trust every future author. Fences are left alone — a blank line inside one is
    content."""
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


def _drop_dead_blocks(text: str) -> str:
    """Delete `**Label:** none` blocks and an empty `## Changed since …` section on the READ path.

    A report is agent-copied from a template, and every template says to delete a block it has
    nothing to put under. Two of them survived in the very first report the owner read ("Needs your
    attention: none." and a "Changed since v<n>" holding "(first run — n/a)") — lines that exist
    only to say nothing, in a document whose whole budget is half a screen. Instructions to a model
    are a suggestion; this is the one read path both the Reports tab and the deputy go through, so
    the hygiene lands here, same reasoning as `_space_labels`.

    Deliberately literal: a block goes only when its value is one of a short list of dead tokens.
    Anything with real content — even one word — is left exactly as written."""
    lines, out = text.split("\n"), []
    i, fenced = 0, False
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and _LABEL_LINE.match(line):
            value = line.split(":**", 1)[1].strip()
            # A label whose value is on the FOLLOWING lines (bullets etc.) is never dead — only a
            # same-line value can be, and only if the block ends right there.
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if value.lower() in _DEAD_VALUES and not nxt:
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
        out.append(line)
        i += 1
    return "\n".join(out).rstrip() + "\n"


def report_text(item_dir: Path, phase: str) -> dict | None:
    """A phase's user-facing report, for the two surfaces that read it: the drilldown's Reports tab
    and the deputy's prompt (§2.1 — the deputy reads what the owner reads). Returns
    {phase, name, text, path, mtime, contract} or None when that phase hasn't written one.

    `contract` is the RELATIVE path to the phase's full agent-facing artifact — §4.3's "Open full
    contract", and the deputy's on-demand read. The report is the compact projection; the contract is
    the whole thing, never pasted into it (§3.3 keeps them two documents). Review and close have no
    separate contract: their report IS the record."""
    path = Path(item_dir) / "reports" / f"report-{phase}.md"
    if not path.is_file():
        return None
    contract = {"triage": "artifacts/brief.md", "plan": "artifacts/plan.md",
                "investigate": "artifacts/investigation.md"}.get(phase)
    if phase in ("build", "vet"):
        # The cycle the report covers is the newest one — build and vet both project the same file.
        reports = cycle_reports(item_dir)
        contract = f"artifacts/{Path(reports[-1]['path']).name}" if reports else None
    try:
        st = path.stat()
    except OSError:
        return None
    return {"phase": phase, "name": f"report-{phase}",
            "text": _drop_dead_blocks(_space_labels(path.read_text())),
            "path": str(path), "mtime": st.st_mtime, "contract": contract}


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


_OWNER_DECISION = re.compile(r"^\*\*Owner's decision:\*\*\s*(.+?)\s*$", re.M)


def owner_decision(item_dir: Path) -> str:
    """The owner's itemization call recorded into `reports/report-review.md` by `itemize` — the
    adopted proposals (with their inbox ids) or an explicit decline. Empty string when the line is
    absent, still an unfilled slot, or still the template's comment: in every one of those cases
    the decision was never actually put to them."""
    path = Path(item_dir) / "reports" / "report-review.md"
    if not path.is_file():
        return ""
    text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL)
    m = _OWNER_DECISION.search(text)
    if not m:
        return ""
    value = m.group(1).strip()
    return "" if FILL.search(value) else value


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
    spec = section_spec(artifact, item_kind)
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
            kv = re.match(r"^- (how|result|note|by|kind|where|why|unknown|passed|deferred|"
                          r"fingerprint): (.*)$", line)
            if kv:
                k, v = kv.group(1), kv.group(2).strip()
                cur[k] = (v == "true") if k in ("passed", "deferred") else v
    return entries


def record_verification(item_dir: Path, repo_dir: Path | None, *, check: str, how: str,
                        result: str, passed: bool, deferred: bool = False, note: str = "",
                        title: str = "", by: str = BY_AGENT) -> dict:
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
             + f"- by: {by}\n"
             + f"- passed: {'true' if passed else 'false'}\n"
             + ("- deferred: true\n" if deferred else "")
             + f"- fingerprint: {fp}\n")
    _append_to_section(Path(cy["path"]), "Verification", entry, in_checks_fence=True)
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
    _append_to_section(Path(cy["path"]), "Verification", entry, in_checks_fence=True)
    return {"check": check, "where": where, "why": why, "unknown": unknown, "cycle": cy["cycle"]}


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
    for line in (body or "").splitlines():
        if FILL.search(line):     # an unfilled slot is not content
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
    planned = [{"check": c["id"], "expect": str(c.get("expect") or ""),
                "mode": str(c.get("mode") or ""), "ran": False,
                "by": BY_MACHINE if c.get("run") else BY_AGENT,
                "passed": False, "deferred": False, "cycle": None, "how": "", "result": ""}
               for c in checks]
    ordered = planned + [{"check": k, "expect": "", "mode": ""} for k in verdicts
                         if k not in covers_of]
    verified: dict[str, list[dict]] = {}
    verified_loose: list[dict] = []
    for base in ordered:
        v = verdicts.get(base["check"])
        row = {**base, **(v or {}), "ran": v is not None,
               "history": history.get(base["check"], [])}
        covers = re.findall(r"t\d+", covers_of.get(row["check"], ""))
        if covers:
            for t in covers:
                verified.setdefault(t, []).append(row)
        else:
            verified_loose.append(row)

    rows = [{"task": t["id"], "text": t["text"], "done": t["done"],
             "built": built.get(t["id"], []), "validated": validated.get(t["id"], []),
             "verified": verified.get(t["id"], [])}
            for t in tasks]
    if built_loose or valid_loose or verified_loose:
        rows.append({"task": "", "text": "item-wide", "done": False, "built": built_loose,
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
    body = skill_template("build-vet").format(
        cycle=cycle, title=title or Path(item_dir).name)
    _atomic_write(path, fm + body)
    return {"cycle": cycle, "path": str(path), "created": True}


def _append_to_section(path: Path, heading: str, entry: str, *,
                       in_checks_fence: bool = False) -> None:
    """Append `entry` inside the `## {heading}` section of a cycle report — at the section's end,
    or (in_checks_fence) inside its ```checks fence, creating the fence when missing. Line-based
    and atomic; raises ValueError when the section heading is absent (a hand-mangled file must
    fail loud, not scatter entries)."""
    lines = path.read_text().splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if re.match(rf"^##\s+{re.escape(heading)}\s*$", ln)), None)
    if start is None:
        raise ValueError(f"cycle report {path.name} has no '## {heading}' section")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    entry_lines = entry.rstrip("\n").splitlines()
    if in_checks_fence:
        close = None
        for i in range(start + 1, end):
            if lines[i].strip() == "```checks":
                close = next((j for j in range(i + 1, end)
                              if lines[j].strip() == "```"), None)
                break
        if close is not None:
            lines[close:close] = entry_lines
        else:
            lines[end:end] = ["", "```checks", *entry_lines, "```"]
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
    entry = (f"### {ts} — {_one_line(decision)}\n"
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


def write_vet_user_report(item_dir: Path, repo_dir: Path | None, *, observations: str = "",
                          title: str = "") -> dict:
    """Write `reports/report-vet.md` from the vet skill's template — a PROJECTION of the recorded
    §Verification fences (renovation §3.3): verdict + check table derived from the entries (never
    agent-asserted; the per-check history marks carry ✗→✓ across cycles), deferred from the
    authorization ledger, `observations` = vet's own prose (real concerns only). Refuses
    (ValueError, itemized) while any plan check has no recorded entry. Overwritten each cycle, so
    the final cycle's version is the loop-exit report. Returns {path, verdict, failed}.

    Under `depth: none` there is no table to project, and refusing would strand the cycle — so the
    report SAYS the item owed no checks, and vet's observations still carry."""
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

    def _mark(e: dict) -> str:
        return "–" if e.get("deferred") else ("✓" if e.get("passed") else "✗")
    rows, failed = [], []
    for c in checks:
        hist = by_check.get(c, [])
        last = hist[-1] if hist else {}
        # One cycle has no story to tell — `✓ (✓)` is the same mark twice. Show the sequence only
        # when the check actually CHANGED across cycles (`✗→✓`), which is the whole reason a loop's
        # proof beats a snapshot. Same rule the Proof pane uses, so the two agree.
        marks = [_mark(e) for e in hist]
        collapsed = [m for i, m in enumerate(marks) if i == 0 or m != marks[i - 1]]
        res = "→".join(collapsed) if collapsed else "?"
        if hist and not last.get("passed") and not last.get("deferred"):
            failed.append(c)
        evid = " ".join(filter(None, [str(last.get("result") or "")[:160],
                                      str(last.get("note") or "")[:120]]))
        rows.append(f"| `{c}` | {res} | {evid} |")
    verdict = {"passed": "all checks green and fresh",
               "failed": f"{len(failed)} check(s) failing: " + ", ".join(failed),
               "stale": "green but STALE — code moved after the checks ran",
               "deferred": "green except checks deferred pending authorization",
               "unverified": "nothing recorded"}.get(ev.get("status", ""), ev.get("status", ""))
    if ev.get("not_required"):
        verdict = "no checks were owed — the approved plan declares `depth: none`"
        rows = ["| _(none)_ | – | the plan declared no observable surface to check |"]
    deferred_all = sorted(deferred_auth | {c for c, h in by_check.items()
                                           if h and h[-1].get("deferred")})
    cycle = cycle_reports(item_dir)[-1]["cycle"] if cycle_reports(item_dir) else 1
    changed = "_first cycle_"
    if cycle > 1:
        deltas = []
        for c in checks:
            hist = by_check.get(c, [])
            if len(hist) >= 2 and _mark(hist[-1]) != _mark(hist[-2]):
                deltas.append(f"`{c}` {_mark(hist[-2])}→{_mark(hist[-1])}")
        changed = ", ".join(deltas) if deltas else "no verdict changes"
    body = skill_template("report-vet")
    body = re.sub(r"<!--.*?-->\n?", "", body, flags=re.DOTALL)   # authoring note, not report content
    # What broke and where, for the failing checks only — a table because it is pairs, and the
    # reader's question ("what do I look at") is answered by the WHERE column alone. `unknown` rides
    # the same cell as the why: it is a qualifier on the reading, not a separate finding.
    diag = diagnoses(item_dir)
    drows = [f"| `{c}` | {d['where']} | {d['why']}"
             + (f" _(undetermined: {d['unknown']})_" if d.get("unknown") else "") + " |"
             for c in failed if (d := diag.get(c))]
    diag_block = ("**What broke:**\n\n| check | where | why |\n|---|---|---|\n"
                  + "\n".join(drows) + "\n") if drows else ""
    body = body.format(
        title=title or item_dir.name, verdict=verdict,
        check_rows="\n".join(rows), diagnoses=diag_block,
        deferred=", ".join(f"`{c}`" for c in deferred_all) or "none",
        observations=(observations or "").strip() or "none",
        prev=max(cycle - 1, 0) or "0", changed=changed)
    rdir = item_dir / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    path = rdir / "report-vet.md"
    _atomic_write(path, body)
    return {"path": str(path), "verdict": ev.get("status", ""), "failed": failed}


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


def convergence_fingerprint(item_dir: Path) -> str:
    """The current cycle's failure fingerprint: sha1 over the sorted (check, normalized latest
    failing result) pairs from the evidence ledger. Empty string when nothing is failing —
    an empty fingerprint never trips the guard."""
    latest: dict[str, dict] = {}
    for e in evidence_entries(item_dir):
        latest[e["check"]] = e
    failing = sorted((c, _normalize_signature(str(e.get("result") or "")))
                     for c, e in latest.items() if not e.get("passed"))
    if not failing:
        return ""
    return hashlib.sha1("\n".join(f"{c}|{sig}" for c, sig in failing).encode()).hexdigest()[:12]


# --------------------------------------------------------------------------- checkpoints

def write_checkpoint(item_dir: Path, repo_dir: Path | None, *, working_on: str, decisions: str,
                     remaining: str, notes: str = "", role: str | None = None) -> str:
    """Bank one continuity checkpoint (gstack 4-section + git-state header) to
    `checkpoints/<YYYYMMDD-HHMMSS>.md`. APPEND-ONLY (a new timestamped file every time, never
    overwrite; filename IS the canonical order) + atomic. Content rule (D11): conversation-native
    reasoning — decisions, leans, tried-and-failed; reference artifacts BY PATH, never duplicate
    them. Returns the file path.

    `role` is the SESSION ROLE that banked it (intake|build|vet). An item has three threads and
    they all bank into this one folder, so without the stamp "the latest checkpoint" is whichever
    thread wrote last. That is harmless for the item-state readers (a gate brief wants the item's
    newest state, from any thread) but WRONG for continuity: handing a compacted intake thread the
    build thread's checkpoint tells it "this is what you were doing" about work it never did, and
    a confidently-wrong recovered memory is worse than none. See `latest_checkpoint(role=…)`."""
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
        want = f"\nrole: {role}\n"
        files = [p for p in files
                 if (t := p.read_text()) and (want in t or "\nrole: " not in t)]
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
