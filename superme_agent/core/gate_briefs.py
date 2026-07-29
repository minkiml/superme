"""Gate briefs + close criteria — the four human gates' decision surfaces (workspace-workflow
S6, D8/D10).

★ Design language (D10, locked): the cost of a human gate is CONTEXT RECONSTRUCTION, not reading
time. Every brief is continuity-preserving (anchors in the owner's last touchpoint), delta-oriented
(what changed since), narratively ordered (what happened → what it means → what's asked), carries
exactly ONE decision, and closes with the uniform decision block: recommendation FIRST · stakes one
line · per-option consequence · dual-scale effort. Every gate is answerable from its brief alone.

The kernel ASSEMBLES the brief from durable state (item fields, artifacts, evidence ledger,
event log, git health) — the agent-authored reports are embedded as the narrative
core, never regenerated. Mechanical rows come from code, never claims.

`close_readiness` is the D8 close gate's mechanical evaluator over KIND_PROFILES.close_criteria —
the complete/promote route refuses on any failing check (three-layer protocol, layer zero).

Pure + file-based (the router feeds it events/git health) — unit-testable without a daemon.
"""

import re
from pathlib import Path

from . import artifacts as A
from . import plan_revision
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


def close_readiness(item: dict, item_dir: Path, all_items: list[dict]) -> dict:
    """Evaluate the kind's close criteria mechanically → {ok, checks:[{criterion, ok, detail}]}.
    Universal first check: every required artifact exists and passes its self-check.

    NOTHING HERE RE-JUDGES THE WORK. Review's exit locked code + git (§2.3), so a close criterion
    can only ask about things close can still act on. `evidence_fresh` and `knowledge_row_resolved`
    were retired from every profile for that reason (see kind_profiles) — and with them went this
    function's read of the evidence ledger and the knowledge delta, hence the two parameters that
    fed them (`dev_root`, `main_repo_dir`). Every remaining criterion reads the item folder or the
    sibling items, so those are the only inputs left."""
    profile = get_profile(item.get("kind"))
    item_dir = Path(item_dir)
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
        elif crit == "findings_delivered":
            # The research deliverable is the report the owner decided on, not an artifact:
            # The `review` entry run writes reports/report-review.md (findings.md retired).
            issues = A.report_issues(item_dir, "report-review")
            checks.append({"criterion": crit, "ok": not issues,
                           "detail": "; ".join(issues) or "report-review.md complete"})
        elif crit == "spawns_exist":
            # What must not happen is a research item closing with its proposals silently dropped.
            # `itemize` records the owner's call (adopted, with inbox ids, vs declined) into the
            # report's decision line; an unrecorded decision is one that was never put to them.
            decision = A.owner_decision(item_dir)
            checks.append({"criterion": crit, "ok": bool(decision),
                           "detail": decision or "the report's proposals were never put to you — "
                                                 "run itemize, or record that none were adopted"})
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
            "cycle": len(A.cycle_reports(item_dir)),
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
        # brief.md is triage's product (renovation §3.1); pre-renovation items sharpened the item
        # body instead — show whichever exists.
        item_body = (_strip_fm(_artifact_text(item_dir, "brief") or "").strip()
                     or str(item.get("description") or "").strip())
        body += ["**What triage concluded** (the item's brief):", "",
                 _cap(item_body, _CAP_BODY) or "_(no brief yet — triage hasn't run)_"]
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
                line += ("\n\n_`depth: none` means NO CHECK will be run: the vet pass still "
                         "happens, confirms there is nothing observable to check, and records "
                         "that. Approving this depth is approving that judgment — nothing "
                         "downstream re-opens it._")
            body += ["", line]
            facts = [{"label": "vet depth",
                      "value": depth + (f" · {len(vp['checks'])} check(s)" if vp.get("checks")
                                        else ""),
                      "tone": "warn" if depth == "none" else ""}]
        # Owner-made decisions from the grill (plan.md ## Decisions & clarifications) — settled
        # provenance the gate shows back (≤3 newest), never re-litigated.
        decisions = A.parse_decisions(plan)
        if decisions:
            facts.append({"label": "decisions", "value": f"{len(decisions)} owner-answered"})
            body += ["", "**Owner decisions (from the Q&A):**", ""]
            body += [f"- {d['question']} → {d['answer'] or '(answer recorded in plan)'}"
                     for d in decisions[-3:]]
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
        # Every re-routing round owes a revision block (§2.1): `revise` is the only way back here,
        # it always records a `review.route` event, and the only way to change plan.md is
        # `revise_plan`, which always writes the block. A round with no block means the plan was
        # hand-edited — so which feedback drove what is unrecoverable, and the next build reads a
        # plan it cannot tell has changed. Fails for a real reason; the gate acts by sending it
        # back to record the pass properly.
        rounds = sum(1 for e in events if e.get("kind") == "review.route")
        if rounds:
            revs = plan_revision.revisions(item_dir)
            checks.append({
                "criterion": "revisions_recorded", "ok": len(revs) >= rounds,
                "detail": (f"{len(revs)} revision block(s) for {rounds} feedback round(s): "
                           + ", ".join(revs)) if len(revs) >= rounds else
                          (f"{rounds} feedback round(s) but only {len(revs)} revision block(s) — "
                           f"fold the feedback in with `revise_plan`, never by rewriting plan.md")})
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
        wt = item.get("git_worktree")
        ev_repo = Path(str(wt)) if wt and Path(str(wt)).is_dir() else main_repo_dir
        ev = A.evidence_status(item_dir, ev_repo)
        checks.append({"criterion": "evidence_fresh", "ok": ev["status"] == "passed",
                       "detail": "no checks were owed — the approved plan declares `depth: none`"
                                 if ev.get("not_required") else
                                 f"evidence ledger: {ev['status']} ({ev.get('entries', 0)} entries)"})
        if git_health:
            fresh = not git_health.get("behind")
            checks.append({"criterion": "git_fresh", "ok": fresh,
                           "detail": (f"branch ahead {git_health.get('ahead', 0)} / "
                                      f"behind {git_health.get('behind', 0)} vs trunk"
                                      + ("" if fresh else " — sync from main first"))})
        facts = [{"label": "evidence", "value": f"{ev['status']} ({ev.get('entries', 0)} entries)",
                  "tone": "" if ev["status"] == "passed" else "warn"}]
        if git_health:
            facts.append({"label": "branch",
                          "value": f"ahead {git_health.get('ahead', 0)} · "
                                   f"behind {git_health.get('behind', 0)}",
                          "tone": "warn" if git_health.get("behind") else ""})
        # Diff facts come from GIT (readiness.md's agent-filled Stats yaml is retired) — a count
        # nobody typed cannot drift from the tree it describes. `git_health` is None for an item
        # with no worktree (research), so guard the dict itself, not just the key.
        if git_health and git_health.get("files") is not None:
            facts.append({"label": "diff",
                          "value": f"{git_health.get('files', '?')} file(s) · "
                                   f"+{git_health.get('insertions', '?')}/"
                                   f"−{git_health.get('deletions', '?')}"})
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
        # THE MUST-RESOLVE RULE (§2.1) — what greys Approve, mechanically. Deliberately NARROWER
        # than "every check is green": only an undecided authorization or a failing/deferred vet
        # check is a hard block, because only those have no answer yet. Freshness debt and an
        # unstaged knowledge row are advisory — real, worth showing, but the owner may merge over
        # them with their eyes open. Everything softer (observations, an agent's assumption note)
        # never blocks: it is a note to pick up on demand, not a gate.
        _MUST_RESOLVE = ("no_pending_authorizations", "evidence_fresh")
        blocked = [c["criterion"] for c in checks
                   if c["criterion"] in _MUST_RESOLVE and not c["ok"]]
        approve_blocked_by = [c["detail"] for c in checks
                              if c["criterion"] in _MUST_RESOLVE and not c["ok"]]
        # This brief is the OWNER's decision surface, and the owner's approve ALWAYS merges —
        # `strict` keys the PR-opening branch off `actor != "owner"` (gates.py), because the owner
        # IS the second pair of eyes that mode exists to buy. Do not condition this text on
        # `review_mode`: I did, on 2026-07-29, reasoning from the Git tab's `landing` line, and a
        # live approve merged the branch while the brief promised nothing would land. `strict`
        # changes who ELSE can approve, not what the owner's approval does.
        _trunk = (git_health or {}).get("trunk") or "main"
        decision = _decision(
            "Approve" if not blocked else "Resolve what's open",
            f"Approving merges this to {_trunk} and locks the item in — close then applies the "
            f"granted authorizations' doc ops. It cannot be un-approved.",
            [{"id": "approve", "label": "Approve",
              "consequence": (f"merges to {_trunk} + advances to close; revert stays one click "
                              f"away via the backup ref") if not blocked else
                             ("greyed — " + "; ".join(approve_blocked_by))},
             {"id": "drop", "label": "Drop",
              "consequence": "disposes the work-item — terminal, branch kept, nothing merges"}],
            "~2 min", "~30 min per re-plan")
        decision["approve_blocked_by"] = approve_blocked_by
    else:  # close
        cr = close_readiness(item, item_dir, all_items)
        checks = cr["checks"]
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
