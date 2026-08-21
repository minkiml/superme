"""Response schemas for the self-inventory route (routers/inventory.py)."""

from pydantic import BaseModel


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
