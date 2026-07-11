"""Filesystem-browse schemas — back the connect-a-domain folder picker. Read-only directory
listing, bounded to the owner's home dir; the FE navigates it to choose (or name) a project dir."""

from __future__ import annotations

from pydantic import BaseModel


class FsEntry(BaseModel):
    name: str
    path: str            # absolute path
    is_git: bool = False  # has a .git — an existing repo
    has_code: bool = False  # any non-hidden content — 'looks like an existing project' hint
    empty: bool = True    # no non-hidden entries — a fresh/empty dir


class FsBrowseResponse(BaseModel):
    path: str             # the directory being listed (absolute)
    parent: str | None    # its parent, or null at the browse-root boundary
    entries: list[FsEntry]
    locked: bool = False   # the OS denied read access (macOS TCC) — entries is empty, not an error
