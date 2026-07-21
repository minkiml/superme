"""Gate briefs + close criteria — the four human gates' decision surfaces (workspace-workflow
S6, D8/D10).

★ Design language (D10, locked): the cost of a human gate is CONTEXT RECONSTRUCTION, not reading
time. Every brief is continuity-preserving (anchors in the owner's last touchpoint), delta-oriented
(what changed since), narratively ordered (what happened → what it means → what's asked), carries
exactly ONE decision, and closes with the uniform decision block: recommendation FIRST · stakes one
line · per-option consequence · dual-scale effort. Every gate is answerable from its brief alone.

The kernel ASSEMBLES the brief from durable state (item fields, artifacts, evidence ledger, staged
knowledge delta, event log, git health) — agent-authored artifacts (readiness.md, closeout.md) are
embedded as the narrative core, never regenerated. Mechanical rows come from code, never claims.

`close_readiness` is the D8 close gate's mechanical evaluator over KIND_PROFILES.close_criteria —
the complete/promote route refuses on any failing check (three-layer protocol, layer zero).

Pure + file-based (the router feeds it events/git health) — unit-testable without a daemon.
"""

import re
from pathlib import Path

from . import artifacts as A
from . import knowledge_delta as KD
from . import status_router
from .kind_profiles import get_profile

# The four briefed human gates, keyed by the phase whose EXIT they guard (D2/D10).
GATE_FOR_PHASE = {"triage": "triage-exit", "plan": "pre-main", "review": "review",
                  "close": "close"}
_GATE_LABEL = {"triage-exit": "Triage exit", "pre-main": "Pre-main (plan approval)",
               "review": "Review (merge decision)", "close": "Close"}
_CAP_EMBED = 4000     # embedded artifact narrative cap
_CAP_BODY = 2000      # item body / section cap

_ITEM_ID = re.compile(r"\b(?:item:)?([0-9a-f]{12})\b")


# --------------------------------------------------------------------------- close criteria (D8)

def _closeout_facts(item_dir: Path) -> dict:
    path = Path(item_dir) / "artifacts" / A.artifact_file("closeout")
    if not path.exists():
        return {}
    facts, _err = A._parse_facts(A._split_sections(path.read_text()).get("Facts", ""))
    return facts


