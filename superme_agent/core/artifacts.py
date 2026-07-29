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
    "attempts": "agent", "gate-report": "user",
}


def reader_for_filename(name: str) -> str:
    """Best-effort reader label for an on-disk artifact filename (files may predate stamping)."""
    if _VET_REPORT_FILE.match(name) or _CYCLE_FILE.match(name):
        return "agent"
    if name.startswith("gate-report"):
        return "user"
    stem = {s["file"]: k for k, s in _SPECS.items()}.get(name)
    if stem:
        return ARTIFACT_READERS[stem]
    if name == "prd.md":
        return "both"
    return "agent"


# --------------------------------------------------------------------------- scaffold + self-check

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

VET_DEPTHS = ("none", "checks", "scenarios")
VET_MODES = ("command", "interaction", "inspection")
_VET_CHECK_ID = re.compile(r"^[a-z0-9-]+$")
_VET_HEADER_KEY = re.compile(r"^(depth|reason|env):\s*(.*)$")
_VET_FIELD = re.compile(r"^-\s*(traces|mode|scenario|expect):\s*(.*)$")
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
    for line in body.splitlines():
        h = _VET_CHECK_HEAD.match(line)
        if h:
            cur = {"id": _vet_value(h.group(1)), "traces": "", "mode": "",
                   "scenario": "", "expect": ""}
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


def parse_behavior_preview(plan_text: str) -> dict:
    """plan.md's `## Behavior preview` → {before, after} (the two fenced blocks, in order).
    Absent/unfilled → empty strings."""
    body = _split_sections(plan_text).get("Behavior preview", "")
    blocks = [b for b in _fenced_blocks(body) if not FILL.search(b)]
    return {"before": blocks[0].strip() if blocks else "",
            "after": blocks[1].strip() if len(blocks) > 1 else ""}


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


def parse_assumptions(plan_text: str) -> list[str]:
    """plan.md's `## Risks & assumptions` bullets → one string per line (unfilled slots and a
    lone 'none' skipped)."""
    body = _split_sections(plan_text).get("Risks & assumptions", "")
    out = []
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if not m:
            continue
        val = _vet_value(m.group(1))
        if val and val.lower() not in ("none", "none.", "—"):
            out.append(val)
    return out


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


def gate_report_issues(item_dir: Path, phase: str = "plan") -> list[str]:
    """The slot check on a generated gate report (`artifacts/gate-report-<phase>.html`): file
    present + no template placeholder left unfilled. Itemized like every gate check."""
    path = Path(item_dir) / "artifacts" / f"gate-report-{phase}.html"
    if not path.is_file():
        return [f"gate-report-{phase}.html does not exist — render it from the skill's template"]
    # Strip html comments first — the template documents its own slots in a comment header,
    # and documentation is not an unfilled slot.
    text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL)
    left = sorted(set(_REPORT_SLOT.findall(text)))
    if left:
        return [f"gate-report-{phase}.html has unfilled template slot(s): "
                + ", ".join(left[:8])]
    return []


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
    """A cheap fingerprint of the repo's CODE STATE: HEAD sha + working-tree porcelain status +
    the uncommitted diff CONTENT. Any commit or uncommitted edit changes it — that's the D6
    stale-on-edit trigger. The diff matters: porcelain alone is byte-identical when an
    already-dirty file keeps changing (` M foo.py` stays ` M foo.py`), which would let evidence
    recorded mid-build stay "fresh" through further edits to the same file. Non-git /
    missing dir → 'no-git' (evidence there can't be freshness-tracked, only recorded)."""
    if not repo_dir or not Path(repo_dir).is_dir():
        return "no-git"
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True,
                              text=True, timeout=10)
        if head.returncode != 0:
            return "no-git"
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir,
                                capture_output=True, text=True, timeout=10)
        diff = subprocess.run(["git", "diff", "HEAD"], cwd=repo_dir,
                              capture_output=True, text=True, timeout=15)
        return hashlib.sha1((head.stdout.strip() + "\n" + status.stdout
                             + "\n" + diff.stdout).encode()).hexdigest()[:16]
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
            kv = re.match(r"^- (how|result|note|passed|deferred|fingerprint): (.*)$", line)
            if kv:
                k, v = kv.group(1), kv.group(2).strip()
                cur[k] = (v == "true") if k in ("passed", "deferred") else v
    return entries


