"""Kernel speech — every in-code prompt the kernel says to an agent.

Admission: AGENT-consumed, and not bound to a code object that is its natural surface. Layers
are triggers (durable) · preambles (per-turn) · assemblers. `test_thread3` snapshots every entry.
"""

import json
from pathlib import Path

from . import artifacts
from .vocab import kind_profiles, sandbox


# triggers (durable user-messages — each opens one background run's transcript)

# Phases whose report is one whole body the agent fills. Each spent a round trip fetching its own
# template.
def intake_trigger(skill: str, item_id: str, title: str,
                   changed: list[str] | None = None) -> str:
    """The background intake run's message.

    The delta is which skill for which item."""
    base = f"Run superme-dev:{skill} for work-item `{item_id}` (\"{title}\")."
    if not changed:
        return base
    files = "\n".join(f"- `{f}`" for f in changed[:12])
    more = f"\n- …and {len(changed) - 12} more" if len(changed) > 12 else ""
    return (
        f"{base}\n\n"
        f"You have run this phase before, and this is the SAME thread — but the item has been "
        f"written to since your last pass. These records changed on disk:\n{files}{more}\n\n"
        f"Re-read every one of them before you judge anything. What you remember about them is "
        f"stale by definition; your earlier reading described the previous version. If your own "
        f"record is one of the files above, someone else revised it — read it too, and do not "
        f"conclude that nothing changed."
    )


def completion_nudge(skill: str) -> str:
    """The completion backstop, fired when a run ended without reporting."""
    return (
        f"Your {skill} run is over, but it never called `report_completion`, so the kernel has no "
        f"outcome for it. Call it now and nothing else.\n"
        f"Judge THIS RUN, not the phase: `success` means the run did the work it was fired to do — "
        f"it does NOT claim the phase is finished or approved, and the item advances only when "
        f"someone approves it. If the run fell short, say so with the outcome that fits "
        f"(`partial`, `blocked`, `needs_user`, `clean_noop`)."
    )


# Both checkpoint triggers open on it, and a rule with two homes drifts in one.
_COMPACTION_IMMINENT = ("This session is about to be compacted — its conversation will be replaced "
                        "by a summary you did not write. ")


def checkpoint_trigger(item_id: str) -> str:
    """The pre-compaction handoff turn · durable. The reason is load-bearing:
    without it the thread writes a status update."""
    return (_COMPACTION_IMMINENT
            + f"Run superme-dev:checkpoint for work-item `{item_id}` now, so what only this "
              f"conversation knows survives.")


def session_checkpoint_trigger(memory_path: str) -> str:
    """The same for a session with no work-item.

    The path is named so the write needs no search."""
    return (_COMPACTION_IMMINENT
            + f"This session is not tied to a work-item, so run superme-dev:checkpoint and WRITE "
              f"the result to `{memory_path}` (create or overwrite it), so what only this "
              f"conversation knows survives.")


def vet_env_script() -> str:
    """Absolute path to the plugin's `vet_env.sh`, or empty.

    It serves both build and vet."""
    from ..paths import DEV_PLUGIN_DIR
    p = DEV_PLUGIN_DIR / "scripts" / "vet_env.sh"
    return str(p) if p.is_file() else ""


def vet_env_note(script: str) -> str:
    """The two lines every phase that runs checks needs. Shared, because vet re-executes
    build's recorded commands."""
    return (f"\n\nThis repo can run its own server from THIS worktree — anything that queries one "
            f"must query that, never an instance already listening (it serves a different checkout, "
            f"where a deleted endpoint still answers):\n"
            f"    eval \"$(bash {script} start)\"\n"
            f"    …commands…\n"
            f"    bash {script} stop\n"
            f"Run `stop` even after a failure: a server you leave holds its port long after this "
            f"worktree is deleted. A recorded validation command that needs the server must BOOT IT "
            f"ITSELF — one that inherits the variable from an earlier command passes for you and "
            f"fails for the vetter, who runs it alone.")


def vet_trigger(item_id: str, title: str, deferred: list[str] | None = None,
                machine: list[dict] | None = None, audit: list[dict] | None = None,
                vet_env: bool = False, kernel: bool = True) -> str:
    """The background vet run, durable since vet forgets each cycle.

    `kernel` false means no sandbox here."""
    base = f"Run superme-dev:vet for work-item `{item_id}` (\"{title}\")."
    if not kernel:
        base += ("\n\nThis host has no sandbox the kernel can run a check in, so NOTHING was run "
                 "for you and the build's own validation went un-audited. Every `run:` block in the "
                 "plan is yours to perform and attest. Say so in your report: on this host a machine "
                 "entry is your word for what you saw, not the kernel's.")
    if machine:
        lines = "\n".join(f"- `{m['check']}` — {'PASS' if m.get('passed') else 'FAIL'} "
                          f"({str(m.get('result') or '').strip()[:200]})"
                          + ("  ⚠ PASSED ONCE ONLY: the identical command did not agree on a "
                             "second run, so this check depends on state it does not control"
                             if m.get("hermetic") is False else "")
                          for m in machine)
        base += ("\n\nThe kernel already ran these checks in the sandbox and recorded each result "
                 "— they are DONE:\n" + lines + "\nDo not re-run or re-record them; a second entry "
                 "is refused. Read them as findings, perform the remaining checks yourself, and "
                 "cover all of them in your report.")
        if any(m.get("hermetic") is False for m in machine):
            base += ("\n\nA check marked PASSED ONCE ONLY is a defect in the CHECK, not evidence "
                     "against the code — the code passed on a clean state. Report it as a finding "
                     "about the plan's verification, and do not fail the item for it.")
    # Only DISAGREEMENTS are named: an audit that agreed is the expected case. Orientation, not
    # the record.
    if (bad := [a for a in (audit or []) if not a.get("agrees")]):
        lines = "\n".join(
            f"- `{a['command']}` — build recorded "
            f"{'PASS' if a.get('claimed') else 'FAIL'}, the kernel just got "
            f"{'PASS' if a.get('actual') else 'FAIL'} ({str(a.get('result') or '').strip()[:200]})"
            for a in bad)
        base += ("\n\nThe kernel re-ran the build's OWN validation commands and they do not agree "
                 "with what the build recorded:\n" + lines + "\nThis is a finding about the build, "
                 "not about the plan's checks — do not add it to the exam. Diagnose it as you "
                 "would any failure and say so plainly in your report; the loop routes the cycle "
                 "back to build on it.")
    if deferred:
        base += ("\n\nThe build DEFERRED these checks to the owner (needs-you items pending "
                 "authorization at review): " + ", ".join(f"`{c}`" for c in deferred) + ". Do NOT "
                 "run or judge them — record each as `deferred` (not fail, not pass) and move on. "
                 "Judge only the remaining checks.")
    if vet_env and (script := vet_env_script()):
        base += vet_env_note(script)
    return base


