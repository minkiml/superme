"""E2E test for the WI-8 manual capture path (build ③) — the `remember` skill → `request_sweep` →
post-turn sweep. SELF-CLEANING.

Drives a real dev WS turn where the owner flags something to remember. Asserts:
  1. the agent invokes `mcp__dev__request_sweep` (the remember skill's hand) — NOT file_candidate
     inline;
  2. after the turn, the daemon fires a sweep over the just-flushed session and files a candidate
     grounded in the exchange;
  3. provenance binds to that session.

Cleans up the chat session it creates + the candidate + sweep events. Daemon must be UP.
Run:  python -m scripts.test_remember_skill
"""

import asyncio
import json
import sqlite3
import sys
import time

import websockets

from superme_agent.gateway import contexts
from superme_agent.runtime.config import DEV_DB_FILE
from superme_agent.core import DevStore
from superme_agent.core.spine import get_spine

CONTEXT_ID = "global"
WS = "ws://127.0.0.1:8787/ws/agent"
# A natural ask that should trigger the `remember` skill. Phrased as the owner flagging a durable
# operational rule — no mention of tools, sweeps, or candidates.
PROMPT = ("Quick one before we continue — please remember, from now on, that whenever we touch the "
          "BFF layer we must keep its routes as THIN passthroughs to the daemon (no business logic "
          "in web/bff). We keep drifting on this. Just keep that for later, then we'll move on.")


async def drive_turn() -> tuple[str | None, list[dict]]:
    """Run one dev turn; return (session_id, tool_calls) observed from the frames."""
    session_id, tools = None, []
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps({"type": "turn", "context_id": CONTEXT_ID, "mode": "dev",
                                  "prompt": PROMPT}))
        while True:
            try:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
            except asyncio.TimeoutError:
                break
            ftype = frame.get("type")
            if ftype == "approval_request":
                # deny anything that wants approval (we expect none — request_sweep is pre-approved).
                await ws.send(json.dumps({"type": "approval_response",
                                          "id": frame.get("id"), "approved": False}))
            elif ftype == "status":
                tool = frame.get("tool_name") or ""
                if tool.startswith("mcp__dev__") or tool == "Skill":
                    tools.append({"tool": tool, "input": frame.get("tool_input")})
            elif ftype == "result":
                session_id = frame.get("session_id")
            elif ftype in ("done", "error"):
                break
    return session_id, tools


def main() -> int:
    spine = get_spine()
    store = DevStore(DEV_DB_FILE)
    ids_before = {c["id"] for c in store.list_memory_candidates(CONTEXT_ID, status="candidate")}
    filed_ids: set[int] = set()
    session_id = None
    ok = True
    try:
        session_id, tools = asyncio.get_event_loop().run_until_complete(drive_turn())
        print(f"[turn] session={session_id}")
        for t in tools:
            print(f"   called {t['tool']} {json.dumps(t['input'])[:160]}")

        called = {t["tool"] for t in tools}
        assert "mcp__dev__request_sweep" in called, \
            "agent did NOT call request_sweep (remember skill didn't fire)"
        assert "mcp__dev__file_candidate" not in called, \
            "agent filed inline instead of requesting a sweep"
        print("[assert] remember skill fired request_sweep (no inline capture) ✓")
        assert session_id, "no session id from the turn"

        # The sweep fires AFTER the turn — poll for its candidate.
        deadline = time.time() + 90
        filed = []
        while time.time() < deadline:
            cands = store.list_memory_candidates(CONTEXT_ID, status="candidate")
            filed_ids = {c["id"] for c in cands} - ids_before
            filed = [c for c in cands if c["id"] in filed_ids
                     and c.get("origin_session_id") == session_id]
            if filed:
                break
            time.sleep(3)
        assert filed, "post-turn sweep filed no candidate bound to this session"
        for c in filed:
            print(f"   filed #{c['id']} [{c.get('form_hint')}/{c.get('scope_hint')}] "
                  f"src={c.get('source')}")
            print(f"      statement: {c['signal']}")
        print("[assert] post-turn sweep filed a grounded candidate, provenance bound ✓")

        print("\n✅ remember-skill (manual path) E2E PASSED")
    except AssertionError as e:
        ok = False
        print(f"\n❌ FAILED: {e}")
    finally:
        # recompute filed ids broadly (in case the assert path was skipped) for this session
        cands = store.list_memory_candidates(CONTEXT_ID, status="candidate")
        filed_ids |= {c["id"] for c in cands
                      if c.get("origin_session_id") == session_id and c["id"] not in ids_before}
        conn = sqlite3.connect(DEV_DB_FILE)
        try:
            if filed_ids:
                conn.executemany("DELETE FROM memory_candidate WHERE id=?", [(c,) for c in filed_ids])
            if session_id:
                conn.execute("DELETE FROM events WHERE context_id=? AND kind LIKE 'sweep.%' "
                             "AND meta LIKE ?", (CONTEXT_ID, f"%{session_id}%"))
            conn.commit()
        finally:
            conn.close()
        if session_id:
            ctx = contexts.resolve(CONTEXT_ID, "dev")
            from superme_agent.core import SessionStore
            SessionStore(spine).purge(ctx, session_id)   # drop the chat session + its transcript
        print(f"[cleanup] purged chat session + {len(filed_ids)} candidate(s) + sweep events")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