def close_readiness(item: dict, item_dir: Path, dev_root: Path, main_repo_dir: Path | None,
                    all_items: list[dict]) -> dict:
    """Evaluate the kind's close criteria mechanically → {ok, checks:[{criterion, ok, detail}]}.
    Universal first check: every required artifact exists and passes its self-check. Evidence
    freshness is judged against the item's WORKTREE when it still exists (the tree validation ran
    in — untouched by the main merge), else the main repo."""
    profile = get_profile(item.get("kind"))
    item_dir = Path(item_dir)
    wt = item.get("git_worktree")
    evidence_repo = Path(str(wt)) if wt and Path(str(wt)).is_dir() else main_repo_dir
    checks: list[dict] = []

    missing = []
    for kind in profile.required_artifacts:
        issues = A.self_check(item_dir, kind, item_kind=profile.kind)
        if issues:
            missing.append(f"{kind}: {issues[0]}" + (f" (+{len(issues) - 1} more)"
                                                     if len(issues) > 1 else ""))
    checks.append({"criterion": "required_artifacts",
                   "ok": not missing,
                   "detail": "; ".join(missing) or
                             f"all present + clean: {', '.join(profile.required_artifacts)}"})

    for crit in profile.close_criteria:
        if crit == "children_terminal":
            ok, open_ids = status_router.children_terminal(all_items, item["id"])
            checks.append({"criterion": crit, "ok": ok,
                           "detail": f"open children: {', '.join(open_ids)}" if not ok
                                     else "no open children"})
        elif crit == "merged_or_logged_no_merge":
            facts = _closeout_facts(item_dir)
            merged = bool(item.get("git_merge_commit") or str(facts.get("merge_commit") or "").strip())
            reason = str(facts.get("no_merge_reason") or "").strip()
            nothing = not (facts.get("changed_files") or [])
            ok = merged or bool(reason) or (nothing and "changed_files" in facts)
            checks.append({"criterion": crit, "ok": ok,
                           "detail": ("merged" if merged else
                                      f"no-merge reason logged: {reason}" if reason else
                                      "no files changed — nothing to merge" if ok else
                                      "not merged and no `no_merge_reason` logged in closeout Facts")})
        elif crit == "evidence_fresh":
            v = A.evidence_status(item_dir, evidence_repo)
            # A `deferred` status (BV-A2) isn't passed → close is correctly refused while any check
            # awaits an authorization grant. Name the pending requests so the refusal is legible.
            pend_auth = A.pending_authorizations(item_dir)
            checks.append({"criterion": crit, "ok": v["status"] == "passed",
                           "detail": f"evidence ledger: {v['status']} ({v.get('entries', 0)} entries)"
                                     + (f" — stale: {', '.join(v['stale_checks'])}" if v.get("stale_checks") else "")
                                     + (f" — failed: {', '.join(v['failed_checks'])}" if v.get("failed_checks") else "")
                                     + (f" — deferred (awaiting authorization): {', '.join(v['deferred_checks'])}"
                                        if v.get("deferred_checks") else "")
                                     + (f" — {len(pend_auth)} authorization(s) pending: "
                                        + "; ".join(a["what"] for a in pend_auth[:2]) if pend_auth else "")})
        elif crit == "knowledge_row_resolved":
            ks = KD.delta_status(item_dir, dev_root, evidence_repo)
            ok = ks["state"] in ("none-staged", "applied", "folded")
            checks.append({"criterion": crit, "ok": ok,
                           "detail": f"knowledge delta: {ks['state']}"
                                     + (f" ({ks['ops']} ops)" if ks.get("ops") else "")
                                     + ("" if ok else " — a staged delta applies at merge; "
                                        "merge first (or it was staged after the merge — restage window missed)")})
        elif crit == "closeout_verified":
            # Unmerged item (deliberate no-merge / nothing landed on main yet): its changed files
            # exist only in its WORKTREE — verifying them against main would brand an honest
            # closeout a liar forever. Merged items verify against main (the worktree may be gone).
            claim_repo = main_repo_dir if item.get("git_merge_commit") else evidence_repo
            ok, issues = A.verify_closeout(item_dir, claim_repo)
            checks.append({"criterion": crit, "ok": ok,
                           "detail": "; ".join(issues) or "all claims verified against ground truth"})
        elif crit == "assumptions_ratified":
            # The teeth behind the assumption ledger. A call the agent made without the owner must
            # have been SEEN by them before the item closes — otherwise "record an assumption"
            # degrades into the same silent parking the open-questions queue was.
            pending = A.unratified_assumptions(item_dir)
            checks.append({"criterion": crit, "ok": not pending,
                           "detail": (f"{len(pending)} assumption(s) never put to you: "
                                      + "; ".join(a["what"] for a in pending[:3]))
                                     if pending else "no assumptions outstanding"})
        elif crit == "findings_delivered":
            issues = A.self_check(item_dir, "findings", item_kind=profile.kind)
            checks.append({"criterion": crit, "ok": not issues,
                           "detail": "; ".join(issues) or "findings.md complete"})
        elif crit == "spawns_exist":
            path = Path(item_dir) / "artifacts" / A.artifact_file("findings")
            follow = A._split_sections(path.read_text()).get("Follow-ups", "") if path.exists() else ""
            ids = _ITEM_ID.findall(follow)
            known = {str(it.get("id")) for it in all_items}
            ghosts = [i for i in ids if i not in known]
            checks.append({"criterion": crit, "ok": not ghosts,
                           "detail": f"claimed spawn(s) don't exist: {', '.join(ghosts)}" if ghosts
                                     else (f"{len(ids)} spawn(s) verified" if ids
                                           else "no spawns claimed")})
        else:  # an unknown slug fails LOUD-ish: visible, never silently green
            checks.append({"criterion": crit, "ok": False,
                           "detail": "no evaluator for this criterion (kernel gap)"})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