def record_verification(item_dir: Path, repo_dir: Path | None, *, check: str, how: str,
                        result: str, passed: bool, deferred: bool = False, note: str = "",
                        title: str = "") -> dict:
    """Append one machine entry to the current cycle report's `§Verification` check fence
    (renovation §3.1 — the fence replaces the retired validation.md ledger; scaffolds the cycle
    file first if none exists): check + how it ran + the machine result + pass/fail + the repo
    fingerprint at record time. Entries are APPEND-ONLY; 'verified' is derived from them, never
    asserted. The `check` MUST be an exact verification-plan id when the plan has one (B4) — see
    _resolve_evidence_check. `note` is the one-line expected-vs-actual context for a failure.

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
    if valid_ids:
        check = _resolve_evidence_check(check, valid_ids)
    # Target the LATEST cycle report even when the driver already closed it (a re-vet after stale
    # re-verifies the SAME cycle); scaffold only when no cycle exists at all.
    reports = cycle_reports(item_dir)
    cy = ({"cycle": reports[-1]["cycle"], "path": reports[-1]["path"]}
          if reports else scaffold_cycle(item_dir, title=title))
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fp = repo_fingerprint(repo_dir)
    entry = (f"### {ts} — {check}\n"
             f"- how: {how}\n"
             f"- result: {result}\n"
             + (f"- note: {note}\n" if note else "")
             + f"- passed: {'true' if passed else 'false'}\n"
             + ("- deferred: true\n" if deferred else "")
             + f"- fingerprint: {fp}\n")
    _append_to_section(Path(cy["path"]), "Verification", entry, in_checks_fence=True)
    return {"ts": ts, "check": check, "passed": passed, "deferred": deferred,
            "fingerprint": fp, "cycle": cy["cycle"]}


def evidence_entries(item_dir: Path) -> list[dict]:
    """Every recorded check entry, in record order: each cycle report's §Verification fence, in
    cycle order. One derived view, so evidence_status and the briefs never care where an entry
    lives. (`validation.md` — the pre-renovation ledger — is retired; a still-open item carrying
    one predates the loop entirely and has no live cycle to gate.)"""
    entries: list[dict] = []
    for r in cycle_reports(item_dir):
        body = _split_sections(Path(r["path"]).read_text()).get("Verification", "")
        for block in _fenced_blocks(body):
            for e in _parse_ledger_entries(block):
                entries.append({**e, "cycle": r["cycle"]})
    return entries


# --- assumptions: RETIRED (workflow-renovation-v2 §3.1 demolition, 2026-07-27) --------------
# `assumptions.md` + `record_assumption` / `ratify_assumptions` / `assumptions_ratified` are gone.
# The ledger was a file nobody opened whose only teeth were a close criterion an autopilot item
# could never satisfy — ratification is owner-only, so autonomy ended at a blocked gate. The
# signal it carried survives where it is actually read: a `## Assumptions` section in the phase's
# own record (cycle report / investigation.md), surfaced in that phase's user report, picked up on
# demand. Do not reintroduce a standalone ledger for it.


# --- the authorization ledger (BV-A2) -------------------------------------------
# A work-item may PROPOSE a contract change (stage_knowledge_delta) but not every such change is
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


def author_review_report(item_dir: Path, repo_dir: Path | None, *, title: str = "",
                         git_stats: dict | None = None, behind: int = 0) -> Path | None:
    """Mechanically render `gate-report-review.html` at advance-to-review, the visual sibling of
    author_readiness (B3). The review gate check requires this HTML report, but the review SKILL
    only runs when a HUMAN drives the phase — so an autopilot item reached review with the report
    missing and the deputy escalated 100% of clean items. This fills the review template's single
    `{{DATA_JSON}}` slot from exactly the sources the skill uses (readiness/git stats, the evidence
    ledger for the checks + delivered pane, plan.md for the promised pane) — every number counted,
    never invented. NEVER raises (returns None on any trouble; the review skill can still render it
    later). A review session may overwrite it with a richer, narrated version."""
    try:
        from ..runtime.config import DEV_PLUGIN_DIR
        tmpl_path = DEV_PLUGIN_DIR / "skills" / "review" / "templates" / "gate-report.html"
        template = tmpl_path.read_text()
        item_dir = Path(item_dir)

        # Checks + delivered pane straight off the evidence ledger (latest verdict per check).
        latest: dict[str, dict] = {}
        for e in evidence_entries(item_dir):
            latest[e["check"]] = e
        checks = [{"id": c, "pass": bool(e.get("passed"))} for c, e in latest.items()]
        last_pass = next((e for e in reversed(evidence_entries(item_dir)) if e.get("passed")), None)
        delivered = (last_pass or {}).get("result", "") or ""
        status = evidence_status(item_dir, repo_dir).get("status", "unverified")
        cycles = len(cycle_reports(item_dir))

        # Promised pane + surface off the plan.
        plan_path = item_dir / "artifacts" / artifact_file("plan")
        plan_text = plan_path.read_text() if plan_path.is_file() else ""
        # New-shape plans have no Behavior preview — the promised pane falls back to ## Intent.
        promised = ""
        if plan_text:
            promised = (parse_behavior_preview(plan_text).get("after", "")
                        or _split_sections(plan_text).get("Intent", "").strip())

        # Warnings + recommendation, derived — never asserted.
        warns: list[str] = []
        if behind and int(behind) > 0:
            warns.append(f"Branch is {int(behind)} commit(s) behind trunk — sync from main before merging.")
        if status != "passed":
            warns.append(f"Evidence ledger is not green (verdict: {status}).")
        if status != "passed":
            rec, reason = "Hold & fix", f"the evidence ledger is not green (verdict: {status})."
        elif behind and int(behind) > 0:
            rec, reason = "Hold & fix", f"sync from main first (branch is {int(behind)} commit(s) behind trunk)."
        else:
            rec, reason = "Merge", f"all {len(checks)} check(s) green and fresh over {cycles} vet cycle(s)."

        st = git_stats or {}
        data = {
            "title": title or item_dir.name,
            "item": item_dir.name,
            "ask": f"Merging lands “{title or item_dir.name}” on the trunk.",
            "recommendation": rec,
            "reason": reason,
            "warnings": warns,
            "stats": {"files": st.get("files", 0), "insertions": st.get("insertions", 0),
                      "deletions": st.get("deletions", 0), "tests": "n/a"},
            "by_file": [{"path": f.get("path", ""), "plus": f.get("plus", 0), "minus": f.get("minus", 0)}
                        for f in (st.get("by_file") or [])],
            "surface": "",
            "promised": promised,
            "delivered": delivered,
            "checks": checks,
            "cycles": cycles,
        }
        # Compact single line, HTML-safe inside the <script type="application/json"> slot.
        payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        rendered = template.replace("{{DATA_JSON}}", payload)
        out = item_dir / "artifacts" / "gate-report-review.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(out, rendered)
        return out
    except Exception:
        log.exception("author_review_report failed for %s (review skill renders it instead)", item_dir)
        return None


# --------------------------------------------------------------------------- cycle reports (renovation §3.1)
# ONE report per build⟷vet cycle: `artifacts/build-vet-<n>.md`, scaffolded from the build skill's
# template at cycle start. Strictly sequential writers — build fills §Built/§Validation, the vet
# pen APPENDS the §Verification check fence (record_verification above), the loop driver APPENDS
# §Cycle outcome, which CLOSES the cycle. The file is both the build→vet handover (vet reads
# §Built/§Validation instead of re-deriving from a raw diff) and the cycle narrative for review.

_CYCLE_FILE = re.compile(r"^build-vet-(\d+)\.md$")
_VET_REPORT_FILE = re.compile(r"^vet-report-(\d+)\.md$")   # legacy files — reader labeling only


def cycle_reports(item_dir: Path) -> list[dict]:
    """All cycle reports in cycle order: [{cycle, path}]."""
    adir = Path(item_dir) / "artifacts"
    if not adir.is_dir():
        return []
    out = []
    for p in adir.iterdir():
        m = _CYCLE_FILE.match(p.name)
        if m:
            out.append({"cycle": int(m.group(1)), "path": str(p)})
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
                         tokens: int | None = None, budget: int | None = None) -> dict | None:
    """Append one driver decision to the LATEST cycle report's §Cycle outcome (closing the cycle).
    `evidence` is the evidence_status verdict; `decision` what the driver did (review|build|revet|
    halt); `fingerprint` the failure fingerprint (convergence-guard input); `tokens`/`budget` the
    meter reading. Returns None (nothing recorded) when no cycle report exists yet — the DB
    loop.decision event still carries the decision."""
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    reports = cycle_reports(item_dir)
    if not reports:
        return None
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry = (f"### {ts} — {_one_line(decision)}\n"
             f"- evidence: {_one_line(evidence)}\n"
             f"- reason: {_one_line(reason)}\n")
    if fingerprint:
        entry += f"- fingerprint: {_one_line(fingerprint)}\n"
    if failed:
        entry += f"- failed: {', '.join(_one_line(str(f)) for f in failed)}\n"
    if tokens is not None and budget is not None:
        entry += f"- tokens: {int(tokens)} / {int(budget)}\n"
    _append_to_section(Path(reports[-1]["path"]), "Cycle outcome", entry)
    return {"ts": ts, "cycle": reports[-1]["cycle"], "decision": decision}


def read_cycle_outcomes(item_dir: Path) -> list[dict]:
    """Every driver decision across the cycle reports, in order: [{ts, cycle, decision, evidence,
    reason, fingerprint?, failed?, tokens?}]. The convergence guard reads the last entry's
    fingerprint; the stale guard the last entry's decision."""
    out: list[dict] = []
    for r in cycle_reports(item_dir):
        body = _split_sections(Path(r["path"]).read_text()).get("Cycle outcome", "")
        cur: dict | None = None
        for line in body.splitlines():
            m = re.match(r"^### (?P<ts>\S+) — (?P<decision>\S+)$", line)
            if m:
                cur = {"ts": m.group("ts"), "cycle": r["cycle"],
                       "decision": m.group("decision")}
                out.append(cur)
            elif cur is not None:
                kv = re.match(r"^- (evidence|reason|fingerprint|failed|tokens): (.*)$", line)
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
    the final cycle's version is the loop-exit report. Returns {path, verdict, failed}."""
    item_dir = Path(item_dir)
    plan_path = item_dir / "artifacts" / artifact_file("plan")
    plan_ids = [c["id"] for c in parse_vet_plan(plan_path.read_text()).get("checks", [])] \
        if plan_path.is_file() else []
    entries = evidence_entries(item_dir)
    by_check: dict[str, list[dict]] = {}
    for e in entries:
        by_check.setdefault(e["check"], []).append(e)
    missing = [c for c in plan_ids if c not in by_check]
    if missing:
        raise ValueError("; ".join(
            f"plan check {c!r} has no recorded entry — run it and record_verification first "
            "(an unrecorded check doesn't exist)" for c in missing))
    if not by_check:
        raise ValueError("no checks recorded — record_verification for every plan check first")
    ev = evidence_status(item_dir, repo_dir)
    checks = plan_ids + [c for c in by_check if c not in plan_ids]
    deferred_auth = {a["check"] for a in pending_authorizations(item_dir) if a.get("check")}

    def _mark(e: dict) -> str:
        return "–" if e.get("deferred") else ("✓" if e.get("passed") else "✗")
    rows, failed = [], []
    for c in checks:
        hist = by_check.get(c, [])
        marks = " ".join(_mark(e) for e in hist)
        last = hist[-1] if hist else {}
        res = _mark(last) if hist else "?"
        if hist and not last.get("passed") and not last.get("deferred"):
            failed.append(c)
        evid = " ".join(filter(None, [str(last.get("result") or "")[:160],
                                      str(last.get("note") or "")[:120]]))
        rows.append(f"| `{c}` | {res} ({marks}) | {evid} |")
    verdict = {"passed": "all checks green and fresh",
               "failed": f"{len(failed)} check(s) failing: " + ", ".join(failed),
               "stale": "green but STALE — code moved after the checks ran",
               "deferred": "green except checks deferred pending authorization",
               "unverified": "nothing recorded"}.get(ev.get("status", ""), ev.get("status", ""))
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
    body = body.format(
        title=title or item_dir.name, verdict=verdict,
        check_rows="\n".join(rows),
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


def loop_instruments(item_dir: Path, *, findings_cap: int = 2500) -> dict:
    """The build⟷vet loop's live instrument panel (renovation §2 Loop row) — everything derived
    from data the loop already writes, nothing stored: check ids in plan order · one verdict
    column per cycle report's §Verification fence (the checks×cycles matrix) · the newest cycle's
    failing entries (the expected-vs-actual the builder is working from) · the driver's §Cycle
    outcome trail. Empty checks + cycles ⇒ the surface hides the panel."""
    item_dir = Path(item_dir)
    plan_path = item_dir / "artifacts" / artifact_file("plan")
    vp = parse_vet_plan(plan_path.read_text()) if plan_path.is_file() else {"checks": []}
    check_ids = [c["id"] for c in vp.get("checks", [])]
    by_cycle: dict[int, dict[str, bool]] = {}
    for e in evidence_entries(item_dir):
        cyc = int(e.get("cycle") or 0)
        by_cycle.setdefault(cyc, {})[e["check"]] = bool(e.get("passed"))
        if e["check"] not in check_ids:   # review-routed checks appear mid-loop — keep them
            check_ids.append(e["check"])
    cycles = [{"cycle": c, "verdicts": v} for c, v in sorted(by_cycle.items()) if c > 0]
    findings = None
    if cycles and not all(cycles[-1]["verdicts"].values()):
        last_cycle = cycles[-1]["cycle"]
        fails = [e for e in evidence_entries(item_dir)
                 if int(e.get("cycle") or 0) == last_cycle and not e.get("passed")
                 and not e.get("deferred")]
        latest_fail: dict[str, dict] = {}
        for e in fails:
            latest_fail[e["check"]] = e
        body = "\n\n".join(
            f"### {c}\n- result: {e.get('result', '')}"
            + (f"\n- note: {e['note']}" if e.get("note") else "")
            for c, e in latest_fail.items())
        findings = body[:findings_cap] if body else None
    attempts = [{"cycle": a["cycle"], "decision": a.get("decision", ""),
                 "reason": a.get("reason", ""), "ts": a["ts"]}
                for a in read_cycle_outcomes(item_dir)]
    return {"checks": check_ids, "cycles": cycles, "findings": findings, "attempts": attempts}


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
