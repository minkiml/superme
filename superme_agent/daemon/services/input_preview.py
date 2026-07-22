"""Input preview (prompt inspector, B) — reconstruct the FULL input a fresh run of a given phase
would receive, and render it as a self-contained HTML page for a new browser tab.

Two channels, exactly as the doc's context-model describes them:
  • system prompt  — the layer-2 system append (persona · charter · constitution catalog ·
    operating-context · session-kind preamble · background contract), assembled through the SAME
    `AgentService.assemble_system_append` a live turn uses — so a preview is byte-for-byte what the
    turn would send (no reproduction drift).
  • prompt body    — the birth-once orient block + the phase's kernel-speech trigger, assembled the
    same way the background runners build their prompt.

This is the "B" (reconstruct-from-current-state) half of the prompt inspector. "A" (capture the
ACTUAL bytes at send time, per run) lands next and reuses `render_input_page` with mode="captured".
Pure read — no run is fired, nothing is persisted.
"""

from __future__ import annotations

import html
import logging

from ...core import artifacts as _arts, kernel_speech, kind_profiles

log = logging.getLogger("superme-agent")


def _phase_trigger(phase: str, item_id: str, title: str, item_dir) -> str | None:
    """The kernel-speech trigger a fresh run of `phase` would open with (None at the review gate,
    which fires no work run). Mirrors what each background runner passes as its trigger."""
    if phase in ("triage", "plan", "investigate", "report"):
        return kernel_speech.intake_trigger(phase, item_id, title)
    if phase == "build":
        rep = _arts.latest_vet_report(item_dir)
        if rep:   # a failure-hop cycle hands over the latest vet report
            return kernel_speech.build_loop_trigger(item_id, title, rep["cycle"], rep["text"])
        return kernel_speech.build_first_trigger(item_id, title)   # the loop's opening cycle
    if phase == "vet":
        deferred = [str(a.get("check")) for a in _arts.pending_authorizations(item_dir)
                    if a.get("check")]
        return kernel_speech.vet_trigger(item_id, title, deferred=deferred or None)
    if phase == "close":
        return kernel_speech.close_trigger(item_id, title)
    return None   # review — a gate, no background work run


def build_preview_input(context_id: str, item_id: str, phase: str) -> dict | None:
    """Reconstruct the full input a fresh `phase` run on `item_id` would receive → a dict of
    {meta, system_prompt, prompt_body, preamble, trigger}, or None when the item/phase is unknown.
    `phase` is validated against the item's kind pipeline (an off-pipeline phase → None → 404)."""
    from ...gateway import contexts
    from .. import app_state
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        return None
    dev = app_state.dev
    spine = app_state.spine
    agent = app_state.agent
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        return None
    if phase not in kind_profiles.get_profile(item.get("kind")).phases:
        return None
    item_dir = dev_root / "work-items" / item_id
    title = item.get("title") or item_id
    # View the item AS IF resting at the requested phase — the preamble + orient both read
    # item["phase"], and we're previewing THIS phase regardless of where the item actually sits.
    item_view = dict(item)
    item_view["phase"] = phase

    is_gate = phase == "review"
    background = not is_gate   # every non-gate phase fires a kernel background run
    preamble = kernel_speech.work_item_preamble(item_id, item_view, str(item_dir))
    trigger = _phase_trigger(phase, item_id, title, item_dir)
    orient = kernel_speech.render_orient_block(item_view, item_dir)
    prompt_body = f"{orient}\n\n---\n\n{trigger}" if trigger else orient
    # The FULL system append, assembled through the live code path (faithful by construction).
    system_prompt = agent.assemble_system_append(ctx, system_append=preamble, background=background)

    meta = {
        "item_id": item_id,
        "title": title,
        "phase": phase,
        "kind": item.get("kind") or "implementation",
        "session_role": kind_profiles.session_role(phase),
        "is_gate": is_gate,
        "background": background,
        "model": spine.effective_model(context_id, item_model=item.get("model")),
        "effort": spine.effective_effort(context_id, item_effort=item.get("effort")),
    }
    return {"meta": meta, "system_prompt": system_prompt, "prompt_body": prompt_body,
            "preamble": preamble, "trigger": trigger}


# --------------------------------------------------------------------------- HTML rendering

