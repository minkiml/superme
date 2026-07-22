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
- Claims verified against GROUND TRUTH where an artifact asserts facts (closeout: files exist,
  commit exists) — a doc cannot acquire a dead pointer at accept time.
- Evidence goes STALE on subsequent repo edits (repo fingerprint at record time vs now);
  "validated" is earned, never asserted.
- Append-only + atomic writes for the continuity channel (checkpoints).

Layout inside a work-item folder (`work-items/<id>/`):
    artifacts/{plan,validation,readiness,findings,closeout}.md   — the gate docs (D6 table)
    checkpoints/<YYYYMMDD-HHMMSS>.md                             — session continuity (append-only)
    notes/                                                       — free scratch, never a gate
    preliminary/                                                 — the pushed inbox folder (S3)

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
# One skeleton per artifact kind; `plan` varies by ITEM kind (implementation vs research — the D2
# pre-main slot differs: build-plan vs question/method/boundaries). Section ORDER is code-owned.

_PLAN_IMPL = """# Plan — {title}

## Approach
<fill:approach — what we'll build and how, grounded in the preliminary brief>

## Touches
```yaml
# one row per component this plan touches — the change map's data
- component: <fill:short component name>
  path: <fill:repo-relative path (file or dir)>
  action: <fill:new | modify | read>
```

## Behavior preview
**Before**
```
<fill:the observable surface today — command output / screen / API shape, captured or reconstructed>
```
**After**
```
<fill:the PREDICTED surface after this plan lands — same surface, same format, so the panes compare>
```

## Tasks
- [ ] <fill:first task — break the approach into checkable steps>

## Risks & assumptions
- <fill:one line per assumption made without the owner, and per risk worth their eyes — these render as the gate's confirm/adjust cards>

## Inner checks
- `<fill:command whose EXIT CODE decides it — the repo's standard test/lint/typecheck lines>`

## Vet plan
depth: <fill:none | checks | scenarios>
reason: <fill:one line — why this depth fits this item (required even for none)>
env: <fill:environment recipe id, or none>

### <fill:check-id — lowercase slug, unique>
- traces: <fill:the written requirement this check defends — PRD deliverable / user story / spec decision>
- mode: <fill:command | interaction | inspection>
- scenario: <fill:the real steps, concretely — commands verbatim; UI steps as a user would take them>
- expect: <fill:falsifiable pass condition — exact output/state, never "works correctly">
"""

_PLAN_RESEARCH = """# Research plan — {title}

## Questions
<fill:the questions this research must answer>

## Method
<fill:how we'll investigate — sources, experiments, code reading>

## Boundaries
<fill:what we will NOT investigate>

## Done criteria
<fill:falsifiable criteria for "the research is complete">

## Tasks
- [ ] <fill:first investigation step>
"""

_VALIDATION = """# Validation — {title}

## Checklist
<fill:the checks planned, from plan.md's Vet plan (check ids verbatim — they key the ledger)>

## Evidence
<!-- appended by record_validation_evidence — machine entries; do not hand-edit -->
"""

_READINESS = """# Readiness — {title}

## Status
<fill:one-paragraph state of the item — what changed since the plan was approved>

## Stats
```yaml
# from `git diff --stat <base>...HEAD` in the worktree — counts, not judgment
files: <fill:changed file count>
insertions: <fill:+ lines>
deletions: <fill:- lines>
tests: <fill:test count after the change, or "none">
by_file:
  - {{path: <fill:repo-relative path>, plus: <fill:+>, minus: <fill:->}}
```

## Validation
<fill:evidence summary — what ran, what's green, freshness>

## Knowledge
<fill:updated | none-needed | stale-warning — the knowledge-delta row, in prose>

## Warnings
<fill:plain-English risks/caveats, or "none">

## Recommendation
<fill:Merge / Hold & fix / Merge anyway — one recommendation with the why>
"""

_FINDINGS = """# Findings — {title}

## Questions
<fill:the questions asked (from plan.md)>

## Findings
<fill:what was found — evidence-backed, pointers to sources>

## Implications
<fill:what this means for the project>

## Follow-ups
<fill:spawned follow-up items (ids) or "none">
"""

_CLOSEOUT = """# Closeout — {title}

## Summary
<fill:1-3 human sentences — what this item delivered>

## Facts
```yaml
changed_files: []      # repo-relative paths this item changed ([] if none)
tests_run: ""          # the validation actually executed, one line ("" if none)
merge_commit: ""       # the merge/final commit sha ("" if never merged)
```

## Artifacts
<fill:bullet list of this item's artifact paths worth keeping, or "none">
"""

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

