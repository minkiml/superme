"""Find code nothing runs. Candidates only — a route handler or a name reached by getattr
looks unused and is not.

    python -m scripts.deadcode              # top-level names nothing refers to
    python -m scripts.deadcode --methods    # class methods nothing calls
    python -m scripts.deadcode --imports    # imported names the module never uses
"""

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "superme_agent"
SKIP_PARTS = ("plugins", "local-harness")

# Text outside the package can name a symbol: skills, templates, the frontend, the suites.
PROSE_ROOTS = ("superme_agent/harness/plugins", "web/frontend/src", "scripts", "general_docs")

# A decorator hands the name to a framework, so nothing needs to import it.
REGISTERED = re.compile(r"@(router|app)\.|@(staticmethod|classmethod|property|dataclass)")


def modules() -> list[Path]:
    return [p for p in sorted(PKG.rglob("*.py")) if not any(x in p.parts for x in SKIP_PARTS)]


def unused_imports(files, text, trees) -> int:
    """An imported name the module never mentions again. A re-export looks the same, so `__init__`
    modules are skipped."""
    total = 0
    for p in files:
        if p.name == "__init__.py":
            continue
        used = {n.id for n in ast.walk(trees[p]) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(trees[p]) if isinstance(n, ast.Attribute)}
        for n in ast.walk(trees[p]):
            if isinstance(n, ast.Attribute):
                root = n
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name):
                    used.add(root.id)
        dead = []
        for node in trees[p].body:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                continue
            for a in node.names:
                bound = (a.asname or a.name).split(".")[0]
                if bound == "*" or bound in used:
                    continue
                if re.search(rf"\b{re.escape(bound)}\b", text[p].replace(ast.unparse(node), "")):
                    continue
                dead.append((node.lineno, bound))
        if dead:
            total += len(dead)
            print(f"\n{p.relative_to(ROOT)}")
            for line, name in dead:
                print(f"    {line:5d}  {name}")
    print(f"\n{total} unused import(s).")
    return 0


def dead_methods(files, text, trees) -> int:
    """A method whose name appears nowhere but its own definition. Common words collide, so this
    is a candidate list, never a delete list."""
    total = 0
    for p in files:
        rows = text[p].splitlines(True)
        found = []
        for cls in [n for n in ast.walk(trees[p]) if isinstance(n, ast.ClassDef)]:
            for m in cls.body:
                if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if m.name.startswith("__"):
                    continue
                if any("router" in ast.unparse(d) or "app." in ast.unparse(d)
                       for d in m.decorator_list):
                    continue
                word = re.compile(rf"\b{re.escape(m.name)}\b")
                rest = "".join(rows[:m.lineno - 1] + rows[m.end_lineno:])
                if word.search(rest):
                    continue
                if any(word.search(t) for q, t in text.items() if q != p):
                    continue
                if any(word.search(t) for t in PROSE):
                    continue
                found.append((m.lineno, f"{cls.name}.{m.name}", m.end_lineno - m.lineno + 1))
        if found:
            total += len(found)
            print(f"\n{p.relative_to(ROOT)}")
            for line, name, span in sorted(found):
                print(f"    {line:5d}  {name:44s} {span:4d} line(s)")
    print(f"\n{total} method candidate(s) — verify each before deleting.")
    return 0


PROSE: list[str] = []


def main() -> int:
    show_all = "--all" in sys.argv
    files = modules()
    text = {p: p.read_text(encoding="utf-8") for p in files}
    trees = {p: ast.parse(t) for p, t in text.items()}

    prose = PROSE
    for rel in PROSE_ROOTS:
        for p in (ROOT / rel).rglob("*"):
            if p.is_file() and p.suffix in (".md", ".ts", ".tsx", ".py", ".json", ".yaml", ".sh"):
                prose.append(p.read_text(errors="ignore", encoding="utf-8"))

    if "--imports" in sys.argv:
        return unused_imports(files, text, trees)
    if "--methods" in sys.argv:
        return dead_methods(files, text, trees)

    defined: dict[str, list] = defaultdict(list)
    for p, tree in trees.items():
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            for n in names:
                defined[n].append((p, node))

    dead = defaultdict(list)
    for name, sites in defined.items():
        if name.startswith("__") or len(sites) > 1:
            continue
        path, node = sites[0]
        decorated = any(REGISTERED.search(ast.unparse(d)) for d in getattr(node, "decorator_list", []))
        if decorated and not show_all:
            continue
        word = re.compile(rf"\b{re.escape(name)}\b")
        elsewhere = any(word.search(t) for p, t in text.items() if p != path)
        if elsewhere:
            continue
        rows = text[path].splitlines(True)
        rest = rows[:node.lineno - 1] + rows[node.end_lineno:]
        if word.search("".join(rest)):
            continue                      # its own module still refers to it
        if any(word.search(t) for t in prose):
            continue                      # named in a skill, the frontend, or a suite
        dead[str(path.relative_to(ROOT))].append((node.lineno, name,
                                                  node.end_lineno - node.lineno + 1))

    total = sum(len(v) for v in dead.values())
    for f in sorted(dead):
        print(f"\n{f}")
        for line, name, span in sorted(dead[f]):
            print(f"    {line:5d}  {name:38s} {span:4d} line(s)")
    print(f"\n{total} candidate(s) in {len(dead)} module(s) — verify each before deleting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
