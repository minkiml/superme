"""The in-code prompt contract.

A prompt lives in a registered home, declares its layer, obeys the rules it states, and says
each rule once.

Run: PYTHONPATH=. python -m scripts.prompt_contract
"""

import ast
import collections
import re
import sys
from pathlib import Path

from superme_agent.paths import APP_DIR

PKG = APP_DIR

# Homes admitted to speak to an agent, and each one's layer. `None` means mixed — the name
# declares it.
HOMES: dict[str, str | None] = {
    "core/kernel_speech.py": None,
    "core/agent_service.py": "system",
    "core/permissions.py": "interjection",
    "core/gate_briefs.py": "interjection",
    "core/verification_library.py": "interjection",
    "core/artifacts/": "interjection",
    "core/deputy.py": "trigger",
    "core/knowledge_delta.py": "interjection",
    "core/git_layer.py": "interjection",
    "core/operational.py": "system",
    "harness/plugins/superme-dev/forge_kit/eval.py": "trigger",
    "daemon/services/deputy.py": "interjection",
}
# Agent-facing, but held to a different written contract. Not this gate's business.
GOVERNED_ELSEWHERE = ("harness/tools/",)

# Suffix → layer, for the mixed home. A prompt that matches none is unclassifiable and fails.
LAYER_BY_SUFFIX = {
    "_preamble": "system",
    "_trigger": "trigger", "_nudge": "trigger", "_notice": "trigger", "_note": "trigger",
    "_block": "trigger", "_rows": "trigger", "_table": "trigger",
}

# Rendered characters per layer, set from measured sizes. An interjection is paid once, so it
# has none.
CEILING = {"system": 3_000, "trigger": 1_500, "interjection": None}

# A prompt long enough to carry a rule. Below this it is a label or a fragment.
PROMPT_MIN = 160
# Set from the shortest real prompt found, a 143c refusal the first floor let through.
ADMISSION_MIN = 140

_YOU = re.compile(r"\b(you|your|yours)\b", re.I)
_IMPERATIVE = re.compile(r"\b(do not|don't|never|always|use|read|run|call|write|say|record)\b", re.I)
# Markup and SQL trip the second-person test on attribute names.
_NOT_PROSE = re.compile(r"(SELECT |INSERT |CREATE TABLE|<div|<span|\{%|font-family|border-radius)", re.I)
# Prose has sentences. A word list — stopwords, enum values — has none.
_SENTENCE = re.compile(r"[.!?](\s|$)")

# Style rules a prompt may state, and the pattern that proves it broke its own rule.
SELF_RULES: list[tuple[re.Pattern, re.Pattern, str]] = [
    (re.compile(r"no em[- ]dash", re.I), re.compile(r"—"), "em dash"),
    (re.compile(r"no semicolons?", re.I), re.compile(r";"), "semicolon"),
    (re.compile(r"no colons? mid[- ]sentence", re.I), re.compile(r"[a-z]: [a-z]"), "mid-sentence colon"),
    (re.compile(r"\b(concise|plain words|short sentences)\b", re.I),
     re.compile(r"\b(absolutely|extremely|very|really|truly)\s+\w", re.I), "stacked intensifier"),
]

# Deliberate sharing. Each entry is a reviewed decision, not a silenced warning: the frame states
# the rule, the interjection catches an agent that broke it anyway.
DUPLICATION_ALLOWED: set[tuple[str, ...]] = {
    # Delivered by git itself and by the permission layer. An agent may meet either alone.
    ("core/git_layer.py::_COMMIT_MSG_HOOK", "core/permissions.py::_NO_VERIFY_NUDGE"),
    # A vetter that learns it cannot write only on being refused has already planned to write.
    ("core/kernel_speech.py::work_item_preamble", "core/permissions.py::VET_READONLY_NUDGE"),
    # Named up front or the shell reaches for `$TMPDIR`, is refused, and abandons the work.
    ("core/kernel_speech.py::work_item_preamble", "core/permissions.py::_BASH_SCRATCH_HINT"),
    # Read-only shapes the whole session, so it cannot wait for the first refused write.
    ("core/kernel_speech.py::diagnosis_preamble", "core/permissions.py::_READONLY_SESSION_NUDGE"),
}
# Below this, a shared run is tool-call syntax or a quoted identifier rather than a repeated rule.
DUP_MIN_RUNS = 3
DUP_WORDS = 9