# kind → (template, required sections for the self-check). `handoff-brief` sections are ALL
# optional (D5: capture friction kills itemizing) — its check only demands one non-empty section.
_SPECS: dict[str, dict] = {
    "plan":        {"file": "plan.md",       "required": (), "reader": "both"},  # per item-kind, resolved below
    "validation":  {"file": "validation.md", "required": ("Checklist",), "reader": "agent"},
    "readiness":   {"file": "readiness.md",  "required": ("Status", "Validation", "Knowledge",
                                                          "Warnings", "Recommendation"),
                    "reader": "user"},
    "findings":    {"file": "findings.md",   "required": ("Questions", "Findings", "Implications",
                                                          "Follow-ups"), "reader": "user"},
    "closeout":    {"file": "closeout.md",   "required": ("Summary", "Facts", "Artifacts"),
                    "reader": "user"},
    "handoff-brief": {"file": "handoff-brief.md", "required": (), "reader": "agent"},
}
_PLAN_REQUIRED = {
    "implementation": ("Approach", "Touches", "Behavior preview", "Tasks",
                       "Risks & assumptions", "Inner checks", "Vet plan"),
    "research": ("Questions", "Method", "Boundaries", "Done criteria", "Tasks"),
}
# Pre-vet-loop plans (scaffolded before 2026-07-17) carry `## Validation criteria` instead of the
# Inner-checks/Vet-plan pair. They stay valid READ-ONLY — the gate accepts the shape they were
# authored against — so mid-flight items don't go red retroactively. Dies with those items.
_PLAN_REQUIRED_LEGACY = ("Approach", "Tasks", "Validation criteria")
# v1 vet-loop plans (before the 2026-07-19 gate-feed sections) lack Touches / Behavior preview /
# Risks & assumptions. Same read-only tolerance: if NONE of the three feed sections is present,
# the plan is judged against the v1 shape; a plan carrying ANY of them owes all three.
_PLAN_FEED_SECTIONS = ("Touches", "Behavior preview", "Risks & assumptions")
_PLAN_REQUIRED_V1 = ("Approach", "Tasks", "Inner checks", "Vet plan")
ARTIFACT_KINDS = tuple(_SPECS)


def _template(artifact: str, item_kind: str | None) -> str:
    if artifact == "plan":
        return _PLAN_RESEARCH if item_kind == "research" else _PLAN_IMPL
    return {"validation": _VALIDATION, "readiness": _READINESS, "findings": _FINDINGS,
            "closeout": _CLOSEOUT, "handoff-brief": _HANDOFF}[artifact]


def required_sections(artifact: str, item_kind: str | None) -> tuple[str, ...]:
    if artifact == "plan":
        return _PLAN_REQUIRED["research" if item_kind == "research" else "implementation"]
    return _SPECS[artifact]["required"]


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
    if _VET_REPORT_FILE.match(name):
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
# ledger (`record_evidence(check=…)` / `evidence_status()` already key on `check`), so plan and
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
    """Parse plan.md's `## Vet plan` section → {present, depth, reason, env, checks}. Pure text →
    data; validity is judged separately (`vet_plan_hard_issues` / `vet_plan_soft_flags`). Header
    fields are the `key: value` lines before the first `### <check-id>`; each check's fields are
    its `- key: value` lines. Unknown lines are ignored (prose between fields is tolerated)."""
    body = _split_sections(plan_text).get("Vet plan")
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
        return ["missing required section '## Vet plan'"]
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


def parse_behavior_preview(plan_text: str) -> dict:
    """plan.md's `## Behavior preview` → {before, after} (the two fenced blocks, in order).
    Absent/unfilled → empty strings."""
    body = _split_sections(plan_text).get("Behavior preview", "")
    blocks = [b for b in _fenced_blocks(body) if not FILL.search(b)]
    return {"before": blocks[0].strip() if blocks else "",
            "after": blocks[1].strip() if len(blocks) > 1 else ""}


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


def parse_readiness_stats(readiness_text: str) -> dict:
    """readiness.md's `## Stats` fenced yaml → {files, insertions, deletions, tests, by_file}.
    Tolerant: absent/unfilled/broken yaml → {} (the surface falls back to prose rows)."""
    body = _split_sections(readiness_text).get("Stats", "")
    blocks = _fenced_blocks(body)
    raw = blocks[0] if blocks else body
    if not raw.strip() or FILL.search(raw):
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {k: data.get(k) for k in ("files", "insertions", "deletions", "tests")}
    bf = data.get("by_file")
    out["by_file"] = [{"path": str(e.get("path") or ""), "plus": e.get("plus"),
                       "minus": e.get("minus")}
                      for e in bf if isinstance(e, dict)] if isinstance(bf, list) else []
    return out


