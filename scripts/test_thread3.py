"""The run protocol and the kernel-speech registry, both directions.

A trigger carries an item id and its payload, nothing more. Every registry entry is rendered
against a byte baseline, so editing one is a deliberate re-baseline.

Run: PYTHONPATH=. python scripts/test_thread3.py
"""

import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from scripts.sources import src

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superme_agent.core import artifacts as _arts
from superme_agent.core import kernel_speech as KS
from superme_agent.harness.tools import run_tools as RT

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "superme_agent/harness/plugins/superme-dev/skills"
BASELINE = Path(__file__).resolve().parent / "prompt_baseline.json"

PASS = 0


def ok(label: str, cond, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {label} {detail}"
    PASS += 1
    print(f"  ok - {label}")


# Narration that must not exist anywhere agent-facing anymore.
NARRATION = ("HEADLESS", "no human", "never ask questions", "autonomous mode",
             "autonomous-run instructions", "autonomous headless")


def _clean(label: str, text: str) -> None:
    for phrase in NARRATION:
        assert phrase not in text, f"FAIL: {label} still carries narration {phrase!r}"


# ------------------------------------------------------------------ the run protocol
def test_run_protocol() -> None:
    print("run protocol — Current-focus background variant; fence contracts retired")
    bg = KS.work_item_preamble("it1", {"title": "T", "phase": "plan",
                                       "kind": "implementation"}, "/dir", interactive=False)
    fg = KS.work_item_preamble("it1", {"title": "T", "phase": "plan",
                                       "kind": "implementation"}, "/dir")
    # Pin the channel and its routes, never the sentence introducing them.
    ok("background variant carries the protocol (never-page · assumptions · authorization · ending)",
       "**Run protocol:**" in bg and "## Assumptions" in bg and "request_authorization" in bg
       and "report_completion" in bg)
    ok("interactive variant carries none of it",
       "Run protocol" not in fg and "report_completion" not in fg)
    _clean("background variant", bg)
    ok("protocol carries no narration", True)
    ok("fence contracts + orient assembler retired",
       not hasattr(KS, "BACKGROUND_RUN_CONTRACT") and not hasattr(KS, "DEPUTY_VERDICT_CONTRACT")
       and not hasattr(KS, "render_orient_block"))
    ok("no completion-report fence anywhere in the protocol", "completion-report" not in bg)

    # The outcome vocabulary lives in the TOOL's schema (the contract's new home), not in prose.
    from superme_agent.harness.tools.registry import _render_schema
    schema = _render_schema(RT.ReportCompletionArgs)
    enum = schema["properties"]["machine"]["properties"]["outcome"]["enum"]
    ok("tool schema advertises exactly RUN_OUTCOMES", tuple(enum) == RT.RUN_OUTCOMES, str(enum))
    ok("deprecated approval_required is gone", "approval_required" not in RT.RUN_OUTCOMES)

    # agent_service: the retired `background` kwarg is gone from the turn surface.
    import inspect
    from superme_agent.core.agent_service import AgentService
    ok("run_turn has no background kwarg",
       "background" not in inspect.signature(AgentService.run_turn).parameters)
    ok("assemble seam has no background kwarg",
       "background" not in inspect.signature(AgentService.assemble_system_append).parameters)


# ------------------------------------------------------------------ thin triggers
def test_triggers() -> None:
    print("triggers — task delta only")
    cases = {
        "intake(plan)": KS.intake_trigger("plan", "it1", "Title"),
        "intake(triage)": KS.intake_trigger("triage", "it1", "Title"),
        "vet": KS.vet_trigger("it1", "Title"),
        "build_loop": KS.build_loop_trigger("it1", "Title", 3, "REPORT-BODY"),
        "phase_feedback": KS.phase_feedback_trigger("it1", "Title", "plan", "plan",
                                                    "the feedback", "DIGEST"),
        "close": KS.close_trigger("it1", "Title"),
        "resolve": KS.resolve_trigger("/wt", "it1", ["a.py"]),
        "distill": KS.distill_trigger(),
        "write": KS.write_trigger({"id": 1, "output_form": "skill", "target_scope": "repo_dev",
                                   "title": "T"}, slug="s", workspace="/ws",
                                  existing_path=None, forge_kit="/fk"),
        "capture": KS.capture_trigger("SLICE-TEXT", "STEER"),
    }
    for name, text in cases.items():
        _clean(f"{name} trigger", text)
        assert "completion-report" not in text, f"FAIL: {name} inlines the completion contract"
    ok("no trigger carries narration or the inlined contract", True)
    ok("intake names skill + item", "superme-dev:plan" in cases["intake(plan)"]
       and "`it1`" in cases["intake(plan)"])
    ok("vet names skill + item", "superme-dev:vet" in cases["vet"] and "`it1`" in cases["vet"])
    ok("build hop carries the failed cycle's report verbatim",
       "build-vet-3.md" in cases["build_loop"] and "REPORT-BODY" in cases["build_loop"])
    ok("phase-feedback re-run carries the verbatim feedback + digest + one action line",
       "> the feedback" in cases["phase_feedback"] and "DIGEST" in cases["phase_feedback"]
       and "superme-dev:plan" in cases["phase_feedback"])
    ok("close trigger names the close skill, the knowledge write, and mechanical clearance",
       "superme-dev:close" in cases["close"] and "apply_knowledge_edits" in cases["close"]
       and "the kernel clears the item" in cases["close"])
    # The kernel counted before firing, so close must not spend a call confirming a known answer.
    ok("close is told NOT to open the verification library when vet nominated nothing",
       "do NOT call" in KS.close_trigger("it1", "T", nominated=0))
    ok("…and IS told to open it when vet nominated something",
       "read_verification_library" in (nom := KS.close_trigger("it1", "T", nominated=2))
       and "do NOT call" not in nom)
    ok("close is handed the merge commit rather than left to hunt for it",
       "abc1234" in KS.close_trigger("it1", "T", merge_commit="abc1234"))
    # Fetching the template cost a round trip in every measured run of these phases.
    for _ph, _fire in (("triage", KS.intake_trigger("triage", "it1", "T")),
                       ("build", KS.build_first_trigger("it1", "T")),
                       ("close", KS.close_trigger("it1", "T"))):
        ok(f"{_ph} trigger carries its report template verbatim",
           _arts.skill_template(f"report-{_ph}").rstrip() in _fire)
    ok("a phase whose report a pen derives carries no template",
       KS.report_template_block("plan") == "" and KS.report_template_block("vet") == "")
    ok("resolve keeps the conflict procedure (task policy, not narration)",
       "conflict marker" in cases["resolve"] and "Do NOT run git commands" in cases["resolve"]
       and "honoring" in cases["resolve"])
    ok("learning triggers name their agents + payloads",
       "superme-dev:distill" in cases["distill"] and "superme-dev:forge" in cases["write"]
       and "PROPOSAL #1" in cases["write"] and "superme-dev:capture" in cases["capture"]
       and "SLICE-TEXT" in cases["capture"] and "STEER" in cases["capture"])

    from superme_agent.core import sessions
    ok("replay noise prefixes match the new thin phrasing",
       sessions._is_noise({}, cases["intake(plan)"])
       and sessions._is_noise({}, cases["intake(triage)"]))


# ------------------------------------------------------------------ runners mount the sink servers
def test_runners_flip() -> None:
    print("runners — sink servers mounted, retired background kwarg gone")
    runs_src = {
        "runs": src("superme_agent/daemon/services/runs.py"),
        "loop": src("superme_agent/daemon/services/loop.py"),
        "learning": src("superme_agent/daemon/services/learning.py"),
        "deputy": src("superme_agent/daemon/services/deputy.py"),
    }
    ok("intake + feedback + close runners mount the report_completion sink",
       runs_src["runs"].count("make_run_report_server(sink)") == 4)
    ok("the completion backstop exists and re-asks through the same sink",
       "async def ensure_completion" in runs_src["runs"]
       and runs_src["runs"].count("await ensure_completion(") == 3
       and runs_src["loop"].count("await ensure_completion(") == 2)
    ok("vet + build runners mount the report_completion sink",
       runs_src["loop"].count("make_run_report_server(sink)") == 2)
    ok("deputy mounts its verdict sink",
       runs_src["deputy"].count("make_deputy_verdict_server(sink)") == 1)
    for name, text in runs_src.items():
        assert "background=True," not in text, f"FAIL: {name}.py still passes background=True to run_turn"
        assert "headless" not in text.lower(), f"FAIL: {name}.py still says headless"
        assert "parse_completion_report" not in text and "parse_deputy_verdict" not in text, \
            f"FAIL: {name}.py still parses a retired fence"
    ok("no runner passes the retired kwarg, says 'headless', or parses a fence", True)


# ------------------------------------------------------------------ skills + preamble hedge
def test_skills() -> None:
    print("skills — Background runs sections keep deltas, lose narration")
    deltas = {
        # Assert the CONCEPT is still taught: pinning the exact heading made a copy edit read as a
        # lost rule.
        "plan": ("Decisions & clarifications", "verification"),
        "vet": ("record_verification", "file_vet_report"),
        "build": ("build-vet-<n>.md", "commit"),
        "triage": ("set_triage_classification", "brief"),
    }
    for name, needles in deltas.items():
        text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        assert "headless" not in text.lower(), f"FAIL: {name} still says headless"
        section = text
        _clean(f"{name} skill", text)
        for n in needles:
            assert n.lower() in section.lower(), f"FAIL: {name} background section lost delta {n!r}"
    ok("plan/vet/build/triage retitled + deltas intact + narration gone", True)
    forge = src("superme_agent/harness/plugins/superme-dev/agents/forge.md")
    ok("forge.md lost the no-human clause",
       "no human" not in forge and "Don't stage more than once" in forge)
    hedge = KS.work_item_preamble("it1", {"phase": "plan", "kind": "implementation",
                                          "title": "T"}, "/dir")
    ok("interactive hedge is one factual line",
       "This is an interactive chat — the user is present." in hedge
       and "headless" not in hedge.lower() and "applied to that run only" not in hedge)


# ------------------------------------------------------------------ tool round-trip
def test_reader() -> None:
    print("report_completion — call → sink lockstep")
    import asyncio
    sink: dict = {}
    call = RT._report_completion(completion_sink=sink)
    r = asyncio.run(call({"machine": {"outcome": "success"},
                          "user": {"summary": "built it", "next": "owner reviews"}}))
    ok("valid call lands in the sink with legacy top-level keys",
       not r.get("is_error") and sink["report"]["outcome"] == "success"
       and sink["report"]["summary"] == "built it" and sink["report"]["next"] == "owner reviews")
    ok("result is minimal, never an echo", r["content"][0]["text"] == "ok")


# ------------------------------------------------------------------ registry snapshot
FIXTURE_DIR = Path("/tmp/superme-thread3-fixture")   # FIXED path — it appears inside rendered text
_FIXTURE_PATH = re.compile(re.escape(str(FIXTURE_DIR)) + r"[\w\\/.-]*")


def _fixture_item_dir() -> Path:
    """A deterministic item dir for the assemblers (fixed path + authored files, no timestamps)."""
    d = FIXTURE_DIR
    shutil.rmtree(d, ignore_errors=True)
    (d / "artifacts").mkdir(parents=True)
    (d / "checkpoints").mkdir()
    (d / "preliminary").mkdir()
    (d / "artifacts" / "plan.md").write_text("# Plan\n\n## Tasks\n- [x] a\n- [ ] b\n", encoding="utf-8")
    (d / "checkpoints" / "20260101T000000.md").write_text("checkpoint body\n", encoding="utf-8")
    (d / "artifacts" / "build-vet-1.md").write_text(
        "# Build⟷vet 1 — Fixture\n\n## Built\nb1\n\n## Validation\nv1\n\n## Verification\n"
        "```checks\n### 2026-01-01T00:00:00 — c1\n- how: run\n- result: bad\n- passed: false\n"
        "- fingerprint: f\n```\n\n## Cycle outcome\n"
        "\n### 2026-01-01T00:00:30 — build\n- evidence: failed\n- reason: r1\n", encoding="utf-8")
    (d / "artifacts" / "build-vet-2.md").write_text(
        "# Build⟷vet 2 — Fixture\n\n## Built\nb2\n\n## Validation\nv2\n\n## Verification\n"
        "```checks\n### 2026-01-01T00:01:00 — c1\n- how: run\n- result: ok\n- passed: true\n"
        "- fingerprint: f\n```\n\n## Cycle outcome\n"
        "\n### 2026-01-01T00:01:30 — review\n- evidence: passed\n- reason: r2\n", encoding="utf-8")
    return d


def render_registry() -> dict[str, str]:
    """Every kernel_speech entry rendered with fixture data — the snapshot payload."""
    item = {"id": "fix1", "title": "Fixture", "kind": "implementation", "phase": "build",
            "status": "active", "description": "desc", "deliverable": "d-x",
            "git_worktree": "/wt", "git_branch": "wi/fix1"}
    d = _fixture_item_dir()
    run = {"feature": "plan", "status": "done", "model": "m", "started_at": "2026-01-01",
           "item_id": "fix1"}
    events = [{"kind": "prompt", "description": "hello"},
              {"kind": "tool", "name": "Read", "description": "a.py"},
              {"kind": "result", "name": "Read", "description": "contents"},
              {"kind": "reply", "description": "did it"}]
    out = {
        "trigger.intake.plan": KS.intake_trigger("plan", "fix1", "Fixture"),
        "trigger.intake.triage": KS.intake_trigger("triage", "fix1", "Fixture"),
        "trigger.vet": KS.vet_trigger("fix1", "Fixture"),
        # A FIXED path: the wording is the contract, and a baseline carrying this machine's
        # install location would fail everywhere else.
        "trigger.vet_env_note": KS.vet_env_note("/S/vet_env.sh"),
        "trigger.build_first": KS.build_first_trigger("fix1", "Fixture"),
        "trigger.build_loop": KS.build_loop_trigger("fix1", "Fixture", 2, "REPORT"),
        "trigger.phase_feedback": KS.phase_feedback_trigger("fix1", "Fixture", "plan", "plan",
                                                            "feedback", "DIGEST"),
        "trigger.close": KS.close_trigger("fix1", "Fixture"),
        "trigger.close.nominated": KS.close_trigger("fix1", "Fixture", nominated=2),
        "trigger.resolve": KS.resolve_trigger("/wt", "fix1", ["a.py", "b.py"]),
        "trigger.distill": KS.distill_trigger(),
        "trigger.write": KS.write_trigger(
            {"id": 7, "output_form": "constitution", "target_scope": "repo_dev", "title": "T",
             "summary": "S", "body": "B", "fields": {"k": "v"},
             "clarification_answers": {"q": "a"}},
            slug="slug", workspace="/ws", existing_path="/rules.md", forge_kit="/fk"),
        "trigger.checkpoint": KS.checkpoint_trigger("fix1"),
        # The general-session twin: same skill and contract, with the write target named
        # explicitly.
        "trigger.checkpoint.session": KS.session_checkpoint_trigger(
            "/k/dev/session-memory/sess-1.md"),
        "trigger.capture": KS.capture_trigger("SLICE"),
        "trigger.capture.steered": KS.capture_trigger("SLICE", "FOCUS"),
        # `shell_cwd` mirrors the runner. A fixture that omits it snapshots a prompt nobody sends.
        "preamble.work_item.build": KS.work_item_preamble("fix1", item, str(d), shell_cwd="/wt"),
        "preamble.work_item.build.bg": KS.work_item_preamble("fix1", item, str(d),
                                                             interactive=False, shell_cwd="/wt"),
        "preamble.work_item.vet": KS.work_item_preamble("fix1", {**item, "phase": "vet"}, str(d),
                                                        shell_cwd="/wt"),
        "preamble.work_item.review.repo_root": KS.work_item_preamble(
            "fix1", {**item, "phase": "review"}, str(d), interactive=False, shell_cwd="/repo"),
        # Mirrors close.py, the one phase handed the anchor tree.
        "preamble.work_item.close": KS.work_item_preamble(
            "fix1", {**item, "phase": "close"}, str(d), interactive=False, shell_cwd="/repo",
            anchor_dir="/know/dev/general"),
        "preamble.work_item.compacted": KS.work_item_preamble(
            "fix1", item, str(d), shell_cwd="/wt",
            compacted_checkpoint=str(d / "checkpoints/20260101T000000.md")),
        "preamble.work_item.research": KS.work_item_preamble(
            "fix1", {"phase": "plan", "kind": "research", "title": "R"}, str(d)),
        "preamble.general": KS.general_preamble(),
        # No item folder to fall back on, so the banked memory is the only surviving copy.
        "preamble.general.compacted": KS.general_preamble() + KS.compaction_notice(
            "/k/dev/session-memory/sess-1.md", has_artifacts=False),
        "preamble.onboarding.init": KS.onboarding_preamble("project-init"),
        "preamble.onboarding.retrofit": KS.onboarding_preamble("retrofit"),
        "preamble.onboarding.unknown": KS.onboarding_preamble(None),
        "preamble.diagnosis": KS.diagnosis_preamble(run, 7),
        "preamble.deputy": KS.deputy_preamble("high"),
        "assembler.deputy_brief": KS.deputy_brief_block(
            "fix1", "Fixture", "review",
            state={"phase": "review", "blocked_by": [],
                   "checks": [{"criterion": "evidence_fresh", "ok": True,
                               "detail": "ledger: passed (2 entries)", "blocking": True}]},
            report={"text": "REPORT", "contract": "artifacts/plan.md"},
            mandate="M", log_digest="L",
            success_signal="the export downloads a valid CSV",
            verdicts=[{"check": "csv-downloads", "passed": True, "deferred": False, "cycle": 1,
                       "how": "curl -s /stats.csv", "result": "200 text/csv"}]),
        "assembler.handoff": KS.render_handoff_block({"id": "fix1"}, d)[0],
        "assembler.diagnosis_trace": KS.diagnosis_trace_block(run, events, 7),
    }
    # The fixture dir and everything under it reach the rendered text with the platform's
    # separator; the baseline holds one spelling.
    return {k: _FIXTURE_PATH.sub(lambda m: m.group(0).replace("\\", "/"), v)
            for k, v in out.items()}


def test_snapshot() -> None:
    print("prompt_baseline.json — the registry snapshot (parity-style)")
    rendered = render_registry()
    ok("every entry renders non-empty", all(v and v.strip() for v in rendered.values()),
       str([k for k, v in rendered.items() if not v]))
    if not BASELINE.exists():
        BASELINE.write_text(json.dumps(rendered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        ok("baseline written (first run) — commit scripts/prompt_baseline.json", True)
        return
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    ok("no entries added/removed vs baseline", set(base) == set(rendered),
       f"only-in-baseline={sorted(set(base) - set(rendered))} "
       f"only-in-code={sorted(set(rendered) - set(base))}")
    diff = [k for k in base if base[k] != rendered[k]]
    ok("every entry byte-identical to baseline (edits = deliberate re-baseline)",
       not diff, f"changed: {diff}")


# ------------------------------------------------------ outside-registry lint A kernel-speech
# marker outside the registry means someone is authoring prompt text at a surface.
LINT_MARKERS = ("Run superme-dev", "sub-agent (superme-dev", "```completion-report")
# The registry itself + the replay noise-prefixes that match the triggers
# (sessions._NOISE_PREFIXES) — consumers, not authors.
LINT_ALLOWED = {"superme_agent/core/kernel_speech.py", "superme_agent/core/sessions.py"}


def test_lint() -> None:
    print("lint — no kernel speech outside kernel_speech.py")
    hits = []
    for p in (ROOT / "superme_agent").rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel in LINT_ALLOWED:
            continue
        text = p.read_text(encoding="utf-8")
        for m in LINT_MARKERS:
            if m in text:
                hits.append(f"{rel}: {m!r}")
    ok("no instruction-shaped strings outside the registry", not hits, str(hits))


def main() -> None:
    test_run_protocol()
    test_triggers()
    test_runners_flip()
    test_skills()
    test_reader()
    test_snapshot()
    test_lint()
    shutil.rmtree(FIXTURE_DIR, ignore_errors=True)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
