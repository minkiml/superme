"""Schemas for the knowledge routes (knowledge.py)."""

from __future__ import annotations

from pydantic import BaseModel


class KnowledgeNode(BaseModel):
    """A node in the knowledge tree. `dir` nodes carry `children`; `file` nodes omit it
    (serialized with response_model_exclude_none so files stay `{name, type, path}`)."""
    name: str
    type: str  # "dir" | "file"
    path: str
    children: list[KnowledgeNode] | None = None


class KnowledgeFileResponse(BaseModel):
    path: str
    content: str


class KnowledgeOkResponse(BaseModel):
    """The write/inject result: ok + the (relative) path written."""
    ok: bool
    path: str


class WriteBody(BaseModel):
    path: str
    content: str
    context_id: str = "global"


class InjectBody(BaseModel):
    title: str
    content: str
    folder: str = "knowledge"
    context_id: str = "global"
