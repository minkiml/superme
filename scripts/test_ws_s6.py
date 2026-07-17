"""WS-S6 gate test (unit half) — gates, knowledge writes & lifecycle (PRD stage S6).

Covers, without a daemon or LLM: the D7 knowledge-delta pipeline (validation rejects dead file
refs / ghost slugs / missing sections / placeholders; the deterministic writer applies update/
append atomically and stamps applied; a blocking child's delta folds into the parent; the
freshness lint detects truth decay); the D8 close-criteria evaluator (each mechanical refusal +
the all-green pass, for both kinds); the four gate briefs (★ language: continuity + delta +
narrative + uniform decision block, recommendation flips with state, mid-phase previews); and
the two new item tools (kind gating, itemized rejects, propose_close's no-state-change refusal
vs its all-green paging). The LIVE half (full lifecycle on the dummy repo through the daemon)
runs separately.

Run: PYTHONPATH=. python -m scripts.test_ws_s6
"""

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from superme_agent.core import artifacts as A
from superme_agent.core import gate_briefs as GB
from superme_agent.core import knowledge_delta as KD
from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.harness.tools.dev_tools import _stage_knowledge_delta, _propose_close

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def _seed_anchor_docs(dev_root: Path) -> None:
    g = dev_root / "general"
    g.mkdir(parents=True, exist_ok=True)
    (g / "project-prd.md").write_text(
        "# PRD\n\n## Problem\nstuff\n\n## Deliverables\n- **d-alpha** — the alpha thing\n")
    (g / "architecture.md").write_text(
        "# Architecture\n\n## Components\nOld component text.\n\n## Flows\nflow text\n")
    (g / "roadmap.md").write_text("# Roadmap\n\n## d-alpha\n- **w1** — first wave 🟢\n")
    (g / "spec.md").write_text("# Spec\n\n## Stack\npython\n")


def _git_repo(tmp: Path, name: str) -> Path:
    repo = tmp / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "real.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def test_delta_validation(tmp: Path) -> None:
    print("knowledge delta — validation (D7)")
    dev_root = tmp / "kd-root"
    _seed_anchor_docs(dev_root)
    repo = _git_repo(tmp, "kd-repo")

    good = [{"doc": "architecture", "section": "Components", "op": "update",
             "content": "New truth referencing `real.py` under d-alpha."}]
    ok("valid ops pass", KD.validate_ops(good, dev_root, repo) == [])
    cases = {
        "unknown doc": [{"doc": "nope", "section": "X", "op": "update", "content": "c"}],
        "missing section": [{"doc": "spec", "section": "Ghost", "op": "update", "content": "c"}],
        "dead file ref": [{"doc": "spec", "section": "Stack", "op": "append",
                           "content": "see `does/not/exist.py`"}],
        "ghost deliverable slug": [{"doc": "spec", "section": "Stack", "op": "append",
                                    "content": "serves d-ghost"}],
        "placeholder": [{"doc": "spec", "section": "Stack", "op": "append",
                         "content": "<fill:later>"}],
        "empty content": [{"doc": "spec", "section": "Stack", "op": "append", "content": " "}],
        "bad op kind": [{"doc": "spec", "section": "Stack", "op": "rewrite", "content": "c"}],
    }
    for name, ops in cases.items():
        assert KD.validate_ops(ops, dev_root, repo), name
    ok("each malformed op rejected with itemized issues", True)
    ok("empty list rejected", KD.validate_ops([], dev_root, repo) != [])


