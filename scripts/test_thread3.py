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

    # The outcome vocabulary lives in the tool's schema (the contract's new home), not in prose.
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
    # A template is part of its skill's package. A trigger carrying the body pays for it every
    # request.
    for _ph, _fire in (("triage", KS.intake_trigger("triage", "it1", "T")),
                       ("build", KS.build_first_trigger("it1", "T")),
                       ("close", KS.close_trigger("it1", "T"))):
        tpl = f"templates/report-{_ph}-template.md"
        body = (SKILLS / _ph / tpl).read_text(encoding="utf-8")
        ok(f"{_ph} trigger carries the delta, not the report template",
           "```markdown" not in _fire and body.strip().splitlines()[0] not in _fire,
           f"{len(_fire)}c")
        ok(f"{_ph}'s skill names its own report template",
           tpl in (SKILLS / _ph / "SKILL.md").read_text(encoding="utf-8"))
    # The server note rides the entry trigger only, and build resumes its own thread.
    _env = lambda t: "vet_env.sh" in t   # noqa: E731 — one predicate, read four ways below
    ok("the entry build trigger carries the server note",
       _env(KS.build_first_trigger("it1", "T", vet_env=True)))
    ok("a failure hop on an intact thread does NOT repeat it",
       not _env(KS.build_loop_trigger("it1", "T", 2, "R", vet_env=False)))
    ok("a compacted failure hop restates it, as it already restates the skill",
       _env(KS.build_loop_trigger("it1", "T", 2, "R", reload_skill=True, vet_env=True)))
    ok("a repo without vet_env never sees it",
       not _env(KS.build_loop_trigger("it1", "T", 2, "R", reload_skill=True)))
    _t = KS.build_loop_trigger("it1", "T", 2, "R", reload_skill=True, vet_env=True)
    ok("...and it sits ahead of the report body, not behind it",
       _t.index("vet_env.sh") < _t.index("--- build-vet-"))
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
       runs_src["runs"].count("make_run_report_server(") == 4)
    # plan alone: its gate check is mechanical, so the run can fix what it is still holding.
    ok("the two plan-capable runners carry an exit check",
       runs_src["runs"].count("exit_check=phase_exit_check(") == 2)
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


# ------------------------------------------------------------------ prompt channels
def test_channels() -> None:
    print("channels — the phase pointer rides the turn, never the cached append")
    from superme_agent.core.agent_service import (SYSTEM_CHANNEL, TURN_CHANNEL, TURN_SEP,
                                                  AgentService as AS)
    frags = [{"text": "STANDING", "sep": "", "channel": SYSTEM_CHANNEL},
             {"text": "POINTER", "sep": "\n\n", "channel": TURN_CHANNEL}]
    ok("the append joins system fragments only", AS._join_fragments(frags) == "STANDING")
    ok("an untagged fragment reads as system-borne, so a pre-split capture still renders",
       AS._join_fragments([{"text": "OLD", "sep": ""}]) == "OLD")
    ok("compose_prompt puts the pointer ahead of the trigger",
       AS.compose_prompt("POINTER", "TRIGGER") == f"POINTER{TURN_SEP}TRIGGER")
    ok("a turn with no pointer sends the trigger untouched",
       AS.compose_prompt(None, "TRIGGER") == "TRIGGER")
    # The chat transcript replays these messages back to the owner, so every injected block is
    # peeled off.
    from superme_agent.core.sessions import _strip_kernel_prefix as strip
    mine, birth = "look at the failing test", "### Work-item orientation\nstuff"
    for label, block in (("work-item", KS.work_item_preamble("it1", {"phase": "plan"}, FIXTURE_DIR,
                                                             interactive=True)),
                         ("general", KS.general_preamble()),
                         ("deputy", KS.deputy_preamble())):
        ok(f"the {label} block comes back off the owner's message",
           strip(AS.compose_prompt(block, mine)) == mine)
    ok("a birth block behind the session block comes off too",
       strip(AS.compose_prompt(KS.general_preamble(), birth + TURN_SEP + mine)) == mine)
    # A straggler is not a stale name. `run_turn` would raise, so only prose can carry a name
    # nothing checks.
    stragglers = sorted(p.relative_to(ROOT).as_posix()
                        for p in (ROOT / "superme_agent").rglob("*.py")
                        if re.search(r"system_append\s*=", p.read_text(encoding="utf-8")))
    ok("nothing hands the phase pointer to the system append", not stragglers, str(stragglers))

    # The X-ray is how the owner checks any of this, so it has to record what was sent.
    from superme_agent.daemon.services.runs import capture as CAP
    recorded: dict = {}
    stub = SimpleNamespace(
        assemble_system_append=lambda ctx, **k: "APPEND",
        assemble_system_fragments=lambda ctx, **k: [],
        compose_prompt=AS.compose_prompt)
    spine = SimpleNamespace(live_run=lambda *a: {"id": 1}, set_run_feature=lambda *a: None,
                            record_run_input=lambda rid, **kw: recorded.update(kw))
    old = CAP._agent, CAP._spine
    try:
        CAP._agent, CAP._spine = stub, spine
        CAP.capture_run_input("ctx", "it1", ctx=SimpleNamespace(internal_root=None),
                              preamble="BLOCK", prompt="TRIGGER", background=True, phase="plan")
    finally:
        CAP._agent, CAP._spine = old
    ok("the capture records the COMPOSED message, not the bare trigger",
       recorded.get("prompt_body") == AS.compose_prompt("BLOCK", "TRIGGER"),
       repr(recorded.get("prompt_body")))


