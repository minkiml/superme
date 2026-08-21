"""Vet's read-only boundary through the real SDK. COSTS TOKENS.

A real vet agent's Write is DENIED with no approval prompt, while its report tools still land.
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
- mode: inspection
- scenario: read probe_s4.py at the worktree root
- expect: probe_s4.py exists and its probe() returns the literal string 's4'
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
    for _ in range(10):
        t = asyncio.run(turn(prompt, work_item_id=work_item_id, resume=resume))
        if "already has a run in progress" not in t["text"]:
            return t
        time.sleep(3)
    raise AssertionError("item stayed run-locked")


def advance(iid: str) -> dict:
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


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def cleanup(trunk_sha0: str, iid: str | None) -> None:
    try:
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
        row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "BV-S4 live: vet probe",
                                          "text": "Vet-mechanics probe item."})
        iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
        item_dir = KHOME / "work-items" / iid
        print(f"item = {iid}")

        advance(iid)  # triage → plan
        (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (item_dir / "artifacts" / "plan.md").write_text(PLAN)
        adv = advance(iid)  # plan → build (worktree)
        wt = Path(adv["git"]["worktree"])
        # The "built work": land the probe file on the item branch (script-written build).
        (wt / "probe_s4.py").write_text("def probe():\n    return 's4'\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-qm", "add probe_s4")
        advance(iid)  # build → vet

        # --- write attempt inside a real vet session ---------------------------------
        print("vet turn: file-write denied, no prompt")
        t1 = turn_retry(
            "Use the Write tool RIGHT NOW to create a file named junk.txt with content 'x' in "
            "your working directory. Then report, verbatim, what the tool call returned. Do "
            "nothing else.", work_item_id=iid)
        ok("no approval prompt fired (denied outright)", t1["approvals"] == 0)
        ok("no file was created", not (wt / "junk.txt").exists())
        # Either shape proves it: the call fired and was DENIED, or the agent refused up front.
        low = t1["text"].lower()
        ok("the agent hit (or pre-empted) the vet read-only contract",
           "vet" in low and any(w in low for w in ("read-only", "disable", "denied", "write")),
           t1["text"][:400])
        srow = spine_session(t1["session_id"])
        ok("vet session role-stamped at the worktree",
           srow and srow["kind"] == "vet" and Path(srow["cwd"]).resolve() == wt.resolve(), str(srow))

        # --- evidence + report through the real tools ----------------------------------
        print("vet turn: evidence + report land via the MCP tools")
        t2 = turn_retry(
            "Run this item's vet plan now: verify the single check `probe-content` per its "
            "scenario/expect (read probe_s4.py). Record the outcome with "
            "record_validation_evidence (check id verbatim), then file the cycle report with "
            "file_vet_report. Keep the final reply to one line.",
            work_item_id=iid, resume=t1["session_id"])
        report = item_dir / "artifacts" / "vet-report-1.md"
        ok("vet-report-1.md landed with the code-owned envelope",
           report.exists() and report.read_text().startswith("# Vet report — cycle 1"),
           t2["text"][:300])
        ok("verdict line present", "probe-content — PASS" in report.read_text(),
           report.read_text()[:300])
        ledger = (item_dir / "artifacts" / "validation.md").read_text()
        ok("ledger carries the probe-content entry", "probe-content" in ledger)
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
        ok("vet session retired on abandon", spine_session(t1["session_id"]) is None)
    finally:
        cleanup(trunk_sha0, iid)

    print(f"\nALL GREEN — {PASS} live checks passed (repo restored).")


if __name__ == "__main__":
    main()
