"""Input inspector (prompt inspector "A") — read back the ACTUAL input a past run sent (captured at
send time into `run_input`) and render it as a self-contained HTML page for a new browser tab.

Two channels, exactly as the doc's context-model describes them:
  • system prompt  — the layer-2 system append (persona · charter · constitution catalog ·
    operating-context · session-kind preamble incl. the background run protocol), captured through the SAME
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
    # What the turn was ALLOWED to do (2026-08-06). Captured in the same row as the prose, because
    # the prose alone can't explain why two runs on the same words behaved differently. None on
    # pre-feature rows → the page simply omits the block.
    surface = None
    try:
        parsed = json.loads(rec.get("turn_surface") or "null")
        surface = parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001 — a corrupt capture must still render the prose
        surface = None
    return {"meta": meta, "system_prompt": rec.get("system_prompt") or "",
            "system_fragments": system_fragments, "surface": surface,
            "prompt_body": rec.get("prompt_body") or "", "trigger": rec.get("prompt_body") or ""}


# --------------------------------------------------------------------------- HTML rendering

# ONE PALETTE, TWO THEMES, AND IT IS THE APP'S (owner, 2026-08-08). These pages open in a real
# browser tab, outside the app's own theme toggle, so the only signal available is the OS/browser
# preference — and they were hardcoded dark, which put a black page beside a light dashboard for
# anyone running light. Every colour is now a variable declared twice: dark in `:root`, light under
# `prefers-color-scheme: light`.
#
# The VALUES are `web/frontend/src/index.css`'s own tokens, verbatim, so a doc opened in its own tab
# is the same document it is inside the drilldown rather than a lookalike in a second palette. Keep
# them in step: `--c-app` → `--bg`, `--c-surface` → `--panel`, `--c-sunken` → `--surface` (the app
# sinks code and inputs), `--c-accent-text` → `--accent`, `--c-warn` → `--warn`, `--c-success` → `--ok`.
# Nothing below may use a literal colour — a hex here is a rule that obeys only one theme.
_PAGE_CSS = """
:root {
  color-scheme: dark light;
  --bg: #0d0e11; --panel: #16181d; --surface: #101216; --line: #262a31;
  --fg: #e7e9ee; --body: #e7e9ee; --soft: #99a0ac; --muted: #99a0ac; --faint: #5f6572;
  --accent: #a7c7ff; --accent-bg: #17263f; --accent-line: #6ea8fe59;
  --warn: #e0a35a; --warn-bg: #e0a35a1f; --warn-line: #e0a35a59;
  --ok: #5fe3b3; --ok-bg: #5fe3b31f; --ok-line: #5fe3b359;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f4f5f7; --panel: #ffffff; --surface: #eef0f3; --line: #dfe2e8;
    --fg: #1c2024; --body: #1c2024; --soft: #5c626c; --muted: #5c626c; --faint: #9aa0ab;
    --accent: #2563eb; --accent-bg: #e4edfd; --accent-line: #3b82f659;
    --warn: #b4791f; --warn-bg: #b4791f1f; --warn-line: #b4791f59;
    --ok: #128a5b; --ok-bg: #128a5b1f; --ok-line: #128a5b59;
  }
}
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif;
  background: var(--bg); color: var(--fg); }
.wrap { max-width: 1240px; margin: 0 auto; padding: 28px 22px 80px; }
.hdr { display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px; margin-bottom: 4px; }
.hdr h1 { font-size: 17px; margin: 0; font-weight: 650; }
.sub { color: var(--muted); font-size: 12.5px; margin: 2px 0 18px; }
.chip { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px;
  font-weight: 600; letter-spacing: .02em; }
.chip.phase { background: var(--accent-bg); color: var(--accent); border: 1px solid var(--accent-line); }
.chip.mode-preview { background: var(--warn-bg); color: var(--warn); border: 1px solid var(--warn-line); }
.chip.mode-captured { background: var(--ok-bg); color: var(--ok); border: 1px solid var(--ok-line); }
.note { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 10px 13px; color: var(--soft); font-size: 12.5px; margin: 0 0 22px; }
section { margin: 0 0 30px; }
.sec-hdr { display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
  border-bottom: 1px solid var(--line); padding-bottom: 6px; margin-bottom: 12px; }
.sec-hdr h2 { font-size: 13.5px; margin: 0; font-weight: 650; color: var(--fg); }
.sec-hdr .meta { color: var(--faint); font-size: 11.5px; font-variant-numeric: tabular-nums; }
/* One fragment = the prompt text card (keeps full reading width) + a fixed right-side info gutter
   that lives in the ADDED page width, so the text card is never squeezed to fit the metadata. */
.frag { display: flex; gap: 16px; align-items: flex-start; margin: 0 0 12px; }
.frag .body { flex: 1 1 auto; min-width: 0; }
.frag .side { flex: 0 0 208px; padding-top: 2px; }
.fname { font-size: 12px; font-weight: 650; color: var(--fg); line-height: 1.4; }
.floc { margin-top: 5px; font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--faint); word-break: break-word; }
.fmeta { margin-top: 9px; color: var(--faint); font-size: 11px; font-variant-numeric: tabular-nums; }
pre { margin: 0; padding: 15px 16px; background: var(--surface); border: 1px solid var(--line);
  border-radius: 8px; white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere;
  font: 12.5px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--body); }
pre.gate { color: var(--muted); font-style: italic; }
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


def _render_surface(surface: dict | None) -> str:
    """④ — what the turn was ALLOWED to do. The prompt is only half of a run's input; the other
    half is its capability, and two runs carrying identical words behave differently when one may
    run a shell in the worktree and the other may not. Omitted entirely for rows captured before
    this existed, rather than rendered as a block of dashes claiming the turn had no powers."""
    if not surface:
        return ""
    def _paths(key: str) -> str:
        vals = surface.get(key) or []
        return ", ".join(str(v) for v in vals) if vals else "— none"
    rows = [
        ("model · effort", f"{surface.get('model') or '—'} · {surface.get('effort') or '—'}"),
        ("MCP servers", ", ".join(surface.get("mcp") or []) or "— none beyond the base preset"),
        ("write boundary", _paths("write_boundary")),
        ("shell sandbox", _paths("sandbox_writes")),
        ("file-write tools", "DENIED — read-only turn" if surface.get("read_only")
                             else "allowed inside the write boundary"),
        ("permission prompts", "auto-denied (background run)" if surface.get("approve") == "denied"
                               else str(surface.get("approve") or "—")),
        ("resumed transcript", "YES — the model also carries this thread's earlier turns, which are "
                               "NOT in this capture" if surface.get("resumes")
                               else "no — this is the whole context"),
    ]
    # Rendered as the same plain-text fragment card every other channel uses, so the page keeps one
    # vocabulary — this is a fourth thing the run was given, not a differently-shaped widget.
    w = max(len(k) for k, _ in rows)
    text = "\n".join(f"{k.ljust(w)}   {v}" for k, v in rows)
    return _render_section("④ Turn surface — what this run was ALLOWED to do", [{
        "name": "capability, not prose", "location": "daemon/services/runs.py · turn_surface()",
        "text": text}])


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
            note="No orient block on this run: the birth-injected orientation is retired "
                 "On a pre-retirement resumed session it sits earlier in the replayed "
                 "transcript. Only the trigger below is new to this run.")

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
    body_html = f"{orient_html}{trig_html}{_render_surface(data.get('surface'))}"
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
