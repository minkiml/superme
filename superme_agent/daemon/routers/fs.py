"""Filesystem browse — the connect-a-domain folder picker's backend. Lists sub-directories of a
path, bounded to the owner's home dir (a localhost dev tool, but never above home). Read-only; no
create/delete here — creating a new project dir happens at registration (POST /repos)."""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..schemas.fs import FsBrowseResponse, FsEntry

router = APIRouter()

_HOME = Path.home().resolve()


def _within_home(p: Path) -> bool:
    return p == _HOME or _HOME in p.parents


def _dir_flags(d: Path) -> tuple[bool, bool, bool]:
    """(is_git, has_code, empty) — cheap signals to hint new-vs-existing. Best-effort: an
    unreadable dir reads as empty rather than erroring the whole listing."""
    try:
        visible = [c for c in d.iterdir() if not c.name.startswith(".")]
    except OSError:
        return (False, False, True)
    is_git = (d / ".git").exists()
    return (is_git, bool(visible), not visible)


@router.get("/fs/browse", response_model=FsBrowseResponse)
def fs_browse(path: str = "") -> dict:
    """List the sub-directories of `path` (default: the owner's home). Directories only — the
    picker chooses a project folder. 400 if the path escapes home or isn't a directory."""
    target = Path(path).expanduser().resolve() if path else _HOME
    if not _within_home(target):
        raise HTTPException(status_code=400, detail="path is outside the home directory")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")
    parent = str(target.parent) if _within_home(target.parent) and target != _HOME else None
    try:
        children = sorted(
            (c for c in target.iterdir() if c.is_dir() and not c.name.startswith(".")),
            key=lambda c: c.name.lower(),
        )
    except PermissionError:
        # macOS TCC (Documents/Desktop/Downloads etc.) — degrade to a locked, empty view rather than
        # erroring the whole picker; the owner can navigate elsewhere or grant Full Disk Access.
        return {"path": str(target), "parent": parent, "entries": [], "locked": True}
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"cannot read directory: {e}") from e
    entries = []
    for c in children:
        is_git, has_code, empty = _dir_flags(c)
        entries.append(FsEntry(name=c.name, path=str(c), is_git=is_git, has_code=has_code, empty=empty))
    return {"path": str(target), "parent": parent, "entries": entries}