def build_first_trigger(item_id: str, title: str, vet_env: bool = False) -> str:
    """The loop's ENTRY build run · durable. Build-first, because a vet against
    an empty tree is a wasted look."""
    return (
        f"The build⟷vet loop just entered BUILD for work-item `{item_id}` (\"{title}\") — this is "
        f"the loop's opening cycle, nothing is built yet. Run superme-dev:build to implement the "
        f"plan: work `artifacts/plan.md`'s `## Tasks` checklist and commit in the worktree. The "
        f"loop vets what you produce automatically — never advance the phase."
        + (vet_env_note(script) if vet_env and (script := vet_env_script()) else "")
    )


def build_loop_trigger(item_id: str, title: str, cycle: int, report_text: str,
                       *, reload_skill: bool = False, vet_env: bool = False,
                       diagnoses: dict[str, dict] | None = None) -> str:
    """The loop's failure-hop build run.

    The failed cycle's report is the payload, injected rather than fetched."""
    head = (
        "Your context was COMPACTED since the last cycle, so the build procedure may no longer be "
        "in it: invoke the `superme-dev:build` skill again before you start. Then fix"
        if reload_skill else
        "Run superme-dev:build to fix"
    )
    found = ""
    if diagnoses:
        lines = "\n".join(
            f"- `{c}` — **{d.get('where') or 'not located'}**: {d.get('why') or ''}"
            + (f" (vet could not determine: {d['unknown']})" if d.get("unknown") else "")
            for c, d in diagnoses.items())
        found = ("\n\nWhat vet found, per failing check — the cause, not the remedy; the change "
                 "is yours to reason out within the current plan:\n" + lines)
    # Ahead of the report body. An instruction behind a wall of report text reads as part of it.
    env = vet_env_note(script) if vet_env and (script := vet_env_script()) else ""
    return (
        f"Verification failed in cycle {cycle} for work-item `{item_id}` (\"{title}\"). {head} "
        f"what its report's `## Verification` entries describe:{found}{env}\n\n"
        f"--- build-vet-{cycle}.md ---\n{report_text}\n---"
    )


def phase_feedback_trigger(item_id: str, title: str, phase: str, skill: str, feedback: str,
                           digest: str | None = None) -> str:
    """The deputy's send-back re-run, worded as the owner's would be."""
    digest_block = f"\n\nWhat happened downstream since the last plan (context for your re-plan):\n{digest}\n" if digest else ""
    # There is one sanctioned way to change plan.md. The whole-file rewrite discards the progress
    # build earned.
    how = ("" if phase != "plan" else
           " Change `plan.md` ONLY through `revise_plan` — never rewrite the file. Split the "
           "feedback into its concerns and give each one its own scope: `resume` when the plan was "
           "right and only needs another generation (no edit — an edit there is refused), "
           "`targeted` when specific things must change, `redesign` when the approach itself was "
           "wrong. Never restate what the feedback didn't touch.")
    return (
        f"Feedback has come back on work-item `{item_id}` (\"{title}\") at the **{phase}** stage. "
        f"Run superme-dev:{skill} to address it: update the docs — including the `## Tasks` track — "
        f"so what's done, what changed, and what's newly needed are all correct, then rest at the "
        f"{phase} gate.{how}"
        f"{digest_block}"
        f"\n\nThe feedback, verbatim:\n> {feedback}"
    )


def close_trigger(item_id: str, title: str, *, merge_commit: str | None = None,
                  nominated: int = 0) -> str:
    """The auto-fired close run.

    Review's exit locked code and git, so close writes knowledge only."""
    # Close spent turns on `git log --grep` hunting for a sha the item record carries.
    landed = (f" It merged as `{merge_commit}` — `git show --stat {merge_commit}` is the change "
              f"inventory, so no searching for it." if merge_commit else "")
    # Every measured close called `read_verification_library` even with nothing to fetch.
    library = (
        f"\n\nVet nominated {nominated} check(s) for the verification library: call "
        f"`read_verification_library` and add each as an `append` op in the same "
        f"`apply_knowledge_edits` call."
        if nominated else
        "\n\nVet nominated nothing for the verification library, so do NOT call "
        "`read_verification_library` — there is nothing there for this item."
    )
    return (
        f"Work-item `{item_id}` (\"{title}\") merged and entered its CLOSE phase.{landed} Run "
        f"superme-dev:close: reflect what LANDED into the general anchor docs through "
        f"`apply_knowledge_edits` (nothing doc-worthy ⇒ write nothing), then write "
        f"`reports/report-close.md` from the item's real artifacts + git — what landed, what the "
        f"anchor docs now say, what was skipped and why. Report when done; the kernel clears the "
        f"item from there."
        + library
    )


def resolve_trigger(worktree, item_id: str, conflicts: list[str]) -> str:
    """The background conflict-resolve run · durable. The procedure sits here because
    no resolve skill owns it yet."""
    files = "\n".join(f"- {f}" for f in conflicts) or "- (see `git status` in the worktree)"
    return (
        f"A sync-from-main merge left CONFLICTS in this work-item's git worktree at `{worktree}` "
        f"(work-item `{item_id}`). Conflicted files:\n{files}\n\n"
        f"Resolve every conflict marker (`<<<<<<<`/`=======`/`>>>>>>>`) in these files, honoring "
        f"BOTH sides' intent: keep this item's changes AND the incoming trunk changes semantically "
        f"intact — never resolve by discarding one side wholesale unless the file makes that "
        f"clearly correct and no features should be lost or broken. Edit the files in place. "
        f"Do NOT run git commands and do NOT commit — your job is done when every conflict "
        f"marker in these files is resolved and the files are saved."
    )