_REPORT_SLOT = re.compile(r"\{\{[A-Z0-9_]+\}\}")


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
    every required section present AND filled · no `<fill:…>` slot left anywhere · closeout's
    Facts yaml parses with the expected keys. Read-only — never mutates state. `path` overrides
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
    required = required_sections(artifact, item_kind)
    is_impl_plan = (artifact == "plan"
                    and get_profile(item_kind).kind == "implementation")
    if is_impl_plan and _is_legacy_plan(sections):
        required = _PLAN_REQUIRED_LEGACY
        is_impl_plan = False  # legacy shape: no vet-plan rules to enforce
    elif is_impl_plan and not any(s in sections for s in _PLAN_FEED_SECTIONS):
        required = _PLAN_REQUIRED_V1  # pre-gate-feed plan: read-only tolerance, vet rules apply
    for req in required:
        if req not in sections:
            issues.append(f"missing required section '## {req}'")
        elif not _section_filled(sections[req]):
            issues.append(f"section '## {req}' is empty")
    # The vet-plan structural gate (§3.4 HARD) — the pre-main gate consumes plan.md, so a plan
    # whose vet plan a fresh agent couldn't execute is not gate-ready. Skip the duplicate
    # "missing section" (already reported above); soft flags go to the gate brief, never here.
    if is_impl_plan and "Vet plan" in sections:
        issues.extend(vet_plan_hard_issues(parse_vet_plan(text)))
    # The change-map feed (renovation §2): a plan CARRYING `## Touches` owes parseable rows —
    # the section is required on new scaffolds, and a filled-but-broken yaml can't feed the map.
    if is_impl_plan and "Touches" in sections and _section_filled(sections["Touches"]):
        issues.extend(touches_hard_issues(text))
    if artifact == "handoff-brief" and not issues:
        if not any(_section_filled(b) for b in sections.values()):
            issues.append("every section is empty — a brief needs at least one filled section")
    if artifact == "closeout" and "Facts" in sections:
        facts, err = _parse_facts(sections["Facts"])
        if err:
            issues.append(err)
        else:
            missing = {"changed_files", "tests_run", "merge_commit"} - set(facts)
            if missing:
                issues.append(f"Facts yaml missing key(s): {', '.join(sorted(missing))}")
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


def record_evidence(item_dir: Path, repo_dir: Path | None, *, check: str, how: str,
                    result: str, passed: bool, deferred: bool = False, title: str = "",
                    item_kind: str | None = None) -> dict:
    """Append one machine entry to the validation.md evidence ledger (scaffolding it first if
    absent): check + how it ran + the machine result + pass/fail + the repo fingerprint at record
    time. Entries are APPEND-ONLY; 'validated' is derived from them, never asserted. The `check`
    MUST be an exact vet-plan check id when the plan has one (B4) — see _resolve_evidence_check.

    `deferred=True` (BV-A2) records a check the build could NOT satisfy because it needs an
    authorization it lacks (an owner-reserved contract edit): it is neither pass nor fail — it
    advances the item to review with the authorization request, rather than failing the loop
    closed. A deferred entry rides `passed: false` for legacy readers but carries `deferred: true`,
    and evidence_status buckets it separately."""
    # Single-line coerce: the ledger is line-oriented (one `- key: value` per line) — an embedded
    # newline in any field would corrupt parsing for every entry after it.
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    check, how, result = _one_line(check), _one_line(how), _one_line(result)
    if not (check and how and result):
        raise ValueError("evidence needs non-empty check, how, and result")
    # Single source of truth for check state: the ledger key MUST be a vet-plan id (when one exists).
    plan_path = Path(item_dir) / "artifacts" / artifact_file("plan")
    valid_ids = [c["id"] for c in parse_vet_plan(plan_path.read_text()).get("checks", [])] \
        if plan_path.is_file() else []
    if valid_ids:
        check = _resolve_evidence_check(check, valid_ids)
    scaffold(item_dir, "validation", title=title, item_kind=item_kind)
    path = Path(item_dir) / "artifacts" / artifact_file("validation")
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fp = repo_fingerprint(repo_dir)
    entry = (f"\n### {ts} — {check}\n"
             f"- how: {how}\n"
             f"- result: {result}\n"
             f"- passed: {'true' if passed else 'false'}\n"
             + ("- deferred: true\n" if deferred else "")
             + f"- fingerprint: {fp}\n")
    _atomic_write(path, path.read_text() + entry)
    return {"ts": ts, "check": check, "passed": passed, "deferred": deferred, "fingerprint": fp}


def evidence_entries(item_dir: Path) -> list[dict]:
    """Parse the ledger back: [{ts, check, how, result, passed, fingerprint}] in file order."""
    path = Path(item_dir) / "artifacts" / artifact_file("validation")
    if not path.exists():
        return []
    entries: list[dict] = []
    cur: dict | None = None
    for line in path.read_text().splitlines():
        m = _EVIDENCE_HEAD.match(line)
        if m:
            cur = {"ts": m.group("ts"), "check": m.group("check")}
            entries.append(cur)
        elif cur is not None:
            kv = re.match(r"^- (how|result|passed|deferred|fingerprint): (.*)$", line)
            if kv:
                k, v = kv.group(1), kv.group(2).strip()
                cur[k] = (v == "true") if k in ("passed", "deferred") else v
    return entries