_PAGE_CSS = """
:root { color-scheme: dark light; }
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif;
  background: #0e1117; color: #e6edf3; }
.wrap { max-width: 980px; margin: 0 auto; padding: 28px 22px 80px; }
.hdr { display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px; margin-bottom: 4px; }
.hdr h1 { font-size: 17px; margin: 0; font-weight: 650; }
.sub { color: #8b949e; font-size: 12.5px; margin: 2px 0 18px; }
.chip { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px;
  font-weight: 600; letter-spacing: .02em; }
.chip.phase { background: #1f6feb22; color: #79c0ff; border: 1px solid #1f6feb55; }
.chip.mode-preview { background: #9e6a0322; color: #e3b341; border: 1px solid #9e6a0355; }
.chip.mode-captured { background: #23863622; color: #7ee787; border: 1px solid #23863655; }
.note { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px 13px;
  color: #adbac7; font-size: 12.5px; margin: 0 0 22px; }
section { margin: 0 0 26px; }
.sec-hdr { display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
  border-bottom: 1px solid #30363d; padding-bottom: 6px; margin-bottom: 10px; }
.sec-hdr h2 { font-size: 13.5px; margin: 0; font-weight: 650; color: #e6edf3; }
.sec-hdr .meta { color: #6e7681; font-size: 11.5px; font-variant-numeric: tabular-nums; }
pre { margin: 0; padding: 15px 16px; background: #161b22; border: 1px solid #30363d;
  border-radius: 8px; white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere;
  font: 12.5px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace; color: #d1d9e0; }
.gate { color: #8b949e; font-style: italic; }
"""


def _approx_tokens(text: str) -> int:
    return round(len(text) / 4)   # a rough char/4 heuristic — enough to gauge weight


def _section(title: str, body: str) -> str:
    body = body or ""
    chars = len(body)
    return (
        '<section><div class="sec-hdr">'
        f'<h2>{html.escape(title)}</h2>'
        f'<span class="meta">{chars:,} chars · ~{_approx_tokens(body):,} tok</span>'
        '</div>'
        f'<pre>{html.escape(body)}</pre></section>'
    )


def render_input_page(data: dict, *, mode: str = "preview") -> str:
    """Render one input-inspector page (self-contained HTML). `mode` = "preview" (B — reconstructed
    from current state) or "captured" (A — the actual bytes a real run sent)."""
    m = data["meta"]
    is_preview = mode != "captured"
    mode_chip = ("mode-preview", "PREVIEW · reconstructed from current state") if is_preview \
        else ("mode-captured", "ACTUAL · as sent to the model")
    banner = (
        "This is what a fresh run of this phase <b>would</b> receive, rebuilt from the item's "
        "current state — it may differ from any past run." if is_preview else
        "This is the exact input that was sent to the model for this run."
    )
    sys_body = _section("System prompt — SuperMe layer-2 append", data.get("system_prompt", ""))
    if m.get("is_gate"):
        body_html = ('<section><div class="sec-hdr"><h2>Prompt body</h2></div>'
                     '<pre class="gate">review is a gate — no background work run fires here; '
                     'the deputy/owner judges it. (Only the orient block is shown above.)</pre>'
                     '</section>') if not data.get("trigger") else _section(
                         "Prompt body — the user message", data.get("prompt_body", ""))
    else:
        body_html = _section("Prompt body — the user message (orient block + trigger)",
                             data.get("prompt_body", ""))
    title = html.escape(f"{m['item_id']} — {m['title']}")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Input · {html.escape(m['phase'])} · {html.escape(m['item_id'])}</title>"
        f"<style>{_PAGE_CSS}</style></head><body><div class='wrap'>"
        f"<div class='hdr'><h1>{title}</h1>"
        f"<span class='chip phase'>{html.escape(m['phase'])}</span>"
        f"<span class='chip {mode_chip[0]}'>{mode_chip[1]}</span></div>"
        f"<div class='sub'>kind <code>{html.escape(m['kind'])}</code> · session role "
        f"<code>{html.escape(m['session_role'])}</code> · model <code>{html.escape(str(m['model']))}</code>"
        f" · effort <code>{html.escape(str(m['effort']))}</code>"
        f"{' · background run' if m.get('background') else ''}</div>"
        f"<div class='note'>{banner}<br>The SDK prepends the Claude Code preset base system prompt "
        "(not authored by SuperMe); everything below is SuperMe's own system append plus the prompt "
        "body written into the transcript.</div>"
        f"{sys_body}{body_html}"
        "</div></body></html>"
    )
