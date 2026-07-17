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
import os
import re
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

import yaml

from .kind_profiles import get_profile

FILL = re.compile(r"<fill:[^>]*>")


# --------------------------------------------------------------------------- templates
# One skeleton per artifact kind; `plan` varies by ITEM kind (implementation vs research — the D2
# pre-main slot differs: build-plan vs question/method/boundaries). Section ORDER is code-owned.

_PLAN_IMPL = """# Plan — {title}

## Approach
<fill:approach — what we'll build and how, grounded in the preliminary brief>

## Tasks
- [ ] <fill:first task — break the approach into checkable steps>

## Validation criteria
<fill:how we'll know it works — each criterion machine-checkable where possible>
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
<fill:the checks planned, from plan.md's validation criteria>

## Evidence
<!-- appended by record_validation_evidence — machine entries; do not hand-edit -->
"""

_READINESS = """# Readiness — {title}

## Status
<fill:one-paragraph state of the item — what changed since the plan was approved>

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
    "plan":        {"file": "plan.md",       "required": ()},  # per item-kind, resolved below
    "validation":  {"file": "validation.md", "required": ("Checklist",)},
    "readiness":   {"file": "readiness.md",  "required": ("Status", "Validation", "Knowledge",
                                                          "Warnings", "Recommendation")},
    "findings":    {"file": "findings.md",   "required": ("Questions", "Findings", "Implications",
                                                          "Follow-ups")},
    "closeout":    {"file": "closeout.md",   "required": ("Summary", "Facts", "Artifacts")},
    "handoff-brief": {"file": "handoff-brief.md", "required": ()},
}
_PLAN_REQUIRED = {
    "implementation": ("Approach", "Tasks", "Validation criteria"),
    "research": ("Questions", "Method", "Boundaries", "Done criteria", "Tasks"),
}
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
          + f"item_kind: {item_kind}\ncreated_at: {date.today().isoformat()}\n---\n")
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
    for req in required_sections(artifact, item_kind):
        if req not in sections:
            issues.append(f"missing required section '## {req}'")
        elif not _section_filled(sections[req]):
            issues.append(f"section '## {req}' is empty")
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
    fm = f"---\nartifact: handoff-brief\ntitle: {title!r}\ncreated_at: {date.today().isoformat()}\n---\n"
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


def record_evidence(item_dir: Path, repo_dir: Path | None, *, check: str, how: str,
                    result: str, passed: bool, title: str = "",
                    item_kind: str | None = None) -> dict:
    """Append one machine entry to the validation.md evidence ledger (scaffolding it first if
    absent): check + how it ran + the machine result + pass/fail + the repo fingerprint at record
    time. Entries are APPEND-ONLY; 'validated' is derived from them, never asserted."""
    # Single-line coerce: the ledger is line-oriented (one `- key: value` per line) — an embedded
    # newline in any field would corrupt parsing for every entry after it.
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    check, how, result = _one_line(check), _one_line(how), _one_line(result)
    if not (check and how and result):
        raise ValueError("evidence needs non-empty check, how, and result")
    scaffold(item_dir, "validation", title=title, item_kind=item_kind)
    path = Path(item_dir) / "artifacts" / artifact_file("validation")
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fp = repo_fingerprint(repo_dir)
    entry = (f"\n### {ts} — {check}\n"
             f"- how: {how}\n"
             f"- result: {result}\n"
             f"- passed: {'true' if passed else 'false'}\n"
             f"- fingerprint: {fp}\n")
    _atomic_write(path, path.read_text() + entry)
    return {"ts": ts, "check": check, "passed": passed, "fingerprint": fp}


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
            kv = re.match(r"^- (how|result|passed|fingerprint): (.*)$", line)
            if kv:
                k, v = kv.group(1), kv.group(2).strip()
                cur[k] = (v == "true") if k == "passed" else v
    return entries


def evidence_status(item_dir: Path, repo_dir: Path | None) -> dict:
    """The derived verdict over the ledger (D6 §2, hermes stale-on-edit): `unverified` (no
    entries) · `failed` (latest entry of any check failed) · `stale` (all latest entries passed
    but the repo fingerprint moved since) · `passed` (green AND fresh)."""
    entries = evidence_entries(item_dir)
    if not entries:
        return {"status": "unverified", "entries": 0}
    latest: dict[str, dict] = {}
    for e in entries:               # last entry per check wins
        latest[e["check"]] = e
    now_fp = repo_fingerprint(repo_dir)
    failed = [c for c, e in latest.items() if not e.get("passed")]
    if failed:
        return {"status": "failed", "entries": len(entries), "failed_checks": failed}
    stale = [c for c, e in latest.items() if e.get("fingerprint") != now_fp]
    if stale:
        return {"status": "stale", "entries": len(entries), "stale_checks": stale}
    return {"status": "passed", "entries": len(entries)}


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
    text = (f"---\ncheckpoint: {ts}\ngit: {git_line}\n---\n"
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
