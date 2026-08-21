"""WS-S5 gate test (LIVE half) — real phase sessions on the dummy repo. COSTS TOKENS.

Drives the PRD S5 gate items that need a live agent: a real triage turn at session birth (the
orient block lands in the transcript EXACTLY ONCE — a resumed second turn must not re-inject it);
a background plan run that emits plan.md with a ## Tasks checklist, ends with a structured
completion report (outcome persisted on the run row), leaves the item awaiting_human, and has a
latest checkpoint banked (the agent's own or the daemon's fallback). Self-cleaning: the work-item
(still pre-build) is hard-deleted at the end.

Run with the daemon up: PYTHONPATH=. python -m scripts.test_ws_s5_live
"""

import asyncio
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

import websockets

B = "http://127.0.0.1:8787"
WS = "ws://127.0.0.1:8787/ws/agent"
CTX = "dummy"
PASS = 0


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


async def turn(prompt: str, *, work_item_id: str | None = None, resume: str | None = None) -> dict:
    """One WS turn; auto-denies any approval request (nothing in this test should need one)."""
    out = {"text": "", "session_id": None, "approvals": 0}
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "turn", "prompt": prompt, "context_id": CTX, "mode": "dev",
            "work_item_id": work_item_id, "resume": resume,
        }))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=420))
            t = frame.get("type")
            if t == "approval_request":
                out["approvals"] += 1
                await ws.send(json.dumps({"type": "approval_response", "id": frame["id"],
                                          "approved": False}))
            elif t == "result":
                out["text"] = frame.get("text") or ""
                out["session_id"] = frame.get("session_id")
                return out
            elif t == "error":
                raise AssertionError(f"turn errored: {frame.get('message')}")


def transcript_path(session_id: str) -> Path | None:
    hits = list(Path.home().glob(f".claude/projects/*/{session_id}.jsonl"))
    return hits[0] if hits else None


def orient_user_count(tp: Path) -> int:
    """Occurrences of the orient block in REAL user messages only. The SDK transcript also mirrors
    each prompt into bookkeeping records (queue-operation, last-prompt) — those aren't conversation
    content and re-count the same single injection."""
    n = 0
    for line in tp.read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "user" and "Work-item orientation (kernel-assembled" in line:
            n += 1
    return n


async def turn_retry(prompt: str, *, work_item_id: str, resume: str | None) -> dict:
    """A resumed follow-up can land inside the tiny window where the previous turn's run row is
    still closing (the result frame streams before _end_run) — retry through the busy reply."""
    for _ in range(10):
        t = await turn(prompt, work_item_id=work_item_id, resume=resume)
        if "already has a run in progress" not in t["text"]:
            return t
        await asyncio.sleep(3)
    raise AssertionError("item stayed run-locked")


def main() -> None:
    # -- mint the item --------------------------------------------------------------
    row = http("POST", "/dev/inbox", {
        "context_id": CTX, "title": "S5 live gate: greet util",
        "text": "Add a tiny greet(name) helper to dummy_code.py that returns 'hello <name>', "
                "with one happy-path check."})
    iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
    print(f"item = {iid}")

    try:
        # -- live triage turn at session BIRTH ---------------------------------------
        print("live triage session (birth + resume)")
        t1 = asyncio.run(turn(
            "Run the superme-dev:triage skill for this work-item. Keep it brief.",
            work_item_id=iid))
        sid = t1["session_id"]
        ok("birth turn returned a session", bool(sid))
        ok("triage proposes a kind",
           "implementation" in t1["text"].lower() or "research" in t1["text"].lower(),
           t1["text"][:300])
        ok("triage mentions a deliverable call", "deliverable" in t1["text"].lower(),
           t1["text"][:300])
        t2 = asyncio.run(turn_retry(
            "Thanks — nothing to change. Just acknowledge in one short sentence.",
            work_item_id=iid, resume=sid))
        sid2 = t2["session_id"] or sid
        n = sum(orient_user_count(tp) for s in {sid, sid2}
                if (tp := transcript_path(s)) is not None)
        ok("orient block in USER messages EXACTLY once (no per-turn re-injection)",
           n == 1, f"count={n}")

        # -- background plan run --------------------------------------------------------
        print("background plan (structured completion)")
        for _ in range(10):   # the follow-up turn's run row may still be closing (409 window)
            try:
                http("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})  # triage → plan
                break
            except Exception:
                time.sleep(3)
        else:
            raise AssertionError("advance stayed 409")
        http("POST", f"/dev/work-items/{iid}/plan", {"context_id": CTX})
        deadline = time.time() + 420
        while time.time() < deadline:
            time.sleep(5)
            item = http("GET", f"/dev/work-items/{iid}/detail?context_id={CTX}")["item"]
            if not item.get("running") and item.get("status") == "awaiting_human":
                break
        ok("plan run finished at awaiting_human (pages the owner)",
           item.get("status") == "awaiting_human", str(item.get("status")))
        detail = http("GET", f"/dev/work-items/{iid}/detail?context_id={CTX}")
        ok("plan.md emitted with a ## Tasks checklist",
           bool(detail.get("plan")) and bool(detail.get("tasks")),
           str(detail.get("tasks"))[:120])

        db = Path("superme_agent") / ".system.db"
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        run = con.execute(
            "SELECT outcome, status FROM run WHERE repo_id=? AND item_id=? AND feature='plan' "
            "ORDER BY id DESC LIMIT 1", (CTX, iid)).fetchone()
        con.close()
        ok("structured completion outcome persisted on the run row",
           run is not None and run["outcome"] in ("success", "clean_noop", "blocked",
                                                  "approval_required", "exhausted", "stagnated"),
           str(dict(run) if run else None))

        kh = Path("superme-knowledge") / f"{CTX}-knowledge" / "dev" / "work-items" / iid
        cps = list((kh / "checkpoints").glob("*.md")) if (kh / "checkpoints").is_dir() else []
        ok("a checkpoint is banked (agent's own or the daemon fallback)", len(cps) >= 1,
           str(kh / "checkpoints"))
    finally:
        # -- purge the dummy item (pre-build → hard delete clears folder + session + inbox row)
        try:
            res = http("DELETE", f"/dev/work-items/{iid}?context_id={CTX}")
            print(f"cleanup: deleted {iid} (session_cleared={res.get('session_cleared')})")
        except Exception as e:  # noqa: BLE001
            print(f"cleanup FAILED for {iid}: {e}")

    print(f"\nALL GREEN — {PASS} live checks passed (item purged).")


if __name__ == "__main__":
    main()