def distill_trigger() -> str:
    """The background distill run · durable in a disposed transcript. Names the
    sub-agent; the steps live in it."""
    return (
        "Use the `distill` sub-agent (superme-dev:distill) to process the un-distilled memory "
        "candidate pool for this context. Invoke the agent and let it file its proposals."
    )


def write_trigger(prop: dict, *, slug: str, workspace, existing_path: str | None,
                  forge_kit) -> str:
    """The background write run · durable in a disposed transcript. Names the forge
    sub-agent and its toolkit paths."""
    fields = prop.get("fields")
    answers = prop.get("clarification_answers")
    parts = [
        "Use the `forge` sub-agent (superme-dev:forge) to author the final artifact for this "
        "approved proposal, validate it with the forge_kit, then stage it via `stage_artifact`.",
        "",
        f"forge_kit: {forge_kit}   (run: python <forge_kit>/lint.py … and python <forge_kit>/eval.py …)",
        f"writing standard: {forge_kit}/references/principle-for-skills.md   (read before drafting)",
        f"scratch workspace: {workspace}   (draft + run the toolkit here; do not write anywhere else)",
        f"publish slug: {slug}   (the artifact's on-disk name — frontmatter `name` must match it)",
        "",
        f"PROPOSAL #{prop['id']}",
        f"output_form: {prop['output_form']}",
        f"target_scope: {prop['target_scope']}",
        f"title: {prop['title']}",
    ]
    if prop.get("summary"):
        parts.append(f"summary: {prop['summary']}")
    if prop.get("body"):
        parts += ["body:", prop["body"]]
    if fields:
        parts += ["fields (the spec):", json.dumps(fields, indent=2)]
    if answers:
        parts += ["owner's answers to the clarifying questions (binding):",
                  json.dumps(answers, indent=2)]
    if existing_path:
        parts.append(f"existing rules in this scope (for the eval conflict check): {existing_path}")
    return "\n".join(parts)


def capture_trigger(slice_text: str, focus: str | None = None) -> str:
    """The background capture-sweep run · durable in a disposed transcript. `focus`
    is the owner's steer on a manual sweep."""
    focus_line = (f"\n\n**A capture steer was supplied for this sweep — treat it as a directive, not "
                  f"a hint.** Prioritize it: file it as a candidate unless it is genuinely "
                  f"non-operational (a pure fact/reference with no effect on how SuperMe behaves). "
                  f"The steer:\n{focus}\n") if focus else ""
    return (
        "Use the `capture` sub-agent (superme-dev:capture) to sweep the conversation slice below "
        "for durable OPERATIONAL learnings and file each as a candidate. Invoke the agent and let "
        "it file what it finds (filing nothing is a valid result)."
        f"{focus_line}\n\n--- conversation slice (oldest first) ---\n{slice_text}\n"
        "--- end slice ---"
    )


# Per-kind session preambles: per-turn system layer, never durable. Adding an agent means a
# preamble here, not a conditional.

# Thin preamble, thick skill: one line of WHAT, plus the skill that owns the procedure. Constants
# only.
_PHASE_CONTRACTS: dict[str, dict] = {
    "triage":      {"skill": "triage",
                    "what": "classify this item (kind, scope, deliverable) and shape its brief — no building yet"},
    "plan":        {"skill": "plan",
                    "what": "produce the approved plan (plan.md with its ## Tasks checklist) — no building yet"},
    "build":       {"skill": "build",
                    "what": "implement the plan's tasks, committing as you go"},
    "vet":         {"skill": "vet",
                    "what": "vet the built work against the plan's vet plan — record machine evidence "
                            "for every check and file the cycle's vet report; never fix anything"},
    "review":      {"skill": "review",
                    "what": "put ONE honest report in front of the owner — read the work segment, "
                            "stage any anchor-doc delta it earned, write reports/report-review.md; "
                            "read git, change none of it, edit no plan, merge nothing"},
    "close":       {"skill": "close",
                    "what": "reflect the locked changes into general knowledge, then report what landed"},
    "investigate": {"skill": "investigate",
                    "what": "answer the plan's research questions within its boundaries — read-only on code"},
}

# EMPTY by design: kind variation belongs in a skill's TEMPLATES, never a second skill. Kept as a
# seam.
_KIND_PHASE_CONTRACTS: dict[tuple[str, str], dict] = {}


def phase_contract(kind: str | None, phase: str) -> dict:
    """What a (kind, phase) pair is for and which skill runs it. Unknown pairs return
    {} — degrade, never raise."""
    return _KIND_PHASE_CONTRACTS.get((str(kind or ""), phase)) or _PHASE_CONTRACTS.get(phase, {})


def compaction_notice(checkpoint_path: str | None, *, has_artifacts: bool = True) -> str:
    """The post-compaction continuity notice, owed until a real turn runs."""
    if not checkpoint_path:
        return ""
    # A general session has no item folder, so "trust the item's artifacts" would point at
    # nothing.
    fallback = ("and trust the item's artifacts over your memory"
                if has_artifacts else "— it is the only record of this thread that survived")
    return (
        "\n\n⚠ **This thread was compacted.** What you seem to remember of the earlier "
        "conversation is a SUMMARY of it, not the thing itself — some of it is gone and some may "
        "read as still-live work that is already finished or cancelled. Before acting on anything "
        f"you think you recall, read `{checkpoint_path}` (banked just before the compaction) "
        f"{fallback}. The user's latest message always wins."
    )


def _shell_is_in(shell_cwd, worktree) -> bool:
    """Will the turn's shell start inside `worktree`? Unknown cwd reads as no."""
    if not shell_cwd or not worktree:
        return False
    try:
        a, b = Path(shell_cwd).resolve(), Path(worktree).resolve()
    except (OSError, ValueError):
        return False
    return a == b or b in a.parents


