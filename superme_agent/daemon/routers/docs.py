"""Docs routes (the SuperMe Docs dashboard): /docs, /docs/{slug}.

Flat markdown reference under superme-docs/. README.md is the overview (slug "overview", always
first); every other <name>.md is a doc whose title is its first `# ` heading.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...runtime.config import ROOT_DIR
from ..schemas.docs import DocsListResponse, DocResponse

router = APIRouter()

DOCS_DIR = ROOT_DIR / "superme-docs"


def _doc_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _doc_slug(p: Path) -> str:
    return "overview" if p.name == "README.md" else p.stem


@router.get("/docs", response_model=DocsListResponse)
async def docs_list() -> dict:
    """List the docs (slug + title), overview first, then alphabetical by title."""
    items = []
    if DOCS_DIR.is_dir():
        for p in DOCS_DIR.glob("*.md"):
            slug = _doc_slug(p)
            items.append({"slug": slug, "title": _doc_title(p.read_text(), p.stem)})
    items.sort(key=lambda d: (d["slug"] != "overview", d["title"].lower()))
    return {"docs": items}


@router.get("/docs/{slug}", response_model=DocResponse)
async def docs_read(slug: str) -> dict:
    """Return one doc's markdown. `overview` maps to README.md. Path-traversal-safe."""
    name = "README.md" if slug == "overview" else f"{slug}.md"
    p = (DOCS_DIR / name).resolve()
    if DOCS_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    text = p.read_text()
    return {"slug": slug, "title": _doc_title(text, p.stem), "content": text}
