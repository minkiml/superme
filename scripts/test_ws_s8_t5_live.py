"""Compaction of a session with NO work-item. COSTS TOKENS.

`/compact` typed by the owner is intercepted rather than passed to the CLI, and the compaction
opens a plain session run claiming zero measured usage. Needs a running daemon.
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
CTX = "test-playground"
MODEL = "sonnet"
MEMDIR = Path("superme-knowledge") / f"{CTX}-knowledge" / "dev" / "session-memory"
DB = Path("superme_agent") / ".system.db"
PASS = 0

# The ask the thread must carry across the boundary — verbatim, per the skill's item 1.
ASK = "and later, remind me to rename the sum command to total"


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


def q(sql: str, args: tuple = ()) -> list[dict]:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql, args).fetchall()]
    con.close()
    return rows


async def turn(prompt: str, *, resume: str | None = None) -> dict:
    """One GENERAL turn — no work_item_id, so this is the session shape with no item."""
    out = {"text": "", "session_id": None}
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps({"type": "turn", "prompt": prompt, "context_id": CTX,
                                  "mode": "dev", "model": MODEL, "resume": resume}))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=600))
            t = frame.get("type")
            if t == "approval_request":
                await ws.send(json.dumps({"type": "approval_response", "id": frame["id"],
                                          "approved": False}))
            elif t == "result":
                out["text"] = frame.get("text") or ""
                out["session_id"] = frame.get("session_id") or resume
                return out
            elif t == "error":
                raise AssertionError(f"turn errored: {frame.get('message')}")


def main() -> None:
    from superme_agent.core.spine import SystemSpine
    spine = SystemSpine()
    before = q("SELECT MAX(id) AS m FROM run")[0]["m"] or 0

    print("1 · a general session with something only the conversation knows")
    t1 = asyncio.run(turn(
        "Quick context for this thread, no action needed yet: I'm thinking about the ledger "
        "CLI's output format, I've decided against adding any new flags for it, "
        f"{ASK}. Just acknowledge in one line."))
    sid = t1["session_id"]
    ok("the turn returned a session", bool(sid), t1["text"][:120])
    row = spine.get_session(sid) or {}
    ok("it is a general (non-item) session", not row.get("item_id")
       and str(row.get("kind") or "general") in ("general", "onboarding"),
       f"kind={row.get('kind')} item={row.get('item_id')}")
    memfile = MEMDIR / f"{sid}.md"
    ok("no session memory exists yet", not memfile.exists())

    print("2 · the owner types /compact — the kernel takes it, the CLI does not")
    t2 = asyncio.run(turn("/compact", resume=sid))
    ok("the reply is the kernel's compaction report, not an agent turn",
       t2["text"].startswith("Compacted:") or "Compaction" in t2["text"], t2["text"][:200])

    print("3 · what the thread banked")
    ok("session-memory/<sid>.md exists — the general session's only disk copy",
       memfile.exists(), str(memfile))
    text = memfile.read_text()
    # The field NAMES are the contract, not their order: nothing parses this file, and its reader
    # is the next agent on the thread.
    low = text.lower().replace("_", " ")
    ok("all four fields present — the same content contract as a work-item checkpoint",
       all(h in low for h in ("working on", "decisions", "remaining", "notes")))
    ok("it carries the owner's unfulfilled ask (item 1 — the field this design turns on)",
       "rename" in text.lower() and ("total" in text.lower() or "sum" in text.lower()),
       text[:600])
    print("--- banked memory ---\n" + text + "---------------------")

    print("4 · the run row — the session-run path that did not exist before T5")
    runs = q("SELECT id,feature,status,item_id,session_id,tokens,tok_input,tok_output"
             " FROM run WHERE id>? AND feature='compact' ORDER BY id DESC", (before,))
    ok("a compact run was opened", bool(runs), "no compact run after this session's turns")
    r = runs[0]
    ok("...with NO item — a plain session run", r["item_id"] is None, str(r))
    ok("...bound to this session", r["session_id"] == sid, str(r))
    ok("...finished", r["status"] == "done", str(r))
    ok("...and claims zero usage, because none was measured (no-estimated-usage)",
       (r["tokens"] or 0) == 0 and (r["tok_input"] or 0) == 0 and (r["tok_output"] or 0) == 0,
       str(r))
    ev = http("GET", f"/dev/log?context_id={CTX}&limit=40")["events"]
    kinds = [e["kind"] for e in ev]
    ok("the checkpoint event precedes the verdict event (checkpoint-FIRST holds here too)",
       "compaction.checkpoint" in kinds and "compaction.verdict" in kinds
       and kinds.index("compaction.checkpoint") > kinds.index("compaction.verdict"),
       str(kinds[:6]))  # newest-first feed, so checkpoint sits LATER in the list
    cp = next(e for e in ev if e["kind"] == "compaction.checkpoint")
    ok("...and the session wrote it ITSELF (handoff turn, not a derived bank)",
       (cp.get("meta") or {}).get("by_agent") is True, str(cp.get("meta")))
    vd = next(e for e in ev if e["kind"] == "compaction.verdict")
    m = vd.get("meta") or {}
    print(f"    verdict: {m.get('pre_tokens')} → {m.get('post_tokens')} tok "
          f"({m.get('gain_pct')}% · mode={m.get('mode')} · ratio={m.get('reclaimed_ratio')})")
    ok("the verdict captured the summary text, not just the numbers", bool(m.get("summary")))
    ok("trigger_pct is None — this was manual, and a 0 would poison the calibration data",
       m.get("trigger_pct") is None, str(m.get("trigger_pct")))

    print("5 · the read-back — owed now, self-clearing after one real turn")
    from superme_agent.daemon.services.runs import compacted_session_memory
    from superme_agent.gateway import contexts
    ctx = contexts.resolve(CTX, "dev")
    ok("the notice is owed and resolves to the banked file",
       compacted_session_memory(ctx, sid) == str(memfile.resolve()),
       str(compacted_session_memory(ctx, sid)))
    t3 = asyncio.run(turn("Thanks — nothing else for now.", resume=sid))
    ok("the next real turn ran on the compacted thread", bool(t3["text"]))
    for _ in range(10):     # the run row closes just after the result frame streams
        if compacted_session_memory(ctx, sid) is None:
            break
        time.sleep(2)
    ok("...and the notice has self-cleared (no permanent floor)",
       compacted_session_memory(ctx, sid) is None)

    print("6 · cleanup (session + memory file; run rows are logs and stay)")
    from superme_agent.daemon.app_state import sessions as _sessions
    _sessions.delete(ctx, sid, cause="test")
    memfile.unlink(missing_ok=True)
    ok("session deleted", spine.get_session(sid) is None)
    ok("memory file removed", not memfile.exists())
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