def _materials_on_disk(item_dir) -> str:
    """The item's readable files, as the paths they actually have.

    Agents compose the path and miss the `artifacts/` segment."""
    try:
        names = {sub: sorted(f.name for f in (Path(item_dir) / sub).glob("*.md") if f.is_file())
                 for sub in ("artifacts", "reports")}
    except (OSError, ValueError, TypeError):
        return ""   # the preamble rides EVERY turn; a bad path must never break one
    out: list[str] = []
    for sub, files in names.items():
        series: dict[str, list[str]] = {}
        for n in files:
            stem = n[:-3]
            head, _, tail = stem.rpartition("-")
            series.setdefault(head if head and tail.isdigit() else stem, []).append(tail)
        for stem, nums in series.items():
            digits = sorted(n for n in nums if n.isdigit())
            # A cycle series is one entry, not five: `build-vet-1..3.md` says the same thing.
            label = (f"{stem}-{digits[0]}..{digits[-1]}.md" if len(digits) > 1
                     else f"{stem}-{digits[0]}.md" if digits else f"{stem}.md")
            out.append(f"`{sub}/{label}`")
    return f" — holds {' · '.join(out)}" if out else " — nothing written yet"


def work_item_preamble(item_id: str, item: dict, item_dir, *, interactive: bool = True,
                       compacted_checkpoint: str | None = None, shell_cwd=None,
                       anchor_dir=None) -> str:
    """The per-turn contract for a work-item turn. `shell_cwd` is the cwd it really runs in,
    `anchor_dir` close's alone."""
    title = item.get("title") or item_id
    phase = str(item.get("phase") or "triage")
    kind = str(item.get("kind") or "implementation")
    c = phase_contract(kind, phase)
    # "Sole subject" survives because nothing else stops a run wandering into a second item.
    presence = (
        "This is an interactive chat — the user is present. Their interactions primarily "
        "center on this item unless they point elsewhere."
        if interactive else
        "This item is your sole subject this run."
    )
    lines = [
        "## Current focus",
        f"- work-item: **{item_id}** — \"{title}\"\n"
        f"- kind: `{kind}`\n"
        f"- phase: `{phase}`\n"
        f"- materials: `{item_dir}/`{_materials_on_disk(item_dir)}"
        + (f"\n- anchor docs: `{anchor_dir}/` — `Read` them there; they are outside the repo and "
           f"outside your item folder, and nothing you do to them is committed or merged"
           if anchor_dir else ""),
        f"\n{presence}",
        "Every claim you make about this item must trace to a file you read — if you cannot name "
        "the file, you have not read it.",
    ]
    if c:
        # The skill name rides EVERY turn: a trigger is one message, and compaction can summarize
        # it away.
        lines.append(f"\n**This phase:** {c['what']} — procedure in the `superme-dev:{c['skill']}` "
                     f"skill.")
    # Only `small` says anything — `standard` is what every skill already describes. Overflow
    # gives padding pressure somewhere to go.
    if kind_profiles.item_scale(item) == "small":
        lines.append(
            "\n**This item is scaled `small`.** Read narrow: this item's own folder, and the files "
            "the change actually touches. Don't go browsing the project's anchor docs (PRD, "
            "architecture, roadmap) or other items' artifacts for background — but this bounds "
            "what you read for CONTEXT, never the work your phase owes: if your own contract names "
            "an anchor doc, that is your job and you do it. Write short: every section you owe "
            "still gets written — none becomes optional — but one to three sentences each, and no "
            "section repeats what another already says.\n"
            "If you find you need a source outside that boundary, or a section that genuinely "
            "cannot be said in three sentences, do not pad and do not quietly go wider: say so in "
            "your report and name what you needed. That is evidence this item was misjudged as "
            "small, and it is the owner's call to make, not yours to absorb."
        )
    # Stated for EVERY phase, not just investigate: review reads findings against the family's
    # bar.
    if (fam := kind_profiles.research_kind(item)):
        why = str(item.get("research_kind_reason") or "").strip()
        lines.append(f"\n**Investigation family: `{fam}`.**"
                     + (f" Triage's reason: {why}" if why else "")
                     + " Your phase's contract says what that family owes.")
    # Triage is the only reader: the kind freezes after this phase. Meet the rule before the wall.
    if phase == "triage" and (proposed := str(item.get("proposed_kind") or "")):
        lines.append(
            f"\n**This item was filed as `{proposed}`.** Read the ask yourself and judge it — but "
            f"that judgment was already made once. If you agree, record `{proposed}` and say "
            "nothing about it. If you disagree, you may not simply record the other one: end your "
            "run with `report_completion(machine.outcome='needs_user')`, ask the owner which it "
            "is, and say what you saw that disagrees.")
    # Each intake phase has its own session, so anything the owner said earlier is gone from this
    # thread.
    if carried := artifacts.carry_owner_input(item_dir):
        lines.append(carried)
    # Edit boundary: worktree during build+, item folder otherwise. Vet is read-only by
    # construction at the permission layer.
    wt = item.get("git_worktree")
    in_wt = _shell_is_in(shell_cwd, wt)
    # A shell outside the worktree can still read it by naming it. `cd` is not a read-only verb.
    reach = ("" if in_wt else
             f" Your shell does NOT start there — it starts in `{shell_cwd}`. Reach the worktree "
             f"by naming it in the command (`git -C {wt} diff …`); a `cd` into it is refused.")
    if phase == "vet":
        lines.append(
            f"\n**Edit boundary (vet — read-only):** you are the VETTER, not the builder. File "
            f"writes are disabled by design; you inspect and RUN things (worktree "
            f"`{wt}/`{' is your working directory' if in_wt else ' holds the code'} — shell for "
            f"tests/commands is fine).{reach} Record each "
            f"check's outcome with `record_verification` and file the cycle's report with "
            f"`file_vet_report`. If something fails, FAIL it and describe what you observed — "
            f"never fix it, never soften it."
            if wt else
            "\n**Edit boundary (vet — read-only):** you are the VETTER, not the builder. File "
            "writes are disabled by design. Record outcomes with `record_verification` and "
            "file the report with `file_vet_report`."
        )
    elif wt and kind_profiles.get_profile(kind).scratch_worktree:
        # A SCRATCH tree: same shape as a build worktree, opposite meaning. It checks out the
        # ANCHOR, and nothing lands.
        base = item.get("git_base")
        lines.append(
            f"\n**Where you are reading:** `{wt}/` — a detached, throwaway checkout of "
            f"`{base or 'the anchor'}` made for this item, NOT the working tree. It exists so a "
            f"read-only investigation cannot touch real code, and it is deleted when the item "
            f"closes. Writes belong in the item's own folder (artifacts, checkpoints); nothing "
            f"here is committed and nothing merges.\n"
            f"Two consequences for what you write: cite files **repo-relative** "
            f"(`superme_agent/core/spine.py:412`), never with this directory's absolute path — a "
            f"reader following an absolute path lands in a tree that will not exist. And what you "
            f"are reading is the committed state of `{base or 'the anchor'}`: uncommitted work in "
            f"the owner's own tree is not here, so if a finding depends on that, say which state "
            f"you read."
        )
    elif wt:
        # The branch BASE rides here too: review diffs `<base>...HEAD`, and nothing else in the
        # session carries that commit.
        base = item.get("git_base")
        lines.append(
            f"\n**Edit boundary:** all code changes happen in this item's git worktree `{wt}/`"
            f"{' (your working directory)' if in_wt else ''}; the item folder holds its artifacts. "
            f"File writes outside those two are denied. Never touch the main repo tree — the merge "
            f"to main happens at the user's review gate."
            + (f" Branch base: `{base}`." if base else "") + reach
        )
    else:
        # Research items never get a worktree — promising them one asserts a future their pipeline
        # lacks.
        code_line = (
            "the repo stays read-only for this item — research changes no code."
            if kind == "research" else
            "the build phase gets a dedicated git worktree."
        )
        lines.append(
            "\n**Edit boundary:** this phase touches no real code — writes belong in the item's "
            "own folder (artifacts, checkpoints); " + code_line
        )
    # Told only where it may NOT write, a shell reaches for `$TMPDIR`, is refused, and abandons
    # the work.
    scratch = sandbox.ensure_scratch(Path(item_dir))
    lines.append(
        f"\n**Scratch space:** intermediate output — inventories, sorted lists, a helper script, "
        f"anything you need a file for rather than a pipe — goes in `{scratch}/`. It is inside the "
        f"boundary, so nothing there needs approval. Use it instead of `$TMPDIR` or `/tmp`, which "
        f"are outside every boundary. Nothing in it is read as a result or kept after this item "
        f"closes, so put working files there freely and cite none of them."
    )
    # The ending belongs to whichever branch owns it. What stays shared is the half about neither:
    # don't run ahead.
    lines.append(
        "\n**Gates:** the user advances phases and closes items — never you. When this phase's "
        "work is done, say so and stop; don't start the next phase's work. Bank a checkpoint "
        "(`write_checkpoint`) before wrapping up a long session."
        if interactive else
        "\n**Gates:** the user advances phases and closes items — never you. Don't start the next "
        "phase's work."
    )
    # Owed only while this thread's newest finished run is the compaction itself, so it clears
    # itself.
    if compacted_checkpoint:
        lines.append(compaction_notice(compacted_checkpoint))
    if interactive and phase == "review":
        # Here, not in review/SKILL.md: the turn hosting the review conversation never invokes the
        # phase skill.
        lines.append(
            "\n**Routing:** `plan.md` is not yours to edit here — the only way it changes is by "
            "routing this item back. Ending a turn with "
            "`report_completion(machine.outcome='revise')` requires their instruction to change "
            "the plan, or your offer and their yes. If the conversation carries neither, your "
            "next turn is that offer — however plainly they named the flaw. Before offering, name "
            "what they have NOT addressed and ask; silence on a point is not agreement. Carry "
            "their words VERBATIM into the routing — the plan phase reads what they said, not "
            "your paraphrase — and the work comes back re-vetted, so nothing you route skips a "
            "check.\n"
            "**This chat fixes nothing.** Do NOT start fixing in this session: no code, no "
            "commits, no plan edits. Work the owner wants that is OUTSIDE this item is a new "
            "item — `create_inbox_item` — never scope quietly added here. And this is a shared "
            "terminal: the owner and the deputy both speak in it, you cannot tell them apart, "
            "and you should not try."
        )
    if not interactive:
        # The counterpart, the never-page rule, and the tool ending. The payload contract lives in
        # the tool's schema.
        lines.append(
            "\n**Run protocol:** no reply from a person arrives mid-run, so never stop to page: a "
            "judgment call you can't make → a `## Assumptions` note in your phase's own record; a "
            "CONTRACT change you can't self-authorize → `request_authorization` (defers it to the "
            "review gate). Either way, finish what you can. End the run by calling "
            "`report_completion` — that call IS your closing statement, so say nothing after it.\n"
            # A run costs the SUM of its growing transcript: a result read at step i is re-sent
            # (N−i) times.
            "\n**Read the span, not the file.** What you read stays in this run's context and is "
            "re-sent on every step after it, so a big file read early is paid many times over. "
            "Once you have a line or a symbol — from a grep, a glob, or the plan — take that span "
            "(`Read` with `offset`/`limit`, `sed -n 'A,Bp'`); read a file whole only when you need "
            "the whole of it."
        )
    return "\n".join(lines)