FAILED: list[str] = []


def fail(item: str, rule: str, detail: str = "") -> None:
    FAILED.append(f"{item}: {rule}" + (f" — {detail}" if detail else ""))


def _docstrings(tree: ast.AST) -> set[int]:
    """Every docstring node in the tree, by identity — they document code, not agents."""
    out = set()
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            out.add(id(body[0].value))
    return out


SEP = "\x00"


def _literals(node: ast.AST, skip: set[int]) -> str:
    """The text an author wrote, joined. Interpolated data is the caller's, never the author's."""
    return SEP.join(c.value for c in ast.walk(node)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and id(c) not in skip)


def _rel(p: Path) -> str:
    return str(p.relative_to(PKG)).replace("\\", "/")


def _in(rel: str, homes) -> bool:
    return any(rel == h or rel.startswith(h) for h in homes)


def prompt_units() -> dict[str, tuple[str, str]]:
    """Every authored prompt in a registered home → (layer, text).

    One function is one prompt, however many ways it renders."""
    units: dict[str, tuple[str, str]] = {}
    for rel, home_layer in HOMES.items():
        paths = sorted((PKG / rel).glob("*.py")) if rel.endswith("/") else [PKG / rel]
        for p in (q for q in paths if q.exists()):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            skip = _docstrings(tree)
            # A class body holds prompts too, and its methods are the same kind of author.
            top = [n for n in tree.body if not isinstance(n, ast.ClassDef)]
            top += [c for n in tree.body if isinstance(n, ast.ClassDef) for c in n.body]
            for n in top:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = n.name
                elif isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
                    name = n.targets[0].id
                elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                    name = n.target.id
                else:
                    continue
                # A dict of prompts holds alternatives, and a turn is sent exactly one.
                value = n.value if isinstance(n, (ast.Assign, ast.AnnAssign)) else None
                if isinstance(value, ast.Dict):
                    for k, v in zip(value.keys, value.values):
                        label = k.value if isinstance(k, ast.Constant) else "?"
                        _admit(units, f"{_rel(p)}::{name}[{label}]", home_layer, name,
                               _literals(v, skip))
                    continue
                _admit(units, f"{_rel(p)}::{name}", home_layer, name, _literals(n, skip))
    return units


def _admit(units: dict, key: str, home_layer: str | None, name: str, text: str) -> None:
    """Keep one candidate if it is a prompt at all, with the layer its home or its name declares."""
    # A home holds data as well as prompts. Prose has sentences; a word list does not.
    if len(text) < PROMPT_MIN or not _SENTENCE.search(text) or _NOT_PROSE.search(text):
        return
    layer = home_layer
    if layer is None:
        if name.startswith("_"):
            return
        layer = next((v for k, v in LAYER_BY_SUFFIX.items() if name.endswith(k)), None)
    units[key] = (layer, text)




def check_admission() -> None:
    """Agent-addressed text outside every registered home — a prompt nothing governs."""
    for p in sorted(PKG.rglob("*.py")):
        rel = _rel(p)
        if _in(rel, HOMES) or _in(rel, GOVERNED_ELSEWHERE):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        skip = _docstrings(tree)
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
                continue
            v = n.value
            if len(v) < ADMISSION_MIN or id(n) in skip:
                continue
            if _NOT_PROSE.search(v) or not _SENTENCE.search(v):
                continue
            if len(_YOU.findall(v)) >= 2 and _IMPERATIVE.search(v):
                fail(f"{rel}:{n.lineno}", "agent-addressed text in an ungoverned home",
                     f"{len(v)}c — move it to a home in HOMES, or register the home")


def check_layers(units) -> None:
    """A prompt whose layer cannot be read is edited to the wrong bar."""
    for key, (layer, _) in sorted(units.items()):
        if layer is None:
            fail(key, "no declared layer",
                 f"name it for one of {', '.join(sorted(set(LAYER_BY_SUFFIX.values())))}")


# A branching prompt charges for every branch on paper and ships one, so measure a rendering.
REGISTRY_LAYER = {"preamble": "system", "trigger": "trigger", "assembler": "trigger"}


def rendered() -> dict[str, str]:
    """Every kernel prompt as a turn sends it, keyed `<layer>.<name>…` by the baseline."""
    from scripts.test_thread3 import render_registry
    return render_registry()


