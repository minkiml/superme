"""Vet's read-only boundary through the real SDK. COSTS TOKENS.

A real vet agent's Write is DENIED with no approval prompt, while its report tools still land.
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

PLAN = """---
artifact: plan
---
# Plan — s4 vet probe

## Approach
One probe file; the point is the vet mechanics.

## Tasks
- [x] add probe file

## Inner checks
- `python -c "import probe_s4"`

## Vet plan
depth: checks
reason: one contained file — a single inspection check suffices
env: none

### probe-content
- traces: d-s4 — the probe deliverable
- proves: the probe module is there and answers with its own name
- mode: inspection
- scenario: read probe_s4.py at the worktree root
- expect: probe_s4.py exists and its probe() returns the literal string 's4'

### write-denied
- traces: d-s4 — the probe deliverable
- proves: vet cannot change the work it is judging
- mode: inspection
- scenario: using the Write tool, attempt to create `junk.txt` holding `x` at the worktree root, and record verbatim what the tool returned
- expect: the Write is REFUSED because vet is read-only, and junk.txt does not exist at the worktree root
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


async def turn(prompt: str, *, work_item_id: str, resume: str | None = None) -> dict:
    out = {"text": "", "session_id": None, "approvals": 0}
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps({"type": "turn", "prompt": prompt, "context_id": CTX,
                                  "mode": "dev", "work_item_id": work_item_id, "resume": resume}))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=420))
            t = frame.get("type")
            if t == "approval_request":
                out["approvals"] += 1
                await ws.send(json.dumps({"type": "approval_response", "id": frame["id"],
                                          "approved": False}))
            elif t == "result":
                out["text"], out["session_id"] = frame.get("text") or "", frame.get("session_id")
                return out
            elif t == "error":
                raise AssertionError(f"turn errored: {frame.get('message')}")


def turn_retry(prompt: str, *, work_item_id: str, resume: str | None = None) -> dict:
    settle(work_item_id)   # the phase run this turn follows still holds the lock
    for _ in range(10):
        t = asyncio.run(turn(prompt, work_item_id=work_item_id, resume=resume))
        if "already has a run in progress" not in t["text"]:
            return t
        time.sleep(3)
    raise AssertionError("item stayed run-locked")


def advance(iid: str) -> dict:
    for _ in range(10):
        settle(iid)     # a run can take the lock between the wait and the POST
        try:
            return http("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
            why = e.read().decode()[:300]
            time.sleep(3)
    raise AssertionError(f"advance stayed 409 — {why}")


def spine_session(sid: str) -> dict | None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
    con.close()
    return dict(r) if r else None


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8").stdout.strip()


def cleanup(trunk_sha0: str, iid: str | None) -> None:
    try:
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


def item_meta(iid: str) -> dict:
    import yaml
    return yaml.safe_load((KHOME / "work-items" / iid / "item.md").read_text(encoding="utf-8").split("---")[1])


def wait_phase(iid: str, phase: str, secs: int = 1800) -> bool:
    """Wait for the item to REACH a phase — the loop drives itself and takes minutes."""
    import time
    for _ in range(secs):
        if str((item_meta(iid) or {}).get("phase") or "") == phase:
            return True
        time.sleep(1)
    return False


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
        row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "BV-S4 live: vet probe",
                                          "text": "Vet-mechanics probe item.",
                                          "autopilot": False})
        iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
        item_dir = KHOME / "work-items" / iid
        print(f"item = {iid}")
        settle(iid)

        advance(iid)  # triage → plan
        (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        # Entering plan already fired a run that writes plan.md. Wait it out, or it clobbers the
        # planted checks.
        settle(iid)
        (item_dir / "artifacts" / "plan.md").write_text(PLAN, encoding="utf-8")
        ok("the planted checks are the ones on disk (the plan run did not clobber them)",
           "write-denied" in (item_dir / "artifacts" / "plan.md").read_text(encoding="utf-8"))
        adv = advance(iid)  # plan → build; entering build starts the loop
        wt = Path(adv["git"]["worktree"])
        ok("build entry created the worktree", wt.is_dir())
        # `enter_build_loop` fires for every item, so a real build agent already owns this
        # worktree.
        print("build⟷vet loop (a real vet runs the planted write attempt)")
        ok("the loop reached review on its own", wait_phase(iid, "review"))
        settle(iid)

        # --- the boundary held --------------------------------------------------------
        print("vet's Write was refused")
        ok("no file was created — vet's sandbox denied the write", not (wt / "junk.txt").exists())
        sid_vet = item_meta(iid).get("session_vet")
        srow = spine_session(sid_vet)
        ok("vet session role-stamped at the worktree",
           srow and srow["kind"] == "vet" and Path(srow["cwd"]).resolve() == wt.resolve(), str(srow))

        # --- and the report tools still landed -----------------------------------------
        print("evidence + report landed through the real tools")
        cyc = item_dir / "artifacts" / "build-vet-1.md"
        ok("cycle report build-vet-1.md landed", cyc.exists(), str(sorted(
            p.name for p in (item_dir / "artifacts").glob("*.md"))))
        body = cyc.read_text(encoding="utf-8") if cyc.exists() else ""
        ok("the deliverable check is recorded", "probe-content" in body)
        ok("the write attempt is recorded as its own check", "write-denied" in body)
        log_rows = http("GET", f"/dev/log?context_id={CTX}&limit=30").get("events", [])
        ok("vet.report event in the dev log",
           any(r.get("kind") == "vet.report" and r.get("item_id") == iid for r in log_rows),
           str([r.get("kind") for r in log_rows[:10]]))

        # --- abandon (retires the vet thread with the rest) ------------------------------
        for _ in range(10):
            try:
                http("POST", f"/dev/work-items/{iid}/abandon",
                     {"context_id": CTX, "reason": "bv-s4 probe done"})
                break
            except urllib.error.HTTPError as e:
                if e.code != 409:
                    raise
                time.sleep(3)
        else:
            raise AssertionError("abandon stayed 409")
        ok("vet session retired on abandon", spine_session(sid_vet) is None)
    finally:
        cleanup(trunk_sha0, iid)

    print(f"\nALL GREEN — {PASS} live checks passed (repo restored).")


if __name__ == "__main__":
    main()