def test_delta_apply(tmp: Path) -> None:
    print("knowledge delta — deterministic writer + fold")
    dev_root = tmp / "ka-root"
    _seed_anchor_docs(dev_root)
    item = tmp / "ka-item"
    (item / "artifacts").mkdir(parents=True)
    KD.stage_delta(item, [
        {"doc": "architecture", "section": "Components", "op": "update", "content": "NEW BODY."},
        {"doc": "architecture", "section": "Flows", "op": "append", "content": "APPENDED LINE."},
    ])
    ok("staged reads back pending", KD.pending_delta(item) is not None
       and KD.delta_status(item, dev_root, None)["state"] == "staged")
    res = KD.apply_delta(item, dev_root)
    arch = (dev_root / "general" / "architecture.md").read_text()
    ok("update REPLACED the section body",
       "NEW BODY." in arch and "Old component text" not in arch)
    ok("append KEPT + extended the section",
       "flow text" in arch and "APPENDED LINE." in arch)
    ok("apply stamped — no longer pending, status=applied",
       res["applied"] == 2 and KD.pending_delta(item) is None
       and KD.delta_status(item, dev_root, None)["state"] == "applied")
    ok("re-apply is a no-op", KD.apply_delta(item, dev_root) == {"applied": 0})

    child, parent = tmp / "ka-child", tmp / "ka-parent"
    (child / "artifacts").mkdir(parents=True)
    (parent / "artifacts").mkdir(parents=True)
    KD.stage_delta(child, [{"doc": "spec", "section": "Stack", "op": "append", "content": "C1"}])
    KD.stage_delta(parent, [{"doc": "spec", "section": "Stack", "op": "append", "content": "P1"}])
    moved = KD.fold_into_parent(child, parent, "parent-id")
    ok("blocking child's delta folds into the parent's",
       moved == 1 and len(KD.pending_delta(parent)["ops"]) == 2
       and KD.delta_status(child, dev_root, None)["state"] == "folded")


def test_freshness_lint(tmp: Path) -> None:
    print("freshness lint (truth decay)")
    dev_root = tmp / "fl-root"
    _seed_anchor_docs(dev_root)
    repo = _git_repo(tmp, "fl-repo")
    ok("clean docs lint clean", KD.freshness_lint(dev_root, repo) == [])
    spec = dev_root / "general" / "spec.md"
    spec.write_text(spec.read_text() + "\nsee `gone/away.py` for details\n")
    warns = KD.freshness_lint(dev_root, repo)
    ok("dead anchor-doc file ref detected", any("gone/away.py" in w for w in warns), str(warns))


def _mk_item(dev, dev_root: Path, title: str, kind: str = "implementation", **fm) -> tuple[str, Path]:
    iid = dev.create_work_item(dev_root, title, "the description", kind=kind)["id"]
    item_dir = dev_root / "work-items" / iid
    return iid, item_dir


def _fill(item_dir: Path, artifact: str, sections: dict[str, str], *, kind: str = "implementation",
          facts: str | None = None) -> None:
    """Scaffold + crudely fill an artifact's sections (test stand-in for the agent)."""
    A.scaffold(item_dir, artifact, title="t", item_kind=kind)
    p = item_dir / "artifacts" / A.artifact_file(artifact)
    text = p.read_text()
    import re as _re
    for sec, content in sections.items():
        text = _re.sub(rf"(?ms)(^##\s+{_re.escape(sec)}\s*\n).*?(?=^##\s|\Z)",
                       rf"\g<1>{content}\n\n", text)
    text = _re.sub(r"<fill:[^>]*>", "filled", text)
    if facts is not None:
        text = _re.sub(r"(?ms)```yaml\n.*?```", f"```yaml\n{facts}\n```", text)
    p.write_text(text)


