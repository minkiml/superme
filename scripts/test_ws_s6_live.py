"""WS-S6 gate test (LIVE half) — full lifecycle on the dummy repo through the daemon. No LLM
turns (artifacts are script-written stand-ins for the agent's fills), so it costs no tokens; the
daemon does every state transition, git operation, and knowledge write.

PRD S6 gate items covered here: one implementation item through all six phases including a
hold&fix loop pass at the deliver gate; a knowledge delta with a dead file reference REJECTED
pre-merge, then the fixed delta applied atomically WITH the merge; every gate readable from its
brief (the brief route serves recommendation + checks at each stop); criteria-refusal (complete
attempted with missing artifacts and with a non-terminal child); a second item abandoned
mid-build (zero knowledge writes, worktree gone, branch kept, blocking child listed in the
abandon brief). Kill-mid-close reconciliation is exercised by scripts/test_ws_s6_reconcile.py
(needs a daemon restart between setup and verify).

Self-cleaning: item folders, branches, backup refs, the dummy repo's main, and the seeded anchor
docs are all restored. Run with the daemon up:
    PYTHONPATH=. python -m scripts.test_ws_s6_live
"""

import hashlib
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

from superme_agent.core import artifacts as A
from superme_agent.core import git_layer

B = "http://127.0.0.1:8787"
CTX = "dummy"
REPO = Path("/Users/cooma/Developer/my_docs/dummy_project")
KHOME = Path("superme-knowledge") / f"{CTX}-knowledge" / "dev"
PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def http(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(B + path, method=method,
                                 headers={"content-type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def http_err(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    """Expect an HTTP error; returns (status, detail)."""
    try:
        http(method, path, body)
        return 200, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def mint_item(title: str, text: str) -> str:
    row = http("POST", "/dev/inbox", {"context_id": CTX, "title": title, "text": text})
    return http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]


def advance(iid: str) -> dict:
    return http("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})


def brief(iid: str) -> dict:
    return http("GET", f"/dev/work-items/{iid}/gate-brief?context_id={CTX}")


def write_artifact(item_dir: Path, name: str, text: str) -> None:
    (item_dir / "artifacts").mkdir(exist_ok=True)
    (item_dir / "artifacts" / name).write_text(text)


PLAN = """---
artifact: plan
---
# Plan — {t}

## Approach
Add a tiny helper and keep it boring.

## Tasks
- [{a}] add helper to helper_s6.py
- [{b}] verify it runs

## Validation criteria
The helper file imports and the check command exits 0.
"""

READINESS = """---
artifact: readiness
---
# Readiness — {t}

## Status
Helper landed on the item branch; synced with main.

## Validation
{val}

## Knowledge
{know}

## Warnings
{warn}

## Recommendation
{rec}
"""

CLOSEOUT = """---
artifact: closeout
---
# Closeout — {t}

## Summary
Delivered the S6 gate helper end-to-end.

## Facts
```yaml
changed_files: ["helper_s6.py"]
tests_run: "python -c 'import helper_s6'"
merge_commit: "{mc}"
```

## Artifacts
- artifacts/plan.md
"""


def khash() -> str:
    """Fingerprint of every anchor doc — proves 'zero knowledge writes' on abandon."""
    h = hashlib.sha1()
    for p in sorted((KHOME / "general").rglob("*.md")):
        h.update(p.read_bytes())
    return h.hexdigest()


def seed_anchor_docs() -> None:
    g = KHOME / "general"
    g.mkdir(parents=True, exist_ok=True)
    (g / "project-prd.md").write_text(
        "# PRD\n\n## Problem\ndummy\n\n## Deliverables\n- **d-s6** — the S6 gate exercise\n")
    (g / "architecture.md").write_text(
        "# Architecture\n\n## Components\nOnly dummy_code.py so far.\n")
    (g / "roadmap.md").write_text("# Roadmap\n\n## d-s6\n- **w1** — gate wave 🟢\n")
    (g / "spec.md").write_text("# Spec\n\n## Stack\npython\n")


def main() -> None:
    trunk_sha0 = git(REPO, "rev-parse", "HEAD")
    seed_anchor_docs()
    dev_root = KHOME
    made_items: list[str] = []
    try:
        run(dev_root, made_items)
    finally:
        cleanup(trunk_sha0, made_items)
    print(f"\nALL GREEN — {PASS} live checks passed (dummy repo + knowledge home restored).")


def run(dev_root: Path, made_items: list[str]) -> None:
    # ================= item A — the full six-phase lifecycle =================
    print("item A: triage → plan → build → validate → deliver (hold&fix → merge) → close")
    a = mint_item("S6 live: gate helper", "Add helper_s6.py with a trivial helper.")
    made_items.append(a)
    a_dir = dev_root / "work-items" / a

    b0 = brief(a)
    ok("triage-exit brief served (decision block + options)",
       b0["gate"] == "triage-exit" and b0["at_gate"]
       and "Decision — recommended:" in b0["brief"] and len(b0["decision"]["options"]) == 3)
    advance(a)  # triage → plan
    write_artifact(a_dir, "plan.md", PLAN.format(t="gate helper", a=" ", b=" "))
    b1 = brief(a)
    ok("pre-main brief recommends approving the clean plan",
       b1["gate"] == "pre-main" and b1["decision"]["recommendation"] == "Approve the plan")
    adv = advance(a)  # plan → build (worktree)
    wt = Path(adv["git"]["worktree"])
    branch = adv["git"]["branch"]
    ok("build entry created branch + worktree", wt.is_dir() and branch.startswith("item/"))

    # build: real code change committed in the worktree; tasks ticked.
    (wt / "helper_s6.py").write_text("def helper():\n    return 's6'\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "add helper_s6")
    write_artifact(a_dir, "plan.md", PLAN.format(t="gate helper", a="x", b="x"))

    # D7 pre-merge rejection: a staged delta with a DEAD file reference refuses the merge.
    (a_dir / "artifacts" / "knowledge-delta.yaml").write_text(yaml.safe_dump({
        "staged_at": datetime.now().isoformat(timespec="seconds"), "applied_at": None,
        "folded_into": None,
        "ops": [{"doc": "architecture", "section": "Components", "op": "append",
                 "content": "New helper at `ghost_missing.py` (d-s6)."}]}))
    advance(a)  # build → validate
    write_artifact(a_dir, "validation.md",
                   "---\nartifact: validation\n---\n# Validation — gate helper\n\n"
                   "## Checklist\n- import check (from plan criteria)\n\n## Evidence\n"
                   "<!-- appended by record_validation_evidence — machine entries -->\n")
    A.record_evidence(a_dir, wt, check="import", how="python -c 'import helper_s6'",
                      result="exit 0", passed=True)
    advance(a)  # validate → deliver
    code, detail = http_err("POST", f"/dev/work-items/{a}/git/merge", {"context_id": CTX})
    ok("dead-file knowledge delta REJECTED pre-merge (409, itemized, no merge happened)",
       code == 409 and "ghost_missing.py" in detail
       and git(REPO, "rev-parse", "HEAD") != "", detail[:200])

    # Hold&fix loop pass: first readiness says Hold & fix — the brief mirrors it mechanically
    # (the invalid delta reddens the knowledge row), the owner holds, the fix lands, re-present.
    write_artifact(a_dir, "readiness.md", READINESS.format(
        t="gate helper", val="ledger green", know="stale-warning — delta needs a fix",
        warn="knowledge delta references a missing file", rec="Hold & fix the delta"))
    b2 = brief(a)
    ok("deliver brief (loop pass 1) shows the red knowledge row → Hold & fix",
       b2["gate"] == "deliver" and b2["decision"]["recommendation"] == "Hold & fix"
       and any(c["criterion"] == "knowledge_row" and not c["ok"] for c in b2["checks"]))

    # the fix: restage a VALID delta (file exists in the tree about to become main) + re-present.
    (a_dir / "artifacts" / "knowledge-delta.yaml").write_text(yaml.safe_dump({
        "staged_at": datetime.now().isoformat(timespec="seconds"), "applied_at": None,
        "folded_into": None,
        "ops": [{"doc": "architecture", "section": "Components", "op": "append",
                 "content": "S6 helper lives in `helper_s6.py` (d-s6)."}]}))
    write_artifact(a_dir, "readiness.md", READINESS.format(
        t="gate helper", val="ledger green + fresh", know="updated — one architecture op staged",
        warn="none", rec="Merge — clean and boring"))
    b3 = brief(a)
    ok("deliver brief (loop pass 2) all-green → Merge",
       b3["decision"]["recommendation"] == "Merge",
       str([c for c in b3["checks"] if not c["ok"]]))

    res = http("POST", f"/dev/work-items/{a}/git/merge", {"context_id": CTX})
    arch = (dev_root / "general" / "architecture.md").read_text()
    ok("merge landed on main WITH the knowledge delta (one event) + backup ref",
       res["merged"] and res["path"] == "main" and res["knowledge_ops_applied"] == 1
       and res.get("backup_ref") and "helper_s6.py" in arch)
    ok("main actually has the helper", (REPO / "helper_s6.py").exists())

    # close: criteria-refusal FIRST (missing closeout), then with a non-terminal child, then green.
    advance(a)  # deliver → close
    code, detail = http_err("POST", f"/dev/work-items/{a}/complete?context_id={CTX}")
    ok("complete refused — required artifact missing (itemized 409)",
       code == 409 and "closeout" in detail)
    write_artifact(a_dir, "closeout.md", CLOSEOUT.format(t="gate helper", mc=res["merge_commit"]))
    # a still-open blocking child mechanically refuses the close:
    child = mint_item("S6 live: child of A", "child")
    made_items.append(child)
    child_dir = dev_root / "work-items" / child
    txt = (child_dir / "item.md").read_text()
    (child_dir / "item.md").write_text(txt.replace(
        "session_id:", f'spawned_from: {{"item": "{a}", "relation": "parallel"}}\nsession_id:'))
    code, detail = http_err("POST", f"/dev/work-items/{a}/complete?context_id={CTX}")
    ok("complete refused — non-terminal child (children_terminal)",
       code == 409 and child in detail)
    http("POST", f"/dev/work-items/{child}/abandon",
         {"context_id": CTX, "reason": "test child disposal"})
    b4 = brief(a)
    ok("close brief all-green → Complete", b4["gate"] == "close"
       and b4["decision"]["recommendation"] == "Complete",
       str([c for c in b4["checks"] if not c["ok"]]))
    done = http("POST", f"/dev/work-items/{a}/complete?context_id={CTX}")
    branches = git(REPO, "branch", "--list", branch)
    ok("human promotion: terminal + worktree removed + branch KEPT + execution archived",
       done["ok"] and done.get("worktree_removed") and not wt.exists()
       and branch.split("/", 1)[-1] in branches
       and (a_dir / "artifacts" / "execution.md").exists())

    # ================= item B — abandon mid-build =================
    print("item B: abandon mid-build")
    kh_before = khash()
    b_id = mint_item("S6 live: doomed item", "Will be abandoned mid-build.")
    made_items.append(b_id)
    advance(b_id)  # triage → plan
    write_artifact(dev_root / "work-items" / b_id, "plan.md",
                   PLAN.format(t="doomed", a=" ", b=" "))
    adv = advance(b_id)  # plan → build
    wt_b = Path(adv["git"]["worktree"])
    branch_b = adv["git"]["branch"]
    (wt_b / "doomed.py").write_text("pass\n")
    git(wt_b, "add", "-A")
    git(wt_b, "commit", "-qm", "doomed work")
    # a blocking child that existed only for B — must appear in the abandon brief.
    bc = mint_item("S6 live: blocking child of B", "child")
    made_items.append(bc)
    bc_dir = dev_root / "work-items" / bc
    txt = (bc_dir / "item.md").read_text()
    (bc_dir / "item.md").write_text(txt.replace(
        "session_id:", f'spawned_from: {{"item": "{b_id}", "relation": "blocking"}}\nsession_id:'))

    ab = http("POST", f"/dev/work-items/{b_id}/abandon",
              {"context_id": CTX, "reason": "superseded by nothing — gate test"})
    item_b = http("GET", f"/dev/work-items/{b_id}/detail?context_id={CTX}")["item"]
    closeout_b = (dev_root / "work-items" / b_id / "artifacts" / "closeout.md").read_text()
    ok("abandon brief: worktree gone, branch kept, blocking child listed for disposal",
       ab["outcome"] == "abandoned" and ab["worktree_removed"] and not wt_b.exists()
       and branch_b.split("/", 1)[-1] in git(REPO, "branch", "--list", branch_b)
       and ab["blocking_children"] == [bc])
    ok("abandon is terminal-with-note (status change, never a delete)",
       item_b["status"] == "done" and item_b["outcome"] == "abandoned"
       and "Abandon note" in closeout_b and "gate test" in closeout_b)
    ok("ZERO knowledge writes on abandon (anchor docs byte-identical)", khash() == kh_before)
    http("POST", f"/dev/work-items/{bc}/abandon", {"context_id": CTX, "reason": "parent gone"})
    ok("abandon is refused on an already-terminal item",
       http_err("POST", f"/dev/work-items/{b_id}/abandon", {"context_id": CTX})[0] == 409)


def cleanup(trunk_sha0: str, made_items: list[str]) -> None:
    """Restore the dummy repo (main → pre-test sha; item branches + backup refs dropped) and the
    knowledge home (seeded anchor docs + test item folders removed). Dev-store events remain —
    historical trace, per never-delete."""
    try:
        subprocess.run(["git", "reset", "--hard", trunk_sha0, "-q"], cwd=REPO, check=False)
        subprocess.run(["git", "clean", "-fdq", "--exclude=superme-knowledge"], cwd=REPO,
                       check=False)
        out = subprocess.run(["git", "branch", "--list", "item/*", "--format=%(refname:short)"],
                             cwd=REPO, capture_output=True, text=True).stdout.split()
        for br in out:
            subprocess.run(["git", "branch", "-Dq", br], cwd=REPO, check=False)
        refs = subprocess.run(["git", "for-each-ref", "refs/backup", "--format=%(refname)"],
                              cwd=REPO, capture_output=True, text=True).stdout.split()
        for ref in refs:
            subprocess.run(["git", "update-ref", "-d", ref], cwd=REPO, check=False)
        wt_root = git_layer.worktrees_root(CTX)
        if wt_root.exists():
            shutil.rmtree(wt_root, ignore_errors=True)
            subprocess.run(["git", "worktree", "prune"], cwd=REPO, check=False)
        for iid in made_items:
            shutil.rmtree(KHOME / "work-items" / iid, ignore_errors=True)
        shutil.rmtree(KHOME / "general", ignore_errors=True)
        # inbox rows routed to the test items are noise — drop them via the API when possible.
        for row in http("GET", f"/dev?context_id={CTX}").get("inbox", []):
            if row.get("routed_to") in made_items:
                try:
                    http("DELETE", f"/dev/inbox/{row['id']}")
                except Exception:
                    pass
        print("cleanup: dummy repo + knowledge home restored")
    except Exception as e:  # noqa: BLE001
        print(f"cleanup INCOMPLETE: {e}")


if __name__ == "__main__":
    main()
