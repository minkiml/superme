"""SessionStore — list and replay past conversations for a Context.

The Claude Agent SDK already writes a full transcript JSONL per session (the same file
`resume` reads from), keyed by cwd under ~/.claude/projects/<encoded-cwd>/<id>.jsonl.
That file is the source of truth for history — we never keep a parallel message log.

But that projects folder is shared: SuperMe's sessions and the owner's own Claude Code
sessions land there together (same cwd). So *which* sessions are SuperMe's comes from the
system spine's session table (which replaced the cross-surface `.sessions.json` in WI-3);
here we only turn those ids into titles and replayable bubbles by reading their transcripts.

Surface-agnostic: a Context carries its cwd, which is all we need to find its transcripts.
"""

import re
import json
import logging
from datetime import datetime, timezone

from ..runtime.config import CLAUDE_PROJECTS_DIR
from .context import Context
from .spine import SystemSpine, get_spine

log = logging.getLogger("superme-agent")

# Transcript record types that carry conversational text.
_ROLE = {"user": "you", "assistant": "superme"}

# Local-command echo artifacts the CLI injects (compact continuation, /command wrappers).
# These aren't real conversation — skip them when replaying a session in the UI. Also the
# internal headless-run prompt (orchestrator plumbing, not something the owner typed).
_NOISE_PREFIXES = (
    "This session is being continued from a previous conversation",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "Run the superme-dev:plan skill for work-item",
)


def _is_noise(record: dict, text: str) -> bool:
    """True if a transcript record is a compact summary / local-command echo, not chat."""
    if record.get("isCompactSummary") or record.get("isMeta"):
        return True
    return text.lstrip().startswith(_NOISE_PREFIXES)


# Kernel-injected birth blocks prepended to a session's FIRST user prompt (`<block>\n\n---\n\n<real
# prompt>`): the work-item orient block and the diagnosis subject-run trace. On replay we show only
# the real prompt — the block is plumbing, and dropping the whole record would hide what the user
# actually typed on an interactive birth turn.
_BIRTH_BLOCK_HEADERS = (
    "### Work-item orientation",
    "### Subject activity-run trace",
)


def _strip_birth_block(text: str) -> str:
    """Cut a kernel-injected birth block off the front of a prompt, keeping the real message after
    the `---` separator (empty when the turn was pure plumbing — e.g. a headless plan trigger,
    which then falls to the noise filter)."""
    if not text.lstrip().startswith(_BIRTH_BLOCK_HEADERS):
        return text
    parts = text.split("\n\n---\n\n", 1)
    return parts[1] if len(parts) == 2 else ""


def _short_id(sid: str) -> str:
    """A short, human-glanceable id for a session (first UUID segment / first 8 chars)."""
    return (sid or "").split("-")[0][:8] or "session"


def short_item_id(item_id: str | None) -> str:
    """The distinguishing tail of a work-item id — `...-536a40` → `536a40`, or the whole opaque token
    when it has no hyphens — so same-titled items stay distinguishable in a session title."""
    return (item_id or "").split("-")[-1] or (item_id or "")


def _preset_title(kind: str, item_id: str | None, subject_run_id, sid: str) -> str:
    """The computed DEFAULT title for a session when the owner hasn't set an override — a consistent,
    identity-bearing preset by kind (session-kinds-diagnose). work_item falls back to the item's short
    id here; the router upgrades it to the item's TITLE + short id (which it resolves). Always editable:
    an owner override wins over this in list()/read()."""
    if kind == "work_item":
        return f"Work-item · {short_item_id(item_id) if item_id else _short_id(sid)}"
    if kind == "diagnosis":
        return f"Diagnosis · run #{subject_run_id}" if subject_run_id else "Diagnosis session"
    if kind == "onboarding":
        return f"Onboarding · {_short_id(sid)}"
    return f"General · {_short_id(sid)}"


