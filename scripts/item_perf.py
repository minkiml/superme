"""Per-item, per-phase MEASURED cost and shape. No estimates: every number is a recorded column
or a counted trace row. Subagent work is under-reported by the SDK, so fan-out phases read low.

Usage: PYTHONPATH=. python item_perf.py <item_id> [more ids...]
"""
import sys
from superme_agent.daemon.app_state import get_spine

s = get_spine()
COLS = ("id", "feature", "phase", "status", "outcome", "model", "tokens",
        "tok_input", "tok_cache_creation", "tok_cache_read", "tok_output",
        "started_at", "ended_at")


def secs(a, b):
    from datetime import datetime
    try:
        return int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())
    except Exception:
        return 0


for item in sys.argv[1:]:
    with s._conn() as c:
        rows = [dict(zip(COLS, r)) for r in c.execute(
            f"SELECT {','.join(COLS)} FROM run WHERE item_id=? ORDER BY id", (item,))]
    if not rows:
        print(f"\n{item}: no runs"); continue
    print(f"\n=== {item} ===")
    print(f"{'run':>5} {'phase':<10} {'feature':<18} {'out':<9} {'crit tok':>9} "
          f"{'cache rd':>9} {'steps':>6} {'sec':>5}")
    tot = crit = 0
    for r in rows:
        with s._conn() as c:
            steps = c.execute("SELECT COUNT(*) FROM run_event WHERE run_id=?", (r["id"],)).fetchone()[0]
        d = secs(r["started_at"], r["ended_at"]) if r["ended_at"] else 0
        cr = (r["tok_input"] or 0) + (r["tok_cache_creation"] or 0) + (r["tok_output"] or 0)
        crit += cr; tot += d
        print(f"{r['id']:>5} {str(r['phase'] or '-'):<10} {str(r['feature'] or '-'):<18} "
              f"{str(r['outcome'] or '-'):<9} {cr:>9,} {(r['tok_cache_read'] or 0):>9,} "
              f"{steps:>6} {d:>5}")
    print(f"{'':>5} {'TOTAL':<10} {'':<18} {'':<9} {crit:>9,} {'':>9} {'':>6} {tot:>5}")
