"""An id no project answers to is refused, never substituted.

Answering as a different project writes one project's work into another's home, and nothing
downstream can tell afterwards.

Run: PYTHONPATH=. python -m scripts.test_context_id
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient                              # noqa: E402

from superme_agent.gateway import contexts                             # noqa: E402
from superme_agent.paths import LOCAL_HARNESS_DIR                      # noqa: E402
from scripts.sources import src                                                # noqa: E402

PASS = FAIL = 0


def ok(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


# --------------------------------------------------------------------------- the resolver

def test_resolver_refuses():
    real = contexts.resolve("global", "dev")
    ok(real.id == "global", "a live id still resolves")

    raised = None
    try:
        contexts.resolve("no-project-answers-to-this", "dev")
    except contexts.UnknownContext as e:
        raised = e
    ok(raised is not None, "an unknown id raises rather than returning a Context")
    ok(getattr(raised, "context_id", None) == "no-project-answers-to-this",
       "…and carries the id, so the handler can name it back")
    ok(contexts.resolve(None, "dev").id == "global", "None still means the hub — that is not a guess")

    ok(contexts.exists("global") and not contexts.exists("no-project-answers-to-this"),
       "`exists` answers without raising, for callers holding a row that may outlive its repo")


# --------------------------------------------------------------------------- the surface

def test_every_surface_404s():
    from superme_agent.daemon.server import app

    bogus = "no-project-answers-to-this"
    # NOT a context manager: entering one runs the lifespan, whose `reconcile()` aborts live runs.
    client = TestClient(app, raise_server_exceptions=False)
    for path in ("/dev", "/dev/attention", "/dev/harness/deputy", "/dev/work-items"):
        r = client.get(path, params={"context_id": bogus})
        ok(r.status_code == 404, f"GET {path} with an unknown context_id is 404, got {r.status_code}")
    body = client.get("/dev/harness/deputy", params={"context_id": bogus}).json()
    ok(body.get("context_id") == bogus, "the 404 body names the id it refused")
    ok(client.get("/dev", params={"context_id": "global"}).status_code == 200,
       "a real id is unaffected")


# --------------------------------------------------------------------------- no side effects

def test_a_refused_id_writes_nothing():
    before = {p.name for p in LOCAL_HARNESS_DIR.iterdir()} if LOCAL_HARNESS_DIR.is_dir() else set()
    try:
        contexts.resolve("would-be-a-new-directory", "dev")
    except contexts.UnknownContext:
        pass
    after = {p.name for p in LOCAL_HARNESS_DIR.iterdir()} if LOCAL_HARNESS_DIR.is_dir() else set()
    ok(before == after, "refusing an id creates no harness cell for it")


def test_no_path_joins_a_caller_string():
    """A containment test is only worth the root it is given, so the root comes from the registry."""
    ok("ctx: Context" in src("superme_agent/core/deputy.py"),
       "deputy_root takes a resolved Context, so an id cannot be joined onto a path unchecked")
    ok("LOCAL_HARNESS_DIR / ctx.id" in src("superme_agent/core/deputy.py"),
       "…and builds its path from the resolved id")


def main():
    test_resolver_refuses()
    test_every_surface_404s()
    test_a_refused_id_writes_nothing()
    test_no_path_joins_a_caller_string()
    print(f"\n{'ALL GREEN' if not FAIL else 'FAILED'} — {PASS} checks passed, {FAIL} failed.")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