# Both un-bound preambles carry it, because neither session may touch real code.
_NO_REAL_CODE = ("do NOT implement or edit the project's real code, or mutate work-items in this "
                 "session; that work happens inside a work-item.")


def general_preamble() -> str:
    """Every interactive un-bound dev turn · per-turn. The advisor: discussion only,
    no code or work-item mutation."""
    return (
        "## General session\n"
        "This session is NOT tied to any work-item. You MAY author and maintain this project's `general/` "
        "memory docs — routine anchor-doc upkeep happens here. But " + _NO_REAL_CODE + " When "
        "implementation work surfaces, don't attempt it — offer to itemize it, and on the user's go-ahead "
        "run the create-inbox-item skill.\n"
        "**Reading is not implementing.** To answer a question about this project you may read anything "
        "it has — files, logs, its database — and the shell is open for it: run the query. A command "
        "that isn't plainly read-only surfaces to the user for approval rather than being refused, so "
        "reach for the real tool (`sqlite3`, `psql`, the project's own CLI) instead of guessing or "
        "declining. Never offer to itemize a QUESTION: an inbox item is for work to be done, and a "
        "read you were about to run is not work.\n"
        "**Operating the board is not doing the work.** SuperMe's own controls are yours to drive: "
        "when the user names an inbox item and says to start it, push it (`push_inbox_item`). "
        "Filing an item is still not starting one — a ticket you just wrote waits for them to say go.\n"
        "**General session chat response style:**\n"
        "- Use plain and easy language.\n"
        "- Keep your response short, clear, and to the point.\n"
        "- Use bullets or numbered lists to organize information if there is more than one point."
    )


