"""Per-kind session AGENTS — the daemon-assembled identity preamble for each kind of dev session.

A SuperMe dev session runs as one of several AGENTS, each with its own identity (persona): how the
turn is centered, what it may touch, and (for subject-bearing agents) what it's pointed at. This
module owns each agent's PREAMBLE — the system-prompt block prepended to the turn — so adding an
agent = adding a preamble here, not threading conditionals through the ws turn loop
(session-kinds-diagnose). The daemon knows the session's stamp + establishment state, so it picks the
preamble; Core just appends what it's handed.

The agents:
  general    — the free-discussion advisor. May author `general/` memory; no code / work-item mutation.
  work_item  — the builder, bound to one primary work-item. Centered on it; owns + advances it.
  onboarding — the general agent's ESTABLISH-MEMORY persona, used while a dev project has no memory
               yet (project-init / retrofit). Transient: it's not a durable stamped kind — the session
               stamps `general`, and this preamble applies per-turn only while the project is
               unestablished, reverting to `general_preamble` once memory exists.
  diagnosis  — the read-only inspector, pointed at a subject run (an Activity row). Its preamble
               auto-injects the run's trace so the session starts oriented; investigates + proposes,
               never advances anything.

`SESSION_KINDS` = the DURABLE stampable `session.kind` values. `onboarding` is stamped at birth as a
category LABEL (the chat picker shows it) — but it does NOT drive behavior: the onboarding PREAMBLE is
selected per-turn by project state (`is_onboarding`, ws.py), so a stamped session still reverts to
`general_preamble` once the project is established. Only `diagnosis` changes runtime behavior.
"""

# The durable `session.kind` values (spine column). `onboarding` is a label-only kind — stamped at a
# session's birth for the picker's category chip, but the onboarding persona is applied per-turn by
# project state, never by this stamp (so it un-applies automatically once memory exists).
SESSION_KINDS = ("general", "work_item", "onboarding", "diagnosis")


def work_item_preamble(item_id: str, item: dict, item_dir) -> str:
    """The work_item agent (the builder): center on its bound item (pointer-only — names the item +
    where its materials live; inlines nothing, keeping ctx% honest)."""
    title = item.get("title") or item_id
    phase = item.get("phase") or "—"
    return (
        f"## Focus\n"
        f"This session is dedicated to work-item **{item_id} — \"{title}\"** (phase: {phase}). "
        f"This is your primary work-item to work on; the user's questions are centred on this "
        f"item's content unless they explicitly point elsewhere. Its materials live at "
        f"`{item_dir}/` — read them on demand to ground your answers rather than guessing. "
        f"You may still read other work-items and repo knowledge when relevant."
    )


def general_preamble() -> str:
    """The general agent (the advisor): discussion-only, may author `general/` memory but no code /
    work-item mutation."""
    return (
        "## General session\n"
        "This session is NOT tied to any work-item. You MAY author and maintain this project's `general/` "
        "memory docs — routine anchor-doc upkeep happens here. But do NOT implement or edit the project's "
        "real code, or mutate work-items, in this session (no code writes, commits, installs, or "
        "migrations — including via shell); that work happens inside a work-item. When implementation "
        "work surfaces, don't attempt it — offer to itemize it, and on the user's go-ahead run the "
        "create-inbox-item skill."
    )


def onboarding_preamble() -> str:
    """The onboarding agent: the general agent's ESTABLISH-MEMORY persona, used while the dev project
    has no SuperMe memory yet. More directive than the general advisor — its job is to stand the
    project's memory up (project-init for greenfield, retrofit for existing code)."""
    return (
        "## Onboarding session\n"
        "This dev project has NO SuperMe memory yet — establishing it IS the work of this session. "
        "Your job is to stand up the project's `general/` memory: run **project-init** (a new/greenfield "
        "project) or **retrofit** (an existing codebase) — grill the owner to pin down intent, then draft "
        "the anchor docs (PRD, spec, roadmap, architecture) for their approval. Authoring `general/` memory "
        "is exactly what you're here to do. But do NOT implement or edit the project's real code, or mutate "
        "work-items (no code writes, commits, installs, or migrations — including via shell); that's for a "
        "work-item once the project is established. Keep the owner in the loop — draft for approval, don't "
        "assume."
    )


# The kinds the trace formatter renders in full (a prompt/reply/tool/result line each). Anything
# else (usage snapshots etc.) is skipped — the diagnosis preamble wants the human-legible trail only.
_TRACE_KINDS = {"prompt", "reply", "tool", "mcp", "skill", "agent", "status", "result"}


def _format_trace(run: dict, events: list[dict]) -> str:
    """The subject run's trail as compact, ordered lines — the same (prompt · reply · call) data the
    Activity trace popup shows, rendered for the agent to read."""
    lines: list[str] = []
    for ev in events:
        kind = (ev.get("kind") or "").lower()
        if kind not in _TRACE_KINDS:
            continue
        name = (ev.get("name") or "").strip()
        desc = " ".join((ev.get("description") or "").split())  # collapse whitespace/newlines
        if len(desc) > 600:
            desc = desc[:600] + " …"
        if kind == "prompt":
            lines.append(f"- **user:** {desc}")
        elif kind == "reply":
            lines.append(f"- **assistant:** {desc}")
        elif kind == "result":
            # The output of a preceding call — labelled with the tool name (parallel calls batch, so
            # position alone can mis-pair) and indented so the trail stays legible.
            label = name or "tool"
            lines.append(f"    ↳ {label} returned: {desc}" if desc else f"    ↳ {label} returned: (empty)")
        else:  # a tool / mcp / skill / agent call
            label = name or kind
            lines.append(f"- `{label}` — {desc}" if desc else f"- `{label}`")
    return "\n".join(lines) if lines else "_(no recorded trail for this run)_"


def diagnosis_preamble(run: dict | None, run_id: int) -> str:
    """The diagnosis agent's IDENTITY + read-only behaviour contract — small and STABLE, so it rides the
    per-turn system prompt cheaply (it caches). The subject run's large TRACE is NOT here: it's injected
    once at session birth via `diagnosis_trace_block` into the transcript, so resumed turns read it from
    cache instead of re-writing ~20k every turn (token-inefficiency-per-turn-append). `run` is the subject
    run row (or None if it can't be loaded — the session still opens, just without the header facts)."""
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
    return (
        "## Diagnosis\n"
        f"You are SuperMe's DIAGNOSIS agent, focusing on one past agentic run activity"
        f" — {subject_line} - against user query. \n\n"
        "It is read-only such that you investigate, explain, and discuss with users, and may propose a fix, improvement, idea, or plan — but you do NOT "
        "edit code, mutate work-items, commit, or run migrations here. If a concrete fix is warranted, "
        "describe it and offer to itemize it (the create-inbox-item skill) so the work happens in its "
        "own work-item.\n\n"
        "To dig deeper you can read this repo's knowledge, other runs (`read_run`), and the dev log "
        "(`read_dev_log`). The subject run's full trace was provided at the start of this session."
    )


def diagnosis_trace_block(run: dict | None, events: list[dict], run_id: int) -> str:
    """The subject run's full trace — injected ONCE into the birth turn's prompt so it lands in the SDK
    transcript (cache-read on every later turn) rather than being re-sent in the per-turn system prompt
    (token-inefficiency-per-turn-append)."""
    return (
        f"### Subject activity-run trace (Activity #{run_id})\n"
        f"{_format_trace(run or {}, events)}"
    )