# ------------------------------------------------------------------ skills + preamble hedge
def test_skills() -> None:
    print("skills — Background runs sections keep deltas, lose narration")
    deltas = {
        # Assert the concept is still taught: pinning the exact heading made a copy edit read as a
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
FIXTURE_DIR = Path("/tmp/superme-thread3-fixture")  # Fixed path — it appears inside rendered text
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
        # A fixed path: the wording is the contract, and a baseline carrying this machine's
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


def test_tool_channel() -> None:
    print("tool channel — the X-ray charges NAMES to the request, never the schemas")
    from superme_agent.harness.tools.base_tools import BASE_TOOLS
    from superme_agent.harness.tools.registry import describe_specs
    from superme_agent.daemon.services.runs.capture import _authored_extras

    class _Ctx:
        pass

    extras = _authored_extras(_Ctx(), {"kind": "implementation"}, "build", ["dev", "run"])
    names = extras.get("tools") or []
    schemas = extras.get("deferred_tools") or []
    ok("both channels are populated", bool(names) and len(schemas) == len(names))

    # Claude Code sends the names and holds the schemas, so the two must not be the same text.
    name_chars = sum(len(f["text"]) for f in names)
    schema_chars = sum(len(f["text"]) for f in schemas)
    ok("names are far smaller than schemas", name_chars * 4 < schema_chars)
    ok("no counted card carries a parameter doc",
       not any("    · " in f["text"] or "\n    " in f["text"] for f in names))
    ok("every counted line is a wire tool name",
       all(ln.startswith("mcp__") for f in names for ln in f["text"].splitlines() if ln.strip()))
    ok("the schema channel still renders the real docs",
       any(describe_specs(BASE_TOOLS)[:40] in f["text"] for f in schemas))

    src = Path("superme_agent/daemon/services/input_preview.py").read_text(encoding="utf-8")
    ok("the total counts `tools` and not `deferred`",
       "*skills, *tools]" in src and "*deferred]" not in src)
    ok("the counted channel no longer claims to carry docs",
       "Tool docs — descriptions + parameter docs" not in src)


def test_plan_coverage() -> None:
    print("plan coverage — the dry-run names the gap, so filing is not the linter")
    import tempfile
    from superme_agent.harness.tools.dev_tools.verification import _plan_coverage

    # The real grammar, copied off a live plan.md.
    plan = """## Tasks
- [ ] t1 — Suppress the placeholder under `--quiet`
      Gate the print behind `not getattr(args, "quiet", False)`.

- [ ] t2 — Rewrite the flag's help text
      It still describes the old, narrower behaviour.

## Verification plan
depth: checks
reason: printed output under a CLI flag is directly observable.
env: none

### quiet-sum-is-silent
- proves: with `--quiet` and no entries, `tally sum` prints nothing
- covers: t1
- mode: command
- run: |
    pytest -k quiet_sum
"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "item"
        (d / "artifacts").mkdir(parents=True)
        (d / "artifacts" / "plan.md").write_text(plan, encoding="utf-8")

        from superme_agent.core.artifacts import proof_rows
        ok("the fixture parses as 2 tasks (guards the grammar)",
           len([r for r in proof_rows(d) if r["task"]]) == 2)
        impl = _plan_coverage(d, "implementation")
        ok("an uncovered task is NAMED, not just counted", "t2" in impl)
        ok("the covered task is not reported as a gap", "t1 (" not in impl)
        # The whole point: planning must learn this at 4d, one step before it files.
        ok("it says the report will repeat the same count", "report" in impl.lower())

        # A research plan declares no checks by design — a gap call-out there would be noise.
        ok("research is exempt", _plan_coverage(d, "research") == "")

        full = plan.replace("- covers: t1", "- covers: t1 t2")
        (d / "artifacts" / "plan.md").write_text(full, encoding="utf-8")
        clean = _plan_coverage(d, "implementation")
        ok("a fully defended plan says so and names no gap",
           "NO check" not in clean and "defended" in clean)

    src = Path("superme_agent/harness/tools/dev_tools/verification.py").read_text(encoding="utf-8")
    ok("coverage rides BOTH return paths, including the no-run: one",
       src.count("+ coverage") + src.count('." + coverage') >= 2)
    skill = Path("superme_agent/harness/plugins/superme-dev/skills/plan/SKILL.md").read_text(
        encoding="utf-8")
    ok("the skill no longer asks for an answer step 5 alone could give",
       "answer it before it is asked" not in skill)


def test_library_without_a_plan() -> None:
    print("verification library — readable by an item that has no plan.md")
    import asyncio
    import tempfile
    from superme_agent.harness.tools.dev_tools.verification import _read_verification_library

    with tempfile.TemporaryDirectory() as tmp:
        dev_root = Path(tmp) / "dev"
        item = dev_root / "work-items" / "aaaabbbbcccc"
        (item / "artifacts").mkdir(parents=True)
        # A research item never writes a plan, and close mounts this tool.
        (item / "item.md").write_text(
            '---\nid: aaaabbbbcccc\nkind: research\nphase: close\n---\nBody.\n',
            encoding="utf-8")
        ok("the fixture really has no plan.md", not (item / "artifacts" / "plan.md").is_file())

        tool = _read_verification_library(store=None, context_id="probe", dev_root=dev_root,
                                          bound_item_id="aaaabbbbcccc")
        res = asyncio.run(tool({"item_id": "aaaabbbbcccc"}))
        text = json.dumps(res)
        # It used to hand back a raw `[error] [Errno 2] No such file or directory: …/plan.md`.
        ok("no errno leaks out", "Errno" not in text and "No such file" not in text)
        ok("the library itself still comes back", "standing" in text and "available" in text)

        # And the addendum still appears when there is a plan, so the guard did not delete it.
        (item / "artifacts" / "plan.md").write_text("## Tasks\n- [ ] t1 — a task\n", encoding="utf-8")
        res2 = asyncio.run(tool({"item_id": "aaaabbbbcccc"}))
        ok("a plan-bearing item still reads clean", "Errno" not in json.dumps(res2))

    src_txt = Path("superme_agent/harness/tools/dev_tools/verification.py").read_text(
        encoding="utf-8")
    ok("the plan read is guarded, not merely wrapped",
       "plan_path.is_file()" in src_txt and 'artifact_file("plan")).read_text' not in src_txt)


def test_stray_run_blocks() -> None:
    print("run: blocks — the dry run now says when a command leaves this item's worktree")
    import tempfile
    from superme_agent.harness.tools.dev_tools.verification import _stray_run_blocks

    PLAN = """## Tasks
- [ ] t1 — a task

## Verification plan
depth: checks

### c1
- proves: something
- covers: t1
- mode: command
- run: |
    {cmd}
"""
    WT = "/Users/cooma/.superme/worktrees/test-playground/aaaabbbbcccc"

    def fires(cmd: str) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "item"
            (d / "artifacts").mkdir(parents=True)
            (d / "artifacts" / "plan.md").write_text(PLAN.format(cmd=cmd), encoding="utf-8")
            return bool(_stray_run_blocks(d, WT))

    # The command runs in the primary checkout, which sits on the anchor without this item's
    # commits.
    ok("a `cd` to another checkout fires",
       fires("cd /Users/cooma/Developer/my_docs/test-playground && pytest -k quiet"))
    ok("an absolute path into another checkout fires",
       fires("pytest /Users/cooma/Developer/my_docs/test-playground/tests/test_ledger.py"))
    ok("a `cd` later in the line fires", fires("make x && cd ../other && pytest"))

    # False positives would nag every plan, so the common idioms must stay silent.
    ok("a relative command is clean", not fires("pytest -k quiet_sum"))
    ok("2>/dev/null is clean", not fires("python -m pytest tests/t.py 2>/dev/null"))
    ok("a /tmp path is clean", not fires("ls /tmp/scratch && pytest tests/"))
    ok("a path INSIDE this worktree is clean", not fires(f"pytest {WT}/tests/test_ledger.py"))
    ok("a word merely containing 'cd' is clean", not fires("python -m mycd --check"))

    # Windows shapes. A guard that cannot see a native path is a guard that never fires.
    WIN = r"C:\Users\me\.superme\worktrees\pg\aaaabbbbcccc"

    def wfires(cmd: str) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "item"
            (d / "artifacts").mkdir(parents=True)
            (d / "artifacts" / "plan.md").write_text(PLAN.format(cmd=cmd), encoding="utf-8")
            return bool(_stray_run_blocks(d, WIN))

    ok("a drive-absolute path into another checkout fires",
       wfires(r"pytest C:\Users\me\Developer\test-playground\tests\test_ledger.py"))
    ok("a UNC path fires", wfires(r"pytest \\build\share\tests\test_ledger.py"))
    ok("a drive-absolute path INSIDE this worktree is clean",
       not wfires(r"pytest C:\Users\me\.superme\worktrees\pg\aaaabbbbcccc\tests\t.py"))
    ok("…and the same path written with forward slashes is clean too",
       not wfires("pytest C:/Users/me/.superme/worktrees/pg/aaaabbbbcccc/tests/t.py"))
    ok("a relative command is still clean against a Windows worktree",
       not wfires("pytest -k quiet_sum"))

    # A helper nobody calls is a detector that never fires — pin the wiring, not just the logic.
    src_txt = Path("superme_agent/harness/tools/dev_tools/verification.py").read_text(
        encoding="utf-8")
    ok("check_plan_commands actually calls it",
       "_stray_run_blocks(d, repo_dir)" in src_txt)
    skill = Path("superme_agent/harness/plugins/superme-dev/skills/plan/SKILL.md").read_text(
        encoding="utf-8")
    ok("the skill still states the rule the check now enforces",
       "never `cd`, never an absolute path" in skill)


def main() -> None:
    test_run_protocol()
    test_triggers()
    test_runners_flip()
    test_channels()
    test_tool_channel()
    test_plan_coverage()
    test_library_without_a_plan()
    test_stray_run_blocks()
    test_skills()
    test_reader()
    test_snapshot()
    test_lint()
    shutil.rmtree(FIXTURE_DIR, ignore_errors=True)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
