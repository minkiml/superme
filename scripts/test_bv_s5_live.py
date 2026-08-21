"""BV-S5 gate test (LIVE half) — the build⟷vet loop driving itself end-to-end. COSTS TOKENS
(≈3 real agent runs on the dummy repo: vet-fail → build-fix → vet-pass). Drives what the offline
suite can't: the daemon-side driver actually chains background runs off evidence_status(), the vet
report hands off to a REAL build cycle that fixes the code, and the loop exits at the review gate
on its own — zero human actions between the launch and the page.

Claims verified live:
  · POST /dev/work-items/{id}/vet launches the loop; the item is left untouched by hand after;
  · cycle 1: a real vet run FAILS the planted defect, files vet-report-1.md, ledger goes red;
  · the driver flips vet→build and a build cycle (handed the report) fixes the worktree code;
  · the driver flips build→vet, a FRESH vet run passes, and the item lands at review /
    awaiting_human — the loop's only happy exit;
  · attempts.md carries the driver's decisions (build → review) and loop events hit the dev log;
  · run history shows the vet/build features; the cycle-1 vet thread was retired (vet forgets).

Artifacts are script-written stand-ins for the gate advances (s6_live pattern). Self-cleaning:
abandon + repo/knowledge restore. Run with the daemon up:
PYTHONPATH=. python -m scripts.test_bv_s5_live
"""

import os
import json
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from superme_agent.core import git_layer

B = "http://127.0.0.1:8787"
CTX = "dummy"
# This suite MUTATES the repo it points at: `git reset --hard`, `git clean -fd`, branch
# deletion. Name a throwaway one, and never a repo holding work you want.
REPO = Path(os.environ.get("SUPERME_TEST_REPO") or "~/superme-test-repo").expanduser()
if not (REPO / ".git").is_dir():
    raise SystemExit(f"SUPERME_TEST_REPO is not a git repo: {REPO}")
KHOME = Path("superme-knowledge") / f"{CTX}-knowledge" / "dev"
DB = Path("superme_agent") / ".system.db"
PASS = 0
LOOP_TIMEOUT = 900   # the whole autonomous loop (3 small runs) — generous

PLAN = """---
artifact: plan
---
# Plan — s5 loop probe

## Approach
One probe file; the point is the loop driving itself.

## Tasks
- [x] add probe file returning the right value

## Inner checks
- `python -c "import probe_s5; assert probe_s5.probe() == 's5'"`

## Vet plan
depth: checks
reason: one contained file — a single inspection check suffices
env: none

### probe-value
- traces: d-s5 — the probe deliverable
- mode: command
- scenario: in the worktree, run `python -c "import probe_s5; print(probe_s5.probe())"`
- expect: the command prints exactly s5 and exits 0
"""


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


def retry_409(method: str, path: str, body: dict | None = None, tries: int = 20) -> dict:
    for _ in range(tries):
        try:
            return http(method, path, body)
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
            time.sleep(3)
    raise AssertionError(f"{path} stayed 409")


def item_row(iid: str) -> dict:
    for it in http("GET", f"/dev?context_id={CTX}").get("work_items", []):
        if it.get("id") == iid:
            return it
    return {}


def spine_session(sid: str) -> dict | None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
    con.close()
    return dict(r) if r else None


