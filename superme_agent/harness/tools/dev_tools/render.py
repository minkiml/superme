"""Rendering a tool result for an agent to read: ids, dates, and the list formats."""

import json
import os
import re
from datetime import date, datetime, timedelta, timezone

# Namespace-qualified ids, because a bare `#99` is ambiguous across the proposal, candidate, event
# and run id spaces.

def _qid(ns: str, ident) -> str:
    """A namespace-qualified id: `proposal:99`, `event:559`, `run:228`, `candidate:172`, `inbox:12`."""
    return f"{ns}:{ident}"


def _artifact_name(path: str | None) -> str | None:
    """The specific artifact a learning event produced, from its staged_path — which skill,
    constitution or agent, not just the form."""
    if not path:
        return None
    stem = os.path.basename(path).rsplit(".", 1)[0]
    # A dir-named artifact (skills/<name>/SKILL.md) → the parent dir carries the real name.
    if stem.upper() in ("SKILL", "AGENT", "README", "INDEX"):
        return os.path.basename(os.path.dirname(path)) or stem
    return stem


def _event_refs(meta: dict | None) -> str:
    """The qualified cross-table refs an event carries in its meta, as a compact ` · refs=…` clause.
    Empty when the event references nothing structured."""
    if not isinstance(meta, dict):
        return ""
    parts: list[str] = []
    if meta.get("proposal_id") is not None:
        parts.append(_qid("proposal", meta["proposal_id"]))
    cids = meta.get("candidate_ids")
    if isinstance(cids, (list, tuple)) and cids:
        parts.append("+".join(_qid("candidate", i) for i in cids))
    name = _artifact_name(meta.get("staged_path"))
    if name:
        parts.append(f'artifact="{name}"')
    return (" · refs=" + " ".join(parts)) if parts else ""


def _day_range(day: str) -> tuple[str, str] | None:
    """Resolve a relative or calendar day to a [start, end) UTC range in the OWNER'S local timezone.

    Events are stored UTC but "today" is a local-calendar notion, so a UTC+12 owner would otherwise
    miss events still dated yesterday."""
    local_tz = datetime.now().astimezone().tzinfo
    d = day.strip().lower()
    if d == "today":
        target = datetime.now(local_tz).date()
    elif d == "yesterday":
        target = datetime.now(local_tz).date() - timedelta(days=1)
    else:
        try:
            target = date.fromisoformat(day.strip())
        except ValueError:
            return None
    start = datetime(target.year, target.month, target.day, tzinfo=local_tz)
    end = start + timedelta(days=1)

    def utc(x: datetime) -> str:
        return x.astimezone(timezone.utc).isoformat(timespec="seconds")

    return utc(start), utc(end)


def _fmt(events: list[dict]) -> str:
    """Render dev-log events as qualified, scannable records, newest first.

    The `refs=` ids are a HISTORICAL record: the referenced row may since have been cleaned, so a
    ref here does not prove a live row."""
    if not events:
        return "(no matching activity)"
    lines = [
        "# read_dev_log · dev event log, newest-first",
        "# record: event:<id> · <ts> · <kind> · <actor>@<scope> · [refs=<qualified ids>] · <summary>",
        "# NOTE refs= are HISTORICAL pointers into other tables (a referenced row may be gone); "
        "for LIVE rows use read_proposals / read_candidates.",
    ]
    for e in events:
        scope = f"item:{e['item_id']}" if e.get("item_id") else "dev-level"
        lines.append(
            f"{_qid('event', e.get('id'))} · {e['created_at']} · {e['kind']} · {e['actor']}@{scope}"
            f"{_event_refs(e.get('meta'))} · {e['summary']}")
    return "\n".join(lines)


def _fmt_candidates(rows: list[dict]) -> str:
    """Render operational-learning candidates for distill to judge — one block per row, with every
    field it needs to classify and draft."""
    if not rows:
        return "(no candidates in this state)"
    out = ["# read_candidates · LIVE learning-candidate rows · head: candidate:<id> · <ts> · src=… [· form_hint · scope · item:…]"]
    for r in rows:
        head = f"{_qid('candidate', r['id'])} · {r['captured_at']} · src={r['source']}"
        if r.get("form_hint"):
            head += f" · form_hint={r['form_hint']}"
        if r.get("scope_hint"):
            head += f" · scope={r['scope_hint']}"
        if r.get("origin_item_id"):
            head += f" · {_qid('item', r['origin_item_id'])}"
        block = [head, f"  statement: {r['signal']}"]
        if r.get("rationale"):
            block.append(f"  rationale: {r['rationale']}")
        if r.get("evidence"):
            ev = r["evidence"]
            block.append(f"  evidence: {json.dumps(ev) if isinstance(ev, (dict, list)) else ev}")
        out.append("\n".join(block))
    return "\n\n".join(out)


