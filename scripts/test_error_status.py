"""`error` — the item whose work STOPPED (recovery-resilience R2).

Before this, a run that died left its item in one of two lies. `awaiting_human` claimed a decision
was wanted — the owner would open the gate and find no report and no explanation. `active` claimed
something was working — the board read IN PROGRESS with a frozen timer, which is how an outage
looked for hours during the 2026-07-30 E2E. Neither said "this stopped".

`error` is that third answer, and it is deliberately NOT the run-level `system_fault` (owner,
2026-07-31): a system fault is a run that COMPLETED while our machinery misbehaved — review's
business, the work advanced — while `error` is a run that stopped, so the item stays where it died.
Never terminal: it is the entry point for Resume (R4) and re-run (R5).

This suite pins: the status is in the vocabulary and outranks every other attention claim, the
reason is stored and cleared honestly, the loop stops rather than advancing past work that never
happened, every item runner writes it through the one writer, and the FE renders it red.

Self-cleaning (tempdir work-items). No daemon needed.

Run: PYTHONPATH=. python -m scripts.test_error_status
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from superme_agent.core import attention
from superme_agent.core.dev_knowledge import DevKnowledgeService, _LIVE_STATUSES, _STATUS_RANK
from superme_agent.daemon.schemas.common import WorkStatus
from superme_agent.daemon.services.loop import decide_after_vet, decide_after_build

PASS = 0
ROOT = Path(__file__).resolve().parents[1]


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def src(rel: str) -> str:
    return (ROOT / rel).read_text()


def _item(dev_root: Path, item_id: str = "e1", status: str = "active") -> Path:
    d = dev_root / "work-items" / item_id
    d.mkdir(parents=True)
    (d / "item.md").write_text(
        f"---\nid: {item_id}\ntitle: Fixture\nphase: build\nstatus: {status}\n"
        f"updated_at: 2026-01-01\n---\n\nbody\n")
    return d


# ── the vocabulary ──────────────────────────────────────────────────────────────────────────────

def test_status_is_first_class() -> None:
    print("\n[vocabulary] error is a status, and a LIVE one")

    ok("`error` is in the wire enum", "error" in WorkStatus.__args__)
    ok("…and is NOT terminal — it is work waiting to resume", "error" != "done")
    ok("an errored item still reads as live (it stays on the board)",
       "error" in _LIVE_STATUSES)
    ok("…and sorts above awaiting_human: stopped is louder than parked",
       _STATUS_RANK["error"] < _STATUS_RANK["awaiting_human"])
    ok("…which still sorts above ordinary active work",
       _STATUS_RANK["awaiting_human"] < _STATUS_RANK["active"])


def test_reason_is_stored_and_cleared(dev_root: Path) -> None:
    print("\n[reason] written where it stopped, gone the moment it isn't stopped")

    dev = DevKnowledgeService()
    d = _item(dev_root)
    ok("marking error writes the status",
       dev.set_work_item_error(dev_root, "e1", "upstream was unavailable — API Error: 529"))
    it = dev.read_work_item(dev_root, "e1") or {}
    ok("…the status is error", str(it.get("status")) == "error")
    ok("…carrying the reason verbatim", "529" in str(it.get("error_reason")))
    ok("…and the phase is untouched — it stopped where it was",
       str(it.get("phase")) == "build")

    # A multi-line or colon-bearing reason must not corrupt line-parsed frontmatter.
    dev.set_work_item_error(dev_root, "e1", "line one:\nline two: with colons")
    it = dev.read_work_item(dev_root, "e1") or {}
    ok("a multi-line reason is flattened, not written raw into the frontmatter",
       "\n" not in str(it.get("error_reason") or ""))
    ok("…and the file still parses", str(it.get("status")) == "error" and it.get("title") == "Fixture")

    # Leaving error clears the reason — a stale one would make a resumed item read broken forever.
    dev.set_work_item_status(dev_root, "e1", "active")
    it = dev.read_work_item(dev_root, "e1") or {}
    ok("leaving error clears the status", str(it.get("status")) == "active")
    ok("…and takes the reason with it", not it.get("error_reason"))

    # Terminal is final on this axis, same as every other status write.
    d2 = _item(dev_root, "e2")
    (d2 / "item.md").write_text(
        "---\nid: e2\ntitle: Done\nphase: close\nstatus: done\ndone_at: 2026-01-01\n"
        "updated_at: 2026-01-01\n---\n\nbody\n")
    ok("a terminal item cannot be dragged back into error",
       not dev.set_work_item_error(dev_root, "e2", "too late"))


# ── the attention engine ────────────────────────────────────────────────────────────────────────

def test_error_outranks_every_other_claim() -> None:
    print("\n[attention] one writer, and error is the top tier")

    ok("error leads the tier order", attention.TIER_ORDER[0] == "error")
    ok("…and it is red", attention.TIER_COLOR["error"] == "red")

    stopped = {"id": "a", "title": "A", "phase": "build", "status": "error",
               "error_reason": "upstream was unavailable"}
    parked = {"id": "b", "title": "B", "phase": "review", "status": "awaiting_human"}
    out = attention.assign([stopped, parked], running_ids=set())
    ok("a stopped item lands in `error`, not `needs_you`",
       [r["id"] for r in out["buckets"]["error"]] == ["a"])
    ok("…and the parked one is untouched",
       [r["id"] for r in out["buckets"]["needs_you"]] == ["b"])
    ok("the badge shows error over needs_you", out["badge"]["tier"] == "error")
    ok("…in red", out["badge"]["color"] == "red")

    reason = out["buckets"]["error"][0]["reason"]
    ok("the row's reason says the work stopped", "stopped" in reason)
    ok("…names where", "build" in reason)
    ok("…and quotes the STORED cause rather than guessing one",
       "upstream was unavailable" in reason)

    # An item with no stored reason still reads honestly rather than inventing a cause.
    bare = attention.assign([{"id": "c", "phase": "vet", "status": "error"}], set())
    ok("a missing reason degrades to the plain fact, not a fabricated one",
       bare["buckets"]["error"][0]["reason"] == "the work stopped during vet")

    # A live run must not out-tier a stopped item (an errored item has no live run by definition,
    # but the ordering is what guarantees it can never be masked).
    masked = attention.assign([stopped], running_ids={"a"})
    ok("even a stray running row cannot mask an errored item",
       masked["badge"]["tier"] == "error")

    # Terminal wins: a done item is never error, whatever its status field once said.
    done = attention.assign([{"id": "d", "status": "error", "done_at": "2026-01-01"}], set())
    ok("a terminal item is never bucketed as error", not done["buckets"]["error"])


# ── the loop ────────────────────────────────────────────────────────────────────────────────────

def test_loop_stops_instead_of_advancing() -> None:
    print("\n[loop] a run that stopped must not advance to a gate")

    live = {"status": "active", "phase": "vet"}
    d = decide_after_vet(live, evidence={"status": "passed"}, fingerprint="", attempts=[],
                         spent=0, budget=100, turn_error=True,
                         fault_reason="upstream was unavailable — API Error: 529")
    ok("a stopped vet run ends at `error`", d["action"] == "error" and d["status"] == "error")
    ok("…typed as such, not as system_fault", d["exit"] == "error")
    ok("…carrying R1's own classification verbatim", "529" in d["reason"])

    # R1's ladder already retried this turn up to seven times — a second ladder here would only
    # multiply the wait, so the old immediate re-vet is gone for THIS cause.
    ok("no second retry ladder for a stopped turn",
       decide_after_vet(live, evidence={"status": "passed"}, fingerprint="", attempts=[],
                        spent=0, budget=100, turn_error=True, faults=0)["action"] == "error")

    # …but the OTHER fault — a run that finished and recorded nothing — keeps its retry, because
    # re-running vet IS the cure for a lost ledger, and it is genuinely `system_fault`.
    d2 = decide_after_vet(live, evidence={"status": "unverified"}, fingerprint="", attempts=[],
                          spent=0, budget=100, turn_error=False, faults=0)
    ok("an empty ledger still retries", d2["action"] == "revet")
    d3 = decide_after_vet(live, evidence={"status": "unverified"}, fingerprint="", attempts=[],
                          spent=0, budget=100, turn_error=False, faults=9)
    ok("…and after its retries is still system_fault → review, NOT error",
       d3["exit"] == "system_fault" and d3["action"] == "review")

    # Normal verdicts are untouched by any of this.
    ok("a green cycle still advances to review",
       decide_after_vet(live, evidence={"status": "passed"}, fingerprint="", attempts=[],
                        spent=0, budget=100)["exit"] == "converged")

    ok("a stopped build turn is still classified infra",
       decide_after_build({"status": "active", "phase": "build"},
                          outcome=None, turn_error=True)["klass"] == "infra")

    loop = src("superme_agent/daemon/services/loop.py")
    ok("the build's infra path now stops the item instead of advancing it to review",
       'mark_item_error(ctx, context_id, item_id, reason, phase="build")' in loop)
    ok("…and no longer CAS-flips build → review on a crashed turn",
       '"build", "review"' not in loop)


# ── the runners ─────────────────────────────────────────────────────────────────────────────────

def test_every_item_runner_labels_its_stop() -> None:
    print("\n[runners] one writer, called wherever a run can die")

    runs = src("superme_agent/daemon/services/runs.py")
    ok("there is exactly one writer of the status", runs.count("def mark_item_error(") == 1)
    ok("…which goes through the dev-knowledge writer",
       "_dev.set_work_item_error(" in runs)
    ok("…and leaves a run.error event", '"run.error"' in runs)
    ok("…best-effort: labelling a failure must not itself raise",
       "could not mark %s as error" in runs)

    # Every runner that owns an ITEM. (distill/write/sweep own proposals, not items — they mark the
    # RUN aborted, which they already did; there is no item to stop.)
    ok("background intake stops rather than paging an empty gate",
       'mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase=skill)' in runs)
    ok("the deputy send-back re-run stops the same way",
       'mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase=phase)' in runs)
    ok("auto-close stops rather than burning its clearance retries on an outage",
       'phase="close")' in runs and "if not stopped:" in runs)
    ok("the conflict resolver distinguishes 'the conflict beat me' from 'I never ran'",
       'phase="resolve")' in runs)

    loop = src("superme_agent/daemon/services/loop.py")
    ok("vet uses the shared writer too", 'mark_item_error(ctx, context_id, item_id, d["reason"]' in loop)
    ok("…imported, not redefined", "def mark_item_error" not in loop)


# ── the surface ─────────────────────────────────────────────────────────────────────────────────

def test_frontend_reads_the_one_verdict() -> None:
    print("\n[surface] red edge, Error stat, and no second derivation")

    common = src("web/frontend/src/features/dev/common.tsx")
    ok("the card's left edge goes red", "error: 'border-l-danger'" in common)
    ok("the status text goes red", "error: 'text-danger'" in common)
    ok("…and reads in the owner's words, not the schema's", "error: 'stopped'" in common)
    ok("the display status READS the tier rather than re-deriving it",
       "if (bucket === 'error') return 'error'" in common)

    panels = src("web/frontend/src/features/dev/panels.tsx")
    ok("the card ring goes red", "ring-1 ring-danger/80" in panels)

    dash = src("web/frontend/src/features/dev/DevDashboard.tsx")
    ok("the attention strip has an Error row", "label: 'Error'" in dash)
    ok("…dotted red", "dot: 'bg-danger'" in dash)
    ok("both tier lists include it (the map and the strip)",
       dash.count("'error', 'needs_you', 'deputy_working', 'running', 'unread'") == 2)

    graph = src("web/frontend/src/features/dev/WorkGraphView.tsx")
    ok("the graph node rings red too", "ring-2 ring-danger/90" in graph)

    ws = src("web/frontend/src/features/dev/DevWorkspace.tsx")
    ok("the repo badge goes red at the top tier", "'bg-danger'" in ws)

    schema = src("web/frontend/src/lib/api/generated/schema.ts")
    ok("the generated wire type carries the status", '"awaiting_human" | "error"' in schema)
    ok("…and the reason field", "error_reason" in schema)


def main() -> None:
    test_status_is_first_class()
    with TemporaryDirectory() as td:
        test_reason_is_stored_and_cleared(Path(td))
    test_error_outranks_every_other_claim()
    test_loop_stops_instead_of_advancing()
    test_every_item_runner_labels_its_stop()
    test_frontend_reads_the_one_verdict()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
