"""Does the token surface add up?

Each breakdown slices the same money, so each must re-sum to the same total. When one does not,
the dashboard is confidently wrong rather than visibly broken. Read-only.

    PYTHONPATH=. python -m scripts.test_token_coherence
"""

import sqlite3
import sys

from superme_agent.core.spine import SystemSpine
from superme_agent.core.token_taxonomy import CATEGORY_ORDER, FEATURE_CATEGORY, display_feature
from superme_agent.paths import SYSTEM_DB_FILE

fails: list[str] = []
warns: list[str] = []


def eq(name: str, got: int, want: int) -> None:
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"{'  ok ' if ok else 'FAIL '} {name:<52} {got:>15,} vs {want:>15,}")


def section(title: str) -> None:
    print(f"\n── {title}")


def main() -> int:
    sp = SystemSpine()
    d = sp.token_usage()
    g, by_repo, arch = d["global"], d["by_repo"], d["archived"]

    with sqlite3.connect(SYSTEM_DB_FILE) as c:
        c.row_factory = sqlite3.Row
        raw = c.execute(
            "SELECT COALESCE(SUM(tok_input),0) i, COALESCE(SUM(tok_cache_creation),0) cc,"
            " COALESCE(SUM(tok_cache_read),0) cr, COALESCE(SUM(tok_output),0) o, COUNT(*) n FROM run"
        ).fetchone()
        # Rows the day axis cannot place carry no tokens, so the money reconciles — but the
        # trend's run count is short by this many.
        odd = c.execute(
            "SELECT id, feature, status, started_at,"
            " tok_input+tok_cache_creation+tok_cache_read+tok_output AS tok"
            " FROM run WHERE date(started_at,'+0 minutes') IS NULL"
        ).fetchall()
        feats = [r[0] for r in c.execute("SELECT DISTINCT feature FROM run").fetchall()]

    section("the headline is the table's own 3-type sum")
    eq("global.total", g["total"], raw["i"] + raw["cc"] + raw["o"])
    for key, col in (("input", "i"), ("cache_creation", "cc"), ("cache_read", "cr"), ("output", "o")):
        eq(f"by_type.{key}", g["by_type"][key], raw[col])

    section("every breakdown re-sums to that headline")
    eq("SUM(by_feature)", sum(g["by_feature"].values()), g["total"])
    eq("SUM(by_scope)", sum(g["by_scope"].values()), g["total"])
    eq("SUM(by_category.total)", sum(c["total"] for c in g["by_category"].values()), g["total"])
    eq("SUM(by_category features)",
       sum(sum(c["features"].values()) for c in g["by_category"].values()), g["total"])
    eq("SUM(by_repo.total)", sum(r["total"] for r in by_repo.values()), g["total"])

    section("each repo's own breakdowns re-sum to its own total")
    for rid, r in sorted(by_repo.items()):
        eq(f"{rid} · by_feature", sum(r["by_feature"].values()), r["total"])
        eq(f"{rid} · by_category", sum(c["total"] for c in r["by_category"].values()), r["total"])
        eq(f"{rid} · by_scope", sum(r["by_scope"].values()), r["total"])
        eq(f"{rid} · by_type 3-type",
           r["by_type"]["input"] + r["by_type"]["cache_creation"] + r["by_type"]["output"], r["total"])

    section("the Per-repo tab's bars reach the headline")
    live = set(sp.repos())
    eq("live repos + Old projects",
       sum(r["total"] for rid, r in by_repo.items() if rid in live) + arch["total"], g["total"])
    eq("Old projects == SUM(its members)", sum(m["total"] for m in arch["repos"]), arch["total"])
    eq("no live repo counted as old", len([m for m in arch["repos"] if m["id"] in live]), 0)

    section("4-type mode (cache read on)")
    full = g["total"] + g["by_type"]["cache_read"]
    eq("SUM(by_feature + its cache_read)",
       sum(g["by_feature"].values()) + sum(g["by_feature_cache_read"].values()), full)
    eq("SUM(by_repo by_type)", sum(sum(r["by_type"].values()) for r in by_repo.values()), full)
    eq("cache_read names ⊆ feature names",
       len(set(g["by_feature_cache_read"]) - set(g["by_feature"])), 0)

    section("Over time plots the same money")
    days = sp.token_timeseries(0)["days"]
    for key in ("input", "cache_creation", "cache_read", "output"):
        eq(f"SUM(days.{key})", sum(x[key] for x in days), g["by_type"][key])
    eq("SUM(days.runs)", sum(x["runs"] for x in days), raw["n"] - len(odd))

    section("nothing is unnamed")
    unreg = sorted({f for f in feats if display_feature(f) not in FEATURE_CATEGORY})
    eq("run features with no category", len(unreg), 0)
    if unreg:
        print(f"       unregistered: {unreg}")
    eq("categories outside CATEGORY_ORDER",
       len([k for k in g["by_category"] if k not in CATEGORY_ORDER]), 0)

    if odd:
        warns.append(f"{len(odd)} run(s) with an unparseable started_at")
        print(f"\n── warning: {len(odd)} run(s) carry a started_at the day axis cannot read.")
        print("   They hold no tokens, so every figure above still reconciles; only the trend's run")
        print("   count omits them. Both writers of the column emit ISO-8601, so these are rows from")
        print("   an older path — repair the value, don't drop the row.")
        for r in odd:
            print(f"   run #{r['id']}  {r['feature']}/{r['status']}  started_at={r['started_at']!r}"
                  f"  tokens={r['tok']}")

    print()
    if fails:
        print(f"✗ {len(fails)} INCOHERENT: {fails}")
        return 1
    print("✓ COHERENT — every breakdown re-sums to the same total"
          + (f"  ({len(warns)} warning: {warns[0]})" if warns else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
