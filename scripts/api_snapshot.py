"""Import-surface parity: proves a refactor changed no module's importable names.

A pure move keeps every signature and only changes `home`, so a package split reads as clean.

    python -m scripts.api_snapshot snapshot   # refresh the committed baseline
    python -m scripts.api_snapshot check      # fail on any lost or changed symbol
"""

import importlib
import inspect
import json
import pkgutil
import re
import sys
from pathlib import Path

PKG = "superme_agent"
BASELINE = Path(__file__).resolve().parent / "api_baseline.json"

# Content, not code: skills and agents are markdown trees with no import surface.
SKIP_PARTS = ("plugins", "local-harness")

# A default rendered as `<Foo object at 0x7f…>` differs every run; the address says nothing.
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")

# Some defaults are paths built from the install root, which differs per checkout.
_ROOT = ""

# `home` already tracks where a class lives, so moving one must not read as a signature change.
_QUALNAME = re.compile(r"superme_agent[\w.]*\.(\w+)")


def _ours(obj) -> bool:
    return str(getattr(obj, "__module__", "")).startswith(PKG)


def _signature(obj) -> str:
    try:
        text = _ADDRESS.sub("", str(inspect.signature(obj)))
    except (TypeError, ValueError):
        return "(?)"
    text = _QUALNAME.sub(r"\1", text)
    return text.replace(_ROOT, "<root>") if _ROOT else text


def _class_methods(cls) -> list[str]:
    """Every method this project defines on the class, base classes included.

    Walking the MRO is what makes a mixin split read as identical: the methods change file,
    the assembled class keeps every one of them."""
    out: dict[str, str] = {}
    for base in reversed(inspect.getmro(cls)):
        if base is object or not _ours(base):
            continue
        for name, val in vars(base).items():
            if name.startswith("__"):
                continue
            if isinstance(val, property):
                out[name] = "property"
            elif isinstance(val, (staticmethod, classmethod)):
                out[name] = _signature(val.__func__)
            elif inspect.isfunction(val):
                out[name] = _signature(val)
    return sorted(f"{n}{s}" for n, s in out.items())


def _module_surface(mod) -> dict:
    """The names another module could import from this one, minus re-exported outsiders."""
    syms: dict[str, dict] = {}
    for key, val in vars(mod).items():
        if key.startswith("__") or inspect.ismodule(val):
            continue
        if inspect.isfunction(val):
            if _ours(val):
                syms[key] = {"sig": f"def{_signature(val)}", "home": val.__module__}
        elif inspect.isclass(val):
            if _ours(val):
                syms[key] = {"sig": "class", "home": val.__module__,
                             "methods": _class_methods(val)}
        else:
            syms[key] = {"sig": f"value:{type(val).__name__}"}
    return syms


def collect() -> dict:
    """Import every module under the package and record its public surface."""
    import superme_agent

    global _ROOT
    _ROOT = str(Path(superme_agent.__file__).resolve().parent.parent)

    out: dict[str, dict] = {}
    for info in pkgutil.walk_packages(superme_agent.__path__, "superme_agent."):
        name = info.name
        if any(p in name for p in SKIP_PARTS):
            continue
        try:
            mod = importlib.import_module(name)
        except Exception as e:            # noqa: BLE001
            out[name] = {"__import_error__": {"sig": f"{type(e).__name__}: {e}"}}
            continue
        out[name] = _module_surface(mod)
    out[PKG] = _module_surface(superme_agent)
    return out


def snapshot() -> None:
    data = collect()
    bad = sorted(m for m, s in data.items() if "__import_error__" in s)
    if bad:
        sys.exit(f"✗ refusing to baseline a tree that will not import: {', '.join(bad)}")
    BASELINE.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    total = sum(len(s) for s in data.values())
    print(f"✓ baseline written: {len(data)} modules, {total} symbols")
    print(f"  → {BASELINE}")


def _importers(module: str, name: str) -> list[str]:
    """Which files reach for this name — `from … import name`, or `mod.name` on an alias."""
    root = Path(__file__).resolve().parent.parent
    tail = module.rsplit(".", 1)[-1]
    hits: list[str] = []
    for path in sorted((root / PKG).rglob("*.py")):
        if any(p in path.parts for p in SKIP_PARTS):
            continue
        text = path.read_text(errors="ignore", encoding="utf-8")
        if re.search(rf"import[^\n]*\b{re.escape(name)}\b", text) or f".{name}" in text:
            if tail in text:
                hits.append(str(path.relative_to(root)))
    return hits


def check() -> int:
    if not BASELINE.exists():
        sys.exit(f"✗ no baseline at {BASELINE} — run `python -m scripts.api_snapshot snapshot` first")
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    cur = collect()

    broke: list[str] = []
    narrowed: list[str] = []
    moved: list[str] = []
    added: list[str] = []

    for mod, syms in sorted(base.items()):
        now = cur.get(mod)
        if now is None:
            broke.append(f"module GONE: {mod}")
            continue
        if "__import_error__" in now:
            broke.append(f"module FAILS TO IMPORT: {mod} — {now['__import_error__']['sig']}")
            continue
        for name, was in sorted(syms.items()):
            is_ = now.get(name)
            if is_ is None:
                if name.startswith("_"):
                    who = _importers(mod, name)
                    narrowed.append(f"{mod}.{name} — " + (f"still reached by {', '.join(who)}"
                                                          if who else "no importer found"))
                else:
                    broke.append(f"{mod}.{name} — no longer importable")
                continue
            if was.get("home") != is_.get("home"):
                moved.append(f"{mod}.{name}: {was.get('home')} → {is_.get('home')}")
            if mod != was.get("home", mod):
                continue                  # a re-export: presence is all this module promises
            if was.get("sig") != is_.get("sig"):
                broke.append(f"{mod}.{name} — {was.get('sig')} → {is_.get('sig')}")
                continue
            for m in sorted(set(was.get("methods", [])) - set(is_.get("methods", []))):
                broke.append(f"{mod}.{name}.{m} — method lost or resignatured")

    for mod, syms in sorted(cur.items()):
        if mod not in base:
            added.append(f"module {mod}")
        else:
            for name in sorted(set(syms) - set(base[mod])):
                added.append(f"{mod}.{name}")

    if moved:
        print(f"• {len(moved)} symbol(s) changed home — expected during a package split:")
        for m in moved[:40]:
            print(f"    ~ {m}")
        if len(moved) > 40:
            print(f"    … and {len(moved) - 40} more")
    if added:
        print(f"• {len(added)} new symbol(s)/module(s) — additive, not a break")
    if narrowed:
        print("✗ PRIVATE NAMES NO LONGER IMPORTABLE:")
        for n in narrowed:
            print(f"    - {n}")
        print("  Re-export them, or re-baseline once the listed files are the whole story.")
    if broke:
        print("✗ IMPORT SURFACE BROKEN:")
        for b in broke:
            print(f"    - {b}")
    if broke or narrowed:
        print("———— API SNAPSHOT FAIL")
        return 1
    total = sum(len(s) for s in cur.values())
    print(f"✓ import surface intact ({len(base)} modules, {total} symbols)")
    print("———— API SNAPSHOT OK")
    return 0


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "snapshot":
        snapshot()
    elif cmd == "check":
        sys.exit(check())
    else:
        sys.exit("usage: python -m scripts.api_snapshot [snapshot|check]")


if __name__ == "__main__":
    main()