# --- the assumption ledger ------------------------------------------------------
# The replacement for parked "open questions". The owner enters only at contracted moments (the
# gates), so an agent that hits an unknown mid-phase must NOT stop and ask — it decides, records the
# call here with its reversal cost, and keeps moving. The next gate brief renders every unratified
# entry as a confirm/adjust card, and `assumptions_ratified` refuses close while any is outstanding.
#
# Why this beats a question queue: an assumption is a question with a default already applied. It
# blocks nothing, it's visible, and it carries the cost of being wrong — whereas a parked question
# blocks nothing AND reminds no one, which is exactly how the owner ends up reading a doc to find
# out what the agent needed from them.
_ASSUMPTION_FILE = "assumptions.md"
_ASSUMPTION_HEAD = re.compile(r"^### (?P<ts>\S+) — (?P<what>.*)$")


def record_assumption(item_dir: Path, *, what: str, why: str, cost: str,
                      phase: str = "", cycle: int | None = None) -> dict:
    """Append one assumption to the item's ledger. Append-only: ratification is a later line, never
    an edit, so the history of what was assumed survives the decision to accept it."""
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    what, why, cost = _one_line(what), _one_line(why), _one_line(cost)
    if not (what and why and cost):
        # `cost` is mandatory on purpose: "what breaks if this is wrong" is the field that makes an
        # assumption reviewable in one glance. Without it the owner has to reconstruct the stakes.
        raise ValueError("an assumption needs what, why, and the cost of being wrong")
    path = Path(item_dir) / "artifacts" / _ASSUMPTION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "" if path.exists() else (
        "# Assumptions\n\nCalls taken without the owner, each awaiting ratification at the next "
        "gate.\n")
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry = (f"\n### {ts} — {what}\n"
             f"- why: {why}\n"
             f"- cost: {cost}\n"
             f"- phase: {phase or 'unknown'}\n"
             f"- cycle: {cycle if cycle is not None else ''}\n"
             f"- ratified: false\n")
    _atomic_write(path, (path.read_text() if path.exists() else head) + entry)
    return {"ts": ts, "what": what, "ratified": False}


def assumption_entries(item_dir: Path) -> list[dict]:
    """Parse the ledger back: [{ts, what, why, cost, phase, cycle, ratified}] in file order."""
    path = Path(item_dir) / "artifacts" / _ASSUMPTION_FILE
    if not path.exists():
        return []
    entries: list[dict] = []
    cur: dict | None = None
    for line in path.read_text().splitlines():
        m = _ASSUMPTION_HEAD.match(line)
        if m:
            cur = {"ts": m.group("ts"), "what": m.group("what"), "ratified": False}
            entries.append(cur)
        elif cur is not None:
            kv = re.match(r"^- (why|cost|phase|cycle|ratified): (.*)$", line)
            if kv:
                k, v = kv.group(1), kv.group(2).strip()
                cur[k] = (v == "true") if k == "ratified" else v
    return entries


def unratified_assumptions(item_dir: Path) -> list[dict]:
    """The ones still owed a human look — what the gate brief shows and the close gate refuses on."""
    return [a for a in assumption_entries(item_dir) if not a.get("ratified")]


def ratify_assumptions(item_dir: Path) -> int:
    """Mark every outstanding assumption ratified — called when the owner APPROVES a gate, because
    approving a brief that listed them IS the ratification. Rewrites only the `ratified:` lines, so
    the ledger stays append-only in substance. Returns how many were newly ratified."""
    path = Path(item_dir) / "artifacts" / _ASSUMPTION_FILE
    if not path.exists():
        return 0
    lines, n = path.read_text().splitlines(keepends=True), 0
    for i, line in enumerate(lines):
        if line.startswith("- ratified: false"):
            lines[i] = line.replace("false", "true", 1)
            n += 1
    if n:
        _atomic_write(path, "".join(lines))
    return n


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


