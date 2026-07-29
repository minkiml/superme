"""Behavior-parity harness for the Backend Refactor (R0) — the gate every R-stage is measured against.

Captures the daemon's API surface as a committed baseline and fails loudly on unintended drift, so a
purely structural refactor (router extraction, DI, BFF proxy) can PROVE it changed organization but not
behavior. Two layers:

  • ROUTE INVENTORY (the hard gate) — the sorted set of "METHOD /path" over every HTTP route (from
    /openapi.json) plus the known WebSocket route(s). MUST stay identical through the pure-move stages
    (R1, R2, R7). Any add / remove / path-change / method-change fails the check.
  • OPENAPI SNAPSHOT (shapes) — the full /openapi.json. Informational by default (today there are no
    response models, so response shapes are undeclared); becomes meaningful as R3 attaches
    response_model=. Re-baseline DELIBERATELY (run `snapshot`) when a stage is meant to change shapes;
    gate on it between pure-move stages with `check --strict-shapes`.
  • WS HANDSHAKE — opens ws://…/ws/agent and closes immediately (no turn frame → no agent run), proving
    the socket route is mounted. OpenAPI can't enumerate WebSocket routes, so this is tracked explicitly.

The daemon must be UP (matches the scripts/ E2E convention). Read-only — never mutates any store.

Usage:
    python -m scripts.parity snapshot                 # write/refresh the committed baseline
    python -m scripts.parity check                    # inventory hard-gate + shapes report + WS (default)
    python -m scripts.parity check --strict-shapes    # also fail on ANY OpenAPI shape drift (pure-move gate)
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
    """Open one daemon socket and close it immediately. Both are side-effect-free to merely open:
    the agent socket does nothing until a turn frame arrives, and the dashboard socket is send-only.
    Sockets are absent from the OpenAPI, so without this probe they could break silently while the
    route inventory stayed green — which is exactly what a parity net exists to prevent."""
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
