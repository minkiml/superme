"""Input inspector (prompt inspector "A") — read back the ACTUAL input a past run sent (captured at
send time into `run_input`) and render it as a self-contained HTML page for a new browser tab.

Two channels, exactly as the doc's context-model describes them:
  • system prompt  — the layer-2 system append (persona · charter · constitution catalog ·
    operating-context · session-kind preamble · background contract), captured through the SAME
    `AgentService.assemble_system_append` a live turn used — byte-for-byte what the run sent.
  • prompt body    — the birth-once orient block + the phase's kernel-speech trigger, as sent.

Capture happens ONLY for throwaway prompt-extraction probe items (the Prompt X-ray tab fires one,
runs a real lifecycle, then tears it down leaving the tagged run trace + these captured inputs).
The reconstruct-from-current-state preview ("B") was removed — a real captured run reflects the
actual accumulation (resumed transcript, real cycles) that a preview cannot. Pure read.
"""

from __future__ import annotations

import html
import json
import logging
import re

from ...core import kind_profiles

log = logging.getLogger("superme-agent")


def build_captured_input(context_id: str, item_id: str, run_id: int) -> dict | None:
    """Prompt inspector "A": the ACTUAL input a past run sent, read back from the run_input capture
    → the same dict shape `render_input_page` takes, or None when this run has no captured input
    (a pre-feature run, or an interactive/chat turn that isn't captured)."""
    from ...gateway import contexts
    from .. import app_state
    spine = app_state.spine
    rec = spine.read_run_input(int(run_id))
    if rec is None or str(rec.get("item_id") or "") != str(item_id):
        return None
    ctx = contexts.resolve(context_id, "dev")
    item = (app_state.dev.read_work_item(ctx.internal_root / "dev", item_id)
            if ctx.internal_root else None) or {}
    run = spine.get_run(int(run_id)) or {}
    phase = rec.get("phase") or run.get("phase") or "?"
    try:
        role = kind_profiles.session_role(phase)
    except Exception:  # noqa: BLE001 — an unknown/legacy phase must still render
        role = "?"
    meta = {
        "item_id": item_id, "title": item.get("title") or item_id, "phase": phase,
        "kind": item.get("kind") or "implementation", "session_role": role,
        "is_gate": phase == "review", "background": bool(rec.get("background")),
        "model": run.get("model") or "—", "effort": run.get("effort") or "—",
        "run_id": int(run_id), "started_at": run.get("started_at"),
    }
    # The system prompt's provenance breakdown (ordered [{name,location,text}]), captured alongside
    # the whole system_prompt. None for pre-feature rows → the renderer falls back to one whole card.
    system_fragments = None
    raw_frags = rec.get("system_fragments")
    if raw_frags:
        try:
            parsed = json.loads(raw_frags)
            if isinstance(parsed, list):
                system_fragments = parsed
        except Exception:  # noqa: BLE001 — a corrupt capture must still render (fallback card)
            system_fragments = None
    return {"meta": meta, "system_prompt": rec.get("system_prompt") or "",
            "system_fragments": system_fragments,
            "prompt_body": rec.get("prompt_body") or "", "trigger": rec.get("prompt_body") or ""}


# --------------------------------------------------------------------------- HTML rendering

_PAGE_CSS = """
:root { color-scheme: dark light; }
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif;
  background: #0e1117; color: #e6edf3; }
.wrap { max-width: 1240px; margin: 0 auto; padding: 28px 22px 80px; }
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
section { margin: 0 0 30px; }
.sec-hdr { display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
  border-bottom: 1px solid #30363d; padding-bottom: 6px; margin-bottom: 12px; }
.sec-hdr h2 { font-size: 13.5px; margin: 0; font-weight: 650; color: #e6edf3; }
.sec-hdr .meta { color: #6e7681; font-size: 11.5px; font-variant-numeric: tabular-nums; }
/* One fragment = the prompt text card (keeps full reading width) + a fixed right-side info gutter
   that lives in the ADDED page width, so the text card is never squeezed to fit the metadata. */
.frag { display: flex; gap: 16px; align-items: flex-start; margin: 0 0 12px; }
.frag .body { flex: 1 1 auto; min-width: 0; }
.frag .side { flex: 0 0 208px; padding-top: 2px; }
.fname { font-size: 12px; font-weight: 650; color: #e6edf3; line-height: 1.4; }
.floc { margin-top: 5px; font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #6e7681; word-break: break-word; }
.fmeta { margin-top: 9px; color: #6e7681; font-size: 11px; font-variant-numeric: tabular-nums; }
pre { margin: 0; padding: 15px 16px; background: #161b22; border: 1px solid #30363d;
  border-radius: 8px; white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere;
  font: 12.5px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace; color: #d1d9e0; }
pre.gate { color: #8b949e; font-style: italic; }
"""


def _approx_tokens(text: str) -> int:
    return round(len(text) / 4)   # a rough char/4 heuristic — enough to gauge weight


def _approx_tokens_n(chars: int) -> int:
    return round(chars / 4)


# The orient block's birth headers (mirror of sessions._BIRTH_BLOCK_HEADERS) and the kernel's
# `orient \n\n---\n\n trigger` join. A body that opens with a birth header carries the orient
# (a session-birth run); a resumed run's body is the trigger ALONE (orient already replayed
# earlier in the transcript) → (None, body).
_ORIENT_HEADERS = ("### Work-item orientation", "### Subject activity-run trace")


def _split_body(body: str) -> tuple[str | None, str]:
    """Split a run's prompt body into (orient_block, trigger). Returns (None, body) when this run
    injected no orient (a resumed run — the orient lives earlier in the replayed transcript)."""
    b = body or ""
    if b.lstrip().startswith(_ORIENT_HEADERS):
        parts = b.split("\n\n---\n\n", 1)   # same boundary sessions._strip_birth_block uses
        return (parts[0], parts[1]) if len(parts) == 2 else (b, "")
    return None, b