def author_readiness(item_dir: Path, repo_dir: Path | None, *, title: str = "",
                     delta_summary: str | None = None, git_stats: dict | None = None,
                     behind: int = 0) -> Path:
    """Mechanically author readiness.md at advance-to-review (B3), overwriting. Before this, an
    autopilot item reached review with NO readiness report — the review skill only runs when a
    HUMAN drives the phase — so the review deputy escalated 100% of items for a doc nobody wrote.
    This fills all five required sections from the evidence ledger, the assumption ledger, the vet
    cycle count, the staged knowledge delta (`delta_summary`) and the git diff (`git_stats`,
    `behind`) — every line derived, not asserted. A review session may still enrich it later."""
    item_dir = Path(item_dir)
    ev = evidence_status(item_dir, repo_dir)
    status = ev.get("status", "unverified")
    latest: dict[str, dict] = {}
    for e in evidence_entries(item_dir):
        latest[e["check"]] = e
    cycles = len(vet_reports(item_dir))
    unratified = unratified_assumptions(item_dir)

    status_line = (f"Built and vetted over {cycles} vet cycle(s); {len(latest)} check(s) recorded, "
                   f"ledger verdict: **{status}**.")
    if latest:
        val = "\n".join(
            f"- `{c}`: {'✓ pass' if e.get('passed') else '✗ FAIL'} — "
            f"{e.get('how', '')} → {e.get('result', '')}" for c, e in latest.items())
    else:
        val = "- (no evidence recorded)"
    if status == "stale":
        val += "\n- ⚠ evidence went STALE — code moved after it was recorded; re-vet before merge."

    warns: list[str] = []
    if behind and int(behind) > 0:
        warns.append(f"Branch is {int(behind)} commit(s) behind trunk — sync from main before merging.")
    if unratified:
        warns.append(f"{len(unratified)} unratified assumption(s) — the close gate requires confirming them.")
    if status != "passed":
        warns.append(f"Evidence ledger is not green (verdict: {status}).")
    warnings = "\n".join(f"- {w}" for w in warns) if warns else "none"

    if status != "passed":
        rec = f"**Hold & fix** — the evidence ledger is not green (verdict: {status})."
    elif behind and int(behind) > 0:
        rec = f"**Hold & fix** — sync from main first (branch is {int(behind)} commit(s) behind trunk)."
    else:
        rec = f"**Merge** — clean. All {len(latest)} check(s) green and fresh over {cycles} vet cycle(s)."
        if unratified:
            rec += f" Confirm {len(unratified)} assumption(s) at the gate."

    stats_block = ""
    if git_stats:
        rows = git_stats.get("by_file") or []
        by = ("\n".join(f"  - {{path: {f['path']}, plus: {f['plus']}, minus: {f['minus']}}}"
                        for f in rows)) if rows else ""
        stats_block = (
            "\n## Stats\n```yaml\n"
            f"files: {git_stats.get('files', 0)}\n"
            f"insertions: {git_stats.get('insertions', 0)}\n"
            f"deletions: {git_stats.get('deletions', 0)}\n"
            "tests: n/a\n"
            + ("by_file:\n" + by + "\n" if by else "by_file: []\n")
            + "```\n")

    doc = (f"# Readiness — {title or item_dir.name}\n\n"
           "> Authored mechanically at advance-to-review from the ledgers + git — every line is "
           "derived, not asserted. A review session may enrich it.\n\n"
           f"## Status\n{status_line}\n"
           f"{stats_block}\n"
           f"## Validation\n{val}\n\n"
           f"## Knowledge\n{delta_summary or 'none-needed'}\n\n"
           f"## Warnings\n{warnings}\n\n"
           f"## Recommendation\n{rec}\n")
    path = item_dir / "artifacts" / artifact_file("readiness")
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, doc)
    return path


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
        cycles = len(vet_reports(item_dir))

        # Promised pane + surface off the plan.
        plan_path = item_dir / "artifacts" / artifact_file("plan")
        plan_text = plan_path.read_text() if plan_path.is_file() else ""
        promised = parse_behavior_preview(plan_text).get("after", "") if plan_text else ""

        # Warnings + recommendation — same derivation as author_readiness (single source of truth
        # for the verdict would be nicer, but the two docs are authored side by side and cheaply).
        unratified = unratified_assumptions(item_dir)
        warns: list[str] = []
        if behind and int(behind) > 0:
            warns.append(f"Branch is {int(behind)} commit(s) behind trunk — sync from main before merging.")
        if unratified:
            warns.append(f"{len(unratified)} unratified assumption(s) — the close gate requires confirming them.")
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


# --------------------------------------------------------------------------- vet reports (build-vet-loop §4b)
# Per vet CYCLE, the narrative handoff: `vet-report-<n>.md`. Code writes the ENVELOPE (§4.5.2
# recipe 4 — heading, cycle number, verdict list, section order); the agent supplies only the
# findings prose. Authored fresh each cycle, never templated (hermes' concrete failure: a static
# continuation prompt that never says why the last attempt was rejected). Verdicts are validated
# against the plan's vet plan AND the evidence ledger before a report can be written — a verdict
# that contradicts the ledger is refused mechanically (anti-self-report: the claim is false).

_VET_REPORT_FILE = re.compile(r"^vet-report-(\d+)\.md$")


def vet_reports(item_dir: Path) -> list[dict]:
    """All vet reports in cycle order: [{cycle, path}]."""
    adir = Path(item_dir) / "artifacts"
    if not adir.is_dir():
        return []
    out = []
    for p in adir.iterdir():
        m = _VET_REPORT_FILE.match(p.name)
        if m:
            out.append({"cycle": int(m.group(1)), "path": str(p)})
    return sorted(out, key=lambda r: r["cycle"])


def next_vet_cycle(item_dir: Path) -> int:
    """The cycle number the NEXT vet report gets (1-based; monotonic — reports are never deleted)."""
    reports = vet_reports(item_dir)
    return (reports[-1]["cycle"] + 1) if reports else 1


