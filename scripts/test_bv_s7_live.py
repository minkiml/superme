"""The review router closing the full circle. COSTS TOKENS.

A real intake agent turns the owner's feedback into a vet-plan check, build implements it, and a
fresh vet passes — with no human action after the feedback.

Needs a running daemon. Writes into `test-playground`, or SUPERME_TEST_CTX.
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
LOOP_TIMEOUT = 600

PLAN = """---
artifact: plan
---
# Plan — s7 router probe

## Approach
One probe file; the point is the review router closing the circle.

## Tasks
- [ ] add `probe_s7.py` at the worktree root, with `probe()` returning exactly the string `s7`

## Inner checks
- `python -c "import probe_s7; assert probe_s7.probe() == 's7'"`

## Vet plan
depth: checks
reason: one contained file — a single command check suffices
env: none

### probe-value
- traces: d-s7 — the probe deliverable
- proves: the probe module is there and answers with its own name
- mode: command
- scenario: in the worktree, run `python -c "import probe_s7; print(probe_s7.probe())"`
- expect: the command prints exactly s7 and exits 0
"""

FEEDBACK_PROMPT = (
    "Owner feedback at this review gate: the probe module ALSO needs an `extra()` function "
    "returning exactly the string s7-extra — checkable by running "
    "`python -c \"import probe_s7; print(probe_s7.extra())\"` in the worktree. "
    "This is not in the plan, so it needs re-planning: end this review with `revise` and carry "
    "the requirement in your summary. One line back to me."
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
                          cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8").stdout.strip()


def cleanup(trunk_sha0: str, iid: str | None) -> None:
    try:
        if iid:
            try:
                retry_409("POST", f"/dev/work-items/{iid}/abandon",
                          {"context_id": CTX, "reason": "bv-s7 probe done"})
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
    from superme_agent.core import artifacts as A
    trunk_sha0 = git(REPO, "rev-parse", "HEAD")
    iid = None
    try:
        row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "BV-S7 live: router probe",
                                          "text": "Review-router probe item.",
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
        # `enter_build_loop` fires on entry, so a real build agent writes the probe and the loop
        # vets it.
        adv = retry_409("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})
        wt = Path(adv["git"]["worktree"])
        ok("build entry created the worktree", wt.is_dir())

        # --- leg 1: the loop reaches review on its own -------------------------------
        print("leg 1: loop running (build writes the probe, vet passes → review)…")
        phase, status = wait_phase(iid, "review", LOOP_TIMEOUT, item_dir)
        ok("loop parked the item at the review gate", phase == "review"
           and status == "awaiting_human", f"phase={phase} status={status}")

        # --- leg 2: the owner's feedback turn (the LAST human action) ----------------
        print("leg 2: routing turn (a real intake agent phrases + routes the feedback)…")
        settle(iid)          # review's entry run holds the lock the moment it lands
        t1 = asyncio.run(turn(FEEDBACK_PROMPT, work_item_id=iid))
        ok("routing turn completed on the review thread", bool(t1["session_id"]))
        # A review `revise` flips the phase and re-runs the target in-thread. The plan phase
        # defines the check when it re-plans.
        for _ in range(40):
            events = [e for e in http("GET", f"/dev/log?context_id={CTX}&limit=60").get("events", [])
                      if e.get("item_id") == iid and e.get("kind") == "review.route"]
            if events:
                break
            time.sleep(3)
        ok("review.route event logged for the send-back", len(events) == 1, t1["text"][:400])
        meta = events[0].get("meta") or {}
        ok("the event names where it came from and where it went",
           meta.get("from") == "review" and meta.get("to") == "plan", str(meta))
        it = item_row(iid)
        ok("the item actually moved back to plan", str(it.get("phase")) == "plan", str(it.get("phase")))
        ok("the send-back spent the approval — the PR is closed",
           not (it.get("git") or {}).get("pr_open"), str(it.get("git")))

        print("leg 3: approve the re-plan → build implements → fresh vet → review…")
        retry_409("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})   # plan → build
        phase, status = wait_phase(iid, "review", LOOP_TIMEOUT, item_dir)
        ok("the loop carried the routed requirement back to review by itself",
           phase == "review" and status == "awaiting_human", f"phase={phase} status={status}")
        out = subprocess.run(["python", "-c", "import probe_s7; print(probe_s7.extra())"],
                             cwd=wt, capture_output=True, text=True, encoding="utf-8")
        ok("build cycle implemented the routed requirement (extra() → s7-extra)",
           out.stdout.strip() == "s7-extra", out.stdout + out.stderr)
        ok("the fix was committed on the item branch",
           len(git(wt, "log", "--oneline").splitlines()) >= 2)
        cycles = sorted((item_dir / "artifacts").glob("build-vet-*.md"))
        ok("the routing opened a SECOND cycle (cycle-1 pass, then the routed check)",
           len(cycles) >= 2, str([c.name for c in cycles]))
        final = cycles[-1].read_text(encoding="utf-8")
        # The plan named the new check itself, so assert the REQUIREMENT is covered, not an id
        # this suite guessed.
        ok("the final cycle carries the original check", "probe-value" in final, final[:400])
        ok("...and a check for the routed requirement", "extra" in final, final[:600])
        # The outcome's decision is the heading suffix under `## Cycle outcome`, not a field.
        outcome = final.split("## Cycle outcome")[-1]
        ok("the final cycle exited to review", "— review" in outcome, outcome[:400])
        feats = [r["feature"] for r in item_runs(iid)]
        ok("run history: routing chat + the loop hops",
           "chat" in feats and feats.count("vet") >= 2 and feats.count("build") >= 1, str(feats))
        ok("no run row left open", all(r["status"] != "running" for r in item_runs(iid)))
    finally:
        cleanup(trunk_sha0, iid)

    print(f"\nALL GREEN — {PASS} live checks passed (repo restored).")


if __name__ == "__main__":
    main()
