"""Turn cost per tool call: does a phase batch its independent calls, or pay a turn for each?

A run's bill is turns x a ~34k context floor (general_docs/item-cost-audit.md), so turns are the
only thing worth counting. Turn boundaries CANNOT be read off `run_event` adjacency: the harness
emits tool/result pairs interleaved even for calls that shared one assistant message, so counting
"tool events with no result between them" undercounts batching badly.

What is exact is the recorded context. `(cache_creation + cache_read) / tool_calls` is the context
a run paid per call; divide by the measured floor and you have turns per call. Below 1.0 means
calls are sharing turns.

    PYTHONPATH=. python scripts/batch_rate.py <item_id> [more ids...]
"""
import sys

from superme_agent.daemon.app_state import get_spine

# scripts/probe_context_floor.py measures the fixed part; the rest is our per-phase payload.
FLOOR = 34_088


def main() -> None:
    s = get_spine()
    print(f"{'run':>5} {'phase':<8} {'feature':<9} {'ctx tok':>10} {'calls':>6} "
          f"{'ctx/call':>9} {'turns/call':>11}")
    for item in sys.argv[1:]:
        print(f"--- {item} ---")
        tot_ctx = tot_calls = 0
        with s._conn() as c:
            rows = c.execute(
                "SELECT id,phase,feature,tok_cache_creation,tok_cache_read FROM run "
                "WHERE item_id=? ORDER BY id", (item,)).fetchall()
            for rid, phase, feature, cw, cr in rows:
                calls = c.execute(
                    "SELECT COUNT(*) FROM run_event WHERE run_id=? AND kind IN ('tool','mcp')",
                    (rid,)).fetchone()[0]
                if not calls:
                    continue
                ctx = (cw or 0) + (cr or 0)
                tot_ctx += ctx
                tot_calls += calls
                print(f"{rid:>5} {str(phase):<8} {str(feature):<9} {ctx:>10,} {calls:>6} "
                      f"{ctx / calls:>9,.0f} {ctx / calls / FLOOR:>11.2f}")
        if tot_calls:
            print(f"{'':>5} {'TOTAL':<8} {'':<9} {tot_ctx:>10,} {tot_calls:>6} "
                  f"{tot_ctx / tot_calls:>9,.0f} {tot_ctx / tot_calls / FLOOR:>11.2f}")


if __name__ == "__main__":
    main()