def onboarding_preamble(mode: str | None = None) -> str:
    """Interactive dev turns while the project has no established memory."""
    if mode == "project-init":
        skill = ("Run **project-init** — this was connected as a NEW/greenfield project.")
    elif mode == "retrofit":
        skill = ("Run **retrofit** — this was connected as an EXISTING codebase, so read the code to "
                 "reconstruct what's there before asking.")
    else:
        skill = ("Run **project-init** (a new/greenfield project) or **retrofit** (an existing "
                 "codebase) — pick from what you find in the repo.")
    return (
        "## Onboarding session\n"
        "This dev project has NO SuperMe memory yet — establishing it IS the work of this session. "
        f"Your job is to stand up the project's `general/` memory. {skill} Grill the user to pin down "
        "intent, then draft the anchor docs (PRD, spec, roadmap, architecture) for their approval.\n"
        "The user is normally told to open with a short description of the project, so if their "
        "message carries one, START FROM IT — grill from there, never re-ask what they just said. "
        "If it doesn't (they asked something else, or said nothing much), just ask for the one-liner "
        "and go. Authoring `general/` memory is exactly "
        "what you're here to do. But " + _NO_REAL_CODE + " (no code writes, commits, installs, or "
        "migrations, including via shell). Keep the user in the loop — draft for "
        "approval, don't assume."
    )


def diagnosis_preamble(run: dict | None, run_id: int) -> str:
    """Every turn of a diagnosis session. Small and STABLE so it caches; the
    subject's TRACE is injected once at birth."""
    run = run or {}
    feature = run.get("feature") or "chat"
    status = run.get("status") or "?"
    model = run.get("model") or "—"
    started = run.get("started_at") or "?"
    item_id = run.get("item_id")
    subject_line = (
        f"Activity **#{run_id}** — {feature} · status `{status}` · model `{model}` · started {started}"
        + (f" · work-item `{item_id}`" if item_id else "")
    )
    trace_line = (
        " The subject run's full trace was provided at the start of this session."
        if run else ""
    )
    return (
        "## Diagnosis\n"
        f"You are SuperMe's DIAGNOSIS agent, examining one past run: {subject_line}.\n\n"
        "This session is read-only: investigate, explain, and discuss with the user; propose a "
        "fix, improvement, idea, or plan — but never edit code, mutate work-items, commit, or run "
        "migrations here. If a concrete fix is warranted, describe it and offer to itemize it "
        "(the create-inbox-item skill) so the work happens in its own work-item.\n\n"
        "To dig deeper you can start from this repo's knowledge, other runs (`read_run`), and the dev log "
        f"(`read_dev_log`).{trace_line}"
    )


# The dial moves ONLY this discretionary band; the refusal floor holds at every level.
_DEPUTY_STRICTNESS = {
    "low": "Maximum delegated autonomy. Approve on your own when vet's coverage is reasonable, and "
           "handle gaps by sending back. Escalate ONLY for decisions the mandate reserves for the "
           "owner and items you genuinely cannot unblock. Act as the owner would on a good day, "
           "decisively.",
    "medium": "Approve on your own when vet's coverage is solid. Also escalate genuinely ambiguous, "
              "high-stakes calls, but only after you have tried to settle them yourself.",
    "high": "Approve alone only when vet clearly establishes the deliverable's success signal. "
            "Escalate anything where the owner personally running it would add real signal beyond "
            "what vet covered.",
    "extra": "Most conservative. Approve alone only for plumbing where nothing is exercisable and "
             "vet is airtight. When you are in doubt whether the owner would want to see it, "
             "escalate, but only after resolving what you can yourself. This is not licence to "
             "page eagerly. See Resolve first.",
}
DEPUTY_STRICTNESS_LEVELS = tuple(_DEPUTY_STRICTNESS)
DEPUTY_STRICTNESS_DEFAULT = "medium"


