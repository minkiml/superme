"""The research SUB-KIND: which family of investigation an item is.

An artifact is judged against the template that PRODUCED it, so re-classifying an item mid-flight
cannot retro-fail a record already written correctly.

Run: PYTHONPATH=. python -m scripts.test_research_kind
"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from superme_agent.core import artifacts as _arts
from superme_agent.core.vocab import kind_profiles as _kp
from superme_agent.core.dev_knowledge import DevKnowledgeService
from scripts.sources import src

_GUIDES = (Path(__file__).resolve().parents[1] / "superme_agent/harness/plugins"
           / "superme-dev/skills/investigate/references")

PASS = 0


def flat(text: str) -> str:
    """Prose with its line wrapping collapsed.

    A guide re-wraps whenever a sentence is edited, so a pin matching across a line break fails on
    an unrelated word change. Structure pins match raw; sentence pins use this."""
    return " ".join(text.split())


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ✓ {msg}")


def _seed(root: Path, item_id: str, kind: str = "research") -> Path:
    """A minimal on-disk work-item, born the way create_work_item mints one."""
    d = root / "work-items" / item_id
    d.mkdir(parents=True)
    (d / "item.md").write_text(
        f"---\nid: {item_id}\ntitle: \"probe\"\nkind: {kind}\n"
        "scale: standard\nscale_reason: null\n"
        "research_kind: null\nresearch_kind_reason: null\n"
        "phase: triage\nstatus: active\ncreated_at: 2026-08-13\nupdated_at: 2026-08-13\n---\n", encoding="utf-8")
    return d


print("\n— the family reader —")
ok("unset reads None — there is no default family",
   _kp.research_kind({"research_kind": None}) is None)
ok("an unknown value reads None, not itself (the judgment is missing, not the item broken)",
   _kp.research_kind({"research_kind": "vibes"}) is None)
ok("a known family reads back", _kp.research_kind({"research_kind": "study"}) == "study")
ok("the six families are known", set(_kp.RESEARCH_KINDS) ==
   {"audit", "refactoring", "housekeeping", "security", "study", "deep-diagnosis"})
ok("`deep-diagnosis` is not `diagnosis` — that word is already a SESSION kind",
   "diagnosis" not in _kp.RESEARCH_KINDS)

print("\n— the writer —")
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    dev = DevKnowledgeService()
    d = _seed(root, "aaa111")
    ok("a family + reason writes", dev.set_work_item_research_kind(root, "aaa111", "study", "  we "
                                                                   "are reading someone else's "
                                                                   "parser  "))
    item = dev.read_work_item(root, "aaa111")
    ok("the family reads back off the item", _kp.research_kind(item) == "study")
    ok("the reason is stored on one line",
       item.get("research_kind_reason") == "we are reading someone else's parser")

    ok("re-classifying overwrites in place",
       dev.set_work_item_research_kind(root, "aaa111", "audit", "it is a whole-surface sweep"))
    ok("the new family reads back",
       _kp.research_kind(dev.read_work_item(root, "aaa111")) == "audit")

    try:
        dev.set_work_item_research_kind(root, "aaa111", "audit", "   ")
        ok("a bare label is refused", False)
    except ValueError as e:
        ok(f"a bare label is refused — {e}")
    try:
        dev.set_work_item_research_kind(root, "aaa111", "spelunking", "why not")
        ok("an unknown family is refused", False)
    except ValueError as e:
        ok("an unknown family is refused at the write", "must be one of" in str(e))

    _seed(root, "bbb222", kind="implementation")
    try:
        dev.set_work_item_research_kind(root, "bbb222", "audit", "wrong item")
        ok("writing a family onto an implementation item is refused", False)
    except ValueError as e:
        ok("writing a family onto an implementation item is refused — a field nobody would read",
           "research" in str(e))

    # An item minted before the field existed carries no line at all.
    legacy = root / "work-items" / "ccc333"
    legacy.mkdir(parents=True)
    (legacy / "item.md").write_text(
        "---\nid: ccc333\ntitle: \"old\"\nkind: research\nphase: plan\nstatus: active\n"
        "updated_at: 2026-01-01\n---\n", encoding="utf-8")
    ok("a pre-field item takes the insert",
       dev.set_work_item_research_kind(root, "ccc333", "deep-diagnosis",
                                       "we are chasing a mechanism"))
    ok("and reads back",
       _kp.research_kind(dev.read_work_item(root, "ccc333")) == "deep-diagnosis")

print("\n— template routing —")
ok("study routes to its own template",
   _arts._template_name("investigation", "research", "study") == "investigation-study")
for fam in _kp.RESEARCH_KINDS:
    ok(f"{fam} routes to its own shape",
       _arts._template_name("investigation", "research", fam) == f"investigation-{fam}")
ok("an unjudged item falls back to the base shape",
   _arts._template_name("investigation", "research", None) == "investigation")
ok("every family owes `Follow-up work` — the investigation AND the work it implies",
   all("Follow-up work" in [h for h, _ in _arts.section_spec("investigation", "research", f)]
       for f in _kp.RESEARCH_KINDS))
ok("no two families share a shape",
   len({tuple(h for h, _ in _arts.section_spec("investigation", "research", f))
        for f in _kp.RESEARCH_KINDS}) == len(_kp.RESEARCH_KINDS))
ok("every family has a guide at references/<slug>.md",
   all((_GUIDES / f"{f}.md").read_text(encoding="utf-8").strip() for f in _kp.RESEARCH_KINDS))
ok("every template home resolves to a file that exists",
   all(_arts.skill_template(name) for name in _arts._TEMPLATE_HOMES))

study_secs = [h for h, _ in _arts.section_spec("investigation", "research", "study")]
base_secs = [h for h, _ in _arts.section_spec("investigation", "research", None)]
ok(f"the study shape splits evidence from proposal — {study_secs}",
   "What they do" in study_secs and "What transfers" in study_secs
   and "What doesn't" in study_secs)
ok("the base shape does not carry those sections",
   not ({"What they do", "What transfers"} & set(base_secs)))

print("\n— scaffold + self_check —")
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    r = _arts.scaffold(d, "investigation", title="probe", item_kind="research",
                       item_id="aaa111", research_kind="study")
    text = (d / "artifacts" / "investigation.md").read_text(encoding="utf-8")
    ok("the study scaffold stamps the shape it was authored under",
       "research_kind: study" in text)
    ok("and it IS the study body", "## What transfers" in text)
    ok("the reported sections are the study's", "What doesn't" in r["sections"])

    issues = _arts.self_check(d, "investigation", item_kind="research")
    ok("a freshly scaffolded study is incomplete (its slots are unfilled)", bool(issues))

    filled = text
    for sec in [h for h, _ in _arts.section_spec("investigation", "research", "study")]:
        filled = filled.replace(f"## {sec}\n", f"## {sec}\nreal content here\n")
    filled = "\n".join(ln for ln in filled.splitlines() if not ln.startswith("<fill:")) + "\n"
    (d / "artifacts" / "investigation.md").write_text(filled, encoding="utf-8")
    ok("a filled study passes its own check",
       _arts.self_check(d, "investigation", item_kind="research") == [])

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    _arts.scaffold(d, "investigation", title="probe", item_kind="research", item_id="bbb222")
    text = (d / "artifacts" / "investigation.md").read_text(encoding="utf-8")
    ok("an unjudged item scaffolds the base shape, unstamped",
       "research_kind:" not in text and "## Evidence" in text)
    ok("an unknown family is forgiven at scaffold, not raised",
       _arts.scaffold(Path(td) / "x", "investigation", item_kind="research",
                      research_kind="vibes")["created"])

print("\n— the invariant: judged against the shape it was authored under —")
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    _arts.scaffold(d, "investigation", title="probe", item_kind="research",
                   item_id="ccc333", research_kind="study")
    p = d / "artifacts" / "investigation.md"
    body = p.read_text(encoding="utf-8")
    for sec in [h for h, _ in _arts.section_spec("investigation", "research", "study")]:
        body = body.replace(f"## {sec}\n", f"## {sec}\nreal content here\n")
    body = "\n".join(ln for ln in body.splitlines() if not ln.startswith("<fill:")) + "\n"
    p.write_text(body, encoding="utf-8")
    # Re-classifying after the record was written must not go red: the owner's correction is not a
    # defect in work already done.
    ok("a complete study record stays green after the item is re-classified",
       _arts.self_check(d, "investigation", item_kind="research") == [])
    ok("because the shape is read from the FILE, not the item",
       "research_kind: study" in p.read_text(encoding="utf-8"))

# --- fan-out is ENFORCED, not asked -------------------------------- Prose with no check is prose
# at zero compliance.
print("\n— fan-out is a checked fact, not a polite ask —")
from superme_agent.core.gate_briefs import fanout_check

ok("the fan-out families are the whole-codebase ones",
   set(_kp.FANOUT_FAMILIES) == {"audit", "refactoring", "housekeeping", "security"})
ok("…and each is a real research family",
   set(_kp.FANOUT_FAMILIES) <= set(_kp.RESEARCH_KINDS))
ok("a single-threaded sweep FAILS", fanout_check("audit", 0)["ok"] is False)
ok("…and the row names the number, not a vibe",
   "0 subagents" in fanout_check("housekeeping", 0)["detail"])
ok("a split sweep passes", fanout_check("security", 4)["ok"] is True)

# The three ways the question must NOT be asked — the rule that cost three defects in one day.
ok("never asked of a family that follows ONE thread (study)", fanout_check("study", 0) is None)
ok("…nor deep-diagnosis", fanout_check("deep-diagnosis", 0) is None)
ok("never asked when nobody COUNTED — None is not zero", fanout_check("audit", None) is None)
ok("never asked of an unjudged item", fanout_check(None, 0) is None)

_gb = src("superme_agent/core/gate_briefs.py")
ok("fanned_out is VISIBLE, never greys Approve (a small area may honestly not split)",
   "fanned_out" not in _gb.split('"review": (')[1].split(")")[0])
_sp = src("superme_agent/core/spine.py")
ok("the count is read from the spine's own subagent rows — unfakeable by the agent",
   "def subagent_count(" in _sp and "e.kind='subagent'" in _sp)

# --- a reader can put a file somewhere --------------------------- A reader inherits the write
# boundary but NOT the briefing, so its empty result reads clean.
print("\n— a spawned reader is told where it may write —")
_INV = Path("superme_agent/harness/plugins/superme-dev/skills/investigate")
_skill = (_INV / "SKILL.md").read_text(encoding="utf-8")
_reader = (_INV / "agents/investigator-agent.md").read_text(encoding="utf-8")

ok("the brief recipe carries a write location, not only a question",
   "scratch" in _skill.split("Each brief carries")[1].split("## 4")[0])
ok("…and the reader is told to expect one in its brief", "scratch" in _reader)
ok("…and told the refusal it would otherwise walk into", "$TMPDIR" in _reader)
ok("the reader's no-write rule is about ARTIFACTS, not about working files — "
   "an absolute ban would make it avoid the directory it was just handed",
   "never write a file" not in flat(_reader))
ok("the census is built ONCE, before readers exist, not per reader",
   "Census first" in _skill or "census" in _skill.lower())

# --- a verdict older than what it judges --------------------------- Two mtimes, no opinion.
print("\n— the review gate states whether the verdict is current —")
from superme_agent.core.gate_briefs import judgment_current

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    (d / "artifacts").mkdir()
    rv, inv = d / "artifacts/review.md", d / "artifacts/investigation.md"
    for p in (rv, inv):
        p.write_text("x\n", encoding="utf-8")
    os.utime(rv, (1_000_000, 1_000_000))
    os.utime(inv, (1_000_180, 1_000_180))   # investigate rewrote 3 minutes after the verdict
    bad = judgment_current(d, "research")
    ok("a verdict older than its investigation FAILS", bad["ok"] is False)
    ok("…and the row says how far behind, in minutes", "by 3m" in bad["detail"])
    os.utime(rv, (1_000_300, 1_000_300))    # …then review re-read and rewrote its record
    ok("…and clears once the verdict is rewritten", judgment_current(d, "research")["ok"] is True)

    # An implementation item has a subject too: its newest build⟷vet cycle.
    for n, t in ((1, 1_000_050), (2, 1_000_400)):
        c = d / f"artifacts/build-vet-{n}.md"
        c.write_text("x\n", encoding="utf-8")
        os.utime(c, (t, t))
    impl = judgment_current(d, "implementation")
    ok("an implementation verdict is judged against its NEWEST cycle, not its first",
       impl["ok"] is False and "build-vet-2.md" in impl["detail"])

    # Asked only where an answerer exists — the rule that cost five defects.
    inv.unlink()
    ok("never asked when there is no subject to compare against",
       judgment_current(d, "research") is None)
    for c in d.glob("artifacts/build-vet-*.md"):
        c.unlink()
    ok("…nor for an implementation item that has not vetted yet",
       judgment_current(d, "implementation") is None)
    rv.unlink()
    ok("…nor before review has written anything", judgment_current(d, "research") is None)

# --- the family method was actually opened ------------------------- Invisible without this: the
# scaffolder stamps the shape either way.
print("\n— the review gate states whether the family's method was read —")
from superme_agent.core.gate_briefs import guide_check

ok("a never-opened guide FAILS", guide_check("audit", 0)["ok"] is False)
ok("…and the row names the file, not a category", "references/audit.md" in guide_check("audit", 0)["detail"])
ok("…and says why the artifact still looks right", "scaffolder" in guide_check("security", 0)["detail"])
ok("a read guide passes, with the count", guide_check("study", 2)["ok"] is True
   and "2×" in guide_check("study", 2)["detail"])
ok("asked of EVERY family — every one has a method, unlike fan-out",
   all(guide_check(f, 0) is not None for f in _kp.RESEARCH_KINDS))
ok("never asked of an unjudged item — no family, nothing to have read",
   guide_check(None, 0) is None)
ok("never asked when nobody COUNTED — None is not zero", guide_check("audit", None) is None)

# The counter measures the ACT, not the tool, and a blocking check that is wrong is worse than
# none.
from superme_agent.core.spine import _opens_a_file

ok("a shell READ of the guide counts — cat, head, less, sed, grep",
   all(_opens_a_file(c) for c in (
       "cat /a/b/references/audit.md",
       "head -50 /a/b/references/audit.md",
       "sed -n '1,80p' /a/b/references/audit.md",
       "grep -n '^##' /a/b/references/audit.md",
       "cd /wt && less /a/b/references/audit.md")))
ok("NAMING the guide is not reading it — ls, find, stat, wc open nothing",
   not any(_opens_a_file(c) for c in (
       "ls -la /a/b/references/",
       "find /a/b/references -name 'audit.md'",
       "stat /a/b/references/audit.md",
       "wc -l /a/b/references/audit.md",
       "echo /a/b/references/audit.md")))
ok("method_read BLOCKS, unlike its neighbours — no item is too small for its family's method",
   "method_read" in _gb.split('"review": (')[1].split(")")[0])
_sk = (Path("superme_agent/harness/plugins/superme-dev/skills/investigate/SKILL.md")).read_text(encoding="utf-8")
ok("the skill names each family's guide file literally, not as a placeholder",
   all(f"`references/{f}.md`" in _sk for f in _kp.RESEARCH_KINDS))
ok("…and the guide is the skill's FIRST numbered step, ahead of the questions and the code",
   _sk.index("## 1 ·") < _sk.index("## 2 ·")
   and "references/" in _sk[_sk.index("## 1 ·"):_sk.index("## 2 ·")])
ok("fan-out defaults to splitting instead of asking the agent to feel out independence",
   "change HOW you read for question A" in _sk)
# Named, not described: an agent type resolves by string, so a paraphrase falls back to a generic
# subagent.
_ag = (Path("superme_agent/harness/plugins/superme-dev/skills/investigate/agents/investigator-agent.md")).read_text(encoding="utf-8")
ok("the skill spawns the investigator BY NAME, and that agent exists to be spawned",
   "subagent_type: superme-dev:investigator" in _sk and "name: investigator" in _ag)
ok("…and it cannot write — the tool allowlist is the enforcement the brief used to only ask for",
   all(t not in _ag.split("---")[1] for t in ("Write", "Edit")))
# The read discipline must reach BOTH readers: the parent does its own synthesis reading.
for _name, _doc in (("skill", _sk), ("agent", _ag)):
    ok(f"the {_name} states the range-read rule, not just 'read less'",
       "offset" in _doc or "RANGE, not the file" in _doc)
# The call ceiling was REMOVED, not forgotten: it never bound a spawn, and only risked capping
# coverage. A checkable coverage claim replaces it.
ok("the agent asks for coverage as NUMBERS, which a reader cannot fake by asserting completeness",
   "as numbers" in _ag.lower() and "cannot be checked" in _ag)
ok("housekeeping starts from a mechanical inventory, not from reading",
   "## Start mechanical" in (Path("superme_agent/harness/plugins/superme-dev/skills/investigate/"
                                 "references/housekeeping.md")).read_text(encoding="utf-8"))
# The checklist is what the agent copies into its reply, so it — not the prose — orders the run.
_boxes = [ln for ln in _sk.splitlines() if ln.strip().startswith("- [ ]")]
ok("the skill and its checklist agree on what comes FIRST — the family guide, not the questions",
   len(_boxes) >= 2 and "guide" in _boxes[0].lower() and "question" in _boxes[1].lower())
# A partial agent id does not error; it silently falls back to a generic reader.
ok("the skill spawns the FULL scoped identifier, not the bare name",
   "superme-dev:investigator" in _sk and "subagent_type: investigator\n" not in _sk)
# A clean claim is unfalsifiable without its enumeration, and it is the claim that RETIRES a
# question. Both readers owe it.
ok("the agent owes a receipt for 'I found nothing', not just for findings",
   "found nothing" in _ag and "enumerated" in _ag)
ok("…and the parent refuses to record a clean area that arrived without numbers",
   "UNSWEPT" in _sk and "Open threads" in _sk)
_hk = (Path("superme_agent/harness/plugins/superme-dev/skills/investigate/"
            "references/housekeeping.md")).read_text(encoding="utf-8")
# Structure, not wording: a name pass cannot see a group whose members only reference each other.
_MECH = _hk.split("## Start mechanical")[1].split("\n## ")[0]
ok("housekeeping sweeps files as well as names — a group is invisible to a name count",
   "**Names**" in _MECH and "**Files**" in _MECH
   and all(f"\n{n}." in _MECH for n in (1, 2, 3, 4)))
ok("…and its fan-out no longer contradicts its own breadth table",
   "one subagent per directory or module" not in _hk)
_SPLIT_STEP = _sk.split("Split the surface and spawn readers")[1].split("\n## ")[0]
ok("…and a single-threaded sweep must SAY why, in the record",
   "investigation.md" in _SPLIT_STEP)

ok("judgment_current is VISIBLE, never greys Approve (an honest no-op writes no file)",
   "judgment_current" not in _gb.split('"review": (')[1].split(")")[0])
ok("…and it is asked of BOTH kinds, not just research",
   "fresh = judgment_current(item_dir, profile.kind)" in _gb
   and _gb.index("fresh = judgment_current") < _gb.index('if profile.kind == "research":\n'
                                                         '            checks.extend'))


# ── brief_carried ───────────────────────────────────────────────── Splitting a surface is not
# giving the workers a bar, and a subagent inherits nothing.
from superme_agent.core.gate_briefs import brief_check, BRIEF_FLOOR

print("\n— the review gate states whether the fan-out briefed what it spawned —")
ok("no spawn recorded a size → the row is ABSENT, not a failure (nobody looked \u2260 thin)",
   brief_check(None) is None and brief_check([]) is None)
ok("briefs with room for a bar pass, and the row states the smallest",
   brief_check([1800, 2400])["ok"] is True and "1800" in brief_check([1800, 2400])["detail"])
ok("one thin brief fails the row even when its siblings are fat",
   brief_check([120, 2400])["ok"] is False)
ok("…and the detail counts which, so the reader knows the blast radius",
   "1 of 2" in brief_check([120, 2400])["detail"])
ok("…and says what to do with those findings — leads, not receipts",
   "leads, not receipts" in brief_check([120, 2400])["detail"])
ok("the floor is generous by design — it proves impossibility, never adequacy",
   BRIEF_FLOOR >= 600 and brief_check([BRIEF_FLOOR])["ok"] is True
   and brief_check([BRIEF_FLOOR - 1])["ok"] is False)
ok("brief_carried is VISIBLE, never greys Approve — size is a proxy for content",
   "brief_carried" not in _gb.split('"review": (')[1].split(")")[0])
ok("…and it is wired into the review gate beside fanned_out",
   "brief_check(brief_sizes)" in _gb)

# The trace row is where the size comes from: no recorded size, no row.
from superme_agent.daemon.services.runs import _artifact_desc
ok("a spawn's trace row carries the brief's size",
   "brief 1843" in _artifact_desc("Agent", {"subagent_type": "Explore", "prompt": "x" * 1843})[2])

# …and the brief ITSELF is kept: a size can prove one too short to carry a bar, never that the bar
# was right.
print("\n— the brief a worker was actually sent is kept, not just its length —")
import tempfile as _tf
from superme_agent.daemon.services.runs import _artifact_payload
from superme_agent.core import spine as _sp

BRIEF = "Housekeeping sweep.\nTHE BAR (quoted from the family guide, `housekeeping.md`): ..."
ok("a spawn's full brief is kept as the row's payload",
   _artifact_payload("Agent", {"subagent_type": "Explore", "prompt": BRIEF}) == BRIEF)
ok("…under either spawn tool name — the SDK sends Task or Agent by build",
   _artifact_payload("Task", {"prompt": BRIEF}) == BRIEF)
ok("a trail row keeps none — its description already IS its content",
   all(_artifact_payload(t, {"command": "ls", "file_path": "/x"}) is None
       for t in ("Bash", "Read", "Grep", "Skill")))
ok("a spawn with no brief stores nothing, rather than an empty string that reads as a silent one",
   _artifact_payload("Agent", {"subagent_type": "Explore"}) is None)

_db = Path(_tf.mkdtemp()) / "s.db"
_s = _sp.SystemSpine(_db)
_rid = _s.start_run(repo_id="r", mode="dev", feature="investigate", item_id="i")
with _s._conn() as _c:
    _c.execute("UPDATE run SET phase='investigate' WHERE id=?", (_rid,))
_s.log_run_event(repo_id="r", kind="subagent", name="Agent", run_id=_rid, item_id="i",
                 description="Subagent (superme-dev:investigator · brief 77)", payload=BRIEF)
_s.log_run_event(repo_id="r", kind="subagent", name="Agent", run_id=_rid, item_id="i",
                 description="Subagent (legacy · brief 900)")     # pre-payload spawn
_kept = _s.subagent_briefs("r", "i", phase="investigate")
ok("the brief comes back whole, with the row that names who was spawned",
   len(_kept) == 1 and _kept[0]["text"] == BRIEF and "investigator" in _kept[0]["label"])
ok("a pre-payload spawn is ABSENT, not present-and-empty — nothing was kept, and an empty "
   "brief would read as one that said nothing",
   all(b["text"] for b in _kept))
ok("sizes still cover both: measured from the brief, parsed for the older spawn",
   _s.brief_sizes("r", "i", phase="investigate") == [len(BRIEF), 900])
shutil.rmtree(_db.parent, ignore_errors=True)

# Both halves can be right while the JOIN is missing: a payload nobody passes is a column that
# stays NULL on every real run.
_runs_src = src("superme_agent/daemon/services/runs.py")
ok("the recorder actually passes the payload through — the join, not just the two ends",
   "payload=_artifact_payload(" in _runs_src)
ok("…and a spawn with no prompt records no size rather than a zero",
   "brief" not in _artifact_desc("Agent", {"subagent_type": "Explore"})[2])

# The skill must say the reader starts WITHOUT the parent's context, so the brief is the only
# channel. The invariant, not the sentence.
_split_step = _SPLIT_STEP
ok("the skill requires a brief that stands alone, since the subagent inherits nothing",
   "read nothing else" in _split_step)
ok("…and requires the family's bar PASTED, not named",
   "pasted" in _split_step and "not as a path" in _split_step)
ok("…and preflights the subject before paying for a split",
   "confirm the subject is there" in _split_step
   and _split_step.index("confirm the subject") < _split_step.index("subagent_type"))
ok("every family guide says what travels in the brief",
   all("brief" in (_GUIDES / f"{f}.md").read_text(encoding="utf-8") for f in _kp.RESEARCH_KINDS))

# One gate, one row set: NEITHER side reads a counter of its own, so adding one cannot reach the
# owner and miss the deputy.
_dep = src("superme_agent/daemon/services/deputy.py")
_rt = src("superme_agent/daemon/routers/dev/gates.py")
ok("the deputy inlines no counter of its own — it spreads the shared reader",
   "gate_counters(" in _dep
   and not any(f"_spine.{k}(" in _dep for k in ("subagent_count", "read_hits", "brief_sizes")))
ok("…and the owner's route does the same, so one gate cannot show two row sets",
   "gate_counters(" in _rt
   and not any(f"spine.{k}(" in _rt for k in ("subagent_count", "read_hits", "brief_sizes")))


# ── two rows that are not about research ───────────────────────────────────── They live here
# because this suite already owns the gate-row contract.
from superme_agent.core.gate_briefs import standards_check, instrumentation_check
from superme_agent.core import git_layer as _gl

print("\n— the review gate asks the SECOND bar, not just the plan —")
ok("no recorded standards → the row is absent (a young repo cannot fail to have read them)",
   standards_check(None) is None)
ok("review that never opened them fails", standards_check(0)["ok"] is False)
ok("…and the row says WHY it matters — a plan-only verdict cannot report a departure",
   "second bar" in standards_check(0)["detail"])
ok("a read passes, with the count", standards_check(3)["ok"] is True and "3×" in standards_check(3)["detail"])
ok("standards_read is VISIBLE, never greys Approve — it says the bar was consulted, not cleared",
   "standards_read" not in _gb.split('"review": (')[1].split(")")[0])
ok("the review skill directs the read", "decisions.md` and `architecture.md`" in
   src("superme_agent/harness/plugins/superme-dev/skills/review/SKILL.md"))
ok("…and the record has a slot for what departs",
   "## Against our own decisions" in
   src("superme_agent/harness/plugins/superme-dev/skills/review/templates/review-template.md"))
ok("…and the two bars are never reranked against each other",
   "own line under `What to push back on`" in
   src("superme_agent/harness/plugins/superme-dev/skills/review/SKILL.md"))

print("\n— the review gate greps the branch for surviving instrumentation —")
ok("no branch to read → absent, never 'clean'", instrumentation_check(None) is None)
ok("an empty grep IS an answer", instrumentation_check([])["ok"] is True)
ok("a surviving probe fails and names its file",
   instrumentation_check([{"path": "a/b.py", "tag": "[DEBUG-a4f2]", "line": "x"}])["ok"] is False
   and "a/b.py" in instrumentation_check([{"path": "a/b.py", "tag": "[DEBUG-a4f2]", "line": "x"}])["detail"])
ok("debug_clean is VISIBLE — a slip worth a line of attention, not a refusal",
   "debug_clean" not in _gb.split('"review": (')[1].split(")")[0])
ok("the tag pattern needs the DEBUG prefix and hex, so prose never trips it",
   _gl.DEBUG_TAG.findall("[DEBUG-a4f2] and [DEBUG-9bd1]") == ["[DEBUG-a4f2]", "[DEBUG-9bd1]"]
   and _gl.DEBUG_TAG.findall("we should debug this [later]") == [])
ok("the build skill asks for the tag and the one-grep sweep",
   all(k in src("superme_agent/harness/plugins/superme-dev/skills/build/SKILL.md")
       for k in ("[DEBUG-", "one grep")))

print("\n— one reader for every counter the item folder cannot answer —")
_dr = src("superme_agent/daemon/services/drilldown.py")
ok("gate_counters returns every gate_state counter, in one place",
   all(k in _dr.split("def gate_counters")[1].split("def build_payload")[0]
       for k in ("subagents", "guide_reads", "brief_sizes", "debug_tags", "standards_reads")))
ok("the owner's route spreads it rather than inlining counters",
   "**drilldown.gate_counters(" in src("superme_agent/daemon/routers/dev/gates.py"))
ok("…and so does the deputy, so one gate can never show two row sets",
   "gate_counters(" in src("superme_agent/daemon/services/deputy.py"))

print("\n— the shared glossary —")
_gl_doc = Path("superme_agent/harness/plugins/superme-dev/references/glossary.md")
ok("the glossary exists where every skill can reach it", _gl_doc.is_file())
_g = _gl_doc.read_text(encoding="utf-8")
ok("…and carries Rejected framings — what a word will NOT mean", "## Rejected framings" in _g)
ok("…and Flagged ambiguities, including the pair that cost weeks",
   "## Flagged ambiguities" in _g and "`feature` vs `phase`" in _g)
ok("…and an Avoid line on the terms that drift",
   _g.count("*Avoid*:") >= 20)
ok("the charter points at it (one line — the charter is always loaded)",
   "glossary.md" in src("superme_agent/harness/dev-charter.md"))
ok("every authoring standard points at it too",
   all("glossary.md" in (Path("superme_agent/harness/plugins/superme-dev/skills") / r).read_text(encoding="utf-8")
       for r in ("forge-skill/references/writing-skills.md",
                 "forge-agent/references/writing-agents.md",
                 "forge-constitution/references/writing-constitutions.md")))


# ── the guide prose pass ────────────────────────────────────────── "12 of 41" is unreadable
# until the record says what 41 was.
print("\n— standing sweeps fork their enumeration, and say which they got —")
_STANDING = ("audit", "refactoring", "housekeeping", "security")
ok("the breadth fork lives in exactly the four standing guides",
   all("whole repo" in (_GUIDES / f"{f}.md").read_text(encoding="utf-8") for f in _STANDING))
ok("…and each names both breadths as a branch table, not a paragraph",
   all((_GUIDES / f"{f}.md").read_text(encoding="utf-8").count("| **whole repo**") == 1
       and "**one area**" in (_GUIDES / f"{f}.md").read_text(encoding="utf-8") for f in _STANDING))
ok("refactoring's whole-repo enumeration is the GIT HISTORY, not the file tree",
   "GIT HISTORY, not the file tree" in flat((_GUIDES / "refactoring.md").read_text(encoding="utf-8")))
ok("…because deepening pays off in future changes, so the churn is the signal",
   "finished" in (_GUIDES / "refactoring.md").read_text(encoding="utf-8").split("| **whole repo**")[1].split("|")[0]
   or "changed most" in (_GUIDES / "refactoring.md").read_text(encoding="utf-8"))
ok("security enumerates by BOUNDARY — directories cut a path in half",
   "Trust boundaries are the unit; directories are not" in flat((_GUIDES / "security.md").read_text(encoding="utf-8")))
ok("housekeeping sweeps by KIND across the tree, not directory by directory",
   "by KIND" in (_GUIDES / "housekeeping.md").read_text(encoding="utf-8"))
_TPL = _GUIDES.parent / "templates"
ok("every standing template demands the breadth in its surface slot",
   all("OPEN WITH THE BREADTH" in (_TPL / f"investigation-{f}-template.md").read_text(encoding="utf-8")
       for f in _STANDING))
ok("the commissioned families get no breadth fork — they follow one thread",
   not any("| **whole repo**" in (_GUIDES / f"{f}.md").read_text(encoding="utf-8")
           for f in ("study", "deep-diagnosis")))

print("\n— deep-diagnosis gates on a tight loop that goes red —")
_dd = (_GUIDES / "deep-diagnosis.md").read_text(encoding="utf-8")
ok("the loop is the gate, stated as a refusal", "no red command, no hypothesis" in _dd)
ok("…and 'tight' is defined, not left to taste",
   all(q in _dd for q in ("**Red-capable**", "**Deterministic**", "**Fast**", "**Yours to run**")))
ok("…with a RANKED ladder of ways to build one", "1. **A script in your item folder" in _dd
   and "7. **Raise the rate" in _dd)
ok("…and the rung a research item cannot use is named, not silently offered",
   "cannot write a test into the repo" in _dd)
ok("failing to build one is a RESULT, with the one thing that would unblock it",
   "When you genuinely cannot build one" in _dd
   and "ONE thing that would unblock it" in flat(_dd))
ok("3-5 ranked falsifiable hypotheses, written before any is tested",
   "3–5 hypotheses, ranked, before testing any" in flat(_dd))
ok("…put to the owner as a non-blocking checkpoint, not a stall",
   "proceed on your own ranking" in flat(_dd))
ok("…and the record has a slot for them, so a post-hoc story cannot pass as method",
   "## Hypotheses, ranked" in (_TPL / "investigation-deep-diagnosis-template.md").read_text(encoding="utf-8"))

print("\n— the remaining step-6 edits —")
ok("refactoring carries the deletion test as a named test",
   "deletion test" in (_GUIDES / "refactoring.md").read_text(encoding="utf-8"))
# The rejection section must exist AND route away what only a later sweep needs, or it becomes a
# stale judgment the next run inherits.
_STAY = flat((_GUIDES / "housekeeping.md").read_text(encoding="utf-8"))
ok("housekeeping's `What must stay` is a named destination for rejected candidates",
   "## What must stay" in _STAY and "What looks dead and isn't" in _STAY)
ok("…and save-it-for-later material is routed to Open threads instead",
   "## Open threads" in _STAY and "next sweep" in _STAY)
# The positive-target pilot: ONE guide first, measured on the next live sweep before rolling out.
ok("audit states its three failure modes as moves to make, not bans",
   "## Three things to do instead" in (_GUIDES / "audit.md").read_text(encoding="utf-8"))
ok("…and audit alone dropped the prohibition HEADING, so the pilot has five controls",
   [f for f in _kp.RESEARCH_KINDS
    if "does NOT do\n" in (_GUIDES / f"{f}.md").read_text(encoding="utf-8").replace("## ", "\n## ")
    .split("\n## ", 1)[-1]] == [f for f in _kp.RESEARCH_KINDS if f != "audit"])
ok("…and audit's contents moved with its section — no heading it no longer has",
   "does NOT do" not in (_GUIDES / "audit.md").read_text(encoding="utf-8"))


# ── the family registry ────────────────────────────────────────── One row per family; what
# cannot derive is pinned, so a new row fails loudly.
print("\n— the family registry is the one declaration —")
ok("RESEARCH_KINDS is derived from the registry, not a second literal",
   _kp.RESEARCH_KINDS == tuple(f.slug for f in _kp.RESEARCH_FAMILIES))
ok("FANOUT_FAMILIES is derived too — one field, not two hand-kept tuples",
   _kp.FANOUT_FAMILIES == tuple(f.slug for f in _kp.RESEARCH_FAMILIES if f.standing))
ok("standing = the four whole-codebase families; commissioned = study + deep-diagnosis",
   [f.slug for f in _kp.standing_families()] ==
   ["audit", "refactoring", "housekeeping", "security"])
ok("audit is STANDING and is the only family that asks an interest first",
   [f.slug for f in _kp.RESEARCH_FAMILIES if f.asks_interest] == ["audit"])
ok("every family carries a launch icon and a blurb, so the bar needs no second table",
   all(f.icon and f.blurb for f in _kp.RESEARCH_FAMILIES))
ok("the slug IS the guide path and the template name — no branch anywhere",
   all(_kp.family_guide(f.slug) == f"references/{f.slug}.md"
       and _kp.family_template(f.slug) == f"investigation-{f.slug}" for f in _kp.RESEARCH_FAMILIES))
ok("…and every derived guide path exists on disk",
   all((_GUIDES.parent / _kp.family_guide(f.slug)).is_file() for f in _kp.RESEARCH_FAMILIES))
ok("…and every derived template resolves through the artifact router",
   all(_arts._template_name("investigation", "research", f.slug) == _kp.family_template(f.slug)
       for f in _kp.RESEARCH_FAMILIES))
ok("the artifact package routes templates off the registry, with no family list of its own",
   "for f in _kp.RESEARCH_FAMILIES" in src("superme_agent/core/artifacts.py"))
ok("the gate's guide needle is built from the registry too",
   "family_guide(fam)" in src("superme_agent/daemon/services/drilldown.py"))

# Agent-facing prose with a different job from the owner-facing blurb, so hand-written and pinned.
_tools = src("superme_agent/harness/tools/dev_tools.py")
ok("the triage tool's Literal lists exactly the registry's families",
   all(f'"{f.slug}"' in _tools.split("research_kind: Annotated[Literal[")[1].split("]")[0]
       for f in _kp.RESEARCH_FAMILIES)
   and _tools.split("research_kind: Annotated[Literal[")[1].split("]")[0].count('"')
   == 2 * len(_kp.RESEARCH_FAMILIES))
ok("the triage skill teaches every family, so none is unpickable in practice",
   all(f.slug in src("superme_agent/harness/plugins/superme-dev/skills/triage/SKILL.md")
       for f in _kp.RESEARCH_FAMILIES))
ok("the registry names both mirrors, so the next person knows where to look",
   all(k in src("superme_agent/core/vocab/kind_profiles.py")
       for k in ("TriageFacts.research_kind", "triage/SKILL.md")))


# ── a button-born sweep, and the plan phase that no longer exists ────────────
print("\n— investigate no longer reads a plan a research item does not have —")
_SK = Path("superme_agent/harness/plugins/superme-dev/skills/investigate")
_isk = (_SK / "SKILL.md").read_text(encoding="utf-8")
# The ORDER is pinned, not the explanation: guide, then questions, then code. Numbered steps
# cannot contradict each other the way prose did.
ok("the questions, the walls and Done are written before any code is read",
   "before you read any code" in flat(_isk))
ok("…and the guide precedes even them, by step number",
   _isk.index("## 1 ·") < _isk.index("## 2 ·") < _isk.index("## 3 ·"))
ok("…with a completion criterion a reader can check",
   "from your questions alone" in flat(_isk))
ok("nothing in the skill, the guides or the templates still points at plan.md",
   not any("plan.md" in p.read_text(encoding="utf-8") for p in
           [_SK / "SKILL.md", *(_SK / "references").glob("*.md"), *(_SK / "templates").glob("*.md")]))
ok("every investigation template demands the walls in its first section",
   all("WRITTEN FIRST, before any code" in (_SK / "templates" / f"investigation-{f}-template.md").read_text(encoding="utf-8")
       for f in _kp.RESEARCH_KINDS))
ok("…and the open-threads slot parks against the walls the run SET, not a plan's",
   "outside the walls you set above" in (_SK / "templates" / "investigation-template.md").read_text(encoding="utf-8"))

print("\n— a standing sweep is born classified, at investigate —")
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    dev = DevKnowledgeService()
    made = dev.create_work_item(root, "Audit — logic in the ledger", "subject",
                                kind="research", research_kind="audit", born_at="investigate")
    it = dev.read_work_item(root, made["id"])
    ok("the button IS the classification — the family is stamped at birth",
       _kp.research_kind(it) == "audit")
    ok("…with a reason, like every other family write", bool(it.get("research_kind_reason")))
    ok("…and it enters at investigate, past a triage with no ticket to read",
       str(it.get("phase")) == "investigate")
    ok("an ordinary mint is untouched: unjudged, at phase 0",
       (lambda x: _kp.research_kind(x) is None and str(x.get("phase")) == "triage")(
           dev.read_work_item(root, dev.create_work_item(root, "ordinary", "", kind="research")["id"])))
    for bad, why in ((dict(kind="implementation", research_kind="audit"),
                      "a family on an implementation item"),
                     (dict(kind="research", research_kind="vibes"), "an unknown family"),
                     (dict(kind="research", born_at="build"), "a phase this kind does not have")):
        try:
            dev.create_work_item(root, "bad", "", **bad)
            ok(f"{why} is refused", False)
        except ValueError:
            ok(f"{why} is refused before any folder is written")

print("\n— the launch bar's routes —")
_rt = src("superme_agent/daemon/routers/dev/sweeps.py")
ok("only STANDING families get a button — a commissioned one is a 400",
   "is commissioned, not standing" in flat(_rt))
ok("audit's interest is required, because its question needs one",
   "needs an interest" in _rt)
ok("the routes sit under /dev/research/, clear of the LEARNING capture sweep at /dev/sweep",
   '"/dev/research/sweeps' in _rt and '"/dev/sweep"' not in _rt)
ok("…and the glossary records the two live meanings of the word",
   "Sweep — two live meanings" in
   src("superme_agent/harness/plugins/superme-dev/references/glossary.md"))
ok("the first investigate run is fired for the owner, not left for a second click",
   "fire_first_investigate" in _rt
   and "def fire_first_investigate" in src("superme_agent/daemon/services/runs.py"))

print(f"\n✓ ALL {PASS} CHECKS PASS")


# The trigger sequence is what a reader acts on first, and a long reference must show its full
# scope on a partial read.
_AG = Path("superme_agent/harness/plugins/superme-dev/skills/investigate/agents/"
           "investigator-agent.md").read_text(encoding="utf-8")
ok("the agent leads with its trigger sequence, not with rationale",
   "When invoked:" in _AG and _AG.index("When invoked:") < len(_AG) // 4)
ok("…and stays near the length the official examples hold to",
   len(_AG.splitlines()) < 140)
ok("the multi-step investigate workflow ships a checklist the agent can tick",
   "Investigation progress:" in _sk and _sk.count("- [ ]") >= 6)
# A reference file arrives whole in one context, so nothing scrolls: the headings ARE the map.
_HARNESS = Path("superme_agent/harness")
_toc = [str(f.relative_to(_HARNESS.parent)) for f in _HARNESS.rglob("references/*.md")
        if "## Contents" in f.read_text(encoding="utf-8")]
ok(f"no reference file duplicates its own headings as a Contents block — found: {_toc}", not _toc)

# ── no prompt surface logs an incident ──────────────────────────── The rule an incident produced
# belongs; the incident does not. Docstrings are exempt.
print("\n— prompts instruct, they do not log —")
import re as _re
_PROMPTS = (Path(__file__).resolve().parents[1] / "superme_agent/harness")
_DATE = _re.compile(r"\b20\d\d-\d\d-\d\d\b")
_ALLOW = ("created_at", "updated_at", "date read", "URL + date", "<date>", "YYYY", "2026-01-01")
_logged = []
for _f in _PROMPTS.rglob("*.md"):
    for _n, _line in enumerate(_f.read_text(errors="ignore", encoding="utf-8").splitlines(), 1):
        if _DATE.search(_line) and not any(a in _line for a in _ALLOW):
            _logged.append(f"{_f.relative_to(_PROMPTS.parent)}:{_n}")
ok(f"no dated incident record in any harness prompt — found {_logged}", not _logged)

# The body of a skill IS the prompt: a markdown comment is tokens the reader cannot act on.
_commented = [str(f.relative_to(_HARNESS.parent)) for f in _HARNESS.rglob("*.md")
              if "/templates/" not in str(f) and "<!--" in f.read_text(errors="ignore", encoding="utf-8")
              and "`<!--" not in f.read_text(errors="ignore", encoding="utf-8")]
ok(f"no markdown comment in any instruction surface — found {_commented}", not _commented)


# ── work_kind: proposed on the row, resolved on the item ──────── The JOIN is what breaks:
# written at one end, never read at the other.
print("\n— work_kind: the filer proposes, triage confirms —")
import superme_agent.core.inbox_flow as _flow
from superme_agent.core.dev_store import DevStore

_ds_src = src("superme_agent/core/dev_store.py")
_fl_src = src("superme_agent/core/inbox_flow.py")
_dt_src = src("superme_agent/harness/tools/dev_tools.py")
_ks_src = src("superme_agent/core/kernel_speech.py")

ok("the column is `work_kind`, never a second `kind` on the same row",
   'ADD COLUMN work_kind TEXT' in _ds_src)
ok("push carries the row's proposal into BOTH the item's kind and its birth stamp",
   'kind=row.get("work_kind") or "implementation"' in _fl_src
   and 'proposed_kind=row.get("work_kind")' in _fl_src)
ok("the filing tool writes it through to the store", "work_kind=wk," in _dt_src)
# WHAT AN INBOX ITEM IS, enforced rather than described: requiring the kind IS requiring that the
# row be work at all.
ok("`work_kind` is REQUIRED on the filing tool — a row that cannot name which machinery it becomes "
   "is not an inbox item", 'work_kind: Required[Annotated[Literal["implementation", "research"]'
   in _dt_src)
ok("…and the refusal says WHY, so the agent files it somewhere real instead of guessing a type",
   "is not an inbox item. A settled decision" in _dt_src)
ok("agents mint `item` and never `note` — a free note is the owner's, which is what makes the "
   "distinction trustworthy rather than advisory", 'kind="item",' in _dt_src)
ok("…and says the choice back, so an owner reading the session can argue with it",
   "def _wk_note(" in _dt_src and "_wk_note(_s(args, 'work_kind'))" in _dt_src)
ok("triage's preamble states the proposal before the tool refuses on it",
   'This item was filed as' in _ks_src and "phase == \"triage\"" in _ks_src)

with tempfile.TemporaryDirectory() as td:
    root, dev = Path(td), DevKnowledgeService()
    store = DevStore(Path(td) / "dev.db")
    row = store.add_inbox("t", "a decision with no code", title="G — three judgment calls",
                          work_kind="research")
    ok("the store round-trips the proposal", row["work_kind"] == "research")
    wi = _flow.push_inbox_item(store, dev, root, row, context_id="t")
    it = dev.read_work_item(root, wi["id"])
    ok("a research-typed row lands a RESEARCH item, not the old implementation default",
       it["kind"] == "research")
    ok("…and the item remembers what was claimed", it.get("proposed_kind") == "research")
    plain = store.add_inbox("t", "no one judged this")
    it2 = dev.read_work_item(root, _flow.push_inbox_item(
        store, dev, root, plain, context_id="t")["id"])
    ok("an unproposed row is today's behaviour exactly: implementation, nothing claimed",
       it2["kind"] == "implementation" and it2.get("proposed_kind") is None)
    for bad in ("Research", "chore"):
        try:
            store.add_inbox("t", "x", work_kind=bad)
            ok(f"a work_kind of {bad!r} is refused at create", False)
        except ValueError:
            ok(f"a work_kind of {bad!r} is refused loud at create, never silently dropped")
        try:
            store.update_inbox(plain["id"], work_kind=bad)
            ok(f"a work_kind of {bad!r} is refused at update", False)
        except ValueError:
            # This field's fallback is NULL, a real state, so a dropped typo reads back as a
            # deliberate clear.
            ok(f"…and at update too — a dropped typo would read as a deliberate clear")
    ok("an empty string is the deliberate clear, and it works",
       store.update_inbox(plain["id"], work_kind="")["work_kind"] is None)
    try:
        dev.create_work_item(root, "no kind at all", "")
        ok("create_work_item without a kind is refused", False)
    except TypeError:
        ok("create_work_item without a kind is refused — the default that bypassed "
           "KIND_PROFILES is gone")

ok("triage refuses a kind that contradicts the filed one, recording nothing",
   "This item was FILED as" in _dt_src
   and "you may not overrule the filer alone" in _dt_src
   and "was recorded. End your run with report_completion(machine.outcome='needs_user')" in _dt_src)
ok("…and the only way past it is the owner's answer, quoted",
   "kind_override_reason" in _dt_src and "item.kind_override" in _dt_src)
ok("the triage-exit gate says out loud when a proposal was overruled",
   "(filed as {proposed})" in src("superme_agent/core/gate_briefs.py"))
_SKILL_HOME = Path("superme_agent/harness/plugins/superme-dev/skills")
_TRI = _SKILL_HOME / "triage" / "SKILL.md"
ok("…and the triage skill names the one legal move on a disagreement",
   "machine.outcome='needs_user'" in _TRI.read_text(encoding="utf-8")
   and "Already filed under a kind" in _TRI.read_text(encoding="utf-8"))
ok("itemize carries the report's own typing instead of discarding it",
   "Carry the proposal's own typing into `work_kind`"
   in (_SKILL_HOME / "itemize" / "SKILL.md").read_text(encoding="utf-8"))

# ── the handoff brief's contract ───────────────────────────────── The BAR is what a slot owes
# when the filer has a source to carry it from.
print("\n— the handoff brief owes a bar, not a template —")
_ITZ = (_SKILL_HOME / "itemize" / "SKILL.md").read_text(encoding="utf-8")
ok("the shape stays ONE skeleton — no per-caller brief template file was added",
   not list((_SKILL_HOME / "itemize").glob("templates/*")))
ok("itemize states what each of the four brief fields owes",
   all(f"`{f}`" in _ITZ for f in ("background", "discussion", "direction", "constraints")))
ok("…including the section-not-just-the-file rule, which is what a cold reader needs",
   "Name the section, not just the file" in _ITZ)
ok("…and the commit, so a finding reads as STALE rather than as wrong",
   "commit sha the report measured against" in _ITZ)
ok("…and that an empty slot is only honest where the report was",
   'Write "none" only where the report wrote none' in _ITZ)
ok("triage is told preliminary/ is read-only, so a thin brief is reported not invented",
   "`preliminary/` is read-only" in (_SKILL_HOME / "triage" / "SKILL.md").read_text(encoding="utf-8"))

_dt = src("superme_agent/harness/tools/dev_tools.py")
from superme_agent.harness.tools.dev_tools import _brief_nudge, _BRIEF_FIELDS
ok("the four brief fields are one list, not four literals",
   _BRIEF_FIELDS == ("background", "discussion", "direction", "constraints"))
_full = dict.fromkeys(_BRIEF_FIELDS, "x")
ok("a fully filled brief is never nudged",
   not _brief_nudge(_full, spawned=True, repairable=True)
   and not _brief_nudge(_full, spawned=False, repairable=True))
ok("a plain capture is nudged only when WHOLLY empty — one filled slot may be all it had",
   not _brief_nudge({"background": "x"}, spawned=False, repairable=True)
   and "EMPTY" in _brief_nudge({}, spawned=False, repairable=True))
ok("a branch-off is held to EACH field — the parent already holds the answer",
   "constraints slot is EMPTY" in _brief_nudge(
       {**_full, "constraints": ""}, spawned=True, repairable=True))
ok("…and once auto-pushed it is told the brief can no longer be amended, not to amend it",
   "cannot be amended" in _brief_nudge({}, spawned=True, repairable=False)
   and "append_inbox_item" not in _brief_nudge({}, spawned=True, repairable=False))
ok("the auto-push branch runs the check too — those children cold-start immediately",
   "_brief_nudge(args, spawned=True, repairable=False)" in _dt)
ok("an append can name its brief SECTION; it no longer lands in `discussion` regardless",
   "brief_field" in _dt and "**{field: addition}" in _dt)
ok("…and an unknown section is refused rather than silently defaulted",
   "`brief_field` must be one of" in _dt)

# PUSH is the honest home: the brief is editable in the inbox and immutable once it lands.
print("\n— the brief check runs where it can still change something —")
with tempfile.TemporaryDirectory() as td:
    root, dev = Path(td), DevKnowledgeService()
    store = DevStore(Path(td) / "p.db")
    def _push(title, **brief):
        row = store.add_inbox("t", "body text", title=title)
        if brief:
            _arts.write_handoff_brief(_flow.inbox_content_dir(root, row["id"]), title, **brief)
        return _flow.push_inbox_item(store, dev, root, row, context_id="t")
    bare = _push("Bare capture")
    ok("a capture with NO brief is named, not passed over silently",
       bare["brief_issues"] and "only context is its row text" in bare["brief_issues"][0])
    row = store.add_inbox("t", "body", title="Empty brief")
    _arts.write_handoff_brief(_flow.inbox_content_dir(root, row["id"]), "Empty brief")
    empty = _flow.push_inbox_item(store, dev, root, row, context_id="t")
    ok("a scaffolded-but-unfilled brief is caught by the check that had no caller",
       empty["brief_issues"] == ["every section is empty — a brief needs at least one filled section"])
    good = _push("Real brief", background="why this was raised")
    ok("one real section is enough — D5 keeps every section optional", good["brief_issues"] == [])
    ok("…and the push still SUCCEEDS in every case: this reports, it never blocks",
       all(w["id"] for w in (bare, empty, good)))
    pushes = [e for e in store.list_events("t") if e["kind"] == "inbox.push"]
    ok("the finding lands in the permanent trace, not only in a return value",
       any((e.get("meta") or {}).get("brief_issues") for e in pushes))

_if = src("superme_agent/core/inbox_flow.py")
ok("the check reads the brief where it now LIVES, not where it was written",
   'preliminary" / "handoff-brief.md"' in _if)
ok("both push callers surface it — the owner's route and the agent's tool",
   '"brief_issues": wi.get("brief_issues") or []'
   in src("superme_agent/daemon/routers/dev/inbox.py")
   and "wi.get('brief_issues')" in _dt)

# ── a research run must see the repo's ignored SOURCE ───────────── A negative claim cannot be
# made from an incomplete tree.
print("\n— the scratch worktree sees the ignored source the owner named —")
from superme_agent.core.spine import RepoConfig, SystemSpine
from superme_agent.daemon.services.git_ops import _mirror_source_ignored, _is_secret

ok("a repo names its ignored source and the config carries it",
   RepoConfig(id="r", label="", cwd="/tmp", source_ignored=["scripts"]).source_ignored == ["scripts"])
ok("…and a repo that names nothing keeps today's behaviour",
   RepoConfig(id="r", label="", cwd="/tmp").source_ignored == [])
ok("the YAML key actually reaches the config — declaring the field is not reading it",
   "source_ignored=spec.get" in src("superme_agent/core/spine.py"))

_rc = RepoConfig(id="probe", label="", cwd="/tmp",
                 source_ignored=["scripts/", "/etc/passwd", "../up", "a/b"])
ok("an absolute path is refused, not silently made relative",
   "etc/passwd" not in _rc.source_ignored and "/etc/passwd" not in _rc.source_ignored)
ok("a parent-escaping path is refused", not any(".." in x for x in _rc.source_ignored))
ok("ordinary paths survive, slash-trimmed", _rc.source_ignored == ["scripts", "a/b"])

for _s in (".env", ".env.local", "id_rsa", "server.pem", "app.key", "credentials.json"):
    ok(f"{_s} is never source", _is_secret(_s))
for _s in ("test_ws_s3.py", "parity.py", "keys.py", "environment.md"):
    ok(f"{_s} is not mistaken for a secret", not _is_secret(_s))

with tempfile.TemporaryDirectory() as td:
    repo, wt = Path(td) / "repo", Path(td) / "wt"
    (repo / "scripts" / "__pycache__").mkdir(parents=True)
    (wt / "scripts").mkdir(parents=True)
    (repo / "scripts" / "test_a.py").write_text("calls descendants()", encoding="utf-8")
    (repo / "scripts" / "stale.pyc").write_bytes(b"junk")
    (repo / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"junk")
    (repo / "scripts" / ".env.local").write_text("SECRET=1", encoding="utf-8")
    (repo / "scripts" / "parity.py").write_text("REPO", encoding="utf-8")
    (wt / "scripts" / "parity.py").write_text("CHECKOUT", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1", encoding="utf-8")
    n, skips = _mirror_source_ignored(repo, wt, ["scripts", "nope", ".env"])
    here = {p.relative_to(wt).as_posix() for p in wt.rglob("*") if p.is_file()}
    ok("the ignored suite lands in the tree the run reads", "scripts/test_a.py" in here)
    ok("a PARTLY-tracked directory is merged, not skipped — the case the defect lived in", n == 1)
    ok("the checkout's own copy of a tracked file wins",
       (wt / "scripts" / "parity.py").read_text(encoding="utf-8") == "CHECKOUT")
    ok("caches and build junk stay out",
       not any(x.endswith(".pyc") or "__pycache__" in x for x in here))
    ok("a secret INSIDE an allowed path is dropped silently",
       "scripts/.env.local" not in here)
    ok("a secret named OUTRIGHT is refused loudly — the allowlist cannot lift that floor",
       ".env" not in here and any("looks like a secret" in s for s in skips))
    ok("a path that is not there is reported, not passed over in silence",
       any("not present in the repo" in s for s in skips))
    ok("mirrored files are read-only, so a copy can never read as a place to edit",
       (wt / "scripts" / "test_a.py").stat().st_mode & 0o222 == 0)
    # …but the DIRECTORIES stay writable: unlinking depends on the dir's write bit, so read-only
    # dirs leave an undeletable worktree behind.
    ok("the mirrored directory stays writable, so the tree can still be swept at close",
       (wt / "scripts").stat().st_mode & 0o200 != 0)
    import shutil as _sh
    _probe = Path(td) / "removable"
    _sh.copytree(wt, _probe)
    _sh.rmtree(_probe)                # raises if a mode blocks disposal
    ok("…proven by removing a copy of the mirrored tree outright", True)

ok("the mirror runs only on a FRESH tree — a reused one already has it, read-only",
   "if not rec.get(\"reused\"):" in src("superme_agent/daemon/services/git_ops.py"))

# ── a stopped run must not lose its correction ──────────────────── A resumed item re-firing the
# plain prompt reads its own finished transcript and no-ops.
print("\n— a resume carries the send-back, and a crash carries its cause —")
from superme_agent.core import deputy as _dep
from superme_agent.core.agent_service import _cli_stderr, cli_stderr_tail
from superme_agent.core import faults as _f

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    ok("no log at all reads as nothing pending", _dep.pending_send_back(d) is None)
    _dep.append_decision(d, "review", "send_back", "claim A is false",
                         change="re-run the grep including the ignored suites")
    p = _dep.pending_send_back(d)
    ok("a send-back is pending, and carries the asked-for CHANGE, not just the reason",
       p and "ignored suites" in p.get("change", ""))
    _dep.append_decision(d, "review", "approve", "fixed now")
    ok("…and stops being pending the moment the deputy says something else",
       _dep.pending_send_back(d) is None)

_rn = src("superme_agent/daemon/services/runs.py")
ok("the feedback firer swaps in the item's own cwd, like every phase runner already did",
   "ensure_scratch_worktree" in _rn.split("def fire_phase_feedback")[1].split("def ")[0])
ok("…and the resolved tree becomes the run's own cwd, not the repo root",
   "replace(ctx, cwd=repo_dir)" in _rn.split("def fire_phase_feedback")[1].split("def ")[0])

_rs = src("superme_agent/daemon/services/resume.py")
ok("resume routes a pending send-back through the FEEDBACK firer, not the plain phase firer",
   "pending_send_back" in _rs and "fire_phase_feedback" in _rs)
ok("…and falls back to the plain re-run if that firer will not start — never a dead button",
   "falling back to a plain re-run" in _rs)

_cli_stderr("Error: unknown option --nope")
ok("the CLI's stderr is captured now that a callback is registered",
   "unknown option" in cli_stderr_tail())
ok("…and the SDK is actually asked for it — no callback, no pipe",
   "stderr=_cli_stderr" in src("superme_agent/core/agent_service.py"))
_fault = _f.classify(exc=RuntimeError("Command failed with exit code 1"))
ok("a launch crash now REPORTS the cause instead of pointing at a stderr nobody kept",
   "unknown option" in _fault.reason)

print("\n— the typed proposal block: what a research item may DECIDE vs must ASK —")
# A research item finds things out; it does not choose. Exactly one shape may withhold work: a
# question the owner has not answered.
_PROP_TEMPLATE = Path("superme_agent/harness/plugins/superme-dev/skills/review/templates/"
                      "review-research-template.md").read_text(encoding="utf-8")
for _field in ("**Title:**", "**Kind:**", "**Why now:**", "**Delivers:**", "**Default applied:**",
               "**Question:**", "**Reserved because:**", "**Suggested:**", "**Answer:**",
               "**Rule:**"):
    ok(f"the research review template names {_field} verbatim — the parser matches on this string",
       _field in _PROP_TEMPLATE)
ok("…and the free-prose `depends-on` line it replaced is gone from the fill slot",
   "depends-on (or none)" not in _PROP_TEMPLATE)
ok("the template states the consequence where the agent writes it: no answer, not filed",
   "is NOT filed as work" in _PROP_TEMPLATE)
ok("…and that `Rule` is the exception, not the habit — an answer is spent, a rule is kept forever",
   "spent when the work is done" in _PROP_TEMPLATE and "usual case" in _PROP_TEMPLATE)
ok("…with the standalone bar stated as something checkable, not as 'be general'",
   "nothing the reader must look up" in _PROP_TEMPLATE)

# Named for a REASON and read as a token: the contract's own grammar produced the failure.
ok("the template says the field takes ONE BARE WORD and where the reasoning goes instead",
   "ONE bare word" in _PROP_TEMPLATE and "goes in Suggested" in _PROP_TEMPLATE)
_msg = _arts.research_proposal_issues(
    [{"title": "T", "question": "q?", "suggested": "s",
      "reserved_because": "destructive — the file is untracked, so there is no undo"}])[0]
ok("the gate's own message names the FIX, not just the fault — the reviser reads this and little "
   "else", "delete everything after it" in _msg and "`Suggested`" in _msg)
ok("…and offers that hint ONLY when the leading word is one of the two, so a genuinely wrong value "
   "is never told it was nearly right",
   "delete everything after it" not in _arts.research_proposal_issues(
       [{"title": "T", "question": "q?", "suggested": "s",
         "reserved_because": "it felt risky"}])[0])


def _review(body: str) -> Path:
    """A work-item folder holding just a review record with the given `## Proposed work` body."""
    d = Path(tempfile.mkdtemp())
    (d / "artifacts").mkdir()
    (d / "artifacts" / _arts.artifact_file("review")).write_text(
        "# Review\n\n## Proposed work\n" + body + "\n\n## Revision rounds\n_None._\n", encoding="utf-8")
    return d


_four = _review("""
**Title:** Nothing to decide
**Kind:** implementation
**Why now:** it rots
**Delivers:** a thing