def test_close_readiness(tmp: Path) -> None:
    print("close criteria (D8) — mechanical refusals + all-green")
    dev = DevKnowledgeService()
    dev_root = tmp / "cr-root"
    _seed_anchor_docs(dev_root)
    repo = _git_repo(tmp, "cr-repo")
    iid, item_dir = _mk_item(dev, dev_root, "closer")
    item = dev.read_work_item(dev_root, iid)

    cr = GB.close_readiness(item, item_dir, dev_root, repo, [item])
    ok("bare item refused — required artifacts missing", not cr["ok"]
       and not next(c for c in cr["checks"] if c["criterion"] == "required_artifacts")["ok"])

    # Fill everything green: artifacts + evidence + merged fact.
    _fill(item_dir, "plan", {"Tasks": "- [x] did it"})
    A.record_evidence(item_dir, repo, check="tests", how="pytest", result="ok", passed=True)
    _fill(item_dir, "validation", {})
    _fill(item_dir, "readiness", {"Knowledge": "none-needed"})
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                         text=True).stdout.strip()
    _fill(item_dir, "closeout", {"Summary": "Delivered the thing."},
          facts=f'changed_files: ["real.py"]\ntests_run: "pytest"\nmerge_commit: "{sha}"')
    cr = GB.close_readiness(item, item_dir, dev_root, repo, [item])
    ok("all-green passes every criterion", cr["ok"],
       str([c for c in cr["checks"] if not c["ok"]]))

    # Individual refusals from the green state:
    kids = [item, {"id": "k1", "status": "active",
                   "spawned_from": {"item": iid, "relation": "parallel"}}]
    ok("non-terminal child refuses", not GB.close_readiness(item, item_dir, dev_root, repo,
                                                            kids)["ok"])
    (repo / "real.py").write_text("x = 2\n")   # repo edit → evidence stale
    cr = GB.close_readiness(item, item_dir, dev_root, repo, [item])
    ok("stale evidence refuses",
       not next(c for c in cr["checks"] if c["criterion"] == "evidence_fresh")["ok"])
    (repo / "real.py").write_text("x = 1\n")
    _fill(item_dir, "closeout", {"Summary": "Delivered."},
          facts=f'changed_files: ["ghost.py"]\ntests_run: "pytest"\nmerge_commit: "{sha}"')
    cr = GB.close_readiness(item, item_dir, dev_root, repo, [item])
    ok("fabricated changed-file claim refuses (ground truth)",
       not next(c for c in cr["checks"] if c["criterion"] == "closeout_verified")["ok"])
    _fill(item_dir, "closeout", {"Summary": "Delivered."},
          facts='changed_files: ["real.py"]\ntests_run: "pytest"\nmerge_commit: ""')
    cr = GB.close_readiness(item, item_dir, dev_root, repo, [item])
    ok("unmerged without a logged reason refuses",
       not next(c for c in cr["checks"] if c["criterion"] == "merged_or_logged_no_merge")["ok"])
    _fill(item_dir, "closeout", {"Summary": "Delivered."},
          facts='changed_files: ["real.py"]\ntests_run: "pytest"\nmerge_commit: ""\n'
                'no_merge_reason: "doc-only change, merged via parent"')
    cr = GB.close_readiness(item, item_dir, dev_root, repo, [item])
    ok("explicit no_merge_reason satisfies merged-or-logged",
       next(c for c in cr["checks"] if c["criterion"] == "merged_or_logged_no_merge")["ok"])
    KD.stage_delta(item_dir, [{"doc": "spec", "section": "Stack", "op": "append",
                               "content": "unapplied"}])
    cr = GB.close_readiness(item, item_dir, dev_root, repo, [item])
    ok("staged-unapplied delta refuses (knowledge row unresolved)",
       not next(c for c in cr["checks"] if c["criterion"] == "knowledge_row_resolved")["ok"])

    # Research kind: findings + spawns.
    rid, r_dir = _mk_item(dev, dev_root, "researcher", kind="research")
    r_item = dev.read_work_item(dev_root, rid)
    _fill(r_dir, "plan", {"Tasks": "- [x] read"}, kind="research")
    _fill(r_dir, "findings", {"Follow-ups": "spawned item:deadbeef0123"}, kind="research")
    _fill(r_dir, "closeout", {"Summary": "Found."}, kind="research",
          facts='changed_files: []\ntests_run: ""\nmerge_commit: ""')
    cr = GB.close_readiness(r_item, r_dir, dev_root, repo, [r_item])
    ok("research: ghost spawn claim refuses (anti-hallucination)",
       not next(c for c in cr["checks"] if c["criterion"] == "spawns_exist")["ok"])
    _fill(r_dir, "findings", {"Follow-ups": "none"}, kind="research")
    cr = GB.close_readiness(r_item, r_dir, dev_root, repo, [r_item])
    ok("research all-green passes", cr["ok"], str([c for c in cr["checks"] if not c["ok"]]))


