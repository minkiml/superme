"""A tiny CLI client for the Core daemon — exercises the agent socket end-to-end.

    python -m superme_agent.daemon.devclient

Connects to ws://HOST:PORT/ws/agent, sends each line you type as a turn in the global
context, prints streamed events, answers approval requests from stdin (y/N), and
chains the session id so the conversation continues. This stands in for the web chat
panel until Stage C builds it. Type 'quit' to exit.
"""

import sys
import json
import asyncio

import websockets

from ..runtime.config import DAEMON_HOST, DAEMON_PORT

URL = f"ws://{DAEMON_HOST}:{DAEMON_PORT}/ws/agent"


async def _prompt(text: str) -> str:
    """Read a line from stdin without blocking the event loop."""
    return (await asyncio.to_thread(sys.stdin.readline)).strip()


async def main() -> None:
    resume: str | None = None
    print(f"connecting to {URL} …")
    async with websockets.connect(URL) as ws:
        print("connected. Type a prompt (or 'quit').")
        while True:
            print("\nyou> ", end="", flush=True)
            prompt = await _prompt("")
            if prompt.lower() in ("quit", "exit"):
                return
            if not prompt:
                continue

            await ws.send(json.dumps({
                "type": "turn", "prompt": prompt,
                "context_id": "global", "resume": resume, "model": None,
            }))

            # Read frames until this turn's result/error.
            while True:
                frame = json.loads(await ws.recv())
                kind = frame["type"]
                if kind == "text_delta":
                    print(frame["text"], end="", flush=True)
                elif kind == "status":
                    print(f"\n  · [{frame['tool_name']}]", flush=True)
                elif kind == "approval_request":
                    print(f"\n  ⚠ APPROVE {frame['tool_name']}? {frame['tool_input']}")
                    print("  [y/N]> ", end="", flush=True)
                    ans = await _prompt("")
                    await ws.send(json.dumps({
                        "type": "approval_response", "id": frame["id"],
                        "approved": ans.lower() in ("y", "yes"),
                    }))
                elif kind == "result":
                    resume = frame.get("session_id") or resume
                    print(
                        f"\n  --- result --- model={frame['model']} "
                        f"ctx={frame['context_pct']}% session={frame['session_id']}"
                    )
                    break
                elif kind == "error":
                    print(f"\n  [error] {frame['message']}")
                    break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