def latest_vet_report(item_dir: Path, *, char_cap: int = 8000) -> dict | None:
    """The newest vet report {cycle, path, text, truncated} — the step-6 handoff payload for the
    next build cycle (capped: hermes build_worker_context precedent, §8·O10)."""
    reports = vet_reports(item_dir)
    if not reports:
        return None
    r = reports[-1]
    text = Path(r["path"]).read_text()
    return {**r, "text": text[:char_cap], "truncated": len(text) > char_cap}


def vet_report_issues(item_dir: Path, verdicts: list[dict], findings: str) -> list[str]:
    """The mechanical gate on a vet report (fail-closed, itemized). Rules:
    verdicts non-empty, each {check, passed}, check ids unique; every check id must exist in
    plan.md's `## Vet plan` (join-key integrity — an invented id can't key the ledger); the report
    must cover EVERY plan check (a silently skipped check reads as covered when it wasn't); every
    verdict must be BACKED by a ledger entry for that check, and `passed` must MATCH the latest
    entry (a verdict that contradicts recorded evidence is false by construction); any FAIL verdict
    requires non-empty findings (describe what was seen)."""
    issues: list[str] = []
    if not verdicts:
        return ["a vet report needs at least one verdict"]
    seen: set[str] = set()
    for v in verdicts:
        cid = str(v.get("check") or "").strip()
        if not cid or "passed" not in v:
            issues.append(f"malformed verdict {v!r} — needs check + passed")
            continue
        if cid in seen:
            issues.append(f"duplicate verdict for check {cid!r}")
        seen.add(cid)
    plan_path = Path(item_dir) / "artifacts" / artifact_file("plan")
    vp = parse_vet_plan(plan_path.read_text()) if plan_path.exists() else {"present": False}
    if vp.get("present"):
        plan_ids = {c["id"] for c in vp["checks"]}
        for cid in sorted(seen - plan_ids):
            issues.append(f"check {cid!r} is not in the plan's vet plan — verdicts key on the "
                          "plan's check ids verbatim")
        for cid in sorted(plan_ids - seen):
            issues.append(f"plan check {cid!r} has no verdict — every vet-plan check must be "
                          "covered (record evidence and give a verdict, pass or fail)")
    latest: dict[str, dict] = {}
    for e in evidence_entries(item_dir):
        latest[e["check"]] = e
    for v in verdicts:
        cid = str(v.get("check") or "").strip()
        if not cid or cid not in seen:
            continue
        e = latest.get(cid)
        if e is None:
            issues.append(f"verdict for {cid!r} has no evidence — record_validation_evidence "
                          "first; a verdict without a ledger entry is an assertion, not a result")
        elif bool(e.get("passed")) != bool(v.get("passed")):
            issues.append(f"verdict for {cid!r} contradicts the ledger (latest evidence says "
                          f"passed={e.get('passed')}) — the ledger is the truth; re-run the check "
                          "or fix the verdict")
    if any(not v.get("passed") for v in verdicts) and not (findings or "").strip():
        issues.append("failing verdicts need findings — expected vs actual + verbatim evidence "
                      "(describe what you saw; never prescribe the fix)")
    return issues


def write_vet_report(item_dir: Path, *, verdicts: list[dict], findings: str,
                     out_of_scope: str = "") -> dict:
    """Write this cycle's `vet-report-<n>.md` — the envelope is code-owned (§4b shape), the
    findings/out-of-scope prose is the agent's. Validates via vet_report_issues first and raises
    ValueError with the itemized refusal (no file written on refusal). Returns {cycle, path}."""
    issues = vet_report_issues(item_dir, verdicts, findings)
    if issues:
        raise ValueError("; ".join(issues))
    cycle = next_vet_cycle(item_dir)
    adir = Path(item_dir) / "artifacts"
    adir.mkdir(parents=True, exist_ok=True)
    path = adir / f"vet-report-{cycle}.md"
    lines = [f"---\nartifact: vet-report\ncycle: {cycle}\nreader: agent\n---",
             f"# Vet report — cycle {cycle}", "", "## Verdicts"]
    lines += [f"- {str(v['check']).strip()} — {'PASS' if v.get('passed') else 'FAIL'}"
              for v in verdicts]
    lines += ["", "## Findings", (findings or "").strip() or "_(all checks passed — nothing to report)_",
              "", "## Out of scope (does NOT gate — for review)",
              (out_of_scope or "").strip() or "_none_", ""]
    _atomic_write(path, "\n".join(lines))
    return {"cycle": cycle, "path": str(path)}


# --------------------------------------------------------------------------- attempts ledger (build-vet-loop §5)
# The LOOP DRIVER's own ledger: one entry per driver decision (`attempts.md`), consumed by the
# convergence guard (previous fingerprint vs current — deterministic, never recalled by an agent)
# and by review (the honest history of what the loop did and why it stopped). APPEND-ONLY, same
# line-oriented shape as the evidence ledger. Written ONLY by code — no agent pen exists for it.

