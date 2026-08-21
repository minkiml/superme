"""Read one module's source by repo-relative path, for suites that assert what lives in it.

Naming a module as a file keeps resolving once it becomes a package: the subject is the module.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def src(rel: str) -> str:
    """The whole source at `rel` — the file, or every module in the package it turned into."""
    path = ROOT / rel
    if path.is_file():
        return path.read_text()
    package = path.with_suffix("")
    if package.is_dir():
        parts = [p.read_text() for p in sorted(package.rglob(f"*{path.suffix}"))]
        if not parts:
            raise FileNotFoundError(f"{package} holds no {path.suffix} — is the extension right?")
        return "".join(parts)
    raise FileNotFoundError(f"no module at {rel}")
