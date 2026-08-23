"""Export the WebSocket frame models' JSON schema for the frontend's type codegen.

The agent socket is not an HTTP route, so its frames never reach the OpenAPI document.

    PYTHONPATH=. python scripts/ws_schema.py
"""

import json
from pathlib import Path

from superme_agent.daemon.schemas.ws import WsFrames

OUT = Path(__file__).resolve().parents[1] / "web/frontend/src/lib/api/generated/ws-schema.json"


def _strip_field_titles(node):
    """Drop pydantic's per-field `title`s so the generator names interfaces from the
    `$defs` keys rather than hoisting an alias per property."""
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
    OUT.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(schema.get('$defs', {}))} frame defs)")


if __name__ == "__main__":
    main()