_ATTEMPT_HEAD = re.compile(r"^### (?P<ts>\S+) — cycle (?P<cycle>\d+)$")

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


def append_attempt(item_dir: Path, *, cycle: int, evidence: str, decision: str, reason: str,
                   fingerprint: str = "", failed: list[str] | tuple = (),
                   tokens: int | None = None, budget: int | None = None) -> dict:
    """Append one driver decision to `artifacts/attempts.md` (creating it first). `cycle` is the
    vet cycle the decision followed; `evidence` the evidence_status verdict; `decision` what the
    driver did (review|build|revet|halt); `reason` the one-line why; `fingerprint` the failure
    fingerprint (convergence guard input); `tokens`/`budget` the meter reading at decision time."""
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    path = Path(item_dir) / "artifacts" / "attempts.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _atomic_write(path, "# Attempts — loop driver ledger\n\nOne entry per driver decision. "
                            "Append-only; written by the daemon, never by an agent.\n")
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry = (f"\n### {ts} — cycle {int(cycle)}\n"
             f"- evidence: {_one_line(evidence)}\n"
             f"- decision: {_one_line(decision)}\n"
             f"- reason: {_one_line(reason)}\n")
    if fingerprint:
        entry += f"- fingerprint: {_one_line(fingerprint)}\n"
    if failed:
        entry += f"- failed: {', '.join(_one_line(str(f)) for f in failed)}\n"
    if tokens is not None and budget is not None:
        entry += f"- tokens: {int(tokens)} / {int(budget)}\n"
    _atomic_write(path, path.read_text() + entry)
    return {"ts": ts, "cycle": int(cycle), "decision": decision}


def read_attempts(item_dir: Path) -> list[dict]:
    """Parse the attempts ledger back: [{ts, cycle, evidence, decision, reason, fingerprint?,
    failed?, tokens?}] in file order (oldest first)."""
    path = Path(item_dir) / "artifacts" / "attempts.md"
    if not path.exists():
        return []
    entries: list[dict] = []
    cur: dict | None = None
    for line in path.read_text().splitlines():
        m = _ATTEMPT_HEAD.match(line)
        if m:
            cur = {"ts": m.group("ts"), "cycle": int(m.group("cycle"))}
            entries.append(cur)
        elif cur is not None:
            kv = re.match(r"^- (evidence|decision|reason|fingerprint|failed|tokens): (.*)$", line)
            if kv:
                cur[kv.group(1)] = kv.group(2).strip()
    return entries


_VERDICT_LINE = re.compile(r"^-\s+(?P<check>\S.*?)\s+—\s+(?P<verdict>PASS|FAIL)\s*$")


def parse_verdict_line(line: str) -> tuple[str, bool] | None:
    """Parse ONE vet-report `## Verdicts` line (`- <check> — PASS|FAIL`, the shape written by
    `write_vet_report`) → `(check, passed)`, or None when the line isn't a verdict. THE single
    reader for the one writer: both `loop_instruments` here and `_report_verdict_summary`
    (kernel_speech) route through this, so the format can't desync into two divergent regexes."""
    m = _VERDICT_LINE.match(line)
    if not m:
        return None
    return m.group("check"), m.group("verdict") == "PASS"


def loop_instruments(item_dir: Path, *, findings_cap: int = 2500) -> dict:
    """The build⟷vet loop's live instrument panel (renovation §2 Loop row) — everything derived
    from data the loop already writes, nothing stored: check ids in plan order · one verdict
    column per filed vet report (the checks×cycles matrix) · the newest report's Findings
    verbatim when anything failed (the expected-vs-actual the builder is working from) · the
    driver's attempt trail. Empty checks + cycles ⇒ the surface hides the panel."""
    item_dir = Path(item_dir)
    plan_path = item_dir / "artifacts" / artifact_file("plan")
    vp = parse_vet_plan(plan_path.read_text()) if plan_path.is_file() else {"checks": []}
    check_ids = [c["id"] for c in vp.get("checks", [])]
    cycles: list[dict] = []
    latest_failed = False
    for r in vet_reports(item_dir):
        text = Path(r["path"]).read_text()
        verdicts = {pv[0]: pv[1] for pv in
                    (parse_verdict_line(ln) for ln in
                     _split_sections(text).get("Verdicts", "").splitlines()) if pv}
        cycles.append({"cycle": r["cycle"], "verdicts": verdicts})
        for cid in verdicts:              # review-routed checks appear mid-loop — keep them
            if cid not in check_ids:
                check_ids.append(cid)
        latest_failed = not all(verdicts.values()) if verdicts else latest_failed
    findings = None
    if cycles and latest_failed:
        latest = latest_vet_report(item_dir, char_cap=findings_cap * 4)
        if latest:
            body = _split_sections(latest["text"]).get("Findings", "").strip()
            findings = body[:findings_cap] if body else None
    attempts = [{"cycle": a["cycle"], "decision": a.get("decision", ""),
                 "reason": a.get("reason", ""), "ts": a["ts"]}
                for a in read_attempts(item_dir)]
    return {"checks": check_ids, "cycles": cycles, "findings": findings, "attempts": attempts}


