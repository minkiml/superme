"""Every skill's writing contract, plus the join no skill file can check about itself.

A description routes. A body names only strings that exist. The tools it instructs are the tools
its run mounts.

Run: PYTHONPATH=. python -m scripts.skill_contract
"""

import re
import sys
from pathlib import Path

import superme_agent.core  # noqa: F401   the package must be warm; dev_tools imports back in
from superme_agent.core.operational import parse_frontmatter
from superme_agent.harness.tools.dev_tools.scopes import TOOL_SCOPES
from superme_agent.paths import ASSET_DIR, DEV_PLUGIN_DIR, PLUGINS_DIR, SELF_FILE

MAX_DESC = 1024
# The judge reads from a throwaway cwd, so the shelf item cannot be the standard's only home.
STANDARD_ASSET = ASSET_DIR / "authoring" / "skill-authoring.md"
SKILL_PRINCIPLES = DEV_PLUGIN_DIR / "forge_kit" / "references" / "principle-for-skills.md"

# Which scopes each skill runs under. Nothing else compares a skill's tools to its mount.
SKILL_SCOPES: dict[str, tuple[str, ...]] = {
    "triage": ("triage",),
    "plan": ("plan",),
    "build": ("build",),
    "vet": ("vet",),
    "review": ("review",),
    "close": ("close",),
    "investigate": ("investigate",),
    "itemize": ("itemize",),
    "checkpoint": ("build", "investigate", "handoff"),
    "create-inbox-item": ("general", "onboarding", "triage", "build", "close"),
    "project-init": ("onboarding",),
    "retrofit": ("onboarding",),
    "forge-skill": ("write",),
    "forge-agent": ("write",),
    "forge-constitution": ("write",),
    "handoff": (),                      # core mode: no dev tools mounted at all
}

ALL_TOOLS = {n for names in TOOL_SCOPES.values() for n in names}

_FM = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
_USE_WHEN = re.compile(r"\bUse (?:it |this )?(?:when|for)\b")
_NEGATIVE = re.compile(r"\b(?:not for|don't use|do not use|never for|never use)\b", re.I)
# One line only: a multi-line span is prose in a fence, never a path.
_BACKTICKED = re.compile(r"`([^`\n]{1,200})`")
_PLACEHOLDER = re.compile(r"[<>*]")
# A voice rule already resident in SELF.md, restated inside a skill, is a copy that will drift.
_VOICE = re.compile(r"^#+ .*response style", re.I | re.M)

FAILED: list[str] = []
PASSED = 0


def fail(skill: str, rule: str, detail: str = "") -> None:
    FAILED.append(f"{skill}: {rule}" + (f" — {detail}" if detail else ""))


def standard_body() -> str:
    """The shelf item minus its frontmatter, which is shelf state rather than the standard."""
    return parse_frontmatter(STANDARD_ASSET.read_text(encoding="utf-8"))[1].lstrip("\n")


def frontmatter(text: str) -> dict[str, str]:
    """The skill's frontmatter as flat key → value. Nested YAML is not used here."""
    if not (m := _FM.match(text)):
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def check_frontmatter(fm: dict, folder: str) -> list[tuple[str, str]]:
    """The routing decision: the only part of a skill resident before it loads."""
    bad = []
    if (fm.get("name") or folder) != folder:
        bad.append(("name must equal the folder", f"{fm.get('name')} vs {folder}"))
    desc = fm.get("description", "")
    if not desc:
        bad.append(("no description", "the whole routing decision"))
        return bad
    if len(desc) > MAX_DESC:
        bad.append(("description over the cap", f"{len(desc)} > {MAX_DESC}"))
    if "<" in desc or ">" in desc:
        bad.append(("angle brackets in the description", "they fail the plugin loader"))
    # A silent skill is never routed to, so a trigger it can't be chosen by is dead text.
    if (fm.get("access") or "").lower() != "silent":
        if not _USE_WHEN.search(desc):
            bad.append(("no trigger clause", "say `Use when …`"))
        if not _NEGATIVE.search(desc):
            bad.append(("no negative trigger", "say what it is NOT for, naming the sibling"))
    return bad


