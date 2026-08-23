"""Handoff promotion through the real socket seam. COSTS TOKENS.

The watermark advances at Result and a SECOND turn does not re-inject: exactly one promotion
header, ever. Needs a running daemon. Writes into `test-playground`, or SUPERME_TEST_CTX.
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
- proves: the probe module is there and answers with its own name
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
    settle(work_item_id)   # the phase run this turn follows still holds the lock
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
    for line in Path(paths[0]).read_text(encoding="utf-8").splitlines():
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
    for line in Path(paths[0]).read_text(encoding="utf-8").splitlines():
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
    (item_dir / "artifacts" / "plan.md").write_text(PLAN, encoding="utf-8")
    A.record_verification(item_dir, REPO, check="probe-value", how="ran the probe",
                      result="printed WRONG, expected s6", passed=False)
    # A red check owes a diagnosis and every standing lens owes a read, or the report is refused.
    A.record_diagnosis(item_dir, check="probe-value", where="probe_s6.py::probe",
                       why="the function returns the literal 'WRONG'")
    for lens in A.STANDING_LENSES:
        A.record_lens(item_dir, probed="the one-line probe module", lens=lens)
    A.write_vet_user_report(item_dir, REPO)
    A.append_cycle_outcome(item_dir, evidence="failed", decision="build",
                     reason="1 check(s) failed — handing the vet report to a build cycle",
                     fingerprint="aabbccddeeff", failed=["probe-value"],
                     tokens=12000, budget=500000)
    A.record_verification(item_dir, REPO, check="probe-value", how="ran the probe",
                      result="printed s6", passed=True)
    for lens in A.STANDING_LENSES:
        A.record_lens(item_dir, probed="the one-line probe module", lens=lens)
    A.write_vet_user_report(item_dir, REPO)
    A.append_cycle_outcome(item_dir, evidence="passed", decision="review",
                     reason="every check green and fresh — advancing to the review gate")


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
    iid = None
    try:
        row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "BV-S6 live: handoff probe",
                                          "text": "Handoff-promotion probe item.",
                                          "autopilot": False})
        iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
        item_dir = KHOME / "work-items" / iid
        print(f"item = {iid}")
        settle(iid)
        seed_record(item_dir)

        # --- turn 1: the promotion rides in ------------------------------------------
        print("turn 1: promotion block rides the intake turn")
        t1 = turn_retry(
            "From the record you were handed this turn, answer in ONE line: how many loop "
            "cycles ran, which check failed in cycle 1, and where did the loop exit?",
            work_item_id=iid)
        # Whether the kernel delivered the block decides how to read anything the model said about
        # it.
        ok("promotion header landed in exactly ONE user message",
           header_count(t1["session_id"]) == 1)
        um1 = user_messages(t1["session_id"])
        ok("the driver's decisions rode in the block",
           "probe-value" in um1 and "→ review" in um1, um1[:300])
        ok("the latest cycle report rode verbatim, named",
           "Latest cycle report (build-vet-1.md, verbatim)" in um1)
        it = item_row(iid)
        ok("watermark advanced at Result", int(it.get("handoffs_promoted") or 0) == 2, str(it))
        # Only now the model's own use of it — a softer claim, and never the first thing to fail.
        low = t1["text"].lower()
        ok("agent narrates FROM the record (names the check + the review exit)",
           "probe-value" in low and "review" in low, t1["text"][:400])

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
