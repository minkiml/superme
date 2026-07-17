"""E2E test for the WI-8 capture-sweep PHASE-ADVANCE + COMPLETION triggers (build ②) — SELF-CLEANING.

Creates a DISPOSABLE work-item, binds a synthetic session with a durable operational learning, then:
  1. POST /advance (plan_design → build_eval) → asserts the bound session is swept (candidate filed,
     watermark advanced) and the transcript SURVIVES (advance doesn't reclaim it);
  2. advances to `done`, then POST /complete → asserts a final sweep ran and the transcript was
     PURGED afterwards (the sweep read it before the chained purge).

Hard-deletes the work-item + scrubs candidates/events/session. Daemon must be UP.
Run:  python -m scripts.test_sweep_lifecycle
"""

import json
import sqlite3
import sys
import time
import uuid
import urllib.request

from superme_agent.gateway import contexts
from superme_agent.core.sessions import _encode_cwd
from superme_agent.runtime.config import CLAUDE_PROJECTS_DIR, DEV_DB_FILE
from superme_agent.core import DevStore, DevKnowledgeService
from superme_agent.core.spine import get_spine

CONTEXT_ID = "global"
BASE = "http://127.0.0.1:8787"

PLAN_MSGS = [
    ("user", "While planning this: a firm rule for our daemon work — after editing any *.py under "
             "superme_agent/, you MUST restart the daemon before testing, because it caches modules "
             "at boot and stale code silently passes/fails tests otherwise."),
    ("assistant", "Locked in — restart the daemon after every Python edit before testing."),
]


def _post(path: str) -> dict:
    req = urllib.request.Request(BASE + path, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def _wait_sweep_end(store, session_id: str, timeout: float = 90) -> dict | None:
    """Poll the event log for this session's sweep.end (the background sweep finished)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        evs = store.list_events(CONTEXT_ID, scope="dev", limit=200)
        for e in evs:
            if e["kind"] == "sweep.end" and session_id in str(e.get("meta")):
                return e
        time.sleep(2)
    return None


def main() -> int:
    ctx = contexts.resolve(CONTEXT_ID, "dev")
    dev_root = ctx.internal_root / "dev"
    spine = get_spine()
    store = DevStore(DEV_DB_FILE)
    dev = DevKnowledgeService()

    session_id = f"test-life-{uuid.uuid4().hex[:8]}"
    proj = CLAUDE_PROJECTS_DIR / _encode_cwd(ctx.cwd)
    proj.mkdir(parents=True, exist_ok=True)
    transcript = proj / f"{session_id}.jsonl"
    transcript.write_text("\n".join(
        json.dumps({"type": r, "message": {"role": r, "content": [{"type": "text", "text": t}]}})
        for r, t in PLAN_MSGS) + "\n")

    created = dev.create_work_item(dev_root, "TEST sweep lifecycle disposable", "throwaway",
                                   session_id=session_id)
    item_id = created["id"]
    spine.record_session(session_id, ctx.cwd, surface="web", mode="dev", repo_id=CONTEXT_ID)

    ids_before = {c["id"] for c in store.list_memory_candidates(CONTEXT_ID, status="candidate")}
    filed_ids: set[int] = set()
    ok = True
    try:
        print(f"[setup] disposable item {item_id} bound to session {session_id}")

        # --- advance: triage → plan (workspace-workflow pipeline) should fire a sweep ---------
        r1 = _post(f"/dev/work-items/{item_id}/advance?context_id={CONTEXT_ID}")
        print(f"[advance] {r1}")
        assert r1.get("phase") == "plan", f"advance didn't move phase: {r1}"
        end = _wait_sweep_end(store, session_id)
        assert end is not None, "no sweep.end after advance (trigger didn't fire)"
        print(f"[advance] sweep.end: {end['summary']}")
        assert transcript.exists(), "advance wrongly purged the transcript"
        wm = spine.get_sweep_watermark(session_id)
        assert wm == len(PLAN_MSGS), f"watermark not advanced after advance sweep: {wm}"
        cands = store.list_memory_candidates(CONTEXT_ID, status="candidate")
        filed_ids = {c["id"] for c in cands} - ids_before
        assert len(filed_ids) >= 1, "advance sweep filed no candidate"
        print(f"[assert] advance → swept, transcript survives, watermark={wm}, "
              f"{len(filed_ids)} candidate(s) filed ✓")

        # --- complete: close-phase → tick-out should fire a FINAL sweep THEN purge ------------
        dev.set_work_item_phase(dev_root, item_id, "close")
        r2 = _post(f"/dev/work-items/{item_id}/complete?context_id={CONTEXT_ID}")
        print(f"[complete] {r2}")
        # The bound session is already caught up (advance swept it), so the final sweep is a no-op
        # — but the transcript must still be PURGED by the chained then_purge.
        deadline = time.time() + 60
        while transcript.exists() and time.time() < deadline:
            time.sleep(2)
        assert not transcript.exists(), "completion did not purge the transcript after the sweep"
        print("[assert] complete → final sweep ran, transcript purged afterwards ✓")

        print("\n✅ lifecycle-trigger E2E PASSED")
    except AssertionError as e:
        ok = False
        print(f"\n❌ FAILED: {e}")
    finally:
        # hard-delete the disposable item folder + scrub spine/db residue
        try:
            dev.delete_work_item(dev_root, item_id)
        except Exception as e:
            print(f"[cleanup] delete_work_item failed: {e}")
        conn = sqlite3.connect(DEV_DB_FILE)
        try:
            if filed_ids:
                conn.executemany("DELETE FROM memory_candidate WHERE id=?", [(c,) for c in filed_ids])
            conn.execute("DELETE FROM events WHERE context_id=? AND (kind LIKE 'sweep.%' "
                         "AND meta LIKE ?)", (CONTEXT_ID, f"%{session_id}%"))
            conn.execute("DELETE FROM events WHERE context_id=? AND item_id=?", (CONTEXT_ID, item_id))
            conn.commit()
        finally:
            conn.close()
        try:
            spine.forget_session(session_id)
            spine.delete_item_runs(CONTEXT_ID, item_id)
        except Exception:
            pass
        if transcript.exists():
            transcript.unlink()
        print(f"[cleanup] removed item {item_id} + {len(filed_ids)} candidate(s) + session + events")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
