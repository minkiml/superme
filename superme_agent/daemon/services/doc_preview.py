"""Agent-facing artifact viewer — one work-item doc as a standalone HTML page.

The owner's reports name their `full contract` (brief.md · plan.md · build-vet-<n>.md). That line
used to be inert text; it is now a link that opens the doc in its own browser tab, the same shape
the prompt inspector already uses for a run's captured input.

The markdown is RENDERED (owner, 2026-08-02) — these documents carry tables, code blocks and
several heading levels, and reading that as raw source is reading a structure that is right there
in the file and simply not being drawn. `markdown_page.render` covers the constructs our own
templates produce; it shares the input inspector's palette so the two standalone views read as one
surface.

Path safety: the caller passes the report's own relative `contract` path. It is accepted only when
it stays inside the item's `artifacts/` folder — resolved and re-checked against the real directory,
so neither `..` nor a symlink can walk out.
"""

import html
from pathlib import Path

from .input_preview import _PAGE_CSS
from .markdown_page import DOC_CSS, render


def resolve_doc(item_dir: Path, rel_path: str) -> Path | None:
    """The absolute path of an item's agent-facing doc, or None when `rel_path` is not a readable
    file inside `<item_dir>/artifacts/`."""
    root = (Path(item_dir) / "artifacts").resolve()
    try:
        target = (Path(item_dir) / rel_path).resolve()
        target.relative_to(root)        # raises when the path escapes the artifacts folder
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def render_doc_page(item_id: str, rel_path: str, text: str) -> str:
    """The doc page: its path as the title, the rendered document below it."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(rel_path)} · {html.escape(item_id)}</title>"
        f"<style>{_PAGE_CSS}{DOC_CSS}</style></head><body><div class='wrap'>"
        f"<div class='hdr'><h1>{html.escape(rel_path)}</h1>"
        f"<span class='chip phase'>{html.escape(item_id)}</span></div>"
        "<div class='sub'>The agent-facing contract — what the phase agents work against.</div>"
        f"<div class='doc'>{render(text)}</div>"
        "</div></body></html>"
    )


def render_missing_doc_page(item_id: str, rel_path: str) -> str:
    """The page for a doc that isn't there — a phase that never wrote its contract, or a stale link."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(rel_path)} · {html.escape(item_id)}</title>"
        f"<style>{_PAGE_CSS}</style></head><body><div class='wrap'>"
        f"<div class='hdr'><h1>{html.escape(rel_path)}</h1>"
        f"<span class='chip phase'>{html.escape(item_id)}</span></div>"
        "<div class='note'>This item has no such document. A phase writes its contract as part of "
        "its work, so a missing one means that phase hasn’t run yet — or the file was renamed after "
        "the report that points here was written.</div>"
        "</div></body></html>"
    )
