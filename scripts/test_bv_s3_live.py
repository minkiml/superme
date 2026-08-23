"""The phase-to-session map, driven by real turns. COSTS TOKENS.

Triage births intake, build births its own and resumes it, vet births its own, review returns to
intake.

Needs a running daemon. Writes into `test-playground`, or SUPERME_TEST_CTX.
"""

import os
import asyncio
import json
import shutil
import sqlite3
import subprocess
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
# Plan — s3 session probe

## Approach
Touch one file; the point is the session machinery, not the code.

## Tasks
- [x] add probe file

## Inner checks
- `python -c "import probe_s3"`

## Vet plan
depth: checks
reason: contained one-file change — one inspection check suffices
env: none

### probe-exists
- traces: d-s3 — the probe deliverable
- proves: the probe module is there and answers with its own name
- mode: inspection
- scenario: read the worktree root for the probe module
- expect: probe_s3.py exists at the worktree root and defines probe() returning 's3'
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
    out = {"text": "", "session_id": None}
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps({"type": "turn", "prompt": prompt, "context_id": CTX,
                                  "mode": "dev", "work_item_id": work_item_id, "resume": resume}))
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


def turn_retry(prompt: str, *, work_item_id: str, resume: str | None = None) -> dict:
    """Ride out the run-lock closing window between consecutive turns."""
    settle(work_item_id)   # the phase run this turn follows still holds the lock
    import time
    for _ in range(10):
        t = asyncio.run(turn(prompt, work_item_id=work_item_id, resume=resume))
        if "already has a run in progress" not in t["text"]:
            return t
        time.sleep(3)
    raise AssertionError("item stayed run-locked")


def advance(iid: str) -> dict:
    """Advance with a retry — a just-finished turn's run row may still be closing (409 window)."""
    import time
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


def item_meta(iid: str) -> dict:
    import yaml
    text = (KHOME / "work-items" / iid / "item.md").read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---")[1])


def transcript_exists(sid: str) -> bool:
    return bool(list(Path.home().glob(f".claude/projects/*/{sid}.jsonl")))


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8").stdout.strip()


def write_artifact(item_dir: Path, name: str, text: str) -> None:
    (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (item_dir / "artifacts" / name).write_text(text, encoding="utf-8")


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
        row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "BV-S3 live: session probe",
                                          "text": "Session-map probe item.",
                                          "autopilot": False})
        iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
        item_dir = KHOME / "work-items" / iid
        print(f"item = {iid}")
        settle(iid)

        # --- triage: the first intake-family slot ----------------------------------
        print("triage session (birth)")
        t1 = turn_retry("Reply with exactly: ok. Do nothing else — no tools.", work_item_id=iid)
        sid_triage = t1["session_id"]
        m = item_meta(iid)
        ok("triage turn filled the TRIAGE slot",
           bool(sid_triage) and m.get("session_triage") == sid_triage, str(m))
        srow = spine_session(sid_triage)
        ok("triage session: role 'intake', repo cwd",
           srow and srow["kind"] == "intake" and Path(srow["cwd"]).resolve() == REPO.resolve(),
           str(srow))

        # --- plan: its OWN slot, the SAME role --------------------------------------
        print("plan session (own slot, same role)")
        advance(iid)   # triage → plan
        t2 = turn_retry("Reply with exactly: ok. Do nothing else — no tools.", work_item_id=iid)
        sid_plan = t2["session_id"]
        m = item_meta(iid)
        ok("plan turn minted a SEPARATE session in the PLAN slot",
           bool(sid_plan) and sid_plan != sid_triage and m.get("session_plan") == sid_plan, str(m))
        ok("...carrying the same intake ROLE — a slot is not a kind",
           (spine_session(sid_plan) or {}).get("kind") == "intake")
        ok("the triage slot survived the move", m.get("session_triage") == sid_triage, str(m))
        ok("triage transcript still on disk (not retired)", transcript_exists(sid_triage))

        # --- build and vet belong to the LOOP, not to us -----------------------------
        print("build⟷vet loop (its own two sessions)")
        # Entering plan already fired a run that writes plan.md. Wait it out, or it clobbers the
        # planted checks.
        settle(iid)
        write_artifact(item_dir, "plan.md", PLAN)
        adv = advance(iid)  # plan → build; entering build starts the loop
        wt = Path(adv["git"]["worktree"])
        ok("build entry created the worktree", wt.is_dir())
        # `loop.py`: every exit lands the item at review, so waiting for review waits for both.
        ok("the loop reached review on its own", wait_phase(iid, "review"))
        settle(iid)                       # review's own entry run mints the review session
        m = item_meta(iid)
        sid_build, sid_vet = m.get("session_build"), m.get("session_vet")
        ok("the loop filled the BUILD slot with a fresh session",
           bool(sid_build) and sid_build not in (sid_triage, sid_plan), str(m))
        srow = spine_session(sid_build)
        ok("build session: role 'build', worktree cwd",
           srow and srow["kind"] == "build" and Path(srow["cwd"]).resolve() == wt.resolve(),
           str(srow))
        ok("the loop filled the VET slot with a session of its own",
           bool(sid_vet) and sid_vet not in (sid_triage, sid_plan, sid_build), str(m))
        srow = spine_session(sid_vet)
        ok("vet session: role 'vet', worktree cwd",
           srow and srow["kind"] == "vet" and Path(srow["cwd"]).resolve() == wt.resolve(), str(srow))

        # --- review is its OWN thread ------------------------------------------------
        print("review session (own slot, NOT a resumed triage)")
        sid_review = m.get("session_review")
        ok("review has a slot of its own", bool(sid_review), str(m))
        ok("...and it is NOT the triage thread — one slot per phase",
           sid_review not in (sid_triage, sid_plan, sid_build, sid_vet), str(sid_review))
        ok("review session: role 'intake' — same family as triage and plan",
           (spine_session(sid_review) or {}).get("kind") == "intake")

        # --- abandon retires every thread -------------------------------------------------
        print("abandon retires all role threads")
        import time
        for _ in range(10):   # same 409 closing-window as advance
            try:
                ab = http("POST", f"/dev/work-items/{iid}/abandon",
                          {"context_id": CTX, "reason": "bv-s3 probe done"})
                break
            except urllib.error.HTTPError as e:
                if e.code != 409:
                    raise
                time.sleep(3)
        else:
            raise AssertionError("abandon stayed 409")
        ok("abandon reports sessions cleared", ab.get("session_cleared") is True, str(ab))
        gone = [s for s in (sid_triage, sid_plan, sid_build, sid_vet, sid_review)
                if spine_session(s) is None and not transcript_exists(s)]
        ok("every slot retired (spine rows + transcripts gone)",
           len(gone) == 5, str(gone))
    finally:
        cleanup(trunk_sha0, iid)

    print(f"\nALL GREEN — {PASS} live checks passed (repo restored).")


if __name__ == "__main__":
    main()