def check_body(path: Path, text: str, repo: Path) -> list[tuple[str, str]]:
    """Strings a body names must exist, and rules it states must live nowhere else."""
    bad = []
    if m := _DATE.search(text):
        bad.append(("a date in a skill", m.group(0)))
    if m := _VOICE.search(text):
        bad.append(("restates the voice rule", f"{m.group(0).strip()} — SELF.md owns it"))
    for ref in _BACKTICKED.findall(text):
        ref = ref.strip()
        if _PLACEHOLDER.search(ref) or ref.startswith(("http", "$", "~")):
            continue
        # A bundle-relative pointer is the only kind whose target this gate can locate.
        if not re.search(r"(?:^|/)(?:references|templates|agents|scripts|assets)/[^/]+\.\w+$", ref):
            # A real FILE the agent will copy verbatim. A bare folder name is a category, not
            # a citation.
            if ref.count("/") >= 1 and Path(ref).suffix and not ref.startswith("/") \
                    and (repo / ref).is_file():
                bad.append(("names this repo's own file", ref))
            continue
        if not (path.parent / ref).resolve().exists():
            bad.append(("bundle path does not resolve", ref))
    return bad


def check_tools(skill: str, body: str) -> list[tuple[str, str]]:
    """The join: what the body instructs against what the run mounts.

    A shared scope mounts for a SESSION, so its tools need not appear in one skill."""
    scopes = SKILL_SCOPES.get(skill)
    if scopes is None:
        return [("not in SKILL_SCOPES", "add it, or the join goes unchecked")]
    if not scopes:
        return []
    named = {t for t in re.findall(r"(?:mcp__dev__)?([a-z_]{4,})", body) if t in ALL_TOOLS}
    union = set().union(*(set(TOOL_SCOPES[s]) for s in scopes))
    bad = [("instructs a tool no scope mounts", t) for t in sorted(named - union)]
    if scopes == (skill,):   # a phase scope exists for this skill and nothing else
        bad += [("mounts a tool the body never names", t)
                for t in sorted(set(TOOL_SCOPES[skill]) - named)]
    return bad


# A relative import needs a parent package, which a file run by path does not have.
_RELATIVE_IMPORT = re.compile(r"(?m)^\s*from\s+\.")


def check_plugin_scripts() -> None:
    """A plugin's own `.py` is launched by path, so it may only import stdlib and its neighbours."""
    for f in sorted(PLUGINS_DIR.rglob("*.py")):
        if m := _RELATIVE_IMPORT.search(f.read_text(encoding="utf-8")):
            fail(f"{f.relative_to(PLUGINS_DIR)}", "relative import in a file run by path",
                 f"{m.group(0).strip()}… inline it, or use the standard library")


def main() -> None:
    global PASSED
    if "--sync" in sys.argv:
        SKILL_PRINCIPLES.write_text(standard_body(), encoding="utf-8")
        print(f"✓ {SKILL_PRINCIPLES.name} rewritten from {STANDARD_ASSET.name}")
        return
    repo = Path(__file__).resolve().parent.parent
    assert SELF_FILE.is_file(), "SELF.md is the voice rule's home and must exist"
    check_plugin_scripts()
    # Two tracked copies of one rule, so the check runs on every machine.
    if SKILL_PRINCIPLES.read_text(encoding="utf-8") != standard_body():
        fail("(standard)", "the forge kit's copy has drifted from the shelf item",
             "run: PYTHONPATH=. python -m scripts.skill_contract --sync")
    files = sorted(PLUGINS_DIR.rglob("SKILL.md"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        folder = f.parent.name
        # Instruction only: a description names a sibling's tool to route AWAY from it.
        bundle = "\n".join(_FM.sub("", p.read_text(encoding="utf-8"))
                           for p in sorted(f.parent.rglob("*.md")))
        problems = (check_frontmatter(frontmatter(text), folder)
                    + check_body(f, text, repo)
                    + check_tools(folder, bundle))
        for p in sorted(f.parent.rglob("*.md")):
            if p != f:
                problems += check_body(p, p.read_text(encoding="utf-8"), repo)
        for rule, detail in problems:
            fail(folder, rule, detail)
        if not problems:
            PASSED += 1
            print(f"  ok  {folder}")
    print()
    if FAILED:
        print(f"✗ SKILL CONTRACT — {len(files) - PASSED} of {len(files)} skill(s) off contract, "
              f"{len(FAILED)} finding(s):")
        for line in FAILED:
            print(f"    - {line}")
        sys.exit(1)
    print(f"✓ every skill on contract ({PASSED} skills)")


if __name__ == "__main__":
    main()