**Title:** Safe default taken
**Kind:** implementation
**Why now:** cheap
**Delivers:** one copy instead of two
**Default applied:** dropped the duplicate; restoring it is one revert

**Title:** Owner must rule
**Kind:** implementation
**Why now:** unclear
**Delivers:** a public method removed
**Question:** keep the pair as intentional API, or delete?
**Reserved because:** expensive_to_reverse
**Suggested:** keep

**Title:** Owner ruled already
**Kind:** research
**Why now:** now
**Delivers:** a decision
**Question:** ship it?
**Reserved because:** destructive
**Suggested:** do not
**Answer:** do not ship it
""")
_props = _arts.research_proposals(_four)
_filed, _held = _arts.filed_and_withheld(_props)
ok("all four proposals parse", [p["title"] for p in _props] ==
   ["Nothing to decide", "Safe default taken", "Owner must rule", "Owner ruled already"])
ok("a proposal with no ruling fields files", "Nothing to decide" in [p["title"] for p in _filed])
ok("a proposal carrying a SAFE DEFAULT files — the default is stated, not withheld",
   "Safe default taken" in [p["title"] for p in _filed])
ok("an ANSWERED question files", "Owner ruled already" in [p["title"] for p in _filed])
ok("an UNANSWERED question is the only shape that withholds work",
   [p["title"] for p in _held] == ["Owner must rule"])
ok("…and it is withheld PER PROPOSAL — its three siblings still file, so one open question "
   "never holds settled work hostage", len(_filed) == 3)
ok("a clean set raises no structural issue", _arts.research_proposal_issues(_props) == [])

_wrapped = _arts.research_proposals(_review(
    "**Title:** Wrapped\n**Kind:** implementation\n**Why now:** a reason that runs past the\n"
    "margin and continues on the next line\n**Delivers:** x\n"))[0]
ok("a wrapped value joins its own field instead of opening a new one",
   _wrapped["why_now"] == "a reason that runs past the margin and continues on the next line")

_bad = _arts.research_proposal_issues(_arts.research_proposals(_review("""
**Title:** No reason given
**Kind:** implementation
**Why now:** x
**Delivers:** y
**Question:** should we?
**Suggested:** yes

