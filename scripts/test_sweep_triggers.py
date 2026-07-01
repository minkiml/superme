"""E2E test for the WI-8 capture-sweep IDLE trigger (build ②) — SELF-CLEANING.

Records a synthetic dev session with a durable operational learning, ages its transcript past the
idle threshold, forces an idle-scan pass, and asserts:
  1. the idle scan picks up the quiet, un-swept session and sweeps it (candidate filed);
  2. the watermark advances to head;
  3. a second idle-scan does NOT re-sweep it (watermark caught up — content-level idempotency);
  4. an ACTIVE (fresh-mtime) session is skipped by the scan.

Self-cleans (transcript, candidates, sweep events, recorded session). Daemon must be UP.
Run:  python -m scripts.test_sweep_triggers
"""

import json
import os
import sqlite3
import sys
import time
import uuid
import urllib.request

from superme_agent.gateway import contexts
from superme_agent.core.sessions import _encode_cwd
from superme_agent.runtime.config import CLAUDE_PROJECTS_DIR, DEV_DB_FILE
from superme_agent.core import DevStore
from superme_agent.core.spine import get_spine

CONTEXT_ID = "global"
BASE = "http://127.0.0.1:8787"

MESSAGES = [
    ("user", "Quick convention for our test scripts going forward: every test that drives the daemon "
             "MUST self-clean — purge the sessions, candidates, and events it creates at the end — so "
             "we never leave dummy sessions piling up in the picker. We keep having to hand-clean."),
    ("assistant", "Agreed — all daemon test harnesses self-clean their residue on exit, every time."),
]


def _post(path: str) -> dict:
    req = urllib.request.Request(BASE + path, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def _write_session(ctx, messages, *, aged_seconds: int) -> tuple[str, "Path"]:
    sid = f"test-trig-{uuid.uuid4().hex[:8]}"
    proj = CLAUDE_PROJECTS_DIR / _encode_cwd(ctx.cwd)
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{sid}.jsonl"
    lines = [json.dumps({"type": role,
                         "message": {"role": role, "content": [{"type": "text", "text": text}]}})
             for role, text in messages]
    path.write_text("\n".join(lines) + "\n")
    # Age the transcript so the idle scan treats it as quiet.
    past = time.time() - aged_seconds
    os.utime(path, (past, past))
    return sid, path


def main() -> int:
    ctx = contexts.resolve(CONTEXT_ID, "dev")
    spine = get_spine()
    store = DevStore(DEV_DB_FILE)

    # An IDLE session (old mtime, un-swept) + an ACTIVE session (fresh mtime) — the scan should sweep
    # the first and skip the second.
    idle_sid, idle_path = _write_session(ctx, MESSAGES, aged_seconds=3600)
    active_sid, active_path = _write_session(ctx, MESSAGES, aged_seconds=0)
    spine.record_session(idle_sid, ctx.cwd, surface="web", mode="dev", repo_id=CONTEXT_ID)
    spine.record_session(active_sid, ctx.cwd, surface="web", mode="dev", repo_id=CONTEXT_ID)

    ids_before = {c["id"] for c in store.list_memory_candidates(CONTEXT_ID, status="candidate")}
    filed_ids: set[int] = set()
    ok = True
    try:
        print(f"[setup] idle session {idle_sid} (aged 1h) + active session {active_sid} (fresh)")

        # idle_seconds=600: idle session (aged 3600s) qualifies; active (0s) does not.
        r1 = _post("/dev/sweep/idle-scan?idle_seconds=600")
        print(f"[idle-scan 1] {r1}")
        swept_sids = {s["session_id"] for s in r1.get("swept", [])}
        assert idle_sid in swept_sids, "idle scan did not sweep the quiet un-swept session"
        assert active_sid not in swept_sids, "idle scan wrongly swept the ACTIVE (fresh) session"
        print("[assert] idle session swept, active session skipped ✓")

        assert spine.get_sweep_watermark(idle_sid) == len(MESSAGES), "watermark not at head"
        cands_after = store.list_memory_candidates(CONTEXT_ID, status="candidate")
        filed_ids = {c["id"] for c in cands_after} - ids_before
        filed = [c for c in cands_after if c["id"] in filed_ids]
        assert len(filed) >= 1, "no candidate filed by the idle sweep"
        for c in filed:
            print(f"   filed #{c['id']} [{c.get('form_hint')}/{c.get('scope_hint')}] "
                  f"origin_session={c.get('origin_session_id')}")
            print(f"      statement: {c['signal']}")
            assert c.get("origin_session_id") == idle_sid, "provenance not bound to the idle session"
        print("[assert] watermark advanced + candidate filed + provenance bound ✓")

        # Second scan: the idle session is now caught up → not re-swept.
        r2 = _post("/dev/sweep/idle-scan?idle_seconds=600")
        print(f"[idle-scan 2] {r2}")
        assert idle_sid not in {s["session_id"] for s in r2.get("swept", [])}, \
            "idle session re-swept (idempotency broken)"
        filed2 = {c["id"] for c in store.list_memory_candidates(CONTEXT_ID, status="candidate")} - ids_before
        assert filed2 == filed_ids, "second idle scan filed a new candidate"
        print("[assert] second idle scan = no re-sweep, no new candidate ✓")

        print("\n✅ idle-trigger E2E PASSED")
    except AssertionError as e:
        ok = False
        print(f"\n❌ FAILED: {e}")
    finally:
        conn = sqlite3.connect(DEV_DB_FILE)
        try:
            if filed_ids:
                conn.executemany("DELETE FROM memory_candidate WHERE id=?", [(c,) for c in filed_ids])
            for sid in (idle_sid, active_sid):
                conn.execute("DELETE FROM events WHERE context_id=? AND kind LIKE 'sweep.%' "
                             "AND meta LIKE ?", (CONTEXT_ID, f"%{sid}%"))
            conn.commit()
        finally:
            conn.close()
        for sid, path in ((idle_sid, idle_path), (active_sid, active_path)):
            try:
                spine.forget_session(sid)
            except Exception:
                pass
            if path.exists():
                path.unlink()
        print(f"[cleanup] removed 2 synthetic sessions + {len(filed_ids)} candidate(s) + sweep events")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