# --------------------------------------------------------------------------- brief assembly

def _cap(text: str, cap: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= cap else text[:cap] + "\n… (truncated)"


def _strip_fm(text: str) -> str:
    return re.sub(r"(?s)\A---\n.*?\n---\n", "", text or "")


def _artifact_text(item_dir: Path, kind: str) -> str | None:
    p = Path(item_dir) / "artifacts" / A.artifact_file(kind)
    return p.read_text() if p.exists() else None


def _task_ratio(item_dir: Path) -> tuple[int, int]:
    plan = _artifact_text(item_dir, "plan") or ""
    done = len(re.findall(r"(?m)^\s*-\s*\[[xX]\]", plan))
    total = done + len(re.findall(r"(?m)^\s*-\s*\[ \]", plan))
    return done, total


def _continuity(item: dict, events: list[dict]) -> tuple[str, list[dict]]:
    """The continuity line + the events SINCE the owner's last touchpoint (the delta window).
    `events` come newest-first from the dev store."""
    bits = [f"`{get_profile(item.get('kind')).kind}` item"]
    if item.get("wave") or item.get("deliverable"):
        bits.append(f"under **{item.get('wave') or item.get('deliverable')}**")
    sf = item.get("spawned_from")
    if isinstance(sf, dict) and sf.get("item"):
        bits.append(f"({sf.get('relation')} child of `{sf['item']}`)")
    last_owner = next((e for e in events if e.get("actor") == "owner"), None)
    if last_owner:
        when = str(last_owner.get("created_at") or "")[:10]
        line = (f"{' '.join(bits)}. Your last touchpoint: {last_owner.get('summary')} ({when}).")
        since = events[:events.index(last_owner)]
    else:
        line = f"{' '.join(bits)}. This is your first decision on it."
        since = list(events)
    return line, since


def _delta_line(item_dir: Path, since: list[dict]) -> str:
    parts = []
    if since:
        kinds: dict[str, int] = {}
        for e in since:
            kinds[e.get("kind") or "event"] = kinds.get(e.get("kind") or "event", 0) + 1
        parts.append("since then: " + ", ".join(f"{n}× {k}" for k, n in sorted(kinds.items())))
    done, total = _task_ratio(item_dir)
    if total:
        parts.append(f"tasks {done}/{total}")
    cp = A.latest_checkpoint(item_dir, char_cap=400)
    if cp:
        head = next((ln.strip() for ln in _strip_fm(cp["text"]).splitlines()
                     if ln.strip() and not ln.startswith("#")), None)
        if head:
            parts.append(f"latest checkpoint: “{head[:160]}”")
    return " · ".join(parts) if parts else "no recorded activity since."


def _checks_md(checks: list[dict]) -> str:
    return "\n".join(f"- {'✓' if c['ok'] else '✗'} **{c['criterion']}** — {c['detail']}"
                     for c in checks)


def _decision(recommendation: str, stakes: str, options: list[dict],
              effort_user: str, effort_agent: str) -> dict:
    return {"recommendation": recommendation, "stakes": stakes, "options": options,
            "effort_user": effort_user, "effort_agent": effort_agent}


def _decision_md(d: dict) -> str:
    lines = [f"**Decision — recommended: {d['recommendation']}.** {d['stakes']}"]
    for o in d["options"]:
        marker = " *(recommended)*" if o["label"] == d["recommendation"] else ""
        lines.append(f"- **{o['label']}**{marker} — {o['consequence']}")
    lines.append(f"\n_Effort: you {d['effort_user']} · agent {d['effort_agent']}_")
    return "\n".join(lines)


def _numbers(item_dir: Path) -> dict:
    """The brief's real-ratio row (renovation law 2 — counts of real things, never scores):
    task checkbox ratio · vet cycle count · latest-per-check ledger passes vs the plan's check
    total. All derived, nothing stored."""
    done, total = _task_ratio(item_dir)
    plan_text = _strip_fm(_artifact_text(item_dir, "plan") or "")
    vp = A.parse_vet_plan(plan_text)
    plan_checks = [c["id"] for c in vp.get("checks", [])]
    latest: dict[str, dict] = {}
    for e in A.evidence_entries(item_dir):
        latest[e["check"]] = e
    passing = sum(1 for cid in plan_checks if latest.get(cid, {}).get("passed"))
    return {"tasks_done": done, "tasks_total": total,
            "cycle": len(A.vet_reports(item_dir)),
            "checks_pass": passing, "checks_total": len(plan_checks)}


def _report_html(item_dir: Path, gate_phase: str) -> str | None:
    """The gate's generated report (`artifacts/gate-report-<phase>.html`, reader: user) as raw
    text — self-contained by contract, so the surface can embed it directly (iframe srcdoc)."""
    p = Path(item_dir) / "artifacts" / f"gate-report-{gate_phase}.html"
    try:
        return p.read_text() if p.is_file() else None
    except OSError:
        return None


def _page_reason(item: dict, events: list[dict]) -> dict | None:
    """When an item rests `awaiting_human` for a REASON beyond 'a clean gate is waiting' — a deputy
    escalation, a build⟷vet loop halt, or a blocked run — name it, so the drilldown can LEAD with why
    it paused and what to decide instead of previewing a future gate. Reads events newest-first and
    returns the first parking cause; stops at the last `phase.advance` (older events belong to a prior
    phase). None ⇒ a plain gate wait, render as before. Pure — {source, gate, headline, detail, next}."""
    if str(item.get("status")) != "awaiting_human":
        return None
    # The blocked/failed run report that explains a halt sits OLDER than the halt marker — grab it up
    # front so the loop branch below can quote its summary + owner-facing `next`.
    blocked = next(({**(e.get("meta") or {})} for e in events
                    if str(e.get("kind")) == "run.report"
                    and str((e.get("meta") or {}).get("outcome")) in ("blocked", "failed", "unverified")),
                   None)
    for e in events:  # newest-first
        kind = str(e.get("kind") or "")
        meta = e.get("meta") or {}
        summary = str(e.get("summary") or "")
        if kind.startswith("deputy.escalate"):
            gate = meta.get("gate")
            return {"source": "deputy", "gate": gate,
                    "headline": str(meta.get("speech") or summary
                                    or f"Your deputy paged you at the {gate or 'gate'}."),
                    "detail": str(meta.get("escalation") or summary or ""),
                    "next": None}
        if kind == "loop.decision" and str(meta.get("action")) == "halt":
            return {"source": "loop", "gate": None,
                    "headline": "The build⟷vet loop stopped and paged you.",
                    "detail": str((blocked or {}).get("summary") or summary or ""),
                    "next": str((blocked or {}).get("next") or "") or None}
        if kind == "phase.advance":
            break
    if blocked:  # a blocked run with no explicit halt marker (defensive)
        return {"source": "agent", "gate": None,
                "headline": f"The {item.get('phase')} run stopped without finishing.",
                "detail": str(blocked.get("summary") or ""),
                "next": str(blocked.get("next") or "") or None}
    return None


def _paged_md(p: dict) -> list[str]:
    """The lead block for a paused item: why it stopped + what to decide, before any gate preview."""
    lines = [f"### ⏸ Paused — {p['headline']}", ""]
    if p.get("detail"):
        lines += [_cap(p["detail"], _CAP_BODY), ""]
    if p.get("next"):
        lines += [f"**What to decide:** {p['next']}", ""]
    lines += ["_Reply in this item's chat with your decision — the phase resumes with it — or use the "
              "buttons below._", "", "---", ""]
    return lines


def render_gate_brief(item: dict, item_dir: Path, dev_root: Path,
                      main_repo_dir: Path | None, *, all_items: list[dict] | None = None,
                      events: list[dict] | None = None,
                      git_health: dict | None = None) -> dict:
    """Assemble one gate's decision surface from durable state → {gate, at_gate, phase, title,
    brief (markdown), decision, checks} + the typed fields the renovated NOW card renders
    (facts[] label:value rows · assumptions[] · flags[] · numbers · report_html). For a phase
    between gates (build/vet/investigate/report) the payload previews the NEXT gate with
    `at_gate: False` — the drilldown still leads with it. `events` = this item's dev-log rows
    newest-first; `git_health` = the S4 health dict (router-supplied; None where git doesn't
    apply)."""
    item_dir, dev_root = Path(item_dir), Path(dev_root)
    all_items, events = all_items or [], events or []
    profile = get_profile(item.get("kind"))
    phase = str(item.get("phase") or profile.phases[0])
    if phase not in profile.phases:   # hand-edited/garbage yaml — degrade, don't 500 the brief
        phase = profile.phases[0]
    terminal = bool(item.get("done_at")) or str(item.get("status")) == "done"
    at_gate = phase in GATE_FOR_PHASE and not terminal  # a terminal item asks nothing anymore
    gate_phase = phase if at_gate else next(
        (p for p in profile.phases[profile.phases.index(phase):] if p in GATE_FOR_PHASE),
        profile.phases[-1])
    gate = GATE_FOR_PHASE[gate_phase]
    title = str(item.get("title") or item["id"])

    paged = _page_reason(item, events)
    continuity, since = _continuity(item, events)
    head = [f"### {_GATE_LABEL[gate]} — {title}",
            "" if at_gate else
            f"_(terminal: {item.get('outcome') or 'done'} — nothing left to decide)_" if terminal
            else f"_(not at this gate yet — the item is mid-`{phase}`; "
                 f"this previews what the gate will ask)_",
            f"_{continuity}_", "", f"**Since then:** {_delta_line(item_dir, since)}", ""]

    checks: list[dict] = []
    body: list[str] = []
    facts: list[dict] = []
    assumptions: list[str] = []
    flags: list[str] = []
    snippet: str | None = None
    authorizations: list[dict] = []
    if gate == "triage-exit":
        item_body = str(item.get("description") or "").strip()
        body += ["**What triage concluded** (the item's sharpened brief):", "",
                 _cap(item_body, _CAP_BODY) or "_(item body is empty — triage hasn't run)_"]
        if (item_dir / "preliminary").is_dir():
            body += ["", f"_Full handoff context: `{item_dir / 'preliminary'}`_"]
        # F1 (playground-e2e-blockers): ready = the `triaged_at` stamp, written only by triage's
        # recording tool (set_triage_classification). The old `kind set + body filled` check was a
        # tautology — an inbox push satisfies both without any triage agent running.
        ready = bool(item.get("triaged_at"))
        facts = [{"label": "kind", "value": str(item.get("kind") or "unset")},
                 {"label": "deliverable", "value": str(item.get("deliverable") or "—")},
                 {"label": "triaged", "value": str(item.get("triaged_at") or "not yet",),
                  "tone": "" if ready else "warn"}]
        checks.append({"criterion": "triage_ran", "ok": ready,
                       "detail": (f"classification recorded {item.get('triaged_at')}" if ready
                                  else "no classification recorded (set_triage_classification "
                                       "never ran)")
                                 + f" · kind={item.get('kind') or 'unset'}, "
                                   f"body {'filled' if item_body.strip() else 'empty'}"})
        decision = _decision(
            "Approve & advance to plan" if ready else "Adjust in chat",
            "This fixes the item's kind and scope — everything downstream (phases, git "
            "isolation, knowledge writes) follows from it.",
            [{"id": "advance", "label": "Approve & advance to plan",
              "consequence": "locks the kind + scope; the plan phase starts from this brief"},
             {"id": "adjust", "label": "Adjust in chat",
              "consequence": "reshape kind/scope/split with the agent, then re-gate"},
             {"id": "delete", "label": "Delete the item",
              "consequence": "hard-removes it (still pre-build — folder, session, inbox row)"}],
            "~1 min", "already done")
    elif gate == "pre-main":
        plan = _strip_fm(_artifact_text(item_dir, "plan") or "")
        issues = A.self_check(item_dir, "plan", item_kind=profile.kind)
        done, total = _task_ratio(item_dir)
        body += ["**The plan** (approve it = approve its task breakdown):", "",
                 _cap(plan, _CAP_EMBED) or "_(no plan.md yet)_"]
        # The vet-plan judgment surface (build⟷vet §3.4 SOFT): depth+reason are a call the owner
        # can veto HERE (cheapest moment — before tokens burn on building), and vague `expect`
        # phrasings are flagged, never blocked — a human is present, the one fail-open that's safe.
        vp = A.parse_vet_plan(plan)
        if profile.kind == "implementation" and vp.get("present"):
            depth = vp.get("depth") or "?"
            line = (f"**Vet plan:** depth `{depth}` — {vp.get('reason') or '(no reason given)'}"
                    + (f" · env `{vp['env']}`" if vp.get("env") else "")
                    + (f" · {len(vp['checks'])} check(s)" if vp.get("checks") else ""))
            soft = A.vet_plan_soft_flags(vp)
            flags += soft
            if soft:
                line += "\n\n⚠ " + "\n⚠ ".join(soft)
            if depth == "none":
                line += ("\n\n_`depth: none` means NO vet pass will run — approve only if there "
                         "is truly no observable surface to check._")
            body += ["", line]
            facts = [{"label": "vet depth",
                      "value": depth + (f" · {len(vp['checks'])} check(s)" if vp.get("checks")
                                        else ""),
                      "tone": "warn" if depth == "none" else ""}]
        # The renovation §2 gate feeds, lifted verbatim from the plan (empty on v1 plans — the
        # surface falls back to prose rows).
        assumptions = A.parse_assumptions(plan)
        touches = A.parse_touches(plan)
        if touches:
            newc = sum(1 for t in touches if t["action"] == "new")
            modc = sum(1 for t in touches if t["action"] == "modify")
            facts.append({"label": "touches",
                          "value": f"{len(touches)} component(s) · {newc} new · {modc} modified"})
        checks.append({"criterion": "plan_complete", "ok": not issues,
                       "detail": "; ".join(issues) or f"plan clean, {total} task(s)"})
        # The generated gate report (slot-validated) — only owed by plans carrying the feed
        # sections; a v1/legacy plan predates the report contract.
        if profile.kind == "implementation" and (touches or assumptions
                                                 or "Behavior preview" in plan):
            r_issues = A.gate_report_issues(item_dir, "plan")
            checks.append({"criterion": "gate_report", "ok": not r_issues,
                           "detail": "; ".join(r_issues) or "gate-report-plan.html rendered"})
        ok = all(c["ok"] for c in checks) and total > 0
        decision = _decision(
            "Approve the plan" if ok else "Send back for revision",
            "Approval starts real work: the build phase gets a branch + worktree and edits code.",
            [{"id": "advance", "label": "Approve the plan",
              "consequence": "creates the item's branch/worktree; build executes these tasks"},
             {"id": "revise", "label": "Send back for revision",
              "consequence": "tell the agent what to change; it reworks plan.md and re-gates"},
             {"id": "hold", "label": "Hold",
              "consequence": "item stays parked at the gate; nothing runs"}],
            "~2 min", "~20 min if revising")
    elif gate == "review":
        readiness = _strip_fm(_artifact_text(item_dir, "readiness") or "")
        r_issues = A.self_check(item_dir, "readiness", item_kind=profile.kind)
        wt = item.get("git_worktree")
        ev_repo = Path(str(wt)) if wt and Path(str(wt)).is_dir() else main_repo_dir
        ev = A.evidence_status(item_dir, ev_repo)
        ks = KD.delta_status(item_dir, dev_root, ev_repo)
        body += ["**Readiness report** (agent-authored, regenerated per loop pass):", "",
                 _cap(readiness, _CAP_EMBED) or "_(no readiness.md yet)_"]
        checks.append({"criterion": "readiness_complete", "ok": not r_issues,
                       "detail": "; ".join(r_issues) or "readiness.md clean"})
        checks.append({"criterion": "evidence_fresh", "ok": ev["status"] == "passed",
                       "detail": f"evidence ledger: {ev['status']} ({ev.get('entries', 0)} entries)"})
        k_ok = ks["state"] in ("staged", "none-staged", "folded", "applied")
        checks.append({"criterion": "knowledge_row", "ok": k_ok,
                       "detail": f"knowledge delta: {ks['state']}"
                                 + (f" ({ks['ops']} ops — applied atomically with the merge)"
                                    if ks["state"] == "staged" else "")
                                 + ("; " + "; ".join(ks.get("issues", [])[:3])
                                    if ks["state"] == "invalid" else "")})
        if git_health:
            fresh = not git_health.get("behind")
            checks.append({"criterion": "git_fresh", "ok": fresh,
                           "detail": (f"branch ahead {git_health.get('ahead', 0)} / "
                                      f"behind {git_health.get('behind', 0)} vs trunk"
                                      + ("" if fresh else " — sync from main first"))})
        facts = [{"label": "evidence", "value": f"{ev['status']} ({ev.get('entries', 0)} entries)",
                  "tone": "" if ev["status"] == "passed" else "warn"},
                 {"label": "knowledge", "value": ks["state"]}]
        if git_health:
            facts.append({"label": "branch",
                          "value": f"ahead {git_health.get('ahead', 0)} · "
                                   f"behind {git_health.get('behind', 0)}",
                          "tone": "warn" if git_health.get("behind") else ""})
        # Renovation review feeds: diff stats from the readiness Stats yaml (counts, agent-filled
        # from `git diff --stat`) + the behavior snippet — the newest PASSING ledger entry's
        # verbatim result, i.e. captured reality, not a claim.
        stats = A.parse_readiness_stats(readiness)
        if stats:
            facts.append({"label": "diff",
                          "value": f"{stats.get('files', '?')} file(s) · "
                                   f"+{stats.get('insertions', '?')}/−{stats.get('deletions', '?')}"
                                   + (f" · tests {stats['tests']}" if stats.get("tests") else "")})
            r_rep = A.gate_report_issues(item_dir, "review")
            checks.append({"criterion": "gate_report", "ok": not r_rep,
                           "detail": "; ".join(r_rep) or "gate-report-review.html rendered"})
        latest_pass = next((e for e in reversed(A.evidence_entries(item_dir))
                            if e.get("passed")), None)
        if latest_pass:
            snippet = (f"[{latest_pass.get('check')}] $ {latest_pass.get('how', '')}\n"
                       f"{latest_pass.get('result', '')}")
        # BV-A2: deferred contract changes the build couldn't self-authorize surface HERE for the
        # owner's grant/deny — a pending request holds the merge (deferred ≠ passed), and each rides
        # the typed `authorizations` feed the FE renders Grant/Deny against. `delegable` tells the
        # owner whether the deputy could have granted it (sync-to-reality) or it's reserved (why it's
        # on their desk); the owner grants either way.
        pend_auth = A.pending_authorizations(item_dir)
        if pend_auth:
            authorizations = [{"id": a.get("id", ""), "what": a.get("what", ""),
                               "why": a.get("why", ""), "doc": a.get("doc", ""),
                               "scope": a.get("scope", ""),
                               "delegable": a.get("scope", "") in A.DELEGABLE_SCOPES}
                              for a in pend_auth]
            checks.append({"criterion": "no_pending_authorizations", "ok": False,
                           "detail": f"{len(pend_auth)} authorization(s) awaiting your grant/deny: "
                                     + "; ".join(a["what"] for a in authorizations[:3])})
            body += ["", f"**Authorization requests awaiting you ({len(pend_auth)}):**", ""]
            body += [f"- **{a['what']}** — {a['why']}  \n"
                     f"  _doc:_ `{a['doc'] or '—'}` · _scope:_ `{a['scope']}` "
                     f"({'the deputy could grant this — sync-to-reality' if a['delegable'] else 'owner-reserved — escalated to you'})"
                     for a in authorizations]
        ok = all(c["ok"] for c in checks)
        decision = _decision(
            "Merge" if ok else "Hold & fix",
            "Merging lands this on main and applies the knowledge delta — one 'codebase "
            "changed' event, with a backup ref for one-click revert.",
            [{"id": "merge", "label": "Merge",
              "consequence": "merges to main + applies the staged knowledge delta; revert stays "
                             "one click away via the backup ref"},
             {"id": "hold", "label": "Hold & fix",
              "consequence": "your feedback re-enters build as scoped input; the agent fixes, "
                             "re-vets, and re-presents a fresh readiness report"},
             {"id": "merge-anyway", "label": "Merge anyway",
              "consequence": "merges despite the open warnings above — they become your risk"}],
            "~2 min", "~30 min per fix loop")
    else:  # close
        cr = close_readiness(item, item_dir, dev_root, main_repo_dir, all_items)
        checks = cr["checks"]
        closeout = _artifact_text(item_dir, "closeout")
        summary = (A._split_sections(closeout).get("Summary", "").strip()
                   if closeout else "")
        body += ["**What this item delivered:**", "",
                 _cap(summary, _CAP_BODY) or "_(no closeout summary yet)_"]
        decision = _decision(
            "Complete" if cr["ok"] else "Send back",
            "Completing is terminal: sessions end, the worktree is removed (branch kept), the "
            "trace archives. It cannot be un-completed.",
            [{"id": "complete", "label": "Complete",
              "consequence": "promotes to terminal + archives; refused mechanically while any "
                             "check above is red"},
             {"id": "send-back", "label": "Send back",
              "consequence": "tell the agent which check to fix; it repairs and re-proposes"},
             {"id": "abandon", "label": "Abandon",
              "consequence": "terminal without completing — worktree removed, branch kept, "
                             "zero knowledge writes"}],
            "~2 min", "~10 min per fix")

    # Assumptions the agent took WITHOUT the owner during an autonomous phase. They ride every
    # gate, not just the plan gate: the whole point of the ledger is that a call made mid-build
    # surfaces at the next contracted human moment instead of waiting in a queue nobody reads.
    # Approving this gate ratifies them (see routers .../gates approve) — which is why they're
    # rendered in the brief body, not tucked behind a drilldown.
    pending = A.unratified_assumptions(item_dir)
    if pending:
        assumptions += [f"{a['what']} — {a.get('why', '')} (**if wrong:** {a.get('cost', '?')})"
                        for a in pending]
        body += ["", f"**Assumed without you ({len(pending)}):**", ""]
        body += [f"- **{a['what']}** — {a.get('why', '')}  \n"
                 f"  _If wrong:_ {a.get('cost', '?')}"
                 + (f" · _taken in {a['phase']}_" if a.get("phase") else "")
                 for a in pending]

    md = "\n".join([*(_paged_md(paged) if paged else []),
                    *head, *body, "", "**Mechanical checks:**", _checks_md(checks) or "- (none)",
                    "", _decision_md(decision)])
    return {"id": item["id"], "gate": gate, "at_gate": at_gate, "phase": phase, "title": title,
            "brief": md, "decision": decision, "checks": checks,
            "facts": facts, "assumptions": assumptions, "flags": flags,
            "numbers": _numbers(item_dir),
            "report_html": _report_html(item_dir, gate_phase),
            "loop": A.loop_instruments(item_dir), "snippet": snippet,
            "terminal": terminal, "paged": paged, "authorizations": authorizations}
