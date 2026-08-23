"""Connect to /ws/agent, watch one item, and print every inbound frame with a stamp.

Deterministic proof of the broker-to-socket live path while a background run streams.

    PYTHONPATH=. python -m scripts.watch_item_stream <item_id> [seconds]
"""
import asyncio
import json
import sys
import time

import websockets

WS = "ws://127.0.0.1:8787/ws/agent"


async def main(item_id: str, seconds: float) -> None:
    t0 = time.time()

    def stamp() -> str:
        return f"+{time.time() - t0:6.1f}s"

    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps({"type": "watch", "item_id": item_id}))
        print(f"{stamp()} WATCH sent for item {item_id}", flush=True)
        n_timeline = 0
        deadline = t0 + seconds
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            f = json.loads(raw)
            t = f.get("type")
            if t == "timeline":
                n_timeline += 1
                desc = (f.get("description") or "").replace("\n", " ")
                if len(desc) > 90:
                    desc = desc[:90] + "…"
                print(f"{stamp()} TIMELINE #{n_timeline} run={f.get('run_id')} "
                      f"kind={f.get('kind')} name={f.get('name')!r} :: {desc}", flush=True)
            elif t == "init":
                print(f"{stamp()} init", flush=True)
            else:
                print(f"{stamp()} other frame: {t}", flush=True)
        print(f"{stamp()} DONE — {n_timeline} timeline frame(s) received", flush=True)


if __name__ == "__main__":
    item = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
    asyncio.run(main(item, secs))
