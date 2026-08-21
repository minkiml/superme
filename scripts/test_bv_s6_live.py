"""BV-S6 gate test (LIVE half) — handoff promotion through the real ws seam. COSTS TOKENS
(2 tiny turns on the dummy repo). Drives what the offline suite can't: the promotion block
actually rides an intake turn into the REAL transcript (the agent narrates the loop from it),
the `handoffs_promoted` watermark advances at Result, and a SECOND turn does NOT re-inject
(exactly one promotion header in the transcript — the per-turn-append scar stays closed).

The loop record itself is script-seeded (a two-cycle fail→pass history, s6_live artifact
pattern) — the loop's own live E2E is bv_s5_live; this probe isolates the §1.4 seam. The item
stays at triage (intake role — mechanically identical to review for this seam, no worktree
needed). Self-cleaning: pre-build hard delete. Run with the daemon up:
PYTHONPATH=. python -m scripts.test_bv_s6_live
"""

import os
import asyncio
import glob
import json
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

from superme_agent.core import artifacts as A

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

HEADER = "Loop record — build⟷vet handoff"

PLAN = """---
artifact: plan
---
# Plan — s6 handoff probe

## Approach
Seeded record; the point is the promotion seam.

## Tasks
- [x] probe

## Inner checks
- `true`

## Vet plan
depth: checks
reason: one contained check suffices
env: none

### probe-value
- traces: d-s6
- mode: command
- scenario: run the probe
- expect: the probe prints exactly s6
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
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
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
    for _ in range(10):
        t = asyncio.run(turn(prompt, work_item_id=work_item_id, resume=resume))
        if "already has a run in progress" not in t["text"]:
            return t
        time.sleep(3)
    raise AssertionError("item stayed run-locked")


def item_row(iid: str) -> dict:
    for it in http("GET", f"/dev?context_id={CTX}").get("work_items", []):
        if it.get("id") == iid:
            return it
    return {}


def header_count(sid: str) -> int:
    """How many REAL user messages in the CLI transcript carry the promotion header. The raw file
    can't be grepped: the CLI also records a `queue-operation` bookkeeping copy of each prompt,
    so only `type == "user"` entries count as injected messages."""
    paths = glob.glob(str(Path.home() / ".claude" / "projects" / "*" / f"{sid}.jsonl"))
    assert paths, f"no transcript found for {sid}"
    n = 0
    for line in Path(paths[0]).read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "user" and HEADER in json.dumps((d.get("message") or {}),
                                                            ensure_ascii=False):
            n += 1
    return n


def user_messages(sid: str) -> str:
    """All user-message content concatenated (for content assertions on what actually rode in)."""
    paths = glob.glob(str(Path.home() / ".claude" / "projects" / "*" / f"{sid}.jsonl"))
    out = []
    for line in Path(paths[0]).read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "user":
            out.append(json.dumps(d.get("message") or {}, ensure_ascii=False))
    return "\n".join(out)


def seed_record(item_dir: Path) -> None:
    """A two-cycle loop history: cycle 1 FAIL → build hop, cycle 2 PASS → review exit."""
    (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (item_dir / "artifacts" / "plan.md").write_text(PLAN)
    A.record_verification(item_dir, REPO, check="probe-value", how="ran the probe",
                      result="printed WRONG, expected s6", passed=False)
    A.write_vet_user_report(item_dir, REPO,
                       findings="### probe-value\n- expected: s6\n- actual: WRONG")
    A.append_cycle_outcome(item_dir, evidence="failed", decision="build",
                     reason="1 check(s) failed — handing the vet report to a build cycle",
                     fingerprint="aabbccddeeff", failed=["probe-value"],
                     tokens=12000, budget=500000)
    A.record_verification(item_dir, REPO, check="probe-value", how="ran the probe",
                      result="printed s6", passed=True)
    A.write_vet_user_report(item_dir, REPO)
    A.append_cycle_outcome(item_dir, evidence="passed", decision="review",
                     reason="every check green and fresh — advancing to the review gate")


def main() -> None:
    iid = None
    try:
        row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "BV-S6 live: handoff probe",
                                          "text": "Handoff-promotion probe item."})
        iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
        item_dir = KHOME / "work-items" / iid
        print(f"item = {iid}")
        seed_record(item_dir)

        # --- turn 1: the promotion rides in ------------------------------------------
        print("turn 1: promotion block rides the intake turn")
        t1 = turn_retry(
            "From the record you were handed this turn, answer in ONE line: how many loop "
            "cycles ran, which check failed in cycle 1, and where did the loop exit?",
            work_item_id=iid)
        low = t1["text"].lower()
        ok("agent narrates FROM the record (names the check + the review exit)",
           "probe-value" in low and "review" in low, t1["text"][:400])
        it = item_row(iid)
        ok("watermark advanced at Result", int(it.get("handoffs_promoted") or 0) == 2, str(it))
        ok("promotion header landed in exactly ONE user message",
           header_count(t1["session_id"]) == 1)
        um1 = user_messages(t1["session_id"])
        ok("latest report rode verbatim, older cycle collapsed",
           "Vet report — cycle 2" in um1 and "vet-report-1.md verdicts:" in um1)

        # --- turn 2: no re-injection ---------------------------------------------------
        print("turn 2: no re-injection on the resumed thread")
        t2 = turn_retry("Reply with exactly: OK", work_item_id=iid, resume=t1["session_id"])
        ok("second turn resumed the same intake thread", t2["session_id"] is not None)
        ok("still exactly ONE promotion header (never per-turn)",
           header_count(t2["session_id"]) == 1)
        ok("watermark unchanged", int(item_row(iid).get("handoffs_promoted") or 0) == 2)

        # --- new loop activity → the NEXT turn promotes only the tail ---------------------
        print("turn 3: fresh record entries promote incrementally")
        A.append_cycle_outcome(item_dir, evidence="stale", decision="revet",
                         reason="evidence is green but the code moved — re-vetting")
        t3 = turn_retry("Reply with exactly: OK", work_item_id=iid, resume=t2["session_id"])
        um3 = user_messages(t3["session_id"])
        ok("new entry promoted (second header, tail only)",
           header_count(t3["session_id"]) == 2 and "revet" in um3.split(HEADER)[-1])
        ok("watermark caught up", int(item_row(iid).get("handoffs_promoted") or 0) == 3)
    finally:
        if iid:
            for _ in range(10):   # the just-finished turn's run row may still be closing (409)
                try:
                    sqlite3.connect(DB).close()
                    http("DELETE", f"/dev/work-items/{iid}?context_id={CTX}")
                    print("cleanup: item hard-deleted (pre-build path)")
                    break
                except urllib.error.HTTPError as e:
                    if e.code != 409:
                        print(f"cleanup INCOMPLETE: {e}")
                        break
                    time.sleep(3)
            else:
                print("cleanup INCOMPLETE: delete stayed 409")

    print(f"\nALL GREEN — {PASS} live checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
