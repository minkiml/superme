"""Clear the Manage-Harness learning data and re-seed a realistic CANDIDATE pool.

Run with --reset to wipe (for context `global` only): candidates + proposals + learning events
(distill/write/sweep) — leaving inbox/work-item data and the `dummy` context untouched. Then it
seeds a fresh, practical pool designed to exercise distill→forge end-to-end.

The pool is built so a correct distill run yields **one of each form** plus one flagged mis-capture:

  • SKILL  (repo_dev)        — a 3-candidate cluster describing ONE ordered multi-step procedure
                               (shipping a dev-store schema change). Recurrence = the need signal.
  • AGENT  (universal_dev)   — a 2-candidate pair describing an isolated, read-only worker that
                               returns a report (test-failure triage). Isolation ⇒ agent, not skill.
  • CONSTITUTION (universal) — a 2-candidate pair stating one standing rule (model IDs = aliases).
  • MIS-CAPTURE (repo_dev)   — a static reference fact (the port numbers). No behaviour ⇒ distill
                               should leave it un-proposed and flag it.

Usage:
    PYTHONPATH=. python scripts/seed_distill_test.py --reset [--context global]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlite3  # noqa: E402

from superme_agent.core.dev_store import DevStore  # noqa: E402
from superme_agent.runtime.config import DEV_DB_FILE, SYSTEM_DB_FILE  # noqa: E402

# Learning leaves three residue surfaces; --reset purges all three for the target context so a
# restart-from-distill is pristine. We deliberately DO NOT touch: inbox.*/item.* events, the live
# cockpit session row, or any other context (e.g. `dummy`).
_LEARN_EVENT_KINDS = ("distill", "write", "sweep", "forge", "memory")
_LEARN_RUN_FEATURES = ("distill", "write", "capture", "sweep")

# Each candidate is a RICH row: the operational signal, why it matters (rationale), and a concrete
# pointer (evidence). form_hint is left unset on purpose — distill must infer the form from the
# consolidated substance, which is the real test.
CANDIDATES = [
    # ── SKILL cluster: one ordered procedure, captured three times from different moments ──
    dict(
        signal="Shipping a dev-store schema change is a fixed procedure: edit the CREATE TABLE in "
               "dev_store.py, update the matching _row_* decoder, bump the DATA SCHEMAS entry in "
               "model.yaml, restart the daemon, then hit the endpoint to confirm the new field flows.",
        rationale="A repeatable multi-step recipe — skipping any step leaves the change half-wired.",
        scope_hint="repo_dev",
        evidence="done this for the inbox, proposal, and candidate tables while building WI-8",
    ),
    dict(
        signal="A schema edit in dev_store.py silently no-ops on an existing .dev.db unless the CREATE "
               "handles the migration, AND the _row_* decoder must be updated or the new column never "
               "reaches the API.",
        rationale="Two recurring gotchas in the same schema-change workflow.",
        scope_hint="repo_dev",
        evidence="new eval_report column didn't surface until the decoder was patched",
    ),
    dict(
        signal="After changing a dev-store model, update model.yaml's DATA SCHEMAS table and bounce the "
               "daemon, otherwise the cockpit keeps showing the old field set.",
        rationale="The doc-sync + restart tail of the schema-change procedure.",
        scope_hint="repo_dev",
        evidence="cockpit showed stale columns until the daemon was restarted",
    ),

    # ── AGENT cluster: an isolated, read-only worker that returns a report ──
    dict(
        signal="A failing test run is best handed to an isolated worker: it runs the test command, "
               "reads the traceback, traces the failure to the offending source line, and returns a "
               "ranked root-cause report — too noisy to do in the main context.",
        rationale="Heavy, multi-step diagnosis that warrants its own context window.",
        scope_hint="universal_dev",
        evidence="manual test triage kept flooding the main conversation with stack frames",
    ),
    dict(
        signal="When tests fail, delegate the diagnosis to a separate read-only agent that greps the "
               "stack frames, opens the implicated files, and reports the most likely cause as "
               "file:line — keeping the main context clean.",
        rationale="Same triage job, framed as a delegation that returns only its conclusion.",
        scope_hint="universal_dev",
        evidence="works well as a Task() that hands back just the verdict",
    ),

    # ── CONSTITUTION cluster: one standing rule, stated twice ──
    dict(
        signal="Never pin a full model ID (like claude-sonnet-4-5) in a learned agent or config — "
               "pinned IDs go stale or were never valid; always reference models by alias: sonnet, "
               "opus, haiku, or inherit.",
        rationale="A guardrail that prevents a whole class of stale/invalid-model failures.",
        scope_hint="universal_dev",
        evidence="forge once emitted an invalid claude-sonnet-4-5 model field",
    ),
    dict(
        signal="We keep hitting invalid or stale model strings; the rule is to reference models only "
               "by alias (sonnet/opus/haiku/inherit), never a pinned version ID.",
        rationale="Recurrence of the same model-aliasing rule.",
        scope_hint="universal_dev",
        evidence="lint now blocks non-alias model fields for this reason",
    ),

    # ── MIS-CAPTURE: a static reference fact dressed as a learning ──
    dict(
        signal="The dev daemon serves on port 8787, the BFF on 8000, and the Vite frontend on 5173.",
        rationale="A fixed lookup fact — knowing it changes nothing about how SuperMe behaves.",
        scope_hint="repo_dev",
        evidence="reference only; not operational",
    ),
]


def _reset(store: DevStore, context: str) -> None:
    with store._conn() as c:
        n_cand = c.execute("DELETE FROM memory_candidate WHERE context_id=?", (context,)).rowcount
        n_prop = c.execute("DELETE FROM memory_proposal WHERE context_id=?", (context,)).rowcount
        # learning events only — never touch inbox.* / item.* events
        ev_clause = " OR ".join("kind LIKE ?" for _ in _LEARN_EVENT_KINDS)
        n_evt = c.execute(
            f"DELETE FROM events WHERE context_id=? AND ({ev_clause})",
            (context, *(f"{k}%" for k in _LEARN_EVENT_KINDS))).rowcount
    # spine: the disposable (sessionless) learning run rows for this context — preserve the live
    # session row and every other context's runs.
    sp = sqlite3.connect(SYSTEM_DB_FILE)
    try:
        run_clause = ",".join("?" for _ in _LEARN_RUN_FEATURES)
        n_run = sp.execute(
            f"DELETE FROM run WHERE repo_id=? AND feature IN ({run_clause})",
            (context, *_LEARN_RUN_FEATURES)).rowcount
        sp.commit()
    finally:
        sp.close()
    print(f"reset[{context}]: -{n_cand} candidate(s), -{n_prop} proposal(s), "
          f"-{n_evt} learning event(s), -{n_run} spine learning run(s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", default="global")
    ap.add_argument("--reset", action="store_true",
                    help="wipe candidates + proposals + learning events for this context first")
    args = ap.parse_args()

    store = DevStore(DEV_DB_FILE)
    if args.reset:
        _reset(store, args.context)

    for spec in CANDIDATES:
        row = store.add_memory_candidate(
            args.context, spec["signal"], source="agent",
            rationale=spec.get("rationale"), scope_hint=spec["scope_hint"],
            evidence=spec.get("evidence"))
        print(f"  + candidate #{row['id']} [{row['scope_hint']}] {row['signal'][:60]}…")

    pool = store.list_memory_candidates(args.context, status="candidate")
    props = store.list_memory_proposals(args.context) if hasattr(store, "list_memory_proposals") else []
    print(f"\nseeded · {len(pool)} un-processed candidate(s), {len(props)} proposal(s) in "
          f"context={args.context!r} → {DEV_DB_FILE}")
    print("expect from a correct distill: 1 skill + 1 agent + 1 constitution proposal, "
          "ports fact flagged as mis-captured")
    print("next: Dev-mode chat → 'run distill' → review in Manage Harness → Learning")


if __name__ == "__main__":
    main()
