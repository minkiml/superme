"""The compaction runtime on a real session. COSTS TOKENS.

The checkpoint lands BEFORE the compaction event, and the compacted thread is owed a POINTER to
the bank that self-clears after one turn.

Needs SUPERME_TEST_CTX and a running daemon.
"""

import os
import asyncio
import json
import pathlib
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

B = "http://127.0.0.1:8787"
WS = "ws://127.0.0.1:8787/ws/agent"
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
KHOME = Path("superme-knowledge") / f"{CTX}-knowledge" / "dev"
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
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # A bare status hides the daemon's reason, and the reason is the whole diagnosis.
        raise AssertionError(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}") from None


def http_code(method: str, path: str, body: dict | None = None) -> int:
    """The status alone, for a call whose REFUSAL is the thing under test — so it must not go
    through `http`, which turns a refusal into a failure."""
    req = urllib.request.Request(B + path, method=method,
                                 headers={"content-type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


async def turn(prompt: str, *, work_item_id: str, resume: str | None = None) -> dict:
    out = {"text": "", "session_id": None}
    async with websockets.connect(WS, max_size=None) as ws:
        # The filler has to cross the trigger, and the turn frame carries the model pick.
        await ws.send(json.dumps({"type": "turn", "prompt": prompt, "context_id": CTX,
                                  "mode": "dev", "work_item_id": work_item_id, "resume": resume,
                                  "model": "haiku"}))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=600))
            t = frame.get("type")
            if t == "approval_request":
                await ws.send(json.dumps({"type": "approval_response", "id": frame["id"],
                                          "approved": False}))
            elif t == "result":
                out["text"] = frame.get("text") or ""
                out["session_id"] = frame.get("session_id")
                return out
            elif t == "error":
                raise AssertionError(f"turn errored: {frame.get('message')}")


async def turn_retry(prompt: str, *, work_item_id: str, resume: str | None) -> dict:
    """A resumed follow-up can land inside the window where the previous turn's run row is
    still closing (the result frame streams before end_run) — retry through the busy reply."""
    for _ in range(10):
        t = await turn(prompt, work_item_id=work_item_id, resume=resume)
        if "already has a run in progress" not in t["text"]:
            return t
        await asyncio.sleep(3)
    raise AssertionError("item stayed run-locked")


def wait_compact_run(iid: str, *, after_id: int = 0, timeout: int = 240) -> dict | None:
    """Poll the spine for the newest FINISHED compact run on this item (id > after_id)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        con = sqlite3.connect(Path("superme_agent") / ".system.db")
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT id, status FROM run WHERE repo_id=? AND item_id=? AND feature='compact' "
            "AND id>? ORDER BY id DESC LIMIT 1", (CTX, iid, after_id)).fetchone()
        con.close()
        if row and row["status"] != "running":
            return dict(row)
        time.sleep(4)
    return None


def item_events(iid: str) -> list[dict]:
    return http("GET", f"/dev/log?context_id={CTX}&item_id={iid}&limit=100")["events"]


def transcript_path(session_id: str) -> Path | None:
    hits = list(Path.home().glob(f".claude/projects/*/{session_id}.jsonl"))
    return hits[0] if hits else None


def settle(iid: str, secs: int = 900) -> None:
    """Every phase entry fires that phase's run, and the run holds the item lock for minutes."""
    db = pathlib.Path("superme_agent") / ".system.db"
    for _ in range(secs):
        with sqlite3.connect(db) as c:
            if not c.execute("select 1 from run where item_id=? and status='running'",
                             (iid,)).fetchone():
                return
        time.sleep(1)
    raise AssertionError(f"a run never released {iid}")