def payload_free(text: str) -> str:
    """`text` minus any verbatim artifact template it carries.

    The ceilings cap AUTHORED prose. A template a trigger injects so the agent need not spend a
    round trip fetching it is data, governed by its own file, and charging the trigger for it would
    price the saving as a breach."""
    from superme_agent.core import kernel_speech as _ks
    for phase in _ks._REPORT_TEMPLATE_PHASES:
        block = _ks.report_template_block(phase)
        if block and block in text:
            text = text.replace(block, "")
    return text


def check_ceilings(units, registry) -> None:
    """Size against the layer's ceiling — rendered where a rendering exists, authored otherwise."""
    for key, text in sorted(registry.items()):
        layer = REGISTRY_LAYER.get(key.split(".")[0])
        cap = CEILING.get(layer)
        prose = payload_free(text)
        if cap and len(prose) > cap:
            fail(key, f"over the {layer} ceiling", f"{len(prose)}c rendered prose, cap {cap}")
    for key, (layer, text) in sorted(units.items()):
        if key.startswith("core/kernel_speech.py"):
            continue
        cap = CEILING.get(layer)
        if cap and len(text) > cap:
            fail(key, f"over the {layer} ceiling", f"{len(text)}c authored, cap {cap}")


def check_self_consistency(units) -> None:
    """A prompt that states a style rule and breaks it teaches the breach, not the rule."""
    for key, (_, text) in sorted(units.items()):
        for states, breaks, label in SELF_RULES:
            if states.search(text) and (m := breaks.search(text)):
                fail(key, f"states a rule it breaks: {label}",
                     f"{len(breaks.findall(text))} in its own text (e.g. {m.group(0)!r})")


def _runs(text: str) -> set[str]:
    w = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    return {" ".join(w[i:i + DUP_WORDS]) for i in range(len(w) - DUP_WORDS + 1)}


def check_duplication(units) -> None:
    """One rule, one prompt. A rule in two prompts drifts in one, and neither reads as stale."""
    index = collections.defaultdict(set)
    for key, (_, text) in units.items():
        for run in _runs(text):
            index[run].add(key)
    groups = collections.defaultdict(list)
    for run, keys in index.items():
        if len(keys) > 1:
            groups[tuple(sorted(keys))].append(run)
    for keys, runs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(runs) < DUP_MIN_RUNS or keys in DUPLICATION_ALLOWED:
            continue
        longest = sorted(runs, key=len)[-1]
        fail(" + ".join(keys), f"one rule stated in {len(keys)} prompts",
             f"{len(runs)} shared runs, e.g. \"{longest[:70]}…\"")


# An injected template rides EVERY turn, so it only beats a one-off read while it stays small.
# Break-even is roughly template x turns-before-it-is-needed vs one turn's context; 4,000c keeps a
# wide margin at the ~15-turn mark where reports get written.
TEMPLATE_CEILING = 4_000


def check_injected_templates() -> None:
    """A template the ceilings excuse must stay small enough to be worth injecting."""
    from superme_agent.core import artifacts as _arts
    from superme_agent.core import kernel_speech as _ks
    for phase in _ks._REPORT_TEMPLATE_PHASES:
        body = _arts.skill_template(f"report-{phase}")
        if len(body) > TEMPLATE_CEILING:
            fail(f"template.report-{phase}", "too big to inject into every turn",
                 f"{len(body)}c, cap {TEMPLATE_CEILING} — read it in the skill instead")


def main() -> None:
    units = prompt_units()
    registry = rendered()
    check_admission()
    check_layers(units)
    check_ceilings(units, registry)
    check_self_consistency(units)
    check_duplication(units)
    check_injected_templates()

    by_layer = collections.Counter(layer for layer, _ in units.values())
    print(f"  {len(units)} prompts in {len(HOMES)} homes — "
          + " · ".join(f"{n} {k}" for k, n in sorted(by_layer.items()) if k))
    print()
    if FAILED:
        print(f"✗ PROMPT CONTRACT — {len(FAILED)} finding(s):")
        for line in FAILED:
            print(f"    - {line}")
        sys.exit(1)
    print(f"✓ every prompt on contract ({len(units)} prompts)")


if __name__ == "__main__":
    main()