def _frag_card(text: str, *, name: str, location: str, gate: bool = False) -> str:
    """One fragment sub-card: the prompt-text box (full reading width) + a right-side info gutter
    (source name · location · size) living in the page's added side space — the text box is never
    narrowed to make room for the info."""
    body = (text or "").strip("\n")
    meta = "" if gate else f'<div class="fmeta">{len(body):,} chars · ~{_approx_tokens(body):,} tok</div>'
    return (
        '<div class="frag">'
        f'<div class="body"><pre{" class=\"gate\"" if gate else ""}>{html.escape(body)}</pre></div>'
        '<aside class="side">'
        f'<div class="fname">{html.escape(name)}</div>'
        f'<div class="floc">{html.escape(location)}</div>'
        f'{meta}</aside></div>'
    )


def _render_section(title: str, frags: list[dict]) -> str:
    """A section = its header (with roll-up size) + one sub-card per fragment."""
    total = sum(len((f.get("text") or "").strip("\n")) for f in frags)
    n = len(frags)
    cards = "".join(_frag_card(f.get("text", ""), name=f.get("name", "—"),
                               location=f.get("location", "—")) for f in frags)
    return (
        f'<section><div class="sec-hdr"><h2>{html.escape(title)}</h2>'
        f'<span class="meta">{total:,} chars · ~{_approx_tokens_n(total):,} tok · '
        f'{n} part{"" if n == 1 else "s"}</span></div>{cards}</section>'
    )


def _gate_section(title: str, *, name: str, note: str) -> str:
    """A section whose channel carried nothing on this run — one italic explanatory card, no size."""
    return (f'<section><div class="sec-hdr"><h2>{html.escape(title)}</h2></div>'
            f'{_frag_card(note, name=name, location="—", gate=True)}</section>')


def _fragment_orient(orient: str) -> list[dict]:
    """Split the orient block into its "### …" sub-sections, each a fragment. The block is written
    once at session birth (kernel_speech.render_orient_block / the sessions birth block)."""
    loc = "orient block · session birth (core/kernel_speech.py)"
    chunks = [c for c in re.split(r"(?m)^(?=### )", orient) if c.strip()]
    frags: list[dict] = []
    for c in chunks:
        first = c.lstrip().split("\n", 1)[0]
        name = first[4:].strip() if first.startswith("### ") else "Orientation preamble"
        frags.append({"name": name, "location": loc, "text": c})
    return frags or [{"name": "Orientation", "location": loc, "text": orient}]


def render_input_page(data: dict) -> str:
    """Render one input-inspector "A" page (self-contained HTML) — the ACTUAL bytes a real run sent,
    read back from the run_input capture. Each of the three channels is broken into per-fragment
    sub-cards, each labelled on the side with its source name + location."""
    m = data["meta"]
    mode_chip = ("mode-captured", "ACTUAL · as sent to the model")
    banner = "This is the exact input that was sent to the model for this run."
    # Three channels, matching the three distinct behaviors:
    #   1. system prompt   — assembled fresh EVERY run (reflects current state); shown as the ordered
    #      provenance fragments it's assembled from (persona · charter · catalog · … )
    #   2. orient block    — written ONCE at session birth (a frozen run-1 snapshot), split into its
    #      "### …" sub-sections; absent on resumed runs (it lives earlier in the replayed transcript)
    #   3. user/trigger    — this run's freshly injected kernel-speech message
    sys_frags = data.get("system_fragments") or [
        {"name": "System append (whole)", "location": "agent_service.assemble_system_append()",
         "text": data.get("system_prompt", "")}]
    sys_html = _render_section(
        "① System prompt — assembled fresh each run (reflects current state)", sys_frags)

    orient, trig = _split_body(data.get("prompt_body", ""))
    orient_title = "② Prompt body · orient block — written once at session birth (run-1 snapshot)"
    if orient is not None:
        orient_html = _render_section(orient_title, _fragment_orient(orient))
    else:
        orient_html = _gate_section(
            orient_title, name="not injected on this run",
            note="This run resumes an existing session, so the orient block was written at that "
                 "session’s birth run and already sits earlier in the replayed transcript (not "
                 "reproduced here). Only the trigger below is new to this run.")

    trig_title = "③ Prompt body · user message / trigger — this run’s injected message"
    if m.get("is_gate"):
        trig_html = _gate_section(
            trig_title, name="gate — no trigger",
            note="review is a gate — no background work run fires here; the deputy/owner judges it, "
                 "so no trigger is sent.")
    else:
        trig_html = _render_section(trig_title, [{
            "name": f"{m.get('phase', '?')} trigger message",
            "location": "core/kernel_speech.py · phase speech", "text": trig}])
    body_html = f"{orient_html}{trig_html}"
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
        f"{sys_html}{body_html}"
        "</div></body></html>"
    )


def render_missing_input_page(item_id: str, run_id: int) -> str:
    """The "A" page for a run with no captured input (a pre-feature run, or a chat/deputy turn)."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Input · run {html.escape(str(run_id))}</title><style>{_PAGE_CSS}</style></head>"
        "<body><div class='wrap'>"
        f"<div class='hdr'><h1>{html.escape(str(item_id))}</h1>"
        f"<span class='chip mode-captured'>run #{html.escape(str(run_id))}</span></div>"
        "<div class='note'>No captured input for this run. Input capture records the exact bytes a "
        "run sends, from the moment the feature shipped — earlier runs, and interactive chat / deputy "
        "turns, have none. Newer phase runs (triage · plan · build · vet · close) carry it.</div>"
        "</div></body></html>"
    )
