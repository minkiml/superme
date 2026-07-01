"""KnowledgeService — file CRUD over a Context's knowledge_root.

The second half of the Core (alongside AgentService). Surface-agnostic: it operates
on a knowledge_root Path and never knows who's calling. Stage C1 keeps it deliberately
simple over a minimal base structure; the real retrieval model (index, metadata,
lifecycle, taxonomy) is a dedicated future design job.

  list_tree  the folder/file tree (relative paths), for the dashboard to render
  read       one file's text
  write      overwrite/create one file (used by in-place editing)
  inject     create a new note from {title, content} and link it in index.md
"""

import re
from pathlib import Path


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "note"


class KnowledgeService:
    """Read/write/list/inject over a file-based knowledge root."""

    def _safe(self, root: Path, relpath: str) -> Path:
        """Resolve relpath under root, refusing anything that escapes it."""
        root_r = Path(root).resolve()
        p = (root_r / (relpath or "")).resolve()
        if p != root_r and root_r not in p.parents:
            raise ValueError("path escapes the knowledge root")
        return p

    def list_tree(self, root: Path) -> dict:
        """Nested tree of dirs/files under root (hidden entries skipped).

        `root` is a context's workspace/ knowledge root; internal knowledge (dev/) lives
        in a sibling internal/ root and never appears here (D-016)."""
        root = Path(root)

        def walk(d: Path) -> list:
            out = []
            for entry in sorted(d.iterdir(), key=lambda e: (e.is_file(), e.name.lower())):
                if entry.name.startswith("."):
                    continue
                rel = str(entry.relative_to(root))
                if entry.is_dir():
                    out.append({"name": entry.name, "type": "dir", "path": rel,
                                "children": walk(entry)})
                else:
                    out.append({"name": entry.name, "type": "file", "path": rel})
            return out

        children = walk(root) if root.exists() else []
        return {"name": root.name, "type": "dir", "path": "", "children": children}

    def read(self, root: Path, relpath: str) -> str:
        return self._safe(root, relpath).read_text()

    def write(self, root: Path, relpath: str, content: str) -> None:
        p = self._safe(root, relpath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def inject(self, root: Path, title: str, content: str, folder: str = "knowledge") -> str:
        """Create a new note and link it in index.md. Returns its relative path."""
        slug = _slugify(title)
        rel = f"{folder}/{slug}.md"
        p = self._safe(root, rel)
        i = 2
        while p.exists():
            rel = f"{folder}/{slug}-{i}.md"
            p = self._safe(root, rel)
            i += 1
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {title}\n\n{content}\n")

        index = Path(root) / "index.md"
        if index.exists():
            with index.open("a") as f:
                f.write(f"\n- [{title}]({rel})")
        return rel