def _encode_cwd(cwd) -> str:
    """The CLI's projects-folder name for a cwd: every non-alphanumeric char -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def _blocks_text(content) -> str:
    """Join the human-readable text blocks of a message, skipping thinking/tools."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") in (None, "text") and isinstance(b.get("text"), str):
            parts.append(b["text"])
        # thinking / tool_use / tool_result blocks are intentionally dropped.
    return "\n".join(p for p in parts if p.strip())


class SessionStore:
    """Lists a Context's sessions (via the shared index) and replays their transcripts."""

    def __init__(self, spine: SystemSpine | None = None):
        self._spine = spine or get_spine()
        self._projects = CLAUDE_PROJECTS_DIR

    def record(self, ctx: Context, session_id: str | None) -> None:
        """Claim a web-created session for this context's workspace (idempotent). The
        session inherits the Context's mode (core|dev) so the picker can scope by it."""
        if not session_id:
            return
        self._spine.record_session(session_id, ctx.cwd, surface="web", mode=ctx.mode,
                                   repo_id=ctx.id)

    def delete(self, ctx: Context, session_id: str, *, cause: str = "deleted") -> bool:
        """The single session deletion (session-deletion-trace-model). Hard-deletes the session's
        resumable material — its spine row + its transcript JSONL on disk — so it leaves the picker
        and can't be reworked (deletion means "won't rework this conversation"). Its RUNS +
        run_events + run_artifacts (the activity + token trace) are PRESERVED and stamped
        `session_fate=cause` — 'deleted' (owner drop) · 'retired' (natural workflow end). Dev-activity
        events and knowledge are untouched. Returns True if the transcript file was removed.

        This is the ONLY deletion — the former forget (index-only) / purge (index+transcript) two-tier
        is gone; every delete is a full hard delete now."""
        if not session_id:
            return False
        self._spine.delete_session_record(session_id, cause=cause)
        return self.discard_transcript(ctx, session_id)

    def discard_transcript(self, ctx: Context, session_id: str) -> bool:
        """Delete a session's transcript JSONL from disk WITHOUT touching the index — for
        session-disposable runs (distill, …) whose session was never recorded, so there is no
        index entry to forget. Completes their disposability: the throwaway conversation leaves
        no resumable trace on disk (the run is still logged in the spine's `run` table)."""
        if not session_id:
            return False
        path = self._transcript(ctx, session_id)
        try:
            if path.exists():
                path.unlink()
                return True
        except OSError as e:
            log.warning("could not delete transcript %s: %s", path.name, e)
        return False

    # --- transcript access ------------------------------------------------------
    def _transcript(self, ctx: Context, session_id: str):
        return self._projects / _encode_cwd(ctx.cwd) / f"{session_id}.jsonl"

    def _scan(self, ctx: Context, session_id: str) -> dict | None:
        """Parse a transcript into {title, messages, updated_at, message_count}.

        Returns None if the transcript file is missing (e.g. cleared by the owner).
        """
        path = self._transcript(ctx, session_id)
        if not path.exists():
            return None
        title = None
        first_user = None
        messages: list[dict] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("type")
            if t == "ai-title" and d.get("aiTitle"):
                title = d["aiTitle"]
            elif t in _ROLE:
                text = _blocks_text((d.get("message") or {}).get("content"))
                if t == "user":
                    text = _strip_birth_block(text)
                if not text.strip() or _is_noise(d, text):
                    continue
                role = _ROLE[t]
                messages.append({"role": role, "text": text})
                if role == "you" and first_user is None:
                    first_user = text
        if title is None:
            title = (first_user or "Untitled").strip().splitlines()[0][:60]
        updated_at = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        return {
            "title": title,
            "messages": messages,
            "updated_at": updated_at,
            "message_count": len(messages),
        }

    def transcript_mtime(self, ctx: Context, session_id: str) -> float | None:
        """The session transcript's last-modified epoch seconds, or None if missing — the idle
        sweep's activity proxy (a session is idle when no message has landed for a while)."""
        path = self._transcript(ctx, session_id)
        try:
            return path.stat().st_mtime if path.exists() else None
        except OSError:
            return None

    def transcript_messages(self, ctx: Context, session_id: str) -> list[dict]:
        """The session's ordered chat messages (oldest first), tools/thinking/noise dropped — the
        raw material the capture sweep reads. Returns [] if the transcript is missing. Same parse
        as the picker (`_scan`), so the watermark counts the same units the sweep consumes."""
        scan = self._scan(ctx, session_id)
        return scan["messages"] if scan else []

    def list(self, ctx: Context, mode: str | None = None) -> list[dict]:
        """Sessions that ran in this workspace (any surface), newest first. When `mode`
        ("core"|"dev") is given, only that mode's threads — the chat picker scopes by mode."""
        out = []
        for rec in self._spine.sessions_for_cwd(ctx.cwd):
            sid = rec["id"]
            sess_mode = rec.get("mode", "core")
            if mode and sess_mode != mode:
                continue
            scan = self._scan(ctx, sid)
            if scan is None:
                continue
            item_id = rec.get("item_id") or None
            eff_kind = rec.get("kind") or ("work_item" if item_id else "general")
            override = rec.get("title")
            out.append({
                "id": sid,
                # Owner override wins; else a consistent identity preset by kind. (The transcript-
                # derived scan title is no longer the default — owner picked preset-by-kind.)
                "title": override or _preset_title(eff_kind, item_id, rec.get("subject_run_id"), sid),
                # Internal (dropped by the response_model): lets the router upgrade a NON-overridden
                # work-item title to the item's resolved TITLE without clobbering an owner rename.
                "has_override": bool(override),
                "surface": rec.get("surface", "web"),
                "mode": sess_mode,
                "updated_at": scan["updated_at"],
                "message_count": scan["message_count"],
                # The durable work-item stamp (work-item-session-recognition-prd): non-null ⇒ this is
                # a WORK-ITEM session. The title is resolved by the router (it has the dev service).
                "item_id": item_id,
                # The session's durable KIND (session-kinds-diagnose): 'diagnosis' | 'onboarding' |
                # 'work_item' | 'general' (NULL ⇒ general). Lets the chat picker label its category.
                "kind": rec.get("kind") or None,
            })
        out.sort(key=lambda s: s["updated_at"], reverse=True)
        return out

    def rename(self, ctx: Context, session_id: str, title: str | None) -> bool:
        """Set (or clear) a session's owner TITLE override, if it belongs to this workspace. A blank
        title reverts to the transcript-derived title. Returns True if a row was updated."""
        if session_id not in {r["id"] for r in self._spine.sessions_for_cwd(ctx.cwd)}:
            return False
        return self._spine.set_session_title(session_id, title)

    def read(self, ctx: Context, session_id: str, limit: int = 10) -> dict | None:
        """One session's title + its most recent `limit` bubbles (older ones skipped),
        or None if the session isn't in this workspace. The agent still resumes with
        full server-side context — we just don't replay the whole transcript in the UI.
        """
        recs = {r["id"]: r for r in self._spine.sessions_for_cwd(ctx.cwd)}
        if session_id not in recs:
            return None
        scan = self._scan(ctx, session_id)
        if scan is None:
            return None
        total = len(scan["messages"])
        messages = scan["messages"][-limit:] if limit and limit > 0 else scan["messages"]
        r = recs[session_id]
        item_id = r.get("item_id") or None
        eff_kind = r.get("kind") or ("work_item" if item_id else "general")
        return {
            "id": session_id,
            # Owner override wins; else the identity preset by kind (matches list()).
            "title": r.get("title") or _preset_title(eff_kind, item_id, r.get("subject_run_id"), session_id),
            "updated_at": scan["updated_at"],
            "messages": messages,
            "total": total,
            "truncated": total > len(messages),
        }