def deputy_preamble(strictness: str = DEPUTY_STRICTNESS_DEFAULT) -> str:
    """Every deputy dispatch. The frame and the floor only."""
    if strictness not in _DEPUTY_STRICTNESS:   # defence in depth, the setting is validated too
        strictness = DEPUTY_STRICTNESS_DEFAULT
    band = _DEPUTY_STRICTNESS[strictness]
    return (
        "## Deputy\n"
        "You are the owner's deputy at one gate of one autopiloted work-item. The owner is away. "
        "Make the call a careful owner would make. Keeping work moving is autopilot's job. Yours "
        "is judgment, and approving work that was not ready removes the last safeguard.\n"
        "You are a fresh session. You never saw the build conversation, so judge from the "
        "artifacts and never from the builder's account of them. Your only memory is the decision "
        "log in your dispatch.\n"
        "The procedure is the `superme-dev:deputy` skill. Run it.\n\n"

        "### Your decision, one of three\n"
        "- **approve.** The work meets the bar. Advance it.\n"
        "- **send_back.** A specific, fixable gap. Your `change` is posted to the item and routes "
        "it back through build and vet for the fix and its re-validation. You do not fix anything "
        "and you do not converse mid-loop. Prefer this over escalate whenever build and vet can "
        "close the gap without the owner.\n"
        "- **escalate.** Page the owner, for a decision the mandate reserves or a confirmation "
        "only their own hands can give.\n"
        "You may not drop, abandon, or supersede work. Ending work is the owner's alone.\n"
        "You may not grant an authorization request, however obviously right it looks. Escalate "
        "it. Every scope is the owner's, and no verdict field grants one.\n\n"

        "### When you must not approve, at every strictness\n"
        "- **Not affirmatively convinced.** Withhold. Send back or escalate. \"Looks fine\" is not "
        "\"is fine\".\n"
        "- **You could not name what you checked.** Every approval states what you inspected and "
        "why it convinced you. A paraphrase of the brief means you rubber-stamped.\n"
        "- **The artifacts do not stand alone.** Needing the build transcript to decide means the "
        "artifacts are incomplete. That is the finding. Send back.\n"
        "- **A required artifact is missing, stale, or fails its own self-check.** Send back.\n"
        "- **A success signal only the owner can confirm, that vet did not cover.** Escalate with "
        "a runbook. If vet did establish it, approve rather than re-summon them.\n"
        "- **The mandate marks it owner-only.** Escalate, however obvious it seems.\n\n"

        "### Your strictness, " + strictness + "\n"
        + band + "\n"
        "This tunes how readily you reach for the owner. It does not relax the floor above. "
        "Resolve first, at every level. Before you page the owner you must have read the artifacts "
        "and vet results in full, reasoned the concern through, and sent it back instead if it was "
        "fixable. You exist to reduce how often the owner is pulled in. Then escalate what survives "
        "your own analysis without hesitation.\n\n"

        "### Your verdict\n"
        "End by calling `submit_gate_verdict` once, then say nothing beyond one short closing "
        "line."
    )


# assemblers (kernel speech built from durable state — moved whole, one file answers
# "what does the kernel say")

