"""SessionStore — list and replay past conversations for a Context.

The Claude Agent SDK already writes a full transcript JSONL per session (the same file
`resume` reads from), keyed by cwd under ~/.claude/projects/<encoded-cwd>/<id>.jsonl.
That file is the source of truth for history — we never keep a parallel message log.

But that projects folder is shared: SuperMe's sessions and the owner's own Claude Code
sessions land there together (same cwd). So *which* sessions are SuperMe's comes from the
shared SessionIndex (the cross-surface `.sessions.json`); here we only turn those ids into
titles and replayable bubbles by reading their transcripts.

Surface-agnostic: a Context carries its cwd, which is all we need to find its transcripts.
"""

import re
import json
import logging
from datetime import datetime, timezone

from ..runtime.config import CLAUDE_PROJECTS_DIR
from .context import Context
from .session_index import SessionIndex

log = logging.getLogger("superme-agent")

# Transcript record types that carry conversational text.
_ROLE = {"user": "you", "assistant": "superme"}

# Local-command echo artifacts the CLI injects (compact continuation, /command wrappers).
# These aren't real conversation — skip them when replaying a session in the UI.
_NOISE_PREFIXES = (
    "This session is being continued from a previous conversation",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-caveat>",
)


def _is_noise(record: dict, text: str) -> bool:
    """True if a transcript record is a compact summary / local-command echo, not chat."""
    if record.get("isCompactSummary") or record.get("isMeta"):
        return True
    return text.lstrip().startswith(_NOISE_PREFIXES)


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

    def __init__(self, index: SessionIndex | None = None):
        self._index = index or SessionIndex()
        self._projects = CLAUDE_PROJECTS_DIR

    def record(self, ctx: Context, session_id: str | None) -> None:
        """Claim a web-created session for this context's workspace (idempotent)."""
        self._index.record(session_id, ctx.cwd, surface="web")

    def forget(self, ctx: Context, session_id: str) -> None:
        """Drop a session from the shared index (the transcript file is left untouched)."""
        self._index.forget(session_id)

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

    def list(self, ctx: Context) -> list[dict]:
        """Every session that ran in this workspace (any surface), newest first."""
        out = []
        for sid in self._index.for_cwd(ctx.cwd):
            scan = self._scan(ctx, sid)
            if scan is None:
                continue
            rec = self._index.get(sid) or {}
            out.append({
                "id": sid,
                "title": scan["title"],
                "surface": rec.get("surface", "web"),
                "updated_at": scan["updated_at"],
                "message_count": scan["message_count"],
            })
        out.sort(key=lambda s: s["updated_at"], reverse=True)
        return out

    def read(self, ctx: Context, session_id: str, limit: int = 10) -> dict | None:
        """One session's title + its most recent `limit` bubbles (older ones skipped),
        or None if the session isn't in this workspace. The agent still resumes with
        full server-side context — we just don't replay the whole transcript in the UI.
        """
        if session_id not in self._index.for_cwd(ctx.cwd):
            return None
        scan = self._scan(ctx, session_id)
        if scan is None:
            return None
        total = len(scan["messages"])
        messages = scan["messages"][-limit:] if limit and limit > 0 else scan["messages"]
        return {
            "id": session_id,
            "title": scan["title"],
            "updated_at": scan["updated_at"],
            "messages": messages,
            "total": total,
            "truncated": total > len(messages),
        }
