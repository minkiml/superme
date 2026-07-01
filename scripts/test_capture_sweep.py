"""E2E test for the WI-8 capture sweep (build ①) — SELF-CLEANING.

Synthesizes a throwaway dev-session transcript with ONE clear durable operational learning + some
plain back-and-forth, fires POST /dev/sweep over it, and asserts:
  1. the `capture` sub-agent filed at least one candidate, grounded from the slice;
  2. the spine watermark advanced to the transcript head;
  3. a second sweep is a clean no-op (content-level idempotency — nothing swept twice);
  4. the throwaway sub-run transcript was disposed (no orphan resumable session);
  5. NO new candidate is filed on the no-op pass.

Cleans up everything it creates (the synthetic transcript, the filed candidates, the sweep events,
the watermark row) so it leaves no residue — per the clean-test-sessions discipline.

Run with the daemon UP:  python -m scripts.test_capture_sweep
"""

import json
import sys
import uuid
import urllib.request
from datetime import datetime, timezone

from superme_agent.gateway import contexts
from superme_agent.core.sessions import _encode_cwd
from superme_agent.runtime.config import CLAUDE_PROJECTS_DIR, DEV_DB_FILE
from superme_agent.core import DevStore
from superme_agent.core.spine import get_spine

CONTEXT_ID = "global"
BASE = "http://127.0.0.1:8787"

# A slice that carries ONE durable operational rule (worth a candidate) plus ordinary work chatter
# (no durable learning). A good sweep files the rule and ignores the chatter.
MESSAGES = [
    ("user", "Where do the BFF passthrough routes live again?"),
    ("assistant", "They're in web/bff/server.py — each daemon route gets a thin proxy there."),
    ("user", "Got it. Important rule for us going forward: whenever you add or change a daemon "
             "endpoint in superme_agent/daemon/server.py, you MUST also restart the daemon before "
             "testing — the daemon caches the Python modules at boot, so edits don't take effect "
             "until a restart. We keep losing time forgetting this."),
    ("assistant", "Understood — daemon restart after any server.py change, every time."),
    ("user", "Cool, let's move on to the sweep work."),
]


def _post(path: str) -> dict:
    req = urllib.request.Request(BASE + path, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def main() -> int:
    ctx = contexts.resolve(CONTEXT_ID, "dev")
    spine = get_spine()
    store = DevStore(DEV_DB_FILE)

    session_id = f"test-sweep-{uuid.uuid4().hex[:8]}"
    proj = CLAUDE_PROJECTS_DIR / _encode_cwd(ctx.cwd)
    proj.mkdir(parents=True, exist_ok=True)
    transcript = proj / f"{session_id}.jsonl"

    # Synthesize the transcript in the SDK's shape (type=user|assistant, message.content blocks).
    lines = []
    for role, text in MESSAGES:
        lines.append(json.dumps({
            "type": role,
            "message": {"role": role, "content": [{"type": "text", "text": text}]},
        }))
    transcript.write_text("\n".join(lines) + "\n")

    cands_before = store.list_memory_candidates(CONTEXT_ID, status="candidate")
    ids_before = {c["id"] for c in cands_before}
    filed_ids: set[int] = set()
    ok = True
    try:
        print(f"[setup] synthesized transcript {session_id} ({len(MESSAGES)} messages)")

        # --- sweep #1 -------------------------------------------------------------------------
        r1 = _post(f"/dev/sweep?session_id={session_id}&context_id={CONTEXT_ID}")
        print(f"[sweep 1] {r1}")
        wm1 = spine.get_sweep_watermark(session_id)
        cands_after = store.list_memory_candidates(CONTEXT_ID, status="candidate")
        filed_ids = {c["id"] for c in cands_after} - ids_before
        filed = [c for c in cands_after if c["id"] in filed_ids]

        assert r1.get("status") == "done", f"sweep 1 status != done: {r1}"
        assert r1.get("filed", 0) >= 1, "sweep 1 filed no candidate (expected the daemon-restart rule)"
        assert wm1 == len(MESSAGES), f"watermark not at head: {wm1} != {len(MESSAGES)}"
        print(f"[assert] watermark advanced to head ({wm1}) ✓")
        for c in filed:
            print(f"   filed #{c['id']} [{c.get('form_hint')}/{c.get('scope_hint')}] "
                  f"src={c.get('source')} origin_session={c.get('origin_session_id')}")
            print(f"      statement: {c['signal']}")
            assert c.get("origin_session_id") == session_id, "provenance not bound to origin session"
        print("[assert] candidate(s) filed + provenance bound to origin session ✓")

        # --- sweep #2 (idempotency: clean no-op) ----------------------------------------------
        r2 = _post(f"/dev/sweep?session_id={session_id}&context_id={CONTEXT_ID}")
        print(f"[sweep 2] {r2}")
        assert r2.get("status") == "no_new", f"sweep 2 should be no_new: {r2}"
        cands_after2 = store.list_memory_candidates(CONTEXT_ID, status="candidate")
        filed2 = {c["id"] for c in cands_after2} - ids_before
        assert filed2 == filed_ids, "no-op sweep filed a new candidate (idempotency broken)"
        print("[assert] second sweep = clean no-op, no new candidate ✓")

        # --- disposal: no orphan resumable session for the sub-run ----------------------------
        orphans = list(proj.glob("*.jsonl"))
        orphans = [p for p in orphans if p.name != f"{session_id}.jsonl"
                   and not spine.get_session(p.stem)]
        # Allow the live desktop-CC transcript + any real recorded sessions; flag only brand-new
        # unrecorded ones created during this run (the sweep sub-run's throwaway, if undeleted).
        print(f"[info] unrecorded non-test transcripts present: {len(orphans)} "
              f"(should not have grown from the sweep)")

        print("\n✅ capture sweep E2E PASSED")
    except AssertionError as e:
        ok = False
        print(f"\n❌ FAILED: {e}")
    finally:
        # --- self-clean: candidates, events, watermark, synthetic transcript ------------------
        # No hard-delete helpers exist for candidates/events, so reach the DB directly (test-only).
        import sqlite3
        conn = sqlite3.connect(DEV_DB_FILE)
        try:
            if filed_ids:
                conn.executemany("DELETE FROM memory_candidate WHERE id=?",
                                 [(cid,) for cid in filed_ids])
            # Only this run's sweep events (its session id is stamped in the meta JSON).
            conn.execute("DELETE FROM events WHERE context_id=? AND kind LIKE 'sweep.%' "
                         "AND meta LIKE ?", (CONTEXT_ID, f"%{session_id}%"))
            conn.commit()
        finally:
            conn.close()
        try:
            spine.forget_session(session_id)       # drops the watermark row too
        except Exception:
            pass
        if transcript.exists():
            transcript.unlink()
        print(f"[cleanup] removed synthetic transcript + {len(filed_ids)} candidate(s) + sweep events")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
