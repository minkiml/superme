"""Every text read, write and subprocess decode names its encoding.

Python picks the LOCALE otherwise, which is cp1252 on a Western Windows and mangles every
non-ASCII artifact.

    python -m scripts.encoding_gate
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOTS = ("superme_agent", "web", "scripts")
# Markdown trees and installed packages, neither of which is ours to gate.
SKIP = ("plugins", "local-harness", "__pycache__", "node_modules", ".venv")
EXTRA = ("setup_superme.py", "run_superme.py")


def _mode(call: ast.Call, idx: int) -> str:
    arg = call.args[idx] if len(call.args) > idx else None
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    for k in call.keywords:
        if k.arg == "mode" and isinstance(k.value, ast.Constant):
            return str(k.value.value)
    return "r"


def offenders(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        kw = {k.arg for k in n.keywords}
        if "encoding" in kw:
            continue
        f = n.func
        attr = f.attr if isinstance(f, ast.Attribute) else None
        name = attr or (f.id if isinstance(f, ast.Name) else None)
        if attr in ("read_text", "write_text"):
            out.append((n.lineno, f"{attr}()"))
        elif attr == "open" and "b" not in _mode(n, 0):
            out.append((n.lineno, "open() in text mode"))
        elif name == "fdopen" and "b" not in _mode(n, 1):
            out.append((n.lineno, "fdopen() in text mode"))
        elif name in ("run", "Popen", "check_output") and ("text" in kw or "universal_newlines" in kw):
            out.append((n.lineno, f"subprocess {name}(text=True)"))
    return out


def files():
    for r in ROOTS:
        for p in (ROOT / r).rglob("*.py"):
            if not any(s in p.parts for s in SKIP):
                yield p
    for e in EXTRA:
        yield ROOT / e


def main() -> int:
    bad = [(p, ln, what) for p in files() for ln, what in offenders(p)]
    if bad:
        print(f"✗ ENCODING NOT DECLARED — {len(bad)} call(s) fall back to the locale:")
        for p, ln, what in bad[:40]:
            print(f"    {p.relative_to(ROOT)}:{ln}  {what}")
        if len(bad) > 40:
            print(f"    … and {len(bad) - 40} more")
        return 1
    print("✓ every text read, write and subprocess decode declares utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
