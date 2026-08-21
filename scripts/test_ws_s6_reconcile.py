"""WS-S6 gate (reconcile half) — kill-mid-close healed by startup reconciliation (D8/nimbalyst).

Two subcommands around a REAL daemon restart:
  setup   — builds a dummy work-item up to a live worktree, then manufactures the exact state a
            daemon dying mid-close leaves behind: item flipped terminal(completed) but the
            worktree dir still on disk, no execution.md snapshot, and a run row stuck 'running'.
  verify  — after the daemon restart: the worktree dir is gone (branch kept), execution.md was
            re-snapshot, and the stuck run was released to 'aborted'. Then cleans everything up.

Run: PYTHONPATH=. python -m scripts.test_ws_s6_reconcile setup
     (restart the daemon)
     PYTHONPATH=. python -m scripts.test_ws_s6_reconcile verify
"""

import os
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

B = "http://127.0.0.1:8787"
CTX = "dummy"
# This suite MUTATES the repo it points at: `git reset --hard`, `git clean -fd`, branch
# deletion. Name a throwaway one, and never a repo holding work you want.
REPO = Path(os.environ.get("SUPERME_TEST_REPO") or "~/superme-test-repo").expanduser()
if not (REPO / ".git").is_dir():
    raise SystemExit(f"SUPERME_TEST_REPO is not a git repo: {REPO}")
KHOME = Path("superme-knowledge") / f"{CTX}-knowledge" / "dev"
STATE = Path("scripts") / ".s6_reconcile_state.json"


def http(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(B + path, method=method,
                                 headers={"content-type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def setup() -> None:
    from superme_agent.core.dev_knowledge import DevKnowledgeService
    from superme_agent.core.spine import SystemSpine
    row = http("POST", "/dev/inbox", {"context_id": CTX, "title": "S6 reconcile victim",
                                      "text": "killed mid-close"})
    iid = http("POST", f"/dev/inbox/{row['id']}/push", {"context_id": CTX})["work_item"]["id"]
    http("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})   # triage → plan
    adv = http("POST", f"/dev/work-items/{iid}/advance?context_id={CTX}", {})  # plan → build
    wt = adv["git"]["worktree"]
    # Manufacture the crash hole: terminal on disk, close steps never ran.
    dev = DevKnowledgeService()
    assert dev.set_work_item_terminal(KHOME, iid, "completed")
    spine = SystemSpine(db_path=Path("superme_agent") / ".system.db")
    run_id = spine.start_item_run(CTX, feature="chat", item_id=iid, model="m")
    exec_md = KHOME / "work-items" / iid / "artifacts" / "execution.md"
    assert Path(wt).is_dir() and not exec_md.exists()
    STATE.write_text(json.dumps({"item": iid, "worktree": wt, "branch": adv["git"]["branch"],
                                 "run_id": run_id, "inbox_id": row["id"]}))
    print(f"setup done — item {iid} is terminal with a live worktree, no snapshot, and run "
          f"#{run_id} stuck 'running'. Restart the daemon, then run `verify`.")


def verify() -> None:
    import sqlite3
    st = json.loads(STATE.read_text())
    iid, wt = st["item"], Path(st["worktree"])
    exec_md = KHOME / "work-items" / iid / "artifacts" / "execution.md"
    branches = subprocess.run(["git", "branch", "--list", st["branch"]], cwd=REPO,
                              capture_output=True, text=True).stdout
    con = sqlite3.connect(Path("superme_agent") / ".system.db")
    status = con.execute("SELECT status FROM run WHERE id=?", (st["run_id"],)).fetchone()[0]
    con.close()
    checks = {
        "worktree dir removed (branch kept)": (not wt.exists()) and bool(branches.strip()),
        "execution.md re-snapshot": exec_md.exists(),
        "stuck run released to aborted": status == "aborted",
    }
    for name, cond in checks.items():
        assert cond, f"FAIL: {name}"
        print(f"  ok  {name}")
    # cleanup: item folder, branch, inbox row, state file.
    shutil.rmtree(KHOME / "work-items" / iid, ignore_errors=True)
    subprocess.run(["git", "branch", "-Dq", st["branch"]], cwd=REPO, check=False)
    subprocess.run(["git", "worktree", "prune"], cwd=REPO, check=False)
    try:
        http("DELETE", f"/dev/inbox/{st['inbox_id']}")
    except Exception:
        pass
    STATE.unlink(missing_ok=True)
    print(f"\nALL GREEN — {len(checks)} reconcile checks passed (residue purged).")


if __name__ == "__main__":
    {"setup": setup, "verify": verify}[sys.argv[1]]()
