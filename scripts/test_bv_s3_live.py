"""BV-S3 gate test (LIVE half) — the phase→session map on the dummy repo. COSTS TOKENS (5 tiny
turns). Drives what the offline suite can't: real ws turns minting real sessions per role.

The step-3 claims verified live:
  · a triage turn births the INTAKE session (repo cwd, spine kind='intake', item.md slot);
  · a build turn (post-worktree) births a SEPARATE build session (worktree cwd, kind='build')
    and the intake thread SURVIVES — the old rotation retired it;
  · a second build turn RESUMES the same build session (build remembers);
  · a vet turn births its own session (kind='vet');
  · a review turn RETURNS to the intake session — same id, no mint (intake narrates end-to-end);
  · abandon retires ALL role threads (every transcript gone, spine rows dropped).

Artifacts are script-written stand-ins (s6_live pattern) so gates pass without agent work.
Self-cleaning: abandon + s6_live-style repo/knowledge restore.
Run with the daemon up: PYTHONPATH=. python -m scripts.test_bv_s3_live
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
CTX = "dummy"
# This suite MUTATES the repo it points at: `git reset --hard`, `git clean -fd`, branch
# deletion. Name a throwaway one, and never a repo holding work you want.
REPO = Path(os.environ.get("SUPERME_TEST_REPO") or "~/superme-test-repo").expanduser()
if not (REPO / ".git").is_dir():
    raise SystemExit(f"SUPERME_TEST_REPO is not a git repo: {REPO}")
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
        try:
            return http("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
            time.sleep(3)
    raise AssertionError("advance stayed 409")


def spine_session(sid: str) -> dict | None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
    con.close()
    return dict(r) if r else None


def item_meta(iid: str) -> dict:
    import yaml
    text = (KHOME / "work-items" / iid / "item.md").read_text()
    return yaml.safe_load(text.split("---")[1])


def transcript_exists(sid: str) -> bool:
    return bool(list(Path.home().glob(f".claude/projects/*/{sid}.jsonl")))


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def write_artifact(item_dir: Path, name: str, text: str) -> None:
    (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (item_dir / "artifacts" / name).write_text(text)


def cleanup(trunk_sha0: str, iid: str | None) -> None:
    try:
        subprocess.run(["git", "reset", "--hard", trunk_sha0, "-q"], cwd=REPO, check=False)
        subprocess.run(["git", "clean", "-fdq", "--exclude=superme-knowledge"], cwd=REPO, check=False)
        wt_root = git_layer.worktrees_root(CTX)   # worktrees first — a live one pins its branch
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
        row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "BV-S3 live: session probe",
                                          "text": "Session-map probe item."})
        iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
        item_dir = KHOME / "work-items" / iid
        print(f"item = {iid}")

        # --- intake birth (triage) ------------------------------------------------
        print("intake birth (triage turn)")
        t1 = turn_retry("Reply with exactly: ok. Do nothing else — no tools.", work_item_id=iid)
        sid_intake = t1["session_id"]
        m = item_meta(iid)
        ok("triage turn filled the INTAKE slot",
           bool(sid_intake) and m.get("session_intake") == sid_intake, str(m))
        srow = spine_session(sid_intake)
        ok("intake session: kind='intake', repo cwd",
           srow and srow["kind"] == "intake" and Path(srow["cwd"]).resolve() == REPO.resolve(),
           str(srow))

        # --- gates to build ---------------------------------------------------------
        advance(iid)   # triage → plan
        write_artifact(item_dir, "plan.md", PLAN)
        adv = advance(iid)  # plan → build
        wt = Path(adv["git"]["worktree"])
        ok("build entry created the worktree", wt.is_dir())

        # --- build birth + persistence ----------------------------------------------
        print("build session (fresh mint, worktree cwd, persists)")
        t2 = turn_retry("Reply with exactly: ok. Do nothing else — no tools.", work_item_id=iid)
        sid_build = t2["session_id"]
        m = item_meta(iid)
        ok("build turn minted a SEPARATE session", bool(sid_build) and sid_build != sid_intake)
        ok("both slots on the item — intake SURVIVED build entry",
           m.get("session_build") == sid_build and m.get("session_intake") == sid_intake, str(m))
        ok("intake transcript still on disk (not retired)", transcript_exists(sid_intake))
        srow = spine_session(sid_build)
        ok("build session: kind='build', worktree cwd",
           srow and srow["kind"] == "build" and Path(srow["cwd"]).resolve() == wt.resolve(),
           str(srow))
        t3 = turn_retry("Reply with exactly: ok again. No tools.", work_item_id=iid,
                        resume=sid_build)
        ok("second build turn RESUMES the same session (build remembers)",
           t3["session_id"] == sid_build, str(t3["session_id"]))

        # --- vet birth ----------------------------------------------------------------
        print("vet session (own mint)")
        advance(iid)   # build → vet
        t4 = turn_retry("Reply with exactly: ok. Do nothing else — no tools.", work_item_id=iid)
        sid_vet = t4["session_id"]
        ok("vet turn minted its own session",
           bool(sid_vet) and sid_vet not in (sid_intake, sid_build))
        srow = spine_session(sid_vet)
        ok("vet session: kind='vet', worktree cwd",
           srow and srow["kind"] == "vet" and Path(srow["cwd"]).resolve() == wt.resolve(), str(srow))

        # --- review returns to intake ---------------------------------------------------
        print("review returns to the intake thread")
        write_artifact(item_dir, "validation.md",
                       "---\nartifact: validation\n---\n# Validation\n\n## Checklist\n"
                       "- probe-exists\n\n## Evidence\n")
        advance(iid)   # vet → review
        t5 = turn_retry("Reply with exactly: ok. Do nothing else — no tools.", work_item_id=iid)
        ok("review turn RESUMED the intake session (same id, no mint)",
           t5["session_id"] == sid_intake, str(t5["session_id"]))

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
        gone = [s for s in (sid_intake, sid_build, sid_vet)
                if spine_session(s) is None and not transcript_exists(s)]
        ok("all three threads retired (spine rows + transcripts gone)",
           len(gone) == 3, str(gone))
    finally:
        cleanup(trunk_sha0, iid)

    print(f"\nALL GREEN — {PASS} live checks passed (repo restored).")


if __name__ == "__main__":
    main()
