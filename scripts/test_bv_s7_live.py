"""The review router closing the full circle. COSTS TOKENS.

A real intake agent turns the owner's feedback into a vet-plan check, build implements it, and a
fresh vet passes — with no human action after the feedback.

Needs SUPERME_TEST_REPO and a running daemon.
"""

import os
import asyncio
import json
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

from superme_agent.core import git_layer

B = "http://127.0.0.1:8787"
WS = "ws://127.0.0.1:8787/ws/agent"
CTX = "dummy"
# This suite MUTATES the repo it points at: reset, clean, branch deletion.
# Name a throwaway one, never a repo holding work you want.
REPO = Path(os.environ.get("SUPERME_TEST_REPO") or "~/superme-test-repo").expanduser()
if not (REPO / ".git").is_dir():
    raise SystemExit(f"SUPERME_TEST_REPO is not a git repo: {REPO}")
KHOME = Path("superme-knowledge") / f"{CTX}-knowledge" / "dev"
DB = Path("superme_agent") / ".system.db"
PASS = 0
LOOP_TIMEOUT = 600

PLAN = """---
artifact: plan
---
# Plan — s7 router probe

## Approach
One probe file; the point is the review router closing the circle.

## Tasks
- [x] add probe file returning the right value

## Inner checks
- `python -c "import probe_s7; assert probe_s7.probe() == 's7'"`

## Vet plan
depth: checks
reason: one contained file — a single command check suffices
env: none

### probe-value
- traces: d-s7 — the probe deliverable
- mode: command
- scenario: in the worktree, run `python -c "import probe_s7; print(probe_s7.probe())"`
- expect: the command prints exactly s7 and exits 0
"""

FEEDBACK_PROMPT = (
    "Owner feedback at this review gate: the probe module ALSO needs an `extra()` function "
    "returning exactly the string s7-extra — checkable by running "
    "`python -c \"import probe_s7; print(probe_s7.extra())\"` in the worktree. "
    "Route this feedback into the loop (route_review_feedback, mode command), then tell me in "
    "one line exactly what check id you routed."
)


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


def item_runs(iid: str) -> list[dict]:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT feature, phase, status FROM run WHERE item_id=? ORDER BY id",
                       (iid,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def wait_phase(iid: str, want: str, timeout: int, item_dir: Path) -> tuple[str, str]:
    deadline = time.time() + timeout
    phase, status = "", ""
    while time.time() < deadline:
        it = item_row(iid)
        phase, status = str(it.get("phase")), str(it.get("status"))
        if phase == want and status == "awaiting_human":
            break
        time.sleep(8)
    return phase, status


async def turn(prompt: str, *, work_item_id: str) -> dict:
    out = {"text": "", "session_id": None}
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps({"type": "turn", "prompt": prompt, "context_id": CTX,
                                  "mode": "dev", "work_item_id": work_item_id}))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=420))
            t = frame.get("type")
            if t == "approval_request":
                await ws.send(json.dumps({"type": "approval_response", "id": frame["id"],
                                          "approved": False}))
            elif t == "result":
                out["text"], out["session_id"] = frame.get("text") or "", frame.get("session_id")
                return out
            elif t == "error":
                raise AssertionError(f"turn errored: {frame.get('message')}")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def cleanup(trunk_sha0: str, iid: str | None) -> None:
    try:
        if iid:
            try:
                retry_409("POST", f"/dev/work-items/{iid}/abandon",
                          {"context_id": CTX, "reason": "bv-s7 probe done"})
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
    from superme_agent.core import artifacts as A
    trunk_sha0 = git(REPO, "rev-parse", "HEAD")
    iid = None
    try:
        row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "BV-S7 live: router probe",
                                          "text": "Review-router probe item."})
        iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
        item_dir = KHOME / "work-items" / iid
        print(f"item = {iid}")

        retry_409("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})  # triage → plan
        (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (item_dir / "artifacts" / "plan.md").write_text(PLAN)
        adv = retry_409("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})  # plan → build
        wt = Path(adv["git"]["worktree"])
        (wt / "probe_s7.py").write_text("def probe():\n    return 's7'\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-qm", "add probe_s7 (correct — cycle 1 must pass)")
        retry_409("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})  # build → vet

        # --- leg 1: the loop reaches review on its own -------------------------------
        r = http("POST", f"/dev/work-items/{iid}/vet", {"context_id": CTX})
        ok("loop launched", r.get("ok") is True and r.get("status") == "vetting")
        print("leg 1: loop running (vet passes → review)…")
        phase, status = wait_phase(iid, "review", LOOP_TIMEOUT, item_dir)
        ok("loop parked the item at the review gate", phase == "review"
           and status == "awaiting_human", f"phase={phase} status={status}")

        # --- leg 2: the owner's feedback turn (the LAST human action) ----------------
        print("leg 2: routing turn (a real intake agent phrases + routes the feedback)…")
        t1 = asyncio.run(turn(FEEDBACK_PROMPT, work_item_id=iid))
        ok("routing turn completed on the intake thread", bool(t1["session_id"]))
        events = [e for e in http("GET", f"/dev/log?context_id={CTX}&limit=60").get("events", [])
                  if e.get("item_id") == iid and e.get("kind") == "review.route"]
        ok("review.route event logged by the agent's tool call", len(events) == 1,
           t1["text"][:400])
        routed = str((events[0].get("meta") or {}).get("check"))
        print(f"  routed check id = {routed}")
        plan_text = (item_dir / "artifacts" / "plan.md").read_text()
        ok("routed check landed in plan.md with review provenance",
           f"### {routed}" in plan_text
           and "- origin: review" in plan_text.split(f"### {routed}", 1)[1])
        ok("grown plan is still gate-clean",
           A.vet_plan_hard_issues(A.parse_vet_plan(plan_text)) == [])

        # --- leg 3: the loop drives the feedback to green ----------------------------
        print("leg 3: deferred build hop → build implements → fresh vet → review…")
        phase, status = wait_phase(iid, "review", LOOP_TIMEOUT, item_dir)
        att = (item_dir / "artifacts" / "attempts.md").read_text() \
            if (item_dir / "artifacts" / "attempts.md").exists() else ""
        ok("item is back at review with no human action after the feedback message",
           phase == "review" and status == "awaiting_human",
           f"phase={phase} status={status} attempts={att[-600:]}")
        out = subprocess.run(["python", "-c", "import probe_s7; print(probe_s7.extra())"],
                             cwd=wt, capture_output=True, text=True)
        ok("build cycle implemented the routed requirement (extra() → s7-extra)",
           out.stdout.strip() == "s7-extra", out.stdout + out.stderr)
        ok("the fix was committed on the item branch",
           len(git(wt, "log", "--oneline").splitlines()) >= 2)
        reports = sorted((item_dir / "artifacts").glob("vet-report-*.md"))
        final = reports[-1].read_text()
        ok("final vet report verdicts BOTH checks PASS",
           "probe-value — PASS" in final and f"{routed} — PASS" in final, final[:400])
        ok("attempts.md shows two review exits (cycle-1 pass, post-routing pass)",
           att.count("decision: review") >= 2, att)
        feats = [r["feature"] for r in item_runs(iid)]
        ok("run history: routing chat + the loop hops",
           "chat" in feats and feats.count("vet") >= 2 and feats.count("build") >= 1, str(feats))
        ok("no run row left open", all(r["status"] != "running" for r in item_runs(iid)))
    finally:
        cleanup(trunk_sha0, iid)

    print(f"\nALL GREEN — {PASS} live checks passed (repo restored).")


if __name__ == "__main__":
    main()