def item_runs(iid: str) -> list[dict]:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT feature, phase, status FROM run WHERE item_id=? ORDER BY id",
                       (iid,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def cleanup(trunk_sha0: str, iid: str | None) -> None:
    try:
        if iid:
            try:
                retry_409("POST", f"/dev/work-items/{iid}/abandon",
                          {"context_id": CTX, "reason": "bv-s5 probe done"})
            except Exception as e:  # noqa: BLE001
                print(f"cleanup: abandon failed ({e})")
        subprocess.run(["git", "reset", "--hard", trunk_sha0, "-q"], cwd=REPO, check=False)
        subprocess.run(["git", "clean", "-fdq", "--exclude=superme-knowledge"], cwd=REPO, check=False)
        wt_root = git_layer.worktrees_root(CTX)
        if wt_root.exists():
            shutil.rmtree(wt_root, ignore_errors=True)
            subprocess.run(["git", "worktree", "prune"], cwd=REPO, check=False)
        for br in subprocess.run(["git", "branch", "--list", "item/*", "--format=%(refname:short)"],
                                 cwd=REPO, capture_output=True, text=True).stdout.split():
            subprocess.run(["git", "branch", "-Dq", br], cwd=REPO, check=False)
        if iid:
            shutil.rmtree(KHOME / "work-items" / iid, ignore_errors=True)
            for row in http("GET", f"/dev?context_id={CTX}").get("inbox", []):
                if row.get("routed_to") == iid:
                    http("DELETE", f"/dev/inbox/{row['id']}")
        print("cleanup: dummy repo + knowledge home restored")
    except Exception as e:  # noqa: BLE001
        print(f"cleanup INCOMPLETE: {e}")


def main() -> None:
    trunk_sha0 = git(REPO, "rev-parse", "HEAD")
    iid = None
    try:
        row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "BV-S5 live: loop probe",
                                          "text": "Loop-driver probe item."})
        iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
        item_dir = KHOME / "work-items" / iid
        print(f"item = {iid}")

        retry_409("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})  # triage → plan
        (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (item_dir / "artifacts" / "plan.md").write_text(PLAN)
        adv = retry_409("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})  # plan → build
        wt = Path(adv["git"]["worktree"])
        # The planted DEFECT: the probe returns the wrong value — cycle 1 must fail on it.
        (wt / "probe_s5.py").write_text("def probe():\n    return 'WRONG'\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-qm", "add probe_s5 (planted defect)")
        retry_409("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})  # build → vet

        # --- the single human action: launch the loop --------------------------------
        r = http("POST", f"/dev/work-items/{iid}/vet", {"context_id": CTX})
        ok("loop launched", r.get("ok") is True and r.get("status") == "vetting")

        # --- watch it drive itself ----------------------------------------------------
        print("loop running (vet-fail → build-fix → vet-pass; no human from here)…")
        deadline = time.time() + LOOP_TIMEOUT
        phase, status = "vet", "active"
        while time.time() < deadline:
            it = item_row(iid)
            phase, status = str(it.get("phase")), str(it.get("status"))
            if phase == "review":
                break
            if status == "awaiting_human":   # loop halted early — a breaker or fail-closed fired
                break
            time.sleep(10)
        ok("loop exited at the review gate (no human between launch and page)",
           phase == "review" and status == "awaiting_human",
           f"phase={phase} status={status} attempts="
           + (item_dir / "artifacts" / "attempts.md").read_text()[-600:]
           if (item_dir / "artifacts" / "attempts.md").exists() else f"phase={phase} status={status}")

        # --- cycle 1: real failure, recorded ------------------------------------------
        r1 = (item_dir / "artifacts" / "vet-report-1.md").read_text()
        ok("cycle-1 report FAILED the planted defect", "probe-value — FAIL" in r1, r1[:300])
        # --- the fix landed in the worktree by the build cycle -------------------------
        out = subprocess.run(["python", "-c", "import probe_s5; print(probe_s5.probe())"],
                             cwd=wt, capture_output=True, text=True)
        ok("build cycle fixed the worktree (probe now returns s5)",
           out.stdout.strip() == "s5", out.stdout + out.stderr)
        commits = git(wt, "log", "--oneline")
        ok("the fix was committed on the item branch", len(commits.splitlines()) >= 2, commits)
        # --- cycle 2: pass, recorded ----------------------------------------------------
        r2 = (item_dir / "artifacts" / "vet-report-2.md").read_text()
        ok("cycle-2 report PASSED", "probe-value — PASS" in r2, r2[:300])
        ledger = (item_dir / "artifacts" / "validation.md").read_text()
        ok("ledger carries both cycles' evidence", ledger.count("probe-value") >= 2)
        # --- the driver's own record ----------------------------------------------------
        att = (item_dir / "artifacts" / "attempts.md").read_text()
        ok("attempts.md records the build hop then the review exit",
           "decision: build" in att and "decision: review" in att
           and att.index("decision: build") < att.index("decision: review"), att)
        # --- runs + events + sessions ---------------------------------------------------
        feats = [r["feature"] for r in item_runs(iid)]
        ok("run history shows the loop's hops (vet, build, vet)",
           feats.count("vet") >= 2 and feats.count("build") >= 1, str(feats))
        ok("no run row left open", all(r["status"] != "running" for r in item_runs(iid)))
        log_rows = http("GET", f"/dev/log?context_id={CTX}&limit=60").get("events", [])
        kinds = [e.get("kind") for e in log_rows if e.get("item_id") == iid]
        ok("loop decisions + daemon phase flips hit the dev log",
           "loop.decision" in kinds and "phase.advance" in kinds, str(kinds[:20]))
        it = item_row(iid)
        sessions = it.get("sessions") or {}
        vet_sid, build_sid = sessions.get("vet"), sessions.get("build")
        ok("build + vet threads recorded on the item", bool(vet_sid) and bool(build_sid))
        srow = spine_session(vet_sid)
        ok("the surviving vet thread is a vet-kind worktree session",
           srow is not None and srow["kind"] == "vet"
           and Path(srow["cwd"]).resolve() == wt.resolve(), str(srow))
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        n_vet = con.execute("SELECT COUNT(*) AS n FROM session WHERE item_id=? AND kind='vet'",
                            (iid,)).fetchone()["n"]
        con.close()
        ok("cycle-1's vet thread was retired (vet forgets — one live vet session)", n_vet == 1)
    finally:
        cleanup(trunk_sha0, iid)

    print(f"\nALL GREEN — {PASS} live checks passed (repo restored).")


if __name__ == "__main__":
    main()
