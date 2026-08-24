"""The writing contract for every in-process tool an agent routes on.

A description says four things in one order: what it does, when to use it, when not to, what it
returns.

Run: PYTHONPATH=. python -m scripts.tool_descriptions
"""

import re
import sys

import superme_agent.core  # noqa: F401   the package must be warm; base_tools imports back in
from superme_agent.harness.tools.base_tools import BASE_TOOLS
from superme_agent.harness.tools.dev_tools import DEV_TOOLS
from superme_agent.harness.tools.run_tools import (SUBMIT_GATE_VERDICT_TOOL,
                                                   REPORT_COMPLETION_TOOL)

MAX_WORDS = 60

# Caps that name a real thing, not caps used to shout. Backticked spans are exempt already.
CAPS_ALLOWED = {"PRD"}

# A tool name leads with one of these. A noun leaves the agent guessing at the operation.
VERBS = {
    "read", "list", "search", "check", "load", "create", "file", "append", "push", "record",
    "write", "apply", "revise", "merge", "drop", "stage", "propose", "nominate", "request",
    "set", "scaffold", "sync", "adopt", "itemize", "launch", "report", "submit", "declare",
    "fold", "bank", "run", "verify",
}

_BACKTICKED = re.compile(r"`[^`]*`")
_CAPS = re.compile(r"\b[A-Z]{2,}\b")
_WHEN = re.compile(r"\b(?:Use|Call) it\b")
_NOT = re.compile(r"\bDo not (?:use|call) it\b")
_RETURNS = re.compile(r"(?:\A|\. )Returns ")
_PAREN = re.compile(r"\(([^)]*)\)")

FAILED: list[str] = []
PASSED = 0


def fail(tool: str, rule: str, detail: str = "") -> None:
    FAILED.append(f"{tool}: {rule}" + (f" — {detail}" if detail else ""))


def check_name(name: str) -> list[str]:
    """A name is snake_case and leads with the operation it performs."""
    bad = []
    if name != name.lower() or " " in name or "-" in name:
        bad.append(("snake_case", name))
    if name.split("_")[0] not in VERBS:
        bad.append(("leads with an action verb", name.split("_")[0]))
    return bad


def check_description(desc: str) -> list[str]:
    """The four moves, and the bans that keep them readable."""
    bad = []
    plain = _BACKTICKED.sub(" ", desc)
    words = len(desc.split())
    if words > MAX_WORDS:
        bad.append(("over the word cap", f"{words} > {MAX_WORDS}"))
    if "—" in desc or "–" in desc:
        bad.append(("no dash clause", "a dash hides the verb; use a second sentence"))
    shouted = sorted({c for c in _CAPS.findall(plain) if c not in CAPS_ALLOWED})
    if shouted:
        bad.append(("no caps for emphasis", ", ".join(shouted)))
    if not _WHEN.search(desc):
        bad.append(("no when clause", "say `Use it when …` or `Call it …`"))
    if not _NOT.search(desc):
        bad.append(("no boundary", "say `Do not use it to …`, naming the right tool"))
    if not _RETURNS.search(desc):
        bad.append(("does not say what it returns", "add a `Returns …` sentence"))
    for inner in _PAREN.findall(plain):
        if len(inner.split()) > 8:
            bad.append(("parenthesis carries a parameter's doc", inner[:40]))
    return bad


def main() -> None:
    global PASSED
    specs = BASE_TOOLS + DEV_TOOLS + [REPORT_COMPLETION_TOOL, SUBMIT_GATE_VERDICT_TOOL]
    for spec in specs:
        problems = check_name(spec.name) + check_description(spec.description)
        for rule, detail in problems:
            fail(spec.name, rule, detail)
        if not problems:
            PASSED += 1
            print(f"  ok  {spec.name}")
    print()
    if FAILED:
        print(f"✗ TOOL DESCRIPTIONS — {len(specs) - PASSED} of {len(specs)} tool(s) off contract, "
              f"{len(FAILED)} finding(s):")
        for line in FAILED:
            print(f"    - {line}")
        sys.exit(1)
    print(f"✓ every tool description on contract ({PASSED} tools)")


if __name__ == "__main__":
    main()
