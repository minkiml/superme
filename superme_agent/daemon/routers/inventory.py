"""TEMPORARY internals inventory — a single read-only snapshot of SuperMe's live data model + the
agent-facing tool surface, for the "Internals" dashboard tab.

Everything here is INTROSPECTED live (SQLite `sqlite_master`/`PRAGMA` for the schemas; the `ToolSpec`
lists for the tools) so the view never drifts from the code. This whole surface — router + FE tab +
nav entry — is a scaffold meant to be deleted once it has served its purpose; keep it self-contained.
"""

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger("superme-agent")

router = APIRouter()


# --- response shapes -------------------------------------------------------------------------
class ColumnInfo(BaseModel):
    name: str
    type: str
    pk: bool
    notnull: bool


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo]
    sql: str | None = None
    rows: int | None = None


class DatabaseInfo(BaseModel):
    name: str
    path: str
    present: bool
    tables: list[TableInfo]


class ToolParam(BaseModel):
    name: str
    required: bool


class ToolInfo(BaseModel):
    name: str
    description: str
    surface: str
    params: list[ToolParam]


class InventoryResponse(BaseModel):
    databases: list[DatabaseInfo]
    tools: list[ToolInfo]


# --- DB introspection ------------------------------------------------------------------------
def _read_db(name: str, path: Path) -> DatabaseInfo:
    if not path.is_file():
        return DatabaseInfo(name=name, path=str(path), present=False, tables=[])
    tables: list[TableInfo] = []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for r in rows:
            cols = [
                ColumnInfo(name=c["name"], type=c["type"] or "", pk=bool(c["pk"]), notnull=bool(c["notnull"]))
                for c in conn.execute(f"PRAGMA table_info('{r['name']}')").fetchall()
            ]
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM '{r['name']}'").fetchone()[0]
            except sqlite3.Error:
                count = None
            tables.append(TableInfo(name=r["name"], columns=cols, sql=r["sql"], rows=count))
    finally:
        conn.close()
    return DatabaseInfo(name=name, path=str(path), present=True, tables=tables)


# --- tool introspection ----------------------------------------------------------------------
def _tool_params(schema) -> list[ToolParam]:
    """Param names + required-ness from a ToolSpec schema — a TypedDict (Annotated fields +
    __required_keys__) or a raw JSON-Schema dict (properties + required)."""
    if isinstance(schema, dict):
        required = set(schema.get("required", []))
        return [ToolParam(name=k, required=k in required) for k in schema.get("properties", {})]
    ann = getattr(schema, "__annotations__", {}) or {}
    required = set(getattr(schema, "__required_keys__", frozenset()))
    return [ToolParam(name=k, required=k in required) for k in ann]


def _collect_tools() -> list[ToolInfo]:
    from ...harness.tools.base_tools import BASE_TOOLS
    from ...harness.tools.dev_tools import _MAIN_DEV_TOOLS, _ITEM_DEV_TOOLS, _LEARNING_DEV_TOOLS
    out: list[ToolInfo] = []
    for surface, specs in (
        ("base", BASE_TOOLS),
        ("dev · main", _MAIN_DEV_TOOLS),
        ("dev · work-item", _ITEM_DEV_TOOLS),
        ("dev · learning", _LEARNING_DEV_TOOLS),
    ):
        for s in specs:
            out.append(ToolInfo(name=s.name, description=s.description, surface=surface,
                                params=_tool_params(s.schema)))
    return out


@router.get("/system/inventory", response_model=InventoryResponse)
async def system_inventory() -> InventoryResponse:
    """A live snapshot of every SuperMe SQLite table (columns + row counts) and every agent-facing
    tool (surface + params). Read-only; introspected fresh on each call so it can't go stale."""
    from ...runtime.config import SYSTEM_DB_FILE, DEV_DB_FILE
    databases = [
        _read_db("system  ·  .system.db", SYSTEM_DB_FILE),
        _read_db("dev  ·  .dev.db", DEV_DB_FILE),
    ]
    return InventoryResponse(databases=databases, tools=_collect_tools())