def _cap(text: str, cap: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= cap else text[:cap].rstrip() + "\n… (truncated)"


_DEPUTY_MANDATE_CAP = 3_000
_DEPUTY_LOG_CAP = 2_000
# A RUNAWAY GUARD, not a summarizer: reports are scaffold-capped already, so this should never
# fire.
_DEPUTY_REPORT_CAP = 12_000


def render_authorizations_block(pending: list[dict]) -> str:
    """The review deputy's authorization surface. Every one escalates."""
    if not pending:
        return ""
    lines = ["The build DEFERRED these changes to what the project INTENDS. You cannot grant one: "
             "**escalate**, and name them in your escalation so the owner grants or denies each:",
             ""]
    for a in pending:
        lines.append(f"- `{a['id']}` — scope `{a.get('scope')}` · doc `{a.get('doc') or '?'}` · "
                     f"blocks check `{a.get('check') or '?'}`\n    what: {a.get('what')}\n"
                     f"    why: {a.get('why')}")
    return "\n".join(lines)


def _deputy_check_rows(state: dict) -> str:
    """The gate's mechanical checks as the deputy reads them — the SAME rows the
    drilldown renders, off one call."""
    checks = state.get("checks") or []
    if not checks:
        return "_(this gate has no mechanical checks.)_"
    # A bare ✗ reads as something to fix, so a failing row says which kind of failing it is.
    lines = [f"- {'✓' if c.get('ok') else '✗'} **{c.get('criterion')}** — {c.get('detail')}"
             + ("" if c.get("ok") else
                "  _[must-resolve: this one greys the owner's Approve]_" if c.get("blocking") else
                "  _[advisory: does NOT grey Approve, and is not grounds for a send_back alone]_")
             for c in checks]
    blocked = state.get("blocked_by") or []
    lines.append("")
    lines.append(f"Approve is currently **greyed** — {len(blocked)} must-resolve item(s) open."
                 if blocked else "Nothing must-resolve is open — Approve is live for the owner.")
    return "\n".join(lines)


def _deputy_verdict_table(rows: list[dict]) -> str:
    """The vet's per-check verdicts, latest per check. The evidence ledger carried
    a count and no verdicts."""
    if not rows:
        return ("_(no check verdicts recorded. Either the approved plan declared `depth: none` — "
                "verify that in `## Verification plan` — or the vet never ran, which is not "
                "something to approve past.)_")
    out = []
    for r in rows:
        mark = "◌ deferred" if r.get("deferred") else ("✓ PASS" if r.get("passed") else "✗ FAIL")
        out.append(f"- **{r.get('check')}** {mark} (cycle {r.get('cycle')}) — `{r.get('how')}`"
                   + (f"\n    → {str(r.get('result'))[:300]}" if r.get("result") else ""))
    return "\n".join(out)


def deputy_brief_block(item_id: str, title: str, gate: str, *,
                       state: dict, report: dict | None = None,
                       mandate: str | None = None, log_digest: str | None = None,
                       delta: str | None = None, success_signal: str | None = None,
                       verdicts: list[dict] | None = None,
                       authorizations: str | None = None) -> str:
    """A deputy dispatch's birth prompt.

    The deputy reads what the owner reads."""
    parts = [f"Run superme-dev:deputy to judge the **{gate}** gate of work-item `{item_id}` "
             f"(\"{title}\").", ""]
    parts += ["### Mandate (this project's standing bar — binding)",
              _cap(mandate or "", _DEPUTY_MANDATE_CAP)
              or "_(no mandate authored yet — judge to the general deputy floor and lean "
                 "conservative.)_", ""]
    parts += ["### Your decision log (your prior calls at THIS gate on this item — your continuity)",
              _cap(log_digest or "", _DEPUTY_LOG_CAP)
              or "_(empty — this is your first recorded call at this gate.)_", ""]
    if (delta or "").strip():
        parts += [_cap(delta.strip(), _DEPUTY_LOG_CAP), ""]
    phase = str(state.get("phase") or gate)
    parts += [f"### What this phase reported — `reports/report-{phase}.md`, the document the owner "
              f"reads",
              _cap((report or {}).get("text") or "", _DEPUTY_REPORT_CAP)
              or f"_(no report-{phase}.md exists. The phase owes one; a gate with nothing reported "
                 f"is not a gate you can clear on the report's word — read the contract below.)_",
              ""]
    parts += ["### Mechanical checks (computed from the item's files — facts, not claims)",
              _deputy_check_rows(state), ""]
    contract = (report or {}).get("contract")
    paths = [p for p in (contract, "artifacts/plan.md") if p]
    parts += ["### The full contract (open with Read if the report leaves you a question)",
              "\n".join(f"- `{p}`" for p in dict.fromkeys(paths))
              + "\n\nThe report is the compact read; these are the whole thing. Open them rather "
                "than approving on a summary you doubt.", ""]
    if gate == "review":
        parts += ["### The deliverable's success signal (the owner's own words for \"good\")",
                  (f"> {success_signal.strip()}" if success_signal and success_signal.strip()
                   else "_(no success signal is on record for this deliverable. You cannot confirm "
                        "a signal that was never written — if the review turns on one, escalate and "
                        "say so.)_"), ""]
        parts += ["### The vet's verdicts (latest per check)",
                  _deputy_verdict_table(verdicts or []), ""]
        if authorizations:
            parts += ["### Authorization requests awaiting a decision", authorizations, ""]
    parts += ["Inspect anything above with Read/Grep, form your own view, then emit your verdict."]
    return "\n".join(parts)


# intake NARRATES: at review it answers from the record. The watermark counts promoted ledger
# entries — a stable cursor.

_HANDOFF_TOTAL_CAP = 12_000
_HANDOFF_REPORT_CAP = 8_000

def _cycle_verdict_summary(item_dir: Path, cycle: int) -> str:
    """One line summarizing a cycle's recorded check verdicts, from the same
    `## Verification` entries the loop reads."""
    latest: dict[str, bool] = {}
    for e in artifacts.evidence_entries(item_dir):
        if int(e.get("cycle") or 0) == cycle:
            latest[e["check"]] = bool(e.get("passed"))
    if not latest:
        return "(no recorded checks)"
    return "; ".join(f"{c} — {'PASS' if p else 'FAIL'}" for c, p in latest.items())


def render_handoff_block(item: dict, item_dir: Path) -> tuple[str | None, int]:
    """The item's intake thread, first turn after new loop activity."""
    item_dir = Path(item_dir)
    try:
        mark = int(str(item.get("handoffs_promoted") or 0).strip() or 0)
    except (TypeError, ValueError):
        mark = 0
    attempts = artifacts.read_cycle_outcomes(item_dir)
    if len(attempts) <= mark:
        return None, mark
    new = attempts[mark:]
    reports = {r["cycle"]: r for r in artifacts.cycle_reports(item_dir)}
    new_cycles = sorted({int(a["cycle"]) for a in new if int(a.get("cycle") or 0) in reports})
    latest_cycle = new_cycles[-1] if new_cycles else 0

    lines = [
        "### Loop record — build⟷vet handoff (kernel-assembled, promoted once)",
        "The build⟷vet loop ran on this item since this thread's last turn. Below is the curated "
        "record — the driver's decisions and the vet verdicts, in time order. You did NOT do this "
        "work: narrate and answer FROM THIS RECORD (and the artifact files it names); for depth, "
        "the build/vet threads and `artifacts/` are on disk — never invent memory of the loop.",
        "",
        "#### Driver decisions (§Cycle outcome)",
    ]
    for a in new:
        bits = [f"cycle {a.get('cycle')}", f"vet {a.get('evidence', '?')}",
                f"→ {a.get('decision', '?')}"]
        if a.get("failed"):
            bits.append(f"failed: {a['failed']}")
        lines.append(f"- {a.get('ts', '?')} · {' · '.join(bits)} — {a.get('reason', '')}")
    for c in new_cycles:
        if c == latest_cycle:
            continue
        lines.append(f"- build-vet-{c}.md checks: {_cycle_verdict_summary(item_dir, c)}")
    if latest_cycle:
        text = _cap(Path(reports[latest_cycle]["path"]).read_text(encoding="utf-8"), _HANDOFF_REPORT_CAP)
        lines += ["", f"#### Latest cycle report (build-vet-{latest_cycle}.md, verbatim)", text]
    return _cap("\n".join(lines), _HANDOFF_TOTAL_CAP), len(attempts)


# --- diagnosis subject-run trace ------------------------------------------------------------

# The kinds the trace formatter renders in full. Anything else is skipped — the human-legible
# trail only.
_TRACE_KINDS = {"prompt", "reply", "tool", "mcp", "skill", "agent", "status", "result"}


def _format_trace(run: dict, events: list[dict]) -> str:
    """The subject run's trail as compact, ordered lines — the same data the Activity
    trace popup shows."""
    lines: list[str] = []
    for ev in events:
        kind = (ev.get("kind") or "").lower()
        if kind not in _TRACE_KINDS:
            continue
        name = (ev.get("name") or "").strip()
        desc = " ".join((ev.get("description") or "").split())
        if len(desc) > 600:
            desc = desc[:600] + " …"
        if kind == "prompt":
            lines.append(f"- **user:** {desc}")
        elif kind == "reply":
            lines.append(f"- **assistant:** {desc}")
        elif kind == "result":
            # Labelled with the tool name — parallel calls batch, so position alone can mis-pair.
            label = name or "tool"
            lines.append(f"    ↳ {label} returned: {desc}" if desc else f"    ↳ {label} returned: (empty)")
        else:  # a tool / mcp / skill / agent call
            label = name or kind
            lines.append(f"- `{label}` — {desc}" if desc else f"- `{label}`")
    return "\n".join(lines) if lines else "_(no recorded trail for this run)_"


def diagnosis_trace_block(run: dict | None, events: list[dict], run_id: int) -> str:
    """A diagnosis session's BIRTH turn · injected once, so later turns cache-read
    it instead of re-sending."""
    return (
        f"### Subject activity-run trace (Activity #{run_id})\n"
        f"{_format_trace(run or {}, events)}"
    )