# --------------------------------------------------------------------------- checkpoints

def write_checkpoint(item_dir: Path, repo_dir: Path | None, *, working_on: str, decisions: str,
                     remaining: str, notes: str = "") -> str:
    """Bank one continuity checkpoint (gstack 4-section + git-state header) to
    `checkpoints/<YYYYMMDD-HHMMSS>.md`. APPEND-ONLY (a new timestamped file every time, never
    overwrite; filename IS the canonical order) + atomic. Content rule (D11): conversation-native
    reasoning — decisions, leans, tried-and-failed; reference artifacts BY PATH, never duplicate
    them. Returns the file path."""
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
    text = (f"---\ncheckpoint: {ts}\ngit: {git_line}\nreader: agent\n---\n"
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


def latest_checkpoint(item_dir: Path, *, char_cap: int = 6000) -> dict | None:
    """The newest checkpoint (by filename — canonical order), char-capped for the S5 cold-start
    orient block (restored state is DATA, not instructions). None when none exist."""
    cdir = Path(item_dir) / "checkpoints"
    if not cdir.is_dir():
        return None
    # Sort by STEM, not filename: a same-second collision file `<ts>-1` must sort AFTER `<ts>`,
    # but with the `.md` suffix attached '-' < '.' would order it before.
    files = sorted(cdir.glob("*.md"), key=lambda p: p.stem)
    if not files:
        return None
    text = files[-1].read_text()
    return {"path": str(files[-1]), "text": text[:char_cap],
            "truncated": len(text) > char_cap}


# --------------------------------------------------------------------------- closeout verification

def _parse_facts(section_body: str) -> tuple[dict, str | None]:
    """The Facts section's fenced yaml block → dict. Returns ({}, error) on a missing/broken block."""
    m = re.search(r"```ya?ml\s*\n(.*?)```", section_body, re.DOTALL)
    if not m:
        return {}, "Facts section has no ```yaml block (the scaffold provides one — keep it)"
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        return {}, f"Facts yaml does not parse: {e}"
    if not isinstance(data, dict):
        return {}, "Facts yaml must be a mapping"
    return data, None


def verify_closeout(item_dir: Path, repo_dir: Path | None) -> tuple[bool, list[str]]:
    """Ground-truth verification of closeout claims (D6 §3, hermes anti-hallucination): every
    `changed_files` path must exist under the repo, a non-empty `merge_commit` must be a real
    commit, and every `## Artifacts` bullet path must exist. Itemized issues, no state change —
    the close gate consumes this (S6). Self-check issues surface here too (one gate, one list)."""
    issues = list(self_check(item_dir, "closeout"))
    path = Path(item_dir) / "artifacts" / artifact_file("closeout")
    if not path.exists():
        return False, issues
    sections = _split_sections(path.read_text())
    facts, err = _parse_facts(sections.get("Facts", ""))
    if not err:
        repo = Path(repo_dir) if repo_dir else None
        for f in facts.get("changed_files") or []:
            if not repo or not (repo / str(f)).exists():
                issues.append(f"claimed changed file does not exist: {f}")
        mc = str(facts.get("merge_commit") or "").strip()
        if mc:
            okc = False
            if repo and repo.is_dir():
                try:
                    okc = subprocess.run(["git", "cat-file", "-e", f"{mc}^{{commit}}"], cwd=repo,
                                         capture_output=True, timeout=10).returncode == 0
                except (OSError, subprocess.SubprocessError):
                    okc = False
            if not okc:
                issues.append(f"claimed merge_commit is not a real commit: {mc}")
    for line in (sections.get("Artifacts") or "").splitlines():
        m = re.match(r"^\s*[-*]\s+`?([^`\s]+)`?\s*$", line)
        if m and m.group(1).lower() not in ("none", "—"):
            p = m.group(1)
            target = Path(p) if os.path.isabs(p) else Path(item_dir) / p
            if not target.exists():
                issues.append(f"claimed artifact path does not exist: {p}")
    return (not issues), issues


# --------------------------------------------------------------------------- computed status

def artifact_status(item: dict, item_dir: Path, repo_dir: Path | None = None) -> dict:
    """The COMPUTED per-artifact status map (D6 §4 — derived from file existence + self-check +
    evidence freshness; never stored in any doc): {kind → {required, present, issues, status}}.
    `validation` additionally carries the evidence verdict. Feeds the S7 drilldown."""
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
        if kind == "validation" and present:
            row["evidence"] = evidence_status(item_dir, repo_dir)
        out[kind] = row
    return out
