"""Export the WebSocket frame models' JSON schema for the frontend's WS-type codegen (R6).

The agent socket isn't an HTTP route, so its frames don't land in the daemon OpenAPI. This dumps the
combined `WsFrames` JSON schema (every frame model in `$defs`) to the FE's generated/ dir, where
`npm run gen:ws` turns it into TypeScript — the same drift-proof pipeline R8 uses for REST.

    PYTHONPATH=. python scripts/ws_schema.py        # writes web/frontend/src/lib/api/generated/ws-schema.json
"""

import json
from pathlib import Path

from superme_agent.daemon.schemas.ws import WsFrames

OUT = Path(__file__).resolve().parents[1] / "web/frontend/src/lib/api/generated/ws-schema.json"


def _strip_field_titles(node):
    """Drop pydantic's per-field `title`s so json-schema-to-typescript names interfaces from the
    `$defs` keys (TurnFrame, ResultFrame, …) instead of hoisting a noisy alias per property."""
    if isinstance(node, dict):
        node.pop("title", None)
        for v in node.values():
            _strip_field_titles(v)
    elif isinstance(node, list):
        for v in node:
            _strip_field_titles(v)


def main() -> None:
    schema = WsFrames.model_json_schema()
    _strip_field_titles(schema)
    schema["title"] = "WsFrames"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {OUT} ({len(schema.get('$defs', {}))} frame defs)")


if __name__ == "__main__":
    main()
