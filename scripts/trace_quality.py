"""Prompt-quality signals read off a run's real trace. Each signal is a behaviour the prompt
was supposed to prevent, so a hit is a prompt defect rather than a model one.

Usage: PYTHONPATH=. python trace_quality.py <run_id> [...]
"""
import collections, sys
from superme_agent.daemon.app_state import get_spine

s = get_spine()


def events(run_id):
    with s._conn() as c:
        return [dict(zip(("seq", "kind", "name", "description"), r)) for r in c.execute(
            "SELECT seq,kind,name,description FROM run_event WHERE run_id=? ORDER BY seq", (run_id,))]


for rid in sys.argv[1:]:
    evs = events(rid)
    if not evs:
        print(f"\nrun {rid}: no trace"); continue
    with s._conn() as c:
        row = c.execute("SELECT feature,phase,tokens FROM run WHERE id=?", (rid,)).fetchone()
    calls = [e for e in evs if e["kind"] in ("tool", "mcp", "skill", "agent")]
    reads = [e for e in calls if e["name"] == "Read"]
    seen = collections.Counter(str(e["description"]) for e in reads)
    findings = []
    # The run protocol tells a background turn to read a SPAN. A whole-file read is the default
    # it was told not to take.
    whole = [d for d in seen if "[whole]" in d]
    if whole:
        findings.append(("whole-file reads", f"{len(whole)} of {len(reads)} Reads took the whole file"))
    if (rep := [d for d, n in seen.items() if n > 1]):
        findings.append(("re-read same file", f"{len(rep)} file(s) read more than once"))
    # A tool the turn already mounts should not need finding.
    if (ts := [e for e in calls if e["name"] == "ToolSearch"]):
        findings.append(("hunted for a tool", f"{len(ts)} ToolSearch call(s): "
                         + "; ".join(str(e["description"])[:50] for e in ts[:2])))
    denied = [e for e in evs if e["kind"] == "result"
              and any(k in str(e["description"]).lower()
                      for k in ("denied", "refused", "not available", "outside", "boundary"))]
    if denied:
        findings.append(("refusals hit", f"{len(denied)}: "
                         + str(denied[0]["description"])[:70]))
    print(f"\nrun {rid} — {row[0] if row else '?'}/{row[1] if row else '?'} "
          f"· {len(evs)} events · {len(calls)} calls · {row[2] if row else 0:,} tok")
    for label, detail in findings:
        print(f"    ! {label}: {detail}")
    if not findings:
        print("    clean")
