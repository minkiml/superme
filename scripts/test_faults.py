"""Fault classification and the retry ladder.

An overloaded upstream and a bad argument used to reach the same outcome: page the owner. An
upstream error handed back as assistant TEXT raised nothing, so a cycle that never ran scored
as a success.

Run: PYTHONPATH=. python -m scripts.test_faults
"""

import asyncio
from pathlib import Path

from superme_agent.core.events import Result, Status, TextDelta
from superme_agent.core.faults import (NO_FAULT, RETRY_LADDER, Fault, classify, next_delay)
from superme_agent.daemon.services.turns import ResilientTurn
from scripts.sources import src

PASS = 0


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


# ── the three kinds ─────────────────────────────────────────────────────────────────────────────

def test_kinds() -> None:
    print("\n[kinds] transient · rate_limited · fault, and the conservative default")

    ok("no exception and no reply is not a failure", classify() is NO_FAULT)
    ok("NO_FAULT does not read as failed", not NO_FAULT.failed)

    for status in (500, 502, 503, 504, 529):
        f = classify(reply=f"API Error: {status} Overloaded")
        ok(f"an API Error {status} reply is transient", f.kind == "transient")

    f = classify(reply="API Error: 429 rate_limit_error")
    ok("a 429 reply is rate_limited, not transient", f.kind == "rate_limited")

    f = classify(reply="API Error: 400 invalid_request_error: bad tool schema")
    ok("a 400 is OUR bug — fault, never retried", f.kind == "fault" and not f.retryable)

    ok("transient is retryable", classify(reply="API Error: 529").retryable)
    ok("rate_limited is retryable", classify(reply="API Error: 429").retryable)

    # The conservative default: unrecognized means fault. A mystery retried seven times is seven
    # identical mysteries and half an hour lost.
    f = classify(exc=ValueError("expected 3 items, got 7"))
    ok("an unrecognized crash defaults to fault, not transient", f.kind == "fault")
    ok("the reason names the actual failure, not the category",
       "expected 3 items" in f.reason)

    ok("a socket timeout is transient",
       classify(exc=TimeoutError("read timed out")).kind == "transient")
    ok("a dropped connection is transient",
       classify(exc=ConnectionResetError("Connection reset by peer")).kind == "transient")
    ok("a cancellation is not filed as our bug",
       classify(exc=asyncio.CancelledError()).kind == "transient")


def test_reply_path_is_narrow() -> None:
    print("\n[reply] an upstream error as TEXT is a failure — but only when nothing was done")

    ok("the SDK's error prefix at the START of a reply is caught",
       classify(reply="API Error: 529 Overloaded").failed)
    # An agent WRITING about an error is reporting, not failing, so quoting a log line is not a
    # fault.
    ok("an error mentioned mid-sentence is NOT a failure",
       not classify(reply="I looked into the API Error: 500 we saw earlier and fixed it").failed)
    ok("a turn that called a tool is never judged by its reply",
       not classify(reply="API Error: 529 Overloaded", did_work=True).failed)
    ok("did_work does not excuse an exception",
       classify(exc=TimeoutError("boom"), did_work=True).failed)


def test_retry_after_is_read() -> None:
    print("\n[retry-after] the upstream's own hint, when it gives one")

    ok("`retry-after: 90` is parsed",
       classify(reply="API Error: 429 rate limit; retry-after: 90").retry_after == 90)
    ok("`try again in 45 seconds` is parsed",
       classify(reply="API Error: 429 usage limit — try again in 45 seconds").retry_after == 45)
    ok("no hint leaves retry_after None",
       classify(reply="API Error: 529 Overloaded").retry_after is None)


# ── the ladder ──────────────────────────────────────────────────────────────────────────────────

def test_ladder_is_the_owners_schedule() -> None:
    print("\n[ladder] 1m · 3m · then 5m ×5, then stop (owner, 2026-07-31)")

    ok("the ladder is 1m, 3m, then five 5m waits",
       RETRY_LADDER == (60, 180, 300, 300, 300, 300, 300))
    ok("seven retries in all", len(RETRY_LADDER) == 7)
    ok("the first wait is one minute", next_delay(0) == 60)
    ok("the second is three", next_delay(1) == 180)
    ok("the rest are five", {next_delay(i) for i in range(2, 7)} == {300})
    ok("the ladder ends — no eighth try", next_delay(7) is None)
    ok("a negative attempt is refused, not wrapped", next_delay(-1) is None)

    t = classify(reply="API Error: 529 Overloaded")
    ok("a transient failure walks the ladder", [t.delay(i) for i in range(3)] == [60, 180, 300])
    ok("…and stops with it", t.delay(7) is None)

    # A fault never waits: our own bug does not heal on a timer.
    ok("a fault gets no delay at any attempt",
       classify(exc=ValueError("x")).delay(0) is None)