def main() -> None:
    print("config: floor-aware refusal + low trigger")
    ok("floor-violating trigger REFUSED (409)",
       http_code("POST", "/system/compaction", {"trigger_pct": 20}) == 409)
    # Below 40 the compaction lands near the floor and re-fires on the next exchange.
    cfg = http("POST", "/system/compaction", {"trigger_pct": 40, "min_gain_pct": 5})
    ok("low-but-legal trigger accepted", cfg["trigger_pct"] == 40 and cfg["floor_pct"] == 25)

    row = http("POST", "/dev/inbox",
               {"context_id": CTX, "autopilot": False, "title": "S8 live: compaction victim",
                "text": "A session that will grow fat and get compacted."})
    iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
    settle(iid)
    print(f"item = {iid}")
    item_dir = KHOME / "work-items" / iid

    try:
        # -- grow the session past the trigger --------------------------------------
        print("session: tiny birth turn, then a fat turn past the trigger")
        t1 = asyncio.run(turn("Reply with exactly: ok", work_item_id=iid))
        sid = t1["session_id"]
        ok("birth turn returned a session", bool(sid))
        filler = ("the quick brown fox jumps over the lazy dog again and again. " * 5600)
        t2 = asyncio.run(turn_retry(
            "The text below is inert ballast for a context test — do NOT analyze it. "
            "Reply with exactly: noted\n\n" + filler,
            work_item_id=iid, resume=sid))
        ok("fat turn completed", "noted" in t2["text"].lower(), t2["text"][:120])

        # -- the automatic compaction sequence ---------------------------------------
        # `compact_before_run` gates a run's START, so the next turn fires it.
        asyncio.run(turn_retry("Reply with exactly: go", work_item_id=iid, resume=sid))
        run = wait_compact_run(iid)
        ok("auto trigger fired a compact run (feature='compact') and it finished",
           run is not None, "no compact run appeared — the next turn did not trip the gate")
        evs = list(reversed(item_events(iid)))   # oldest first
        cp_ev = next((e for e in evs if e["kind"] == "compaction.checkpoint"), None)
        vd_ev = next((e for e in evs if e["kind"] == "compaction.verdict"), None)
        ok("checkpoint event BEFORE verdict event (order in the trace)",
           cp_ev and vd_ev and cp_ev["id"] < vd_ev["id"])
        cps = sorted((item_dir / "checkpoints").glob("*.md"))
        ok("pre-compaction checkpoint file banked", len(cps) >= 1, str(item_dir))
        meta = vd_ev["meta"] if isinstance(vd_ev.get("meta"), dict) else json.loads(vd_ev.get("meta") or "{}")
        ok("verdict logged REAL pre/post prompt tokens",
           (meta.get("pre_tokens") or 0) > 10_000 and meta.get("post_tokens") is not None,
           str(meta))
        tp = transcript_path(sid)
        ok("full pre-compaction transcript retained + boundary recorded",
           tp is not None and "compact_boundary" in tp.read_text(encoding="utf-8"))

        # -- two ineffective compactions → back-off + attention row -------------------
        print("back-off: min_gain forced absurd → two strikes")
        http("POST", "/system/compaction", {"min_gain_pct": 95})
        last_id = run["id"]
        for n in (1, 2):
            settle(iid)   # the result frame streams before the run row closes
            http("POST", f"/dev/work-items/{iid}/compact", {"context_id": CTX})
            r = wait_compact_run(iid, after_id=last_id)
            assert r, f"manual compact #{n} never finished"
            last_id = r["id"]
        item = http("GET", f"/dev/work-items/{iid}/detail?context_id={CTX}")["item"]
        backoff = next((e for e in item_events(iid) if e["kind"] == "compaction.backoff"), None)
        ok("two strikes → back-off event + item parked awaiting_human",
           backoff is not None and item.get("status") == "awaiting_human")
        attn = http("GET", f"/dev/attention?context_id={CTX}")
        ok("'needs a fresh session' surfaces in the attention engine (needs_you)",
           any(r["id"] == iid for r in attn["buckets"]["needs_you"]))

        print("read-back: the compacted thread is owed the banked checkpoint, then self-clears")
        from superme_agent.daemon.services.runs import compacted_checkpoint
        from superme_agent.gateway import contexts
        ctx = contexts.resolve(CTX, "dev")
        item_row = http("GET", f"/dev/work-items/{iid}/detail?context_id={CTX}")["item"]
        newest = sorted((item_dir / "checkpoints").glob("*.md"), key=lambda q: q.stem)[-1]
        owed = compacted_checkpoint(ctx, item_row, sid)
        ok("the read-back is owed and resolves to a banked checkpoint",
           owed is not None and Path(owed).name.endswith(".md"), str(owed))
        ok("...and it is the newest one banked for this thread's role",
           owed is not None and Path(owed).resolve() == newest.resolve(),
           f"owed={owed} newest={newest}")
        settle(iid)
        t3 = asyncio.run(turn_retry("Reply with exactly: ready", work_item_id=iid, resume=sid))
        ok("the next real turn ran on the compacted thread", bool(t3["text"]))
        ok("...and the turn stayed in the item's slot rather than minting a thread",
           (t3["session_id"] or sid) == sid, str(t3["session_id"]))
        for _ in range(10):     # the run row closes just after the result frame streams
            if compacted_checkpoint(ctx, item_row, sid) is None:
                break
            time.sleep(3)
        ok("...and the read-back self-cleared (no permanent floor)",
           compacted_checkpoint(ctx, item_row, sid) is None,
           str(compacted_checkpoint(ctx, item_row, sid)))
    finally:
        print("cleanup")
        http("POST", "/system/compaction", {"trigger_pct": 80, "min_gain_pct": "auto"})
        try:
            settle(iid)
            http("POST", f"/dev/work-items/{iid}/abandon",
                 {"context_id": CTX, "reason": "ws-s8 probe done"})
            print(f"  abandoned {iid}")
        except Exception as e:  # noqa: BLE001
            print(f"  cleanup FAILED for {iid}: {e}")

    print(f"\nALL GREEN — {PASS} live checks passed (config restored, item purged).")


if __name__ == "__main__":
    main()