def _fmt_proposals(rows: list[dict]) -> str:
    """Render the OPEN proposals for distill to consolidate against, so it can spot a standing
    proposal that already covers a learning and merge rather than duplicate."""
    if not rows:
        return "(no open proposals — nothing to consolidate against)"
    out = ["# read_proposals · LIVE open-proposal rows · head: proposal:<id> · <status> · <form>/<scope> [· cluster]"]
    for r in rows:
        head = f"{_qid('proposal', r['id'])} · {r['status']} · {r.get('output_form')}/{r.get('target_scope')}"
        if r.get("cluster"):
            head += f" · cluster={r['cluster']}"
        cids = r.get("candidate_ids") or []
        block = [head, f"  title: {r['title']}"]
        if r.get("summary"):
            block.append(f"  summary: {r['summary']}")
        block.append(f"  draws on: {' '.join(_qid('candidate', i) for i in cids) if cids else '—'}")
        out.append("\n".join(block))
    return "\n\n".join(out)


def _fmt_run_list(rows: list[dict]) -> str:
    """One line per recent run — enough for the agent to pick the id to inspect."""
    if not rows:
        return "(no runs recorded for this repo yet)"
    out = [
        "# read_run · recent runs, newest-first · record: run:<id> · <feature> · <status> · <model> · <tok> · <ts> [· item:…]",
        "# call read_run with a run_id (the number in run:<id>) to inspect one's full trace.",
    ]
    for r in rows:
        item = f" · {_qid('item', r['item_id'])}" if r.get("item_id") else ""
        out.append(
            f"{_qid('run', r['id'])} · {r.get('feature')} · {r.get('status')} · {r.get('model') or '—'}"
            f" · {r.get('tokens') or 0} tok · {r.get('started_at')}{item}")
    return "\n".join(out)


def _fmt_run_trace(run: dict, events: list[dict]) -> str:
    """One run's full trace: its summary header plus the ordered prompt, reply and call trail — the
    diagnosis substrate."""
    head = [
        f"{_qid('run', run['id'])} · {run.get('feature')} · {run.get('mode')} · {run.get('status')}",
        f"  model={run.get('model') or '—'} · tokens={run.get('tokens') or 0}"
        f" · ctx={run.get('ctx_pct') if run.get('ctx_pct') is not None else '—'}%"
        f" · phase={run.get('phase') or '—'}",
        f"  started={run.get('started_at')} · ended={run.get('ended_at') or '(running)'}",
    ]
    if run.get("item_id"):
        head.append(f"  work-item={_qid('item', run['item_id'])}")
    if run.get("session_fate"):
        head.append(f"  origin session: {run['session_fate']} (trace preserved)")
    if not events:
        head.append("\n(no per-event trail recorded for this run)")
        return "\n".join(head)
    trail = ["", "trace:"]
    for e in events:
        desc = (e.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 300:
            desc = desc[:300] + "…"
        label = e.get("name") or e.get("kind")
        # Indent a sub-agent's rows under its spawn: flat, a fan-out reads as one agent thrashing.
        indent = "    " if e.get("parent_tool_id") else "  "
        trail.append(f"{indent}{e.get('seq')}. [{e.get('kind')}] {label}" + (f" — {desc}" if desc else ""))
    return "\n".join(head + trail)


def _ids(raw) -> list[int]:
    """Parse a candidate-id list from a comma/space-separated string or a list (MCP args arrive as
    either). Non-numeric tokens are skipped."""
    if raw in (None, ""):
        return []
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[,\s]+", str(raw))
    seen = []
    for tok in items:
        try:
            n = int(str(tok).strip())
        except (ValueError, TypeError):
            continue
        if n not in seen:
            seen.append(n)
    return seen


def _s(args: dict, k: str) -> str | None:
    """A trimmed string arg, or None when absent/blank."""
    v = args.get(k)
    return str(v).strip() if v not in (None, "") else None


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}
