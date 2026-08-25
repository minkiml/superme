"""A session sees only the tools its work needs.

`dev_tools.TOOL_SCOPES` is the one table. Each recorder belongs to one phase, diagnosis writes
nothing, and every skill's named tools sit inside its own scope.

Run: PYTHONPATH=. python scripts/test_tool_scopes.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superme_agent.harness.tools.dev_tools import (          # noqa: E402
    DEV_TOOLS, TOOL_SCOPES, dev_tool_specs,
)

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "superme_agent/harness/plugins/superme-dev/skills"

PASS = 0


def ok(label: str, cond, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {label} {detail}"
    PASS += 1
    print(f"  ok - {label}")


# Editing a row here is the deliberate act of widening a scope, never a side effect.
EXPECTED: dict[str, set[str]] = {
    "general": {"read_dev_log", "read_inbox", "read_run", "read_candidates", "read_proposals",
                "create_inbox_item", "append_inbox_item", "push_inbox_item", "itemize_and_launch"},
    "onboarding": {"read_dev_log", "read_inbox", "create_inbox_item", "append_inbox_item",
                   "itemize_and_launch"},
    "diagnosis": {"read_dev_log", "read_run"},
    # Every scope that can ask the owner reads decisions first, or it asks what was already
    # settled.
    "triage": {"scaffold_artifact", "set_triage_classification", "create_inbox_item",
               "file_phase_report", "read_decisions"},
    "plan": {"scaffold_artifact", "check_plan_commands", "read_verification_library",
             "file_plan_report", "revise_plan", "read_dev_log"},
    "build": {"record_validation", "request_authorization", "sync_from_anchor_branch",
              "write_checkpoint",
              "create_inbox_item", "file_phase_report"},
    "vet": {"record_verification", "record_diagnosis", "record_lens", "nominate_check",
            "read_verification_library", "file_vet_report"},
    "review": {"scaffold_artifact", "file_phase_report", "read_decisions"},
    "close": {"apply_knowledge_edits", "read_verification_library", "create_inbox_item",
              "file_phase_report"},
    "investigate": {"scaffold_artifact", "write_checkpoint", "file_phase_report",
                    "read_decisions"},
    # Not here: it belongs to the chat scopes, where the owner approves each call.
    "itemize": {"read_inbox", "read_dev_log", "create_inbox_item", "read_research_proposals"},
    "deputy": {"read_dev_log", "read_run"},
    "handoff": {"write_checkpoint"},
    "resolve": {"read_dev_log"},
    "capture": {"file_candidate", "read_dev_log"},
    "distill": {"read_candidates", "read_proposals", "propose_learning", "merge_into_proposal",
                "drop_candidates"},
    "write": {"stage_artifact"},
}

PHASES = ("triage", "plan", "build", "vet", "review", "close", "investigate")


def names(scope: str) -> set[str]:
    return {t.name for t in dev_tool_specs(scope)}


def test_table() -> None:
    print("the table — every scope resolves to exactly its declared set")
    ok("no scope was added or dropped without updating this suite",
       set(TOOL_SCOPES) == set(EXPECTED),
       str(set(TOOL_SCOPES) ^ set(EXPECTED)))
    for scope, expect in EXPECTED.items():
        ok(f"{scope} mounts {len(expect)} tool(s)", names(scope) == expect,
           str(names(scope) ^ expect))
    ok("an unknown scope raises rather than falling back to everything",
       _raises(lambda: dev_tool_specs("veting")))
    ok("no scope mounts the full catalogue",
       all(len(v) < len(DEV_TOOLS) for v in TOOL_SCOPES.values()))


def _raises(fn) -> bool:
    try:
        fn()
    except KeyError:
        return True
    return False


def test_invariants() -> None:
    print("invariants — what the table exists to guarantee")
    # One recorder, one phase. Build grading its own work is the self-grading vet exists to catch.
    for tool, owner in (("record_validation", "build"), ("record_verification", "vet"),
                        ("record_diagnosis", "vet"), ("record_lens", "vet"),
                        ("file_vet_report", "vet"), ("file_plan_report", "plan"),
                        ("revise_plan", "plan"), ("apply_knowledge_edits", "close"),
                        ("request_authorization", "build"),
                        ("set_triage_classification", "triage")):
        holders = [s for s in PHASES if tool in names(s)]
        ok(f"{tool} belongs to {owner} alone", holders == [owner], str(holders))
    # A diagnosis session is read-only BY DESIGN — the preamble says so; now the surface does too.
    ok("diagnosis mounts reads only",
       all(n.startswith("read_") for n in names("diagnosis")), str(names("diagnosis")))
    # An unbound chat has no item to point the item pens at; they would only ever refuse.
    item_pens = {"scaffold_artifact", "set_triage_classification", "record_validation",
                 "record_verification", "record_diagnosis", "record_lens", "file_plan_report",
                 "file_vet_report", "revise_plan", "apply_knowledge_edits", "write_checkpoint",
                 "sync_from_anchor_branch", "request_authorization", "check_plan_commands",
                 "nominate_check"}
    for scope in ("general", "onboarding", "diagnosis"):
        ok(f"{scope} holds no item pen", not (names(scope) & item_pens),
           str(names(scope) & item_pens))
    # The learning pens stay out of every human-facing surface (the pre-existing rule, now per-scope).
    pens = {"file_candidate", "propose_learning", "merge_into_proposal", "drop_candidates",
            "stage_artifact"}
    leaked = {s for s in TOOL_SCOPES if s not in ("capture", "distill", "write")
              and (names(s) & pens)}
    ok("learning pens reach only the learning runs", not leaked, str(leaked))


# A skill naming a tool its session cannot see is an instruction that always fails.
OTHER_SERVERS = {"report_completion", "submit_gate_verdict"}
ALL_TOOLS = {t.name for t in DEV_TOOLS}


def test_skills_name_only_visible_tools() -> None:
    print("skills — every tool a phase skill names is in that phase's scope")
    for scope in PHASES:
        path = SKILLS / scope / "SKILL.md"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        named = {t for t in ALL_TOOLS if re.search(rf"`{t}\b|\b{t}\(", text)}
        missing = named - names(scope) - OTHER_SERVERS
        ok(f"{scope}/SKILL.md names {len(named)} dev tool(s), all visible", not missing,
           str(missing))


# A computed scope is listed so it is at least accounted for.
COMPUTED = ("scope=scope",          # the two dev_mcp helpers, forwarding their caller's choice
            "scope=skill",          # the generic intake runner: the skill it fires IS the scope
            "scope=tool_scope",     # ws.py: session kind, or the bound item's current phase
            'scope=str(live_item.get("phase") or phase)')   # deputy send-back, post review→plan flip


def _call_at(text: str, start: int) -> str:
    """The whole call expression starting at `start`, paren-matched.

    A regex misses sites whose scope is itself a call, and an unseen site cannot be checked."""
    depth, i = 0, text.index("(", start)
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return " ".join(text[start:j + 1].split())
    raise AssertionError(f"unbalanced call at {start}")


def _arg_after(call: str, key: str) -> str:
    """The argument value following `key`, ending at the first top-level comma.

    A scope that is itself a call keeps its parens, and a trailing comment never joins the value."""
    rest = call[call.index(key) + len(key):]
    depth = 0
    for i, ch in enumerate(rest):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return rest[:i].strip()
            depth -= 1
        elif ch == "," and depth == 0:
            return rest[:i].strip()
    return rest.strip()


def test_call_sites() -> None:
    print("call sites — every mount passes a scope the table knows")
    sites: list[tuple[str, str]] = []
    for p in (ROOT / "superme_agent").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        rel = str(p.relative_to(ROOT))
        if rel.endswith("harness/tools/dev_tools.py"):
            continue
        assert "learning=True" not in text, f"FAIL: {rel} still passes the retired learning= kwarg"
        for m in re.finditer(r"\b(?:make_dev_mcp_server|dev_mcp)\s*\(", text):
            if text[:m.start()].rstrip().endswith("def"):
                continue                       # the helper's own definition
            call = _call_at(text, m.start())
            assert "scope=" in call, f"FAIL: {rel} mounts the dev server with no scope: {call[:90]}"
            sites.append((rel, f"scope={_arg_after(call, 'scope=')}"))
    # A new mount lands here or this fails, which is the point of counting rather than sampling.
    ok("every dev-server mount is accounted for", len(sites) == 14, f"{len(sites)} sites")
    for rel, expr in sites:
        literal = re.fullmatch(r'scope="([a-z]+)"', expr)
        if literal:
            ok(f"{rel}: {literal.group(1)}", literal.group(1) in TOOL_SCOPES)
        else:
            ok(f"{rel}: {expr} (computed)", expr in COMPUTED, expr)


def main() -> None:
    test_table()
    test_invariants()
    test_skills_name_only_visible_tools()
    test_call_sites()
    print(f"\nALL GREEN — {PASS} checks passed.")


if __name__ == "__main__":
    main()


def test_a_scoped_tool_is_not_refused_by_policy() -> None:
    """A tool can be registered, scoped to a phase, and still refused at the callback.

    Nothing joins the two lists, so a background run is denied with nobody to ask."""
    from superme_agent.harness.policy import is_safe
    from superme_agent.harness.tools.dev_tools import TOOL_SCOPES

    BACKGROUND = ("triage", "plan", "build", "vet", "review", "close", "investigate",
                  "deputy", "handoff", "resolve", "capture", "distill", "write")
    KNOWN_GAP = {"itemize_and_launch"}
    refused = sorted({t for scope in BACKGROUND for t in TOOL_SCOPES.get(scope, ())
                      if not is_safe(f"mcp__dev__{t}", {})} - KNOWN_GAP)
    ok(f"every tool a background phase can call is one the policy allows — refused: {refused}",
       not refused)


test_a_scoped_tool_is_not_refused_by_policy()