**Title:** Reason off the list
**Kind:** implementation
**Why now:** x
**Delivers:** y
**Question:** should we?
**Reserved because:** because I said so
**Suggested:** yes

**Title:** Both at once
**Kind:** implementation
**Why now:** x
**Delivers:** y
**Default applied:** did it
**Question:** should we?
**Reserved because:** destructive
**Suggested:** yes

**Title:** No suggestion
**Kind:** implementation
**Why now:** x
**Delivers:** y
**Question:** should we?
**Reserved because:** destructive

**Title:** Answer with no question
**Kind:** implementation
**Why now:** x
**Delivers:** y
**Answer:** sure
""")))
_blob = " | ".join(_bad)
ok("a question with no `Reserved because` is a structural fault — naming the limb is what keeps "
   "the owner from being asked five times a run", "No reason given" in _blob)
ok("a `Reserved because` off the closed set is a fault, not a free-text note",
   "Reason off the list" in _blob)
ok("carrying BOTH a default and a question is a fault — a call is one party's or the other's",
   "Both at once" in _blob)
ok("a question with no `Suggested` is a fault — it makes the owner redo the research",
   "No suggestion" in _blob)
ok("an `Answer` with no `Question` is a fault — a ruling nobody can read back",
   "Answer with no question" in _blob)
ok("the reserved-reason set is closed to the two that earn a page",
   _arts.RESERVED_REASONS == ("destructive", "expensive_to_reverse"))

print("\n— the withhold JOIN: registered AND scoped AND policy-allowed —")
# Each of the three is correct and the tool still useless: registered-but-unallowed is callable
# and refused.
from superme_agent.harness import policy as _pol                          # noqa: E402
from superme_agent.harness.tools import dev_tools as _dt                  # noqa: E402
ok("`read_research_proposals` is registered as a tool",
   "read_research_proposals" in {s.name for s in _dt.DEV_TOOLS})
ok("…and scoped to the `itemize` run, the one run that files proposals",
   "read_research_proposals" in _dt.TOOL_SCOPES["itemize"])
ok("…and policy-allowed, or a background itemize is denied and files the withheld ones anyway",
   "mcp__dev__read_research_proposals" in _pol.SAFE_TOOLS)

with tempfile.TemporaryDirectory() as _td:
    _r = Path(_td)
    _i = _r / "work-items" / "aaaabbbbcccc"
    (_i / "artifacts").mkdir(parents=True)
    (_i / "item.md").write_text("---\nid: aaaabbbbcccc\nkind: research\n---\n", encoding="utf-8")
    (_i / "artifacts" / _arts.artifact_file("review")).write_text(
        "# R\n\n## Proposed work\n"
        "**Title:** Files fine\n**Kind:** implementation\n**Why now:** rot\n**Delivers:** a thing\n"
        "**Default applied:** removed it; one revert restores\n\n"
        "**Title:** Owner must rule\n**Kind:** implementation\n**Why now:** unclear\n"
        "**Delivers:** a removed public method\n**Question:** keep or delete?\n"
        "**Reserved because:** expensive_to_reverse\n**Suggested:** keep\n\n## Revision rounds\n", encoding="utf-8")
    _fn = _dt._read_research_proposals(store=None, context_id="global", dev_root=_r,
                                       bound_item_id="aaaabbbbcccc")
    _out = asyncio.run(_fn({"item_id": "aaaabbbbcccc"}))["content"][0]["text"]
    ok("the tool hands itemize a filed-able list", "## File these (1)" in _out)
    ok("…and a withheld list it is told not to file",
       "## Do NOT file these (1)" in _out and "the owner has not ruled" in _out)
    ok("the withheld entry carries its QUESTION, so step 3 can name it to the owner",
       "question: keep or delete?" in _out)
    ok("the filed entry carries the default that was applied, for the brief's constraints",
       "default applied: removed it" in _out)
    _miss = asyncio.run(_dt._read_research_proposals(
        store=None, context_id="global", dev_root=_r,
        bound_item_id="ffffffffffff")({"item_id": "ffffffffffff"}))
    ok("an unknown item is refused, never a crash the run cannot report",
       bool(_miss.get("is_error")))

_REVIEW_SKILL = src("superme_agent/harness/plugins/superme-dev/skills/review/SKILL.md")
ok("the review skill states the sort that decides which calls reach the owner at all",
   "Sort every open call before writing the block" in _REVIEW_SKILL)
for _reason in _arts.RESERVED_REASONS:
    ok(f"…and names {_reason!r} — the skill's closed set and the parser's are ONE set, or a "
       "correctly-written question fails validation", _reason in _REVIEW_SKILL)
ok("the skill forbids the shape the parser rejects: a default and a question together",
   "Never both a default and a question" in _REVIEW_SKILL)
ok("investigate is told to settle what reading settles — limb 1 is the only one that must never "
   "reach the owner",
   "Settle what reading can settle" in
   src("superme_agent/harness/plugins/superme-dev/skills/investigate/SKILL.md"))

print("\n— the review gate: the owner sees what waits on them, and Approve stays live —")
from superme_agent.core import gate_briefs as _gb                         # noqa: E402


def _rulings(body: str) -> dict:
    d = _review(body)
    return next(c for c in _gb.research_readiness(d) if c["criterion"] == "owner_rulings")


_clean = _rulings("**Title:** A\n**Kind:** implementation\n**Why now:** x\n**Delivers:** y\n")
ok("a review needing no ruling says so plainly",
   _clean["ok"] and _clean["detail"] == "no proposal needs your ruling")

_waiting = _rulings(
    "**Title:** A\n**Kind:** implementation\n**Why now:** x\n**Delivers:** y\n\n"
    "**Title:** B\n**Kind:** implementation\n**Why now:** x\n**Delivers:** y\n"
    "**Question:** keep or delete?\n**Reserved because:** destructive\n**Suggested:** keep\n")
ok("a waiting proposal is NAMED at the gate — an absence from the inbox is invisible otherwise",
   "1 of 2 proposal(s) wait on you" in _waiting["detail"] and
   "keep or delete?" in _waiting["detail"])
ok("…with the suggested answer, so ruling is a confirmation not a second investigation",
   "suggested: keep" in _waiting["detail"])
ok("…and says what approving without ruling costs", "drops them" in _waiting["detail"])
ok("an open question does NOT fail the row — Approve stays live, per-proposal not per-review",
   _waiting["ok"] is True)

_broken = _rulings("**Title:** A\n**Kind:** implementation\n**Why now:** x\n**Delivers:** y\n"
                   "**Question:** should we?\n**Reserved because:** vibes\n**Suggested:** yes\n")
ok("a MALFORMED ruling field fails the row — the owner cannot answer terms that do not parse, "
   "and a bad reason silently changes which proposals file", _broken["ok"] is False)
ok("`owner_rulings` is in the review gate's blocking set, so the malformed case greys Approve",
   "owner_rulings" in _gb._BLOCKING["review"])

_DEPUTY = src("superme_agent/core/kernel_speech.py")
ok("the deputy is told an outstanding owner ruling is the designed resting state, not a gap — "
   "without this it send_backs to clear a question it may not answer, forever",
   "designed resting state, not a gap" in _DEPUTY)
ok("…and that no strictness or delegated authority lets it answer one",
   "under any delegated authority" in _DEPUTY)
ok("the deputy holds no tool that could write an answer into the record",
   set(_dt.TOOL_SCOPES["deputy"]) == {"read_dev_log", "read_run"})

ok("an UNFILLED template yields no proposals — a slot is never a ghost item",
   _arts.research_proposals(_review(
       "**Title:** <fill:imperative — what would be done>\n"
       "**Kind:** <fill:implementation | research>\n")) == [])
ok("a review with no `## Proposed work` at all yields none",
   _arts.research_proposals(Path(tempfile.mkdtemp())) == [])

# Older records keep parsing and filing: reading their prose as a question would retroactively
# withhold work.
_legacy = _arts.research_proposals(_review(
    "**Title:** Older record\n**Kind:** implementation\n**Why now:** a reason\n"
    "**Depends-on:** owner's ruling on question A\n**Delivers:** a thing\n"))
ok("a legacy `Depends-on:` lands in its own field instead of running on into `Why now`",
   _legacy[0]["why_now"] == "a reason" and
   _legacy[0]["legacy_depends_on"] == "owner's ruling on question A")
ok("…and it withholds nothing — it never had a reader, and closed items must not re-open",
   _arts.filed_and_withheld(_legacy)[1] == [])

print("\n— the decision ledger: a RULE outlives the item, an instruction does not —")
# The promotion test is the RULE, not the reason: one says whose call it was, not whether the
# answer generalizes.
from superme_agent.core import decision_ledger as _dl                     # noqa: E402

_RULED = """
**Title:** Nothing to decide
**Kind:** implementation
**Why now:** it rots
**Delivers:** a thing

