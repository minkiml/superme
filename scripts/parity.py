"""Behavior-parity harness: proves a refactor moved code without changing the API.

The route inventory is the hard gate — the sorted "METHOD /path" set from /openapi.json plus
the WebSocket routes, which OpenAPI cannot enumerate. The shape snapshot is informational
until `--strict-shapes`. Read-only, and the daemon must be up.

Usage:
    python -m scripts.parity snapshot                 # write/refresh the committed baseline
    python -m scripts.parity check                    # inventory gate + shapes report + WS
    python -m scripts.parity check --strict-shapes    # also fail on any OpenAPI shape drift
    python -m scripts.parity check --no-ws            # skip the WS handshake
"""

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

DAEMON = os.environ.get("SUPERME_DAEMON_URL", "http://127.0.0.1:8787").rstrip("/")
BASELINE = Path(__file__).resolve().parent / "parity_baseline.json"

# Routes OpenAPI can't enumerate (WebSocket) — tracked explicitly so the inventory stays complete.
WS_ROUTES = ["WS /ws/agent", "WS /ws/dashboard"]


def _fetch_openapi() -> dict:
    try:
        with urllib.request.urlopen(f"{DAEMON}/openapi.json", timeout=10) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"✗ could not reach the daemon at {DAEMON}/openapi.json — is it up?  ({e})")


def _inventory(openapi: dict) -> list[str]:
    """Sorted ['METHOD /path', …] over every HTTP operation + the known WS route(s)."""
    items: list[str] = []
    for path, item in openapi.get("paths", {}).items():
        for method in item:
            if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}:
                items.append(f"{method.upper()} {path}")
    return sorted(items + WS_ROUTES)


def _ws_ok(name: str) -> bool:
    """Open one daemon socket and close it. Both are side-effect-free to merely open.

    Sockets are absent from the OpenAPI, so without this probe one could break silently while
    the route inventory stayed green."""
    base = DAEMON.replace("http://", "ws://").replace("https://", "wss://")

    async def _probe() -> bool:
        import websockets  # available (SDK dep); imported lazily so --no-ws needs nothing
        try:
            async with websockets.connect(f"{base}/ws/{name}", open_timeout=5, close_timeout=2):
                return True
        except Exception:  # noqa: BLE001
            return False

    return asyncio.run(_probe())


def snapshot() -> None:
    openapi = _fetch_openapi()
    inv = _inventory(openapi)
    BASELINE.write_text(json.dumps({"inventory": inv, "openapi": openapi}, indent=2, sort_keys=True))
    print(f"✓ baseline written: {len(inv)} routes ({len(inv) - len(WS_ROUTES)} HTTP + {len(WS_ROUTES)} WS)")
    print(f"  → {BASELINE}")


def check(*, strict_shapes: bool, do_ws: bool) -> int:
    if not BASELINE.exists():
        sys.exit(f"✗ no baseline at {BASELINE} — run `python -m scripts.parity snapshot` first")
    base = json.loads(BASELINE.read_text())
    openapi = _fetch_openapi()
    inv = _inventory(openapi)

    failed = False

    # --- ROUTE INVENTORY (hard gate) ---
    base_inv = set(base["inventory"])
    cur_inv = set(inv)
    added = sorted(cur_inv - base_inv)
    removed = sorted(base_inv - cur_inv)
    if added or removed:
        failed = True
        print("✗ ROUTE INVENTORY DRIFT:")
        for r in removed:
            print(f"    - {r}   (in baseline, now MISSING)")
        for r in added:
            print(f"    + {r}   (NEW, not in baseline)")
    else:
        print(f"✓ route inventory identical ({len(inv)} routes)")

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

    # --- WS HANDSHAKE ---
    if do_ws:
        for name in ("agent", "dashboard"):
            if _ws_ok(name):
                print(f"✓ ws /ws/{name} reachable")
            else:
                failed = True
                print(f"✗ ws /ws/{name} UNREACHABLE")

    print("—" * 4, "PARITY FAIL" if failed else "PARITY OK")
    return 1 if failed else 0


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "check"
    if cmd == "snapshot":
        snapshot()
    elif cmd == "check":
        sys.exit(check(strict_shapes="--strict-shapes" in args, do_ws="--no-ws" not in args))
    else:
        sys.exit(f"usage: python -m scripts.parity [snapshot|check] [--strict-shapes] [--no-ws]")


if __name__ == "__main__":
    main()
