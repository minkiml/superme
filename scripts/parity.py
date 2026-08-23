"""Behavior-parity harness: proves a refactor moved code without changing the API.

Route inventory is the hard gate. Read in-process, so it describes the code on disk.

    python -m scripts.parity snapshot         # refresh the committed baseline
    python -m scripts.parity check            # add --strict-shapes to gate on shape drift
    python -m scripts.parity check --live     # ask a running daemon instead, and probe WS
"""

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

DAEMON = os.environ.get("SUPERME_DAEMON_URL", "http://127.0.0.1:8787").rstrip("/")
BASELINE = Path(__file__).resolve().parent / "parity_baseline.json"

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from superme_agent.daemon.server import app
    return app


def _ws_routes(app) -> list[str]:
    """The WebSocket paths the app actually declares — absent from OpenAPI, so measured here.

    FastAPI keeps changing how an included router is stored: a plain list once, then objects
    carrying `routes`, and now a wrapper whose only handle is `original_router`. Follow every
    shape — missing one reports the routes as DELETED, on an app that still serves them."""
    from starlette.routing import WebSocketRoute

    found: list[str] = []
    seen: set[int] = set()

    def walk(routes) -> None:
        for r in routes:
            if id(r) in seen:
                continue
            seen.add(id(r))
            if isinstance(r, WebSocketRoute):
                found.append(f"WS {r.path}")
            elif (sub := getattr(r, "routes", None)) is not None:
                walk(sub)
            elif (inner := getattr(r, "original_router", None)) is not None:
                walk(getattr(inner, "routes", []))

    walk(app.router.routes)
    return sorted(found)


def _fetch_openapi() -> dict:
    try:
        with urllib.request.urlopen(f"{DAEMON}/openapi.json", timeout=10) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"✗ could not reach the daemon at {DAEMON}/openapi.json — is it up?  ({e})")


def _source(live: bool) -> tuple[dict, list[str], str]:
    """The OpenAPI document, the WS routes, and where they came from."""
    if live:
        base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {"inventory": []}
        ws = [r for r in base["inventory"] if r.startswith("WS ")]
        return _fetch_openapi(), ws, DAEMON
    app = _app()
    return app.openapi(), _ws_routes(app), "in-process"


def _inventory(openapi: dict, ws: list[str]) -> list[str]:
    """Sorted ['METHOD /path', …] over every HTTP operation, plus the WS routes."""
    items = [f"{method.upper()} {path}"
             for path, item in openapi.get("paths", {}).items()
             for method in item if method.lower() in HTTP_METHODS]
    return sorted(items + ws)


def _ws_ok(path: str) -> bool:
    """Open one daemon socket and close it. Both are side-effect-free to merely open."""
    base = DAEMON.replace("http://", "ws://").replace("https://", "wss://")

    async def probe() -> bool:
        import websockets  # available (SDK dep); imported lazily so a plain check needs nothing
        try:
            async with websockets.connect(f"{base}{path}", open_timeout=5, close_timeout=2):
                return True
        except Exception:  # noqa: BLE001
            return False

    return asyncio.run(probe())


def snapshot(live: bool) -> None:
    openapi, ws, where = _source(live)
    inv = _inventory(openapi, ws)
    BASELINE.write_text(json.dumps({"inventory": inv, "openapi": openapi}, indent=2, sort_keys=True), encoding="utf-8")
    print(f"✓ baseline written from {where}: {len(inv)} routes "
          f"({len(inv) - len(ws)} HTTP + {len(ws)} WS)")
    print(f"  → {BASELINE}")


def check(*, strict_shapes: bool, live: bool) -> int:
    if not BASELINE.exists():
        sys.exit(f"✗ no baseline at {BASELINE} — run `python -m scripts.parity snapshot` first")
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    openapi, ws, where = _source(live)
    inv = _inventory(openapi, ws)

    failed = False

    # --- ROUTE INVENTORY (hard gate) ---
    added = sorted(set(inv) - set(base["inventory"]))
    removed = sorted(set(base["inventory"]) - set(inv))
    if added or removed:
        failed = True
        print("✗ ROUTE INVENTORY DRIFT:")
        for r in removed:
            print(f"    - {r}   (in baseline, now MISSING)")
        for r in added:
            print(f"    + {r}   (NEW, not in baseline)")
    else:
        print(f"✓ route inventory identical ({len(inv)} routes, from {where})")

    # --- OPENAPI SHAPES (informational unless --strict-shapes) ---
    base_paths = base["openapi"].get("paths", {})
    cur_paths = openapi.get("paths", {})
    changed = sorted(
        p for p in set(base_paths) | set(cur_paths)
        if json.dumps(base_paths.get(p), sort_keys=True) != json.dumps(cur_paths.get(p), sort_keys=True)
    )
    base_comp = base["openapi"].get("components", {}).get("schemas", {})
    cur_comp = openapi.get("components", {}).get("schemas", {})
    comp_changed = sorted(
        c for c in set(base_comp) | set(cur_comp)
        if json.dumps(base_comp.get(c), sort_keys=True) != json.dumps(cur_comp.get(c), sort_keys=True)
    )
    if changed or comp_changed:
        tag = "✗ OPENAPI SHAPE DRIFT" if strict_shapes else "• openapi shapes changed (informational)"
        print(tag + ":")
        for p in changed:
            print(f"    ~ path  {p}")
        for c in comp_changed:
            print(f"    ~ model {c}")
        if strict_shapes:
            failed = True
    else:
        print("✓ openapi shapes identical")

    # --- WS HANDSHAKE (only against a real daemon) ---
    if live:
        for route in ws:
            path = route.split(" ", 1)[1]
            if _ws_ok(path):
                print(f"✓ ws {path} reachable")
            else:
                failed = True
                print(f"✗ ws {path} UNREACHABLE")

    print("—" * 4, "PARITY FAIL" if failed else "PARITY OK")
    return 1 if failed else 0


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args and not args[0].startswith("-") else "check"
    live = "--live" in args
    if cmd == "snapshot":
        snapshot(live)
    elif cmd == "check":
        sys.exit(check(strict_shapes="--strict-shapes" in args, live=live))
    else:
        sys.exit("usage: python -m scripts.parity [snapshot|check] [--strict-shapes] [--live]")


if __name__ == "__main__":
    main()
