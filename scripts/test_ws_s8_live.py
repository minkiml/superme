"""The compaction runtime on a real session. COSTS TOKENS.

The pre-compaction checkpoint lands BEFORE the compaction event, the verdict logs real boundary
tokens, and two ineffective attempts latch a back-off. A fresh session cold-starts from the bank.

Run with the daemon up: PYTHONPATH=. python -m scripts.test_ws_s8_live
"""

import asyncio
import json
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

B = "http://127.0.0.1:8787"
WS = "ws://127.0.0.1:8787/ws/agent"
CTX = "dummy"
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
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def http_code(method: str, path: str, body: dict | None = None) -> int:
    try:
        http(method, path, body)
        return 200
    except urllib.error.HTTPError as e:
        return e.code


async def turn(prompt: str, *, work_item_id: str, resume: str | None = None) -> dict:
    out = {"text": "", "session_id": None}
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps({"type": "turn", "prompt": prompt, "context_id": CTX,
                                  "mode": "dev", "work_item_id": work_item_id, "resume": resume}))
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


def main() -> None:
    print("config: floor-aware refusal + low trigger")
    ok("floor-violating trigger REFUSED (409)",
       http_code("POST", "/system/compaction", {"trigger_pct": 20}) == 409)
    cfg = http("POST", "/system/compaction", {"trigger_pct": 26, "min_gain_pct": 5})
    ok("low-but-legal trigger accepted", cfg["trigger_pct"] == 26 and cfg["floor_pct"] == 25)

    row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "S8 live: compaction victim",
                                      "text": "A session that will grow fat and get compacted."})
    iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
    http("POST", f"/dev/work-items/{iid}/model", {"context_id": CTX, "model": "haiku"})
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
        run = wait_compact_run(iid)
        ok("auto trigger fired a compact run (feature='compact') and it finished",
           run is not None, "no compact run appeared — fill may not have crossed the trigger")
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
           tp is not None and "compact_boundary" in tp.read_text())

        # -- two ineffective compactions → back-off + attention row -------------------
        print("back-off: min_gain forced absurd → two strikes")
        http("POST", "/system/compaction", {"min_gain_pct": 95})
        last_id = run["id"]
        for n in (1, 2):
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

        # -- fresh session cold-starts from EXACTLY the banked checkpoint -------------
        print("cold start: new session reads the banked checkpoint via the orient block")
        newest = sorted((item_dir / "checkpoints").glob("*.md"), key=lambda p: p.stem)[-1]
        marker = next(ln.strip() for ln in newest.read_text().splitlines()
                      if ln.strip().startswith("## Working on"))
        body_line = newest.read_text().split("## Working on", 1)[1].strip().splitlines()[0]
        t3 = asyncio.run(turn("Reply with exactly: ready", work_item_id=iid))  # no resume → fresh
        sid3 = t3["session_id"]
        tp3 = transcript_path(sid3)
        txt3 = tp3.read_text() if tp3 else ""
        ok("fresh session's orient block carries the LATEST checkpoint content",
           sid3 and sid3 != sid and "Latest checkpoint" in txt3 and body_line[:40] in txt3,
           f"marker={body_line[:40]!r}")
    finally:
        print("cleanup")
        http("POST", "/system/compaction", {"trigger_pct": 80, "min_gain_pct": "auto"})
        try:
            res = http("DELETE", f"/dev/work-items/{iid}?context_id={CTX}")
            print(f"  deleted {iid} (session_cleared={res.get('session_cleared')})")
        except Exception as e:  # noqa: BLE001
            print(f"  cleanup FAILED for {iid}: {e}")

    print(f"\nALL GREEN — {PASS} live checks passed (config restored, item purged).")


if __name__ == "__main__":
    main()