def test_gate_briefs(tmp: Path) -> None:
    print("gate briefs (★ D10 language + uniform decision block)")
    dev = DevKnowledgeService()
    dev_root = tmp / "gb-root"
    _seed_anchor_docs(dev_root)
    iid, item_dir = _mk_item(dev, dev_root, "briefed")
    item = dev.read_work_item(dev_root, iid)

    b = GB.render_gate_brief(item, item_dir, dev_root, None)
    ok("triage phase → triage-exit gate, at_gate", b["gate"] == "triage-exit" and b["at_gate"])
    ok("first-decision continuity line", "first decision" in b["brief"])
    ok("uniform decision block: recommendation first + stakes + options + dual effort",
       "Decision — recommended:" in b["brief"] and b["decision"]["stakes"]
       and len(b["decision"]["options"]) == 3 and "you" in b["brief"] and "agent" in b["brief"])
    ok("triage-exit recommends approve (kind set + body filled)",
       b["decision"]["recommendation"].startswith("Approve"))

    events = [{"kind": "run.report", "actor": "agent", "created_at": "2026-07-16T10:00:00"},
              {"kind": "phase.advance", "actor": "owner", "summary": "Approved triage → plan",
               "created_at": "2026-07-15T09:00:00"}]
    item["phase"] = "plan"
    b = GB.render_gate_brief(item, item_dir, dev_root, None, events=events)
    ok("plan phase → pre-main; continuity anchors the last owner touchpoint + delta since",
       b["gate"] == "pre-main" and "Approved triage → plan" in b["brief"]
       and "1× run.report" in b["brief"])
    ok("no plan.md → recommends send back", b["decision"]["recommendation"] == "Send back for revision")
    _fill(item_dir, "plan", {"Tasks": "- [ ] one\n- [x] two"})
    b = GB.render_gate_brief(item, item_dir, dev_root, None)
    ok("clean plan with tasks → recommends approve",
       b["decision"]["recommendation"] == "Approve the plan" and "tasks 1/2" in b["brief"])

    item["phase"] = "build"
    b = GB.render_gate_brief(item, item_dir, dev_root, None)
    ok("mid-build → deliver gate PREVIEW (at_gate false)",
       b["gate"] == "deliver" and not b["at_gate"] and "not at this gate yet" in b["brief"])

    item["phase"] = "deliver"
    b = GB.render_gate_brief(item, item_dir, dev_root, None,
                             git_health={"ahead": 2, "behind": 1})
    ok("deliver with no evidence + behind trunk → Hold & fix (mechanical rows red)",
       b["decision"]["recommendation"] == "Hold & fix"
       and any(c["criterion"] == "git_fresh" and not c["ok"] for c in b["checks"]))
    repo = _git_repo(tmp, "gb-repo")
    A.record_evidence(item_dir, repo, check="t", how="h", result="r", passed=True)
    _fill(item_dir, "validation", {})
    _fill(item_dir, "readiness", {"Recommendation": "Merge — clean"})
    # evidence_fresh in the brief reads the item's worktree if set, else main repo — point at repo.
    b = GB.render_gate_brief(item, item_dir, dev_root, repo, git_health={"ahead": 2, "behind": 0})
    ok("deliver all-green → Merge, readiness embedded",
       b["decision"]["recommendation"] == "Merge" and "Merge — clean" in b["brief"],
       str([c for c in b["checks"] if not c["ok"]]))

    item["phase"] = "close"
    b = GB.render_gate_brief(item, item_dir, dev_root, repo, all_items=[item])
    ok("close gate embeds the criteria table + recommends per readiness",
       b["gate"] == "close" and any(c["criterion"] == "closeout_verified" for c in b["checks"])
       and b["decision"]["recommendation"] in ("Complete", "Send back"))
    ok("every brief is deterministic",
       GB.render_gate_brief(item, item_dir, dev_root, repo, all_items=[item])["brief"] == b["brief"])