def test_rate_limit_waits_for_the_window() -> None:
    print("\n[rate limit] wait for the WINDOW, not the ladder's head")

    r = classify(reply="API Error: 429 rate limit")
    ok("a rate limit never retries in sixty seconds", r.delay(0) >= 300)
    ok("…the floor holds on every rung", all(r.delay(i) >= 300 for i in range(7)))

    hinted = Fault("rate_limited", "spent", retry_after=900)
    ok("a server-supplied retry-after wins over the floor", hinted.delay(0) == 900)
    ok("…but is capped, so one bad header can't park a run for a day",
       Fault("rate_limited", "spent", retry_after=999_999).delay(0) == 3600)


# ── ResilientTurn ───────────────────────────────────────────────────────────────────────────────

class _FakeAgent:
    """An agent whose turns are scripted. Each script entry is either a list of events to yield or
    an exception to raise, so a test can say "fail twice, then succeed"."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def run_turn(self, ctx, prompt, **kw):
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(step, BaseException):
            raise step
        for ev in step:
            yield ev


async def _drain(turn: ResilientTurn, agent) -> list:
    return [ev async for ev in turn.stream(agent, None, "go")]


def test_resilient_turn() -> None:
    print("\n[ResilientTurn] the ladder in one place, and the rule that keeps it safe")

    # Patch the sleep so the suite doesn't actually wait 29 minutes.
    import superme_agent.daemon.services.turns as turns_mod
    slept: list[int] = []
    real_sleep = turns_mod.asyncio.sleep

    async def _fake_sleep(sec):
        slept.append(sec)

    turns_mod.asyncio.sleep = _fake_sleep
    try:
        # A clean turn: no retry, no fault.
        agent = _FakeAgent([[TextDelta("done"), Result(text="done", tokens=10)]])
        t = ResilientTurn("test")
        evs = asyncio.run(_drain(t, agent))
        ok("a clean turn runs once", agent.calls == 1)
        ok("…yields its events untouched", len(evs) == 2)
        ok("…and reports no fault", not t.fault.failed)
        ok("…counting one attempt", t.attempts == 1)

        # A transient crash that clears on the second try.
        slept.clear()
        agent = _FakeAgent([TimeoutError("upstream timed out"),
                            [Result(text="ok", tokens=5)]])
        t = ResilientTurn("test")
        asyncio.run(_drain(t, agent))
        ok("a transient crash is retried", agent.calls == 2)
        ok("…after the ladder's first wait", slept == [60])
        ok("…and the recovered turn carries no fault", not t.fault.failed)

        # A fault is never retried.
        slept.clear()
        agent = _FakeAgent([ValueError("bad argument")])
        t = ResilientTurn("test")
        asyncio.run(_drain(t, agent))
        ok("a fault is not retried", agent.calls == 1)
        ok("…and never sleeps", slept == [])
        ok("…and surfaces as a fault", t.fault.kind == "fault")

        # The ladder is finite.
        slept.clear()
        agent = _FakeAgent([TimeoutError("still down")])
        t = ResilientTurn("test")
        asyncio.run(_drain(t, agent))
        ok("a permanent outage exhausts the ladder and stops",
           agent.calls == len(RETRY_LADDER) + 1)
        ok("…having waited exactly the owner's schedule", slept == list(RETRY_LADDER))
        ok("…and hands back the typed verdict", t.fault.kind == "transient")

        # THE SAFETY RULE: a turn that touched anything is never replayed, however retryable the
        # failure looks.
        slept.clear()
        agent = _FakeAgent([[Status("Bash", {"command": "git commit"}), TimeoutError("died")]])

        class _MidCrash:
            calls = 0

            async def run_turn(self, ctx, prompt, **kw):
                _MidCrash.calls += 1
                yield Status("Bash", {"command": "git commit"})
                raise TimeoutError("died after the commit")

        t = ResilientTurn("test")
        asyncio.run(_drain(t, _MidCrash()))
        ok("a turn that called a tool before crashing is NOT replayed", _MidCrash.calls == 1)
        ok("…no wait either", slept == [])
        ok("…but the fault is still typed for the owner", t.fault.kind == "transient")

        # Run 804's exact shape: no exception, no tool call, one API-error reply.
        slept.clear()
        agent = _FakeAgent([[TextDelta("API Error: 529 Overloaded"), Result(text="", tokens=0)],
                            [Status("Read", {}), Result(text="real work", tokens=99)]])
        t = ResilientTurn("test")
        asyncio.run(_drain(t, agent))
        ok("an upstream error handed back as TEXT is caught, not counted as success",
           agent.calls == 2)
        ok("…and the recovered cycle is clean", not t.fault.failed)

        # Interactive turns opt out — the owner is watching.
        slept.clear()
        agent = _FakeAgent([TimeoutError("down")])
        t = ResilientTurn("chat", retry=False)
        asyncio.run(_drain(t, agent))
        ok("retry=False classifies without waiting", agent.calls == 1 and slept == [])
        ok("…and still names the kind", t.fault.kind == "transient")

        # The notify hook is what makes a sleeping run distinguishable from a hung one.
        seen: list[tuple] = []
        agent = _FakeAgent([TimeoutError("down"), [Result(text="ok", tokens=1)]])
        t = ResilientTurn("test", notify=lambda f, a, d: seen.append((f.kind, a, d)))
        asyncio.run(_drain(t, agent))
        ok("each wait notifies with (kind, attempt, seconds)", seen == [("transient", 1, 60)])
    finally:
        turns_mod.asyncio.sleep = real_sleep


# ── the wiring ──────────────────────────────────────────────────────────────────────────────────

def test_every_background_runner_is_wrapped() -> None:
    print("\n[wiring] one classifier, applied by every runner")

    runners = {
        "superme_agent/daemon/services/loop.py": 2,        # vet + build
        # The completion backstop is a real background turn, so it is classified — but never
        # retried.
        "superme_agent/daemon/services/runs.py": 5,        # deputy-feedback, close, intake, resolve, backstop
        "superme_agent/daemon/services/learning.py": 3,    # distill, write, sweep
        "superme_agent/daemon/services/deputy.py": 1,      # the judge
        "superme_agent/daemon/services/compaction.py": 2,  # handoff + /compact
    }
    for rel, n in runners.items():
        body = src(rel)
        ok(f"{Path(rel).name}: {n} runner(s) go through ResilientTurn",
           body.count("ResilientTurn(") == n)
        ok(f"{Path(rel).name}: no raw run_turn call is left",
           "_agent.run_turn(" not in body)

    # The interactive turn deliberately keeps its own loop: it classifies, but must not wait.
    ws = src("superme_agent/daemon/routers/ws.py")
    ok("the chat turn classifies its failure", "classify(exc=e)" in ws)
    ok("…and does NOT climb the ladder (the owner is watching)",
       "ResilientTurn" not in ws)

    # The old local matcher is gone, not merely unused — one reader of the question, not two.
    loop = src("superme_agent/daemon/services/loop.py")
    ok("is_infra_reply is retired from loop.py", "is_infra_reply" not in loop)
    ok("…and its regex with it", "_API_ERROR_REPLY" not in loop)

    # Everything the runners lost with their `finally` must still be reached. `ResilientTurn`
    # returns rather than raises, which is what makes the straight-line version equivalent.
    turns = src("superme_agent/daemon/services/turns.py")
    ok("the wrapper re-raises CancelledError rather than filing a shutdown as a fault",
       "except asyncio.CancelledError:" in turns and "raise" in turns)
    ok("the safety rule is in the code, not just the docstring",
       "not saw_call" in turns)


def test_retry_leaves_a_trail() -> None:
    print("\n[trail] a sleeping run must not look like a hung one")

    runs = src("superme_agent/daemon/services/runs.py")
    ok("there is one shared retry-notice helper", "def retry_notice(" in runs)
    ok("…which writes a run.retry dev event", '"run.retry"' in runs)
    ok("…naming the wait in minutes", "min" in runs.split("def retry_notice(")[1][:900])

    loop = src("superme_agent/daemon/services/loop.py")
    ok("the loop reuses it rather than defining a second one",
       "retry_notice" in loop and "def retry_notice" not in loop)


def main() -> None:
    test_kinds()
    test_reply_path_is_narrow()
    test_retry_after_is_read()
    test_ladder_is_the_owners_schedule()
    test_rate_limit_waits_for_the_window()
    test_resilient_turn()
    test_every_background_runner_is_wrapped()
    test_retry_leaves_a_trail()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
