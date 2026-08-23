"""The build-vet loop driving itself end to end. COSTS TOKENS.

Approving the plan gate is the last human action. The cycle COUNT is not asserted, since build
often fixes a planted defect first time.

Needs a running daemon. Writes into `test-playground`, or SUPERME_TEST_CTX.
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
def _ctx_repo(ctx: str) -> Path:
    """The context's OWN checkout. Naming the repo separately lets a suite write knowledge into one
    project and branches into another."""
    from superme_agent.gateway import contexts
    return Path(contexts.resolve(ctx, "dev").cwd)


def _pick_ctx(preferred: str) -> str:
    """This suite's context, else the one named in the environment. Never a guess: it creates and
    abandons work-items in whatever it picks."""
    from superme_agent.gateway import contexts
    if contexts.exists(preferred):
        return preferred
    named = os.environ.get("SUPERME_TEST_CTX") or ""
    if named and contexts.exists(named):
        return named
    raise SystemExit(f"context {preferred!r} is not in this SuperMe's registry, and "
                     "SUPERME_TEST_CTX names no known repo — set it to a throwaway repo id "
                     "from your repos.yaml")


CTX = _pick_ctx("test-playground")
REPO = _ctx_repo(CTX)
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
- [ ] add `probe_s5.py` at the worktree root, with `probe()` returning exactly the string `s5`

## Inner checks
- `python -c "import probe_s5; assert probe_s5.probe() == 's5'"`

## Vet plan
depth: checks
reason: one contained file — a single inspection check suffices
env: none

### probe-value
- traces: d-s5 — the probe deliverable
- proves: the probe module is there and answers with its own name
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
    iid = path.split("/dev/work-items/", 1)[1].split("/", 1)[0] if "/dev/work-items/" in path else ""
    for _ in range(tries):
        if iid:
            settle(iid)     # a phase run holds the item lock; a 409 retry cannot outwait it
        try:
            return http(method, path, body)
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
            why = e.read().decode()[:300]
            time.sleep(3)
    raise AssertionError(f"{path} stayed 409 — {why}")


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
                          cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8").stdout.strip()


def cleanup(trunk_sha0: str, iid: str | None) -> None:
    try:
        if iid:
            try:
                retry_409("POST", f"/dev/work-items/{iid}/abandon",
                          {"context_id": CTX, "reason": "bv-s5 probe done"})
            except Exception as e:  # noqa: BLE001
                print(f"cleanup: abandon failed ({e})")
        # ONLY this item's. `item/*` and the worktrees root would take every other item with them.
        if iid:
            for wt in git_layer.worktrees_root(CTX).glob(f"{iid}*"):
                shutil.rmtree(wt, ignore_errors=True)
            subprocess.run(["git", "worktree", "prune"], cwd=REPO, check=False)
            for br in subprocess.run(["git", "branch", "--list", f"item/{iid}*",
                                      "--format=%(refname:short)"],
                                     cwd=REPO, capture_output=True, text=True, encoding="utf-8").stdout.split():
                subprocess.run(["git", "branch", "-Dq", br], cwd=REPO, check=False)
        if iid:
            shutil.rmtree(KHOME / "work-items" / iid, ignore_errors=True)
            for row in http("GET", f"/dev?context_id={CTX}").get("inbox", []):
                if row.get("routed_to") == iid:
                    http("DELETE", f"/dev/inbox/{row['id']}")
        print(f"cleanup: {iid} branch + worktree + knowledge home removed")
    except Exception as e:  # noqa: BLE001
        print(f"cleanup INCOMPLETE: {e}")


def settle(iid: str, secs: int = 900) -> None:
    """Every phase entry fires that phase's run, and the run holds the item lock for minutes."""
    import time
    for _ in range(secs):
        with sqlite3.connect(DB) as c:
            if not c.execute("select 1 from run where item_id=? and status='running'",
                             (iid,)).fetchone():
                return
        time.sleep(1)
    raise AssertionError(f"a run never released {iid}")


def main() -> None:
    trunk_sha0 = git(REPO, "rev-parse", "HEAD")
    iid = None
    try:
        row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "BV-S5 live: loop probe",
                                          "text": "Loop-driver probe item.",
                                          "autopilot": False})
        iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
        item_dir = KHOME / "work-items" / iid
        print(f"item = {iid}")
        settle(iid)

        retry_409("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})  # triage → plan
        (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        # Entering plan already fired a run that writes plan.md. Wait it out, or it clobbers the
        # planted checks.
        settle(iid)
        (item_dir / "artifacts" / "plan.md").write_text(PLAN, encoding="utf-8")
        # plan → build. `enter_build_loop` fires on entry: from here nothing human touches it.
        adv = retry_409("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})
        wt = Path(adv["git"]["worktree"])
        ok("build entry created the worktree", wt.is_dir())

        # --- watch it drive itself ----------------------------------------------------
        print("loop running (build → vet → …; no human from here)…")
        deadline = time.time() + LOOP_TIMEOUT
        phase, status = "build", "active"
        while time.time() < deadline:
            it = item_row(iid)
            phase, status = str(it.get("phase")), str(it.get("status"))
            # `review` + `active` means review's own entry run is still writing: not rested yet.
            if status == "awaiting_human":
                break
            time.sleep(10)
        ok("loop exited at the review gate (no human between the plan gate and the page)",
           phase == "review" and status == "awaiting_human", f"phase={phase} status={status}")

        # --- every cycle recorded, the last one green ---------------------------------
        cycles = sorted((item_dir / "artifacts").glob("build-vet-*.md"))
        ok("the loop wrote at least one cycle report", cycles, str(sorted(
            f.name for f in (item_dir / "artifacts").glob("*.md"))))
        final = cycles[-1].read_text(encoding="utf-8")
        ok("the final cycle carries the plan's check", "probe-value" in final, final[:300])
        # The outcome's decision is the heading suffix under `## Cycle outcome`, not a field.
        outcome = final.split("## Cycle outcome")[-1]
        ok("the final cycle exited to review", "— review" in outcome, outcome[:400])

        # --- and the work is really there ----------------------------------------------
        out = subprocess.run(["python", "-c", "import probe_s5; print(probe_s5.probe())"],
                             cwd=wt, capture_output=True, text=True, encoding="utf-8")
        ok("the build's work satisfies the check for real (probe returns s5)",
           out.stdout.strip() == "s5", out.stdout + out.stderr)
        commits = git(wt, "log", "--oneline")
        ok("the work was committed on the item branch", commits.strip() != "", commits)
        # --- runs + events + sessions ---------------------------------------------------
        feats = [r["feature"] for r in item_runs(iid)]
        ok("run history shows the loop ran both halves itself",
           feats.count("vet") >= 1 and feats.count("build") >= 1, str(feats))
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