class _Store:
    def __init__(self):
        self.events = []

    def log_event(self, *a, **kw):
        self.events.append((a, kw))


def test_tools(tmp: Path) -> None:
    print("tools: stage_knowledge_delta + propose_close")
    dev = DevKnowledgeService()
    dev_root = tmp / "tl-root"
    _seed_anchor_docs(dev_root)
    repo = _git_repo(tmp, "tl-repo")
    iid, item_dir = _mk_item(dev, dev_root, "tooled")
    store = _Store()

    stage = _stage_knowledge_delta(store=store, context_id="t", dev_root=dev_root,
                                   repo_dir=repo, bound_item_id=iid)
    r = asyncio.run(stage({"item_id": iid, "ops": json.dumps(
        [{"doc": "spec", "section": "Stack", "op": "append", "content": "see `real.py`"}])}))
    ok("valid delta stages", "staged" in json.dumps(r) and KD.pending_delta(item_dir))
    r = asyncio.run(stage({"item_id": iid, "ops": json.dumps(
        [{"doc": "spec", "section": "Ghost", "op": "append", "content": "x"}])}))
    ok("invalid delta rejected itemized, prior staging left intact",
       r.get("is_error") and len(KD.pending_delta(item_dir)["ops"]) == 1)
    rid, _r_dir = _mk_item(dev, dev_root, "res", kind="research")
    stage_r = _stage_knowledge_delta(store=store, context_id="t", dev_root=dev_root,
                                     repo_dir=repo, bound_item_id=rid)
    r = asyncio.run(stage_r({"item_id": rid, "ops": "[]"}))
    ok("research kind refused (knowledge_writes=False)", r.get("is_error")
       and "never writes" in json.dumps(r))

    propose = _propose_close(store=store, context_id="t", dev_root=dev_root,
                             main_repo_dir=repo, bound_item_id=iid)
    r = asyncio.run(propose({"item_id": iid}))
    ok("propose_close refused off the close phase", r.get("is_error")
       and "close phase" in json.dumps(r))
    dev.set_work_item_phase(dev_root, iid, "close")
    r = asyncio.run(propose({"item_id": iid}))
    item = dev.read_work_item(dev_root, iid)
    ok("red criteria → itemized refusal, NO state change", r.get("is_error")
       and item.get("status") == "active")
    # Green it up (reuse the close_readiness fixture shape) + clear the pending delta by applying.
    _fill(item_dir, "plan", {"Tasks": "- [x] did"})
    A.record_evidence(item_dir, repo, check="t", how="h", result="r", passed=True)
    _fill(item_dir, "validation", {})
    _fill(item_dir, "readiness", {"Knowledge": "updated"})
    KD.apply_delta(item_dir, dev_root)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                         text=True).stdout.strip()
    _fill(item_dir, "closeout", {"Summary": "Done."},
          facts=f'changed_files: ["real.py"]\ntests_run: "t"\nmerge_commit: "{sha}"')
    r = asyncio.run(propose({"item_id": iid}))
    item = dev.read_work_item(dev_root, iid)
    ok("all-green propose pages the owner (awaiting_human + close.proposed event)",
       not r.get("is_error") and item.get("status") == "awaiting_human"
       and any(a[1] == "close.proposed" for a, _ in store.events), json.dumps(r))
    ok("cross-item refused", asyncio.run(propose({"item_id": "other"})).get("is_error") is True)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_delta_validation(tmp)
        test_delta_apply(tmp)
        test_freshness_lint(tmp)
        test_close_readiness(tmp)
        test_gate_briefs(tmp)
        test_tools(tmp)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