**Title:** Owner ruled this one
**Kind:** implementation
**Why now:** a real caller exists, so deleting is no longer mechanical
**Delivers:** the pair removed or kept
**Question:** keep as intentional API, or delete?
**Reserved because:** expensive_to_reverse
**Suggested:** keep
**Answer:** keep them as intentional public API
**Rule:** an exported symbol with a live external caller is kept and documented, never deleted as dead

**Title:** Answered, but nothing general came of it
**Kind:** implementation
**Why now:** it sits in an ambiguous half-retired state
**Delivers:** the file gone
**Question:** delete it, or leave a stub?
**Reserved because:** destructive
**Suggested:** delete
**Answer:** delete it outright

**Title:** Still waiting on the owner
**Kind:** implementation
**Why now:** x
**Delivers:** y
**Question:** remove or relocate?
**Reserved because:** destructive
**Suggested:** remove
"""

with tempfile.TemporaryDirectory() as _td:
    _root = Path(_td)
    _item = _root / "work-items" / "aaaabbbbcccc"
    (_item / "artifacts").mkdir(parents=True)
    (_item / "artifacts" / _arts.artifact_file("review")).write_text(
        "# R\n\n## Proposed work\n" + _RULED + "\n\n## Revision rounds\n", encoding="utf-8")
    _ids = _dl.record_rulings(_root, _item, "aaaabbbbcccc", date="2026-08-17", project="Probe")
    _led = (_root / "general" / "decisions.md").read_text(encoding="utf-8")
    ok("a ruling that establishes a RULE becomes a ledger entry", _ids == ["D-001"])
    ok("…and only that one. The answered question with no `Rule` records NOTHING — an instruction "
       "is spent when its work is done, so a ledger holding it teaches a later reader nothing",
       _led.count("### D-") == 1 and "delete it outright" not in _led)
    ok("…nor does an unanswered question, nor a proposal that was never the owner's call",
       "remove or relocate" not in _led and "Nothing to decide" not in _led)
    ok("the HEADING is the rule — the heading is the whole index a later phase reads, so it must "
       "say what holds, not which ticket it came from",
       "### D-001 · an exported symbol with a live external caller is kept and documented, never "
       "deleted as dead · accepted" in _led)
    ok("…the rule again as the body's first claim", "- **Rule**: an exported symbol with a live "
       "external caller is kept and documented, never deleted as dead" in _led)
    ok("…the report's own reasoning as the Why — the words they were reading when they ruled",
       "- **Why**: a real caller exists, so deleting is no longer mechanical" in _led)
    ok("…the ruling that settled it, so the rule is traceable to an act and not to an agent",
       "- **Ruling that settled it**: keep them as intentional public API" in _led)
    ok("…and the provenance line naming the item AND the question it answered",
       "- **Source**: aaaabbbbcccc · owner ruling on: keep as intentional API, or delete?" in _led)
    ok("no fabricated `Rejected` line — nothing in the typed block records what was turned down, "
       "and an authored one is filler a future reader cannot use", "**Rejected**" not in _led)
    ok("the ledger is CREATED when a repo's first rule lands before anyone wrote the doc",
       _led.startswith("# Probe — decisions"))
    ok("…and its own header states the bar, so a hand-written entry meets the same one",
       "binding work nobody has proposed yet" in _led)

    # Approve can fire more than once, and an append-only ledger cannot take an entry back.
    ok("firing again records nothing — idempotent on (item, question)",
       _dl.record_rulings(_root, _item, "aaaabbbbcccc", date="2026-08-18") == [])
    ok("…and the file is untouched by the second call",
       (_root / "general" / "decisions.md").read_text(encoding="utf-8") == _led)

    # A rule from a DIFFERENT question appends, and the id never reuses a number.
    (_item / "artifacts" / _arts.artifact_file("review")).write_text(
        "# R\n\n## Proposed work\n"
        "**Title:** A second ruling\n**Kind:** implementation\n**Why now:** it ships for nothing\n"
        "**Delivers:** the files moved\n**Question:** remove or relocate?\n"
        "**Reserved because:** destructive\n**Suggested:** remove\n"
        "**Answer:** relocate them out of the served tree\n"
        "**Rule:** build output is never served from the source tree\n\n## Revision rounds\n", encoding="utf-8")
    ok("a rule from a different question appends",
       _dl.record_rulings(_root, _item, "aaaabbbbcccc", date="2026-08-18") == ["D-002"])
    ok("ids are monotonic and never reused — the id is the grep anchor and supersession target",
       [e["id"] for e in _dl.read_entries(_root)] == ["D-001", "D-002"])
    ok("the index a phase reads is HEADINGS only, so the cost stays flat as the ledger grows",
       _dl.settled_index(_root).count("\n") == 1 and "**Why**" not in _dl.settled_index(_root))

print("\n— fan-out: the check answers to triage, not to the family default —")
# A judgement living in prose no reader parses lets the check contradict a decision made upstream,
# and blame the run for obeying.
from superme_agent.core.gate_briefs import fanout_check as _fo             # noqa: E402
from superme_agent.core.vocab.kind_profiles import ITEM_FANOUT, item_fanout      # noqa: E402

ok("`fanout` is a CLOSED set, and separate from `scale` — size and splittability are different "
   "questions, and one field answering both is the defect this codebase keeps meeting",
   ITEM_FANOUT == ("expected", "bounded"))
ok("an item nobody judged reads as `expected`, so the family's prescription still stands",
   item_fanout({}) == "expected" and item_fanout({"fanout": "junk"}) == "expected")
ok("a whole-repo family that split nothing still FAILS when no one judged the surface bounded",
   _fo("housekeeping", 0)["ok"] is False)
ok("…and PASSES when triage judged it bounded — the run did as its brief said",
   _fo("housekeeping", 0, fanout="bounded")["ok"] is True)
ok("…which is not silence: the judgement is named, so the owner argues with the SIZING",
   "triage judged this surface bounded" in _fo("housekeeping", 0, fanout="bounded")["detail"]
   and "not the run" in _fo("housekeeping", 0, fanout="bounded")["detail"])
ok("the two pre-existing exemptions survive — a family that never splits, and a count nobody took",
   _fo("study", 0) is None and _fo("housekeeping", None) is None)
_TRI = src("superme_agent/harness/plugins/superme-dev/skills/triage/SKILL.md")
ok("triage is told the field exists and that prose is not a carrier",
   'Set `fanout: "bounded"`' in _TRI and "A judgement that lives in prose is one no" in _TRI)
ok("…and what stating it in prose alone actually costs",
   "blaming it for doing" in _TRI)

ok("an empty ledger says so rather than reading as a missing file",
   "no recorded decisions yet" in _dl.settled_index(Path(tempfile.mkdtemp())))

# The promotion test in isolation: a reason every proposal carries would promote every ruling,
# filling the ledger with spent instructions.
_p = _arts.research_proposals(_review(
    "**Title:** T\n**Kind:** implementation\n**Why now:** w\n**Delivers:** d\n"
    "**Question:** q?\n**Reserved because:** destructive\n**Suggested:** s\n**Answer:** a\n"))[0]
ok("an answered, owner-reserved ruling with no `Rule` is NOT promotable — being the owner's call to "
   "make says the ACTION was risky, not that the answer generalizes",
   not _arts.proposal_promotable(_p))
_p["rule"] = "a superseded module is deleted outright — no shim, no tombstone"
ok("…adding the rule promotes it, and that is the only field that does",
   _arts.proposal_promotable(_p))
_p["answer"] = ""
ok("a `Rule` with no `Answer` promotes nothing — a rule is what a ruling established, so with no "
   "ruling there is nothing it could have established", not _arts.proposal_promotable(_p))
ok("…and the gate calls that malformed, where the owner can still send the item back",
   any("`Rule` with no `Answer`" in i for i in _arts.research_proposal_issues([_p])))

# A rule outlives the item, so the gate is the last place to refuse one. Named even when no
# question is open.
_ruled_gate = _rulings(_RULED)["detail"]
ok("the review gate states what approving will write into the ledger, quoting the rule itself",
   "an exported symbol with a live external caller is kept" in _ruled_gate
   and "standing rule" in _ruled_gate)
ok("…and says what to do if it overreaches, since an append-only entry cannot be taken back",
   "reaches further than your ruling" in _ruled_gate)
ok("…while still naming the proposal that waits — a rule to confirm must not hide a call to make",
   "wait on you" in _ruled_gate)

print("\n— reading it back: the half that closes the loop —")
# Promotion alone changes nothing while only one phase reads the ledger.
ok("`read_decisions` is registered", "read_decisions" in {s.name for s in _dt.DEV_TOOLS})
for _ph in ("triage", "investigate", "review"):
    ok(f"…and scoped to `{_ph}` — a phase that can ask the owner something must be able to check "
       "what they already answered", "read_decisions" in _dt.TOOL_SCOPES[_ph])
ok("…and policy-allowed: a denial here does not stop the run, it makes it re-ask a settled question",
   "mcp__dev__read_decisions" in _pol.SAFE_TOOLS)
_INV = src("superme_agent/harness/plugins/superme-dev/skills/investigate/SKILL.md")
ok("investigate is told to check the ledger BEFORE passing any call up",
   "Before you pass ANY call up, `read_decisions`" in _INV)
ok("review is told to check it before writing a question",
   "Before writing any limb-3 question, `read_decisions`" in _REVIEW_SKILL)
ok("review is told `Reserved because` takes one bare word, and where the reasoning goes instead",
   "takes ONE BARE WORD" in _REVIEW_SKILL and "belongs in `**Suggested:**`" in _REVIEW_SKILL)
ok("…and is shown the exact miss, since the RIGHT word plus prose still fails the gate",
   "the word is right and the field still fails" in _REVIEW_SKILL)
ok("…and before writing a RULE, so a second entry never restates a settled one",
   "If a `D-NNN` already says it, there is no new rule" in _REVIEW_SKILL)
ok("review is told that most rulings establish no rule, so an empty line reads as correct rather "
   "than as a gap the agent should fill", "leaving the line out is the correct outcome"
   in _REVIEW_SKILL)
ok("…and what an over-broad rule actually costs, since 'be careful' changes no behaviour",
   "silently suppresses questions that should have been asked" in _REVIEW_SKILL)
ok("triage reads it when scoping — a settled subject makes an item smaller, sometimes moot",
   "read_decisions" in src("superme_agent/harness/plugins/superme-dev/skills/triage/SKILL.md"))

_D7 = src("superme_agent/harness/tools/dev_tools.py")
ok("D7's refusal now says why a kernel-written decisions entry is not a violation of it — "
   "otherwise the next reader meets a ledger entry from a research item and calls it a bug",
   "immutable HISTORY, not current-state truth" in _D7)
ok("…and that no agent is in that write path", "THE KERNEL writes" in _D7)
_GATES = src("superme_agent/daemon/services/gates.py")
ok("the approve path is where a rule is recorded, beside the itemize it feeds",
   "decision_ledger" in _GATES)
ok("…and only an OWNER approve records one — the owner ruled on the question, but an AGENT wrote "
   "the sentence generalising it, and no later reader can tell the difference",
   'if actor == "owner":' in _GATES)
# Read the RENDERED preamble: the source stores adjacent literals, so a grep matches on where the
# author broke the line.
from superme_agent.core import kernel_speech as _ks                       # noqa: E402
_DEPUTY = _ks.deputy_preamble("high")
ok("the deputy is told to escalate rather than approve a review that would record a rule, and to "
   "quote it — the owner cannot refuse a sentence they were never shown",
   "escalate and quote the rule" in _DEPUTY and "not delegable" in _DEPUTY)
ok("…at every strictness, so a low-strictness deputy does not quietly become the one who decides "
   "what this project remembers",
   all("escalate and quote the rule" in _ks.deputy_preamble(s)
       for s in ("low", "medium", "high")))

print("\n— the line that SUMMONS the owner, not just the one inside the item —")
# A question the owner is never told about is a question never asked, however carefully the
# artifact records it.
from superme_agent.core import attention as _att                          # noqa: E402

_parked = {"id": "aaaabbbbcccc", "title": "probe", "kind": "research",
           "phase": "review", "status": "awaiting_human"}
_plain = _att.assign([_parked], set())["buckets"]["needs_you"][0]["reason"]
_asked = _att.assign([_parked], set(),
                     rulings_by_item={"aaaabbbbcccc": 2})["buckets"]["needs_you"][0]["reason"]
ok("an item merely waiting at review reads as a plain gate", _plain == "at the review gate — your decision")
ok("an item HOLDING A CALL for the owner does not read the same — otherwise it is invisible "
   "among the ones that only finished", _asked != _plain)
ok("…it says how many calls wait", "2 proposal(s)" in _asked)
ok("…and what approving without ruling costs", "drops them" in _asked)

print(f"\n✓ ALL {PASS} CHECKS PASS\n")
