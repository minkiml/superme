"""Token accounting: what one run spent, and what a repo or a day spent in total."""

from datetime import date, timedelta

from .common import _UNRECONCILED_STATUSES, log


class TokenOps:
    @staticmethod
    def _usage_parts(usage: dict | None) -> tuple[int, int, int, int]:
        """Split a raw SDK usage dict into (input, cache_creation, cache_read, output) ints."""
        u = usage or {}
        return (
            int(u.get("input_tokens", 0) or 0),
            int(u.get("cache_creation_input_tokens", 0) or 0),
            int(u.get("cache_read_input_tokens", 0) or 0),
            int(u.get("output_tokens", 0) or 0),
        )

    @staticmethod
    def _legacy_tokens(i: int, cc: int, cr: int, o: int) -> int:
        """The back-compat `tokens` scalar: input + output + cache_creation (EXCLUDES cache_read),
        matching agent_service._sum_tokens so existing telemetry/guard readers are unchanged."""
        return i + o + cc

    @staticmethod
    def _display_tokens(row) -> int:
        """The per-run token amount for DISPLAY: input + cache_creation + output, EXCLUDING
        cache_read.

        Only a PRE-MIGRATION row falls back to the legacy scalar. A run that never returned a final usage
        carries a live estimate, not a measurement."""
        typed = ((row["tok_input"] or 0) + (row["tok_cache_creation"] or 0)
                 + (row["tok_output"] or 0))
        if typed > 0:
            return typed
        try:
            status = row["status"]
        except (IndexError, KeyError):
            status = None
        return 0 if status in _UNRECONCILED_STATUSES else (row["tokens"] or 0)

    def _run_dict(self, r) -> dict:
        """Row → dict for a `run` row, with `tokens` overridden to the 3-type display amount."""
        d = dict(r)
        d["tokens"] = self._display_tokens(r)
        return d

    def token_usage(self) -> dict:
        """System-wide token aggregation. Every token is attributable along TWO axes that
        reconcile by construction: `by_category` and `by_type`.

        A row with four zero columns contributes NOTHING — it never returned a final usage."""
        from ..token_taxonomy import (
            category_for, display_feature, CATEGORY_ORDER, CATEGORY_LABELS, COLLAPSED_CATEGORIES,
        )
        with self._conn() as c:
            rows = c.execute(
                "SELECT repo_id, mode, feature, COUNT(*) AS n,"
                " COALESCE(SUM(tok_input),0) AS ti, COALESCE(SUM(tok_cache_creation),0) AS tcc,"
                " COALESCE(SUM(tok_cache_read),0) AS tcr, COALESCE(SUM(tok_output),0) AS to_"
                " FROM run GROUP BY repo_id, mode, feature"
            ).fetchall()

        def _blank_type() -> dict:
            return {"input": 0, "cache_creation": 0, "cache_read": 0, "output": 0}

        def _add_type(d: dict, r) -> int:
            # `by_type` keeps all four components; the RETURNED accounted amount is 3-type, which
            # is what every by_* bucket sums.
            d["input"] += r["ti"]; d["cache_creation"] += r["tcc"]
            d["cache_read"] += r["tcr"]; d["output"] += r["to_"]
            return r["ti"] + r["tcc"] + r["to_"]

        total = 0
        by_scope: dict[str, int] = {}
        by_feature: dict[str, int] = {}
        by_feature_cr: dict[str, int] = {}   # so "By operation" can render the 4th type
        by_type = _blank_type()
        by_category: dict[str, dict] = {}
        by_repo: dict[str, dict] = {}
        for r in rows:
            amt = _add_type(by_type, r)
            total += amt
            # Retired feature names report under the live name that absorbed them; the DB row
            # keeps its own spelling.
            feat = display_feature(r["feature"])
            by_scope[r["mode"]] = by_scope.get(r["mode"], 0) + amt
            by_feature[feat] = by_feature.get(feat, 0) + amt
            by_feature_cr[feat] = by_feature_cr.get(feat, 0) + r["tcr"]
            cat = category_for(feat)
            cnode = by_category.setdefault(cat, {"total": 0, "features": {}})
            cnode["total"] += amt
            cnode["features"][feat] = cnode["features"].get(feat, 0) + amt
            pr = by_repo.setdefault(r["repo_id"], {
                "total": 0, "runs": 0, "by_scope": {}, "by_feature": {},
                "by_type": _blank_type(), "by_category": {},
            })
            _add_type(pr["by_type"], r)
            pr["total"] += amt
            pr["runs"] += r["n"] or 0
            pr["by_scope"][r["mode"]] = pr["by_scope"].get(r["mode"], 0) + amt
            pr["by_feature"][feat] = pr["by_feature"].get(feat, 0) + amt
            pcat = pr["by_category"].setdefault(cat, {"total": 0, "features": {}})
            pcat["total"] += amt
            pcat["features"][feat] = pcat["features"].get(feat, 0) + amt

        # Each node carries its display name and whether to draw ONE bar — taxonomy decisions
        # travel WITH the tree.
        def _order(tree: dict) -> dict:
            out = {}
            for k in CATEGORY_ORDER:
                if k not in tree:
                    continue
                out[k] = {**tree[k], "label": CATEGORY_LABELS.get(k, k),
                          "collapsed": k in COLLAPSED_CATEGORIES}
            return out

        by_category = _order(by_category)
        for pr in by_repo.values():
            pr["by_category"] = _order(pr["by_category"])

        # "Old projects" — spend whose repo left. Its runs stay in `total`, so without this bucket
        # they would be counted-but-unnameable.
        live, tombs = self.repos(), self.archived_repos()
        members = [
            {"id": rid, "label": (tombs.get(rid) or {}).get("label") or rid,
             "total": pr["total"], "runs": pr["runs"],
             "disconnected_at": (tombs.get(rid) or {}).get("disconnected_at")}
            for rid, pr in by_repo.items() if rid not in live
        ]
        members.sort(key=lambda m: m["total"], reverse=True)
        archived = {"total": sum(m["total"] for m in members),
                    "runs": sum(m["runs"] for m in members), "repos": members}
        return {
            "global": {
                "total": total,
                "by_scope": by_scope,
                "by_feature": by_feature,
                "by_feature_cache_read": by_feature_cr,
                "by_type": by_type,
                "by_category": by_category,
            },
            "by_repo": by_repo,
            "archived": archived,
        }

    def token_timeseries(self, tz_offset: int = 0) -> dict:
        """Per-day token usage for the trend graph, bucketed by LOCAL day. Derived on the
        fly — no materialized rollup."""
        modifier = f"{int(tz_offset):+d} minutes"
        with self._conn() as c:
            rows = c.execute(
                "SELECT date(started_at, ?) AS day, COUNT(*) AS n,"
                " COALESCE(SUM(tok_input),0) AS ti, COALESCE(SUM(tok_cache_creation),0) AS tcc,"
                " COALESCE(SUM(tok_cache_read),0) AS tcr, COALESCE(SUM(tok_output),0) AS to_"
                " FROM run WHERE started_at IS NOT NULL GROUP BY day ORDER BY day",
                (modifier,),
            ).fetchall()
        # A row with an unparseable `started_at` would vanish from the axis without a word. Say so
        # instead.
        by_day = {r["day"]: r for r in rows if r["day"]}
        if (lost := sum(r["n"] for r in rows if not r["day"])):
            log.warning("token_timeseries: %d run(s) have an unparseable started_at and are absent "
                        "from the day axis", lost)
        days: list[dict] = []
        cumulative = 0
        if by_day:
            # A CONTIGUOUS day axis: gaps become zero-days, so bars cannot mis-space and the date
            # axis cannot lie.
            start, end = date.fromisoformat(min(by_day)), date.fromisoformat(max(by_day))
            d = start
            while d <= end:
                key = d.isoformat()
                r = by_day.get(key)
                ti, tcc, tcr, to_, n = (
                    (r["ti"], r["tcc"], r["tcr"], r["to_"], r["n"] or 0) if r else (0, 0, 0, 0, 0)
                )
                # `total` is 3-type; cache_read rides its own field. Four zero columns contribute
                # NOTHING — unmeasured is not an estimate.
                total = ti + tcc + to_
                cumulative += total
                days.append({
                    "day": key,
                    "input": ti, "cache_creation": tcc, "cache_read": tcr, "output": to_,
                    "total": total, "cumulative": cumulative, "runs": n,
                })
                d += timedelta(days=1)
        return {"days": days, "total": cumulative}

    def item_phase_tokens(self, repo_id: str, item_id: str,
                          phases: tuple[str, ...] = ("build", "vet")) -> int:
        """An item's 3-type spend over the given phases, live and finished.

        Rows with no typed usage FALL BACK to `tokens`, unlike `_display_tokens`: an aborted run's tokens
        were really spent. Discarded runs are excluded, so the breaker inherits no spent budget."""
        ph = ",".join("?" for _ in phases)
        with self._conn() as c:
            r = c.execute(
                f"SELECT COALESCE(SUM(CASE WHEN (tok_input+tok_cache_creation+tok_cache_read+tok_output)=0"
                f" THEN COALESCE(tokens,0) ELSE tok_input+tok_cache_creation+tok_output END),0) AS t"
                f" FROM run WHERE repo_id=? AND item_id=? AND discarded_at IS NULL"
                f" AND phase IN ({ph})",
                (repo_id, item_id, *phases),
            ).fetchone()
            return int(r["t"] or 0)
