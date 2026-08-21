"""SessionStore — list and replay past conversations for a Context.

The SDK already writes a transcript JSONL per session, keyed by cwd. That file is the source of
truth for history; we never keep a parallel message log.

The projects folder is shared with the owner's own Claude Code sessions, so WHICH sessions are
SuperMe's comes from the spine. Here we only turn those ids into titles and bubbles.
"""

import re
import json
import logging
from datetime import datetime, timezone

from ..paths import CLAUDE_PROJECTS_DIR
from .context import Context
from .spine import SystemSpine, get_spine
from .kind_profiles import is_conversation

log = logging.getLogger("superme-agent")

# Transcript record types that carry conversational text.
_ROLE = {"user": "you", "assistant": "superme"}


def _turn_count(messages: list[dict]) -> int:
    """How many MESSAGES a transcript holds, counted the way the owner reads them: one turn is
    one message, however many bubbles it renders as.

    An agent turn emits a text block per stretch of work, each its own record, so counting records
    read a four-bubble reply as "4 msgs". Consecutive records from one speaker are one message."""
    turns = 0
    prev: str | None = None
    for m in messages:
        if m["role"] != prev:
            turns += 1
            prev = m["role"]
    return turns

# CLI echo artifacts and the internal background-run trigger — plumbing, not conversation.
_NOISE_PREFIXES = (
    "This session is being continued from a previous conversation",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    # ONE PER INTAKE SKILL — every skill `_background_intake_run` can fire. Miss one and the
    # kernel's order to itself opens that session as the OWNER's first message.
    "Run superme-dev:plan for work-item",
    "Run superme-dev:triage for work-item",
    "Run superme-dev:investigate for work-item",
    "Run superme-dev:review for work-item",
    "Run superme-dev:close for work-item",
    "Run superme-dev:itemize for work-item",
    # The pre-compaction handoff trigger: replaying it shows a machine asking itself for a
    # checkpoint mid-conversation.
    "This session is about to be compacted",
)


def _is_noise(record: dict, text: str) -> bool:
    """True if a transcript record is a compact summary / local-command echo, not chat."""
    if record.get("isCompactSummary") or record.get("isMeta"):
        return True
    return text.lstrip().startswith(_NOISE_PREFIXES)


# Prepended to a session's FIRST user prompt. Replay shows only the real prompt after them;
# dropping the whole record would hide what the owner typed.
_BIRTH_BLOCK_HEADERS = (
    "### Work-item orientation",
    "### Subject activity-run trace",
)


def _strip_birth_block(text: str) -> str:
    """Cut a kernel-injected birth block off a prompt, keeping the real message after the `---`.
    Empty when the turn was pure plumbing, which then falls to the noise filter."""
    if not text.lstrip().startswith(_BIRTH_BLOCK_HEADERS):
        return text
    parts = text.split("\n\n---\n\n", 1)
    return parts[1] if len(parts) == 2 else ""


def _short_id(sid: str) -> str:
    """A short, human-glanceable id for a session (first UUID segment / first 8 chars)."""
    return (sid or "").split("-")[0][:8] or "session"


def short_item_id(item_id: str | None) -> str:
    """The distinguishing tail of a work-item id, so same-titled items stay apart in a title."""
    return (item_id or "").split("-")[-1] or (item_id or "")


def _preset_title(kind: str, item_id: str | None, subject_run_id, sid: str) -> str:
    """The DEFAULT title when the owner has set no override — a consistent, identity-bearing
    preset by kind. The router upgrades a work-item one to the item's real title."""
    if kind in ("intake", "build", "vet"):   # role-stamped item sessions (build-vet-loop §1.3)
        return f"Work-item · {short_item_id(item_id) if item_id else _short_id(sid)} · {kind}"
    if kind == "work_item":                  # legacy pre-roles item sessions (kind derived)
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
        """Claim a web-created session for this workspace (idempotent). It inherits the Context's
        mode, so the picker can scope by it."""
        if not session_id:
            return
        self._spine.record_session(session_id, ctx.cwd, surface="web", mode=ctx.mode,
                                   repo_id=ctx.id)

    def delete(self, ctx: Context, session_id: str, *, cause: str = "deleted") -> bool:
        """The single session deletion. Hard-deletes the resumable material — spine row and
        transcript — so it leaves the picker. Runs, events and artifacts are PRESERVED and stamped
        `session_fate=cause`. Returns True if the transcript file went.

        Located by the SESSION'S recorded cwd, not the caller's: role sessions live under their
        worktree's projects folder and a repo-cwd caller would otherwise miss them."""
        if not session_id:
            return False
        rec = self._spine.get_session(session_id)
        cwd = (rec or {}).get("cwd") or ctx.cwd
        self._spine.delete_session_record(session_id, cause=cause)
        return self.discard_transcript(ctx, session_id, cwd=cwd)

    def delete_channel(self, ctx: Context, session_id: str, *, cause: str = "deleted") -> int:
        """Delete a whole CHANNEL — every thread the picker showed as one row. Returns how many.

        A work-item is one conversation with several threads underneath, so dropping the row drops all
        of them; leaving one behind reads as a delete that did not work. Scoped to CONVERSATIONS: the
        headless build/vet threads go with the item's own disposal, not with this."""
        rec = self._spine.get_session(session_id) or {}
        item_id = rec.get("item_id") or None
        targets = [session_id]
        if item_id:
            targets = [str(r["id"]) for r in self._spine.sessions_for_repo(ctx.id)
                       if r.get("item_id") == item_id and is_conversation(r.get("kind"))]
            if session_id not in targets:
                targets.append(session_id)
        for sid in targets:
            self.delete(ctx, sid, cause=cause)
        return len(targets)

    def discard_transcript(self, ctx: Context, session_id: str, cwd=None) -> bool:
        """Delete a transcript from disk WITHOUT touching the index — for disposable runs whose
        session was never recorded. `cwd` overrides the projects-folder key for worktree-born sessions."""
        if not session_id:
            return False
        path = self._transcript(ctx, session_id, cwd=cwd)
        try:
            if path.exists():
                path.unlink()
                return True
        except OSError as e:
            log.warning("could not delete transcript %s: %s", path.name, e)
        return False

    # --- transcript access ------------------------------------------------------
    def _transcript(self, ctx: Context, session_id: str, cwd=None):
        """The session's transcript path, keyed by the SESSION'S recorded cwd when the spine knows
        it (worktree-born role sessions would otherwise be missed), else the caller's."""
        if cwd is None:
            cwd = (self._spine.get_session(session_id) or {}).get("cwd")
        return self._projects / _encode_cwd(cwd or ctx.cwd) / f"{session_id}.jsonl"

    def has_transcript(self, ctx: Context, session_id: str) -> bool:
        """Whether the session's resume material still exists. The CLI expires transcripts on its
        own clock, so a row can outlive its JSONL. Same path resolution as `_scan`."""
        return self._transcript(ctx, session_id).exists()

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
            "message_count": _turn_count(messages),
        }

    def transcript_mtime(self, ctx: Context, session_id: str) -> float | None:
        """The transcript's last-modified epoch seconds, or None — the idle sweep's activity proxy."""
        path = self._transcript(ctx, session_id)
        try:
            return path.stat().st_mtime if path.exists() else None
        except OSError:
            return None

    def transcript_messages(self, ctx: Context, session_id: str) -> list[dict]:
        """The session's ordered chat messages, tools and noise dropped — what the capture sweep
        reads. Same parse as `_scan`, so the watermark counts the units the sweep consumes."""
        scan = self._scan(ctx, session_id)
        return scan["messages"] if scan else []

    def _workspace_rows(self, ctx: Context) -> list[dict]:
        """This workspace's resumable rows — the one membership answer `list`, `read` and `rename`
        all use, so a thread cannot be listable but unreadable.

        Scoped by REPO, not cwd: a phase run swaps its cwd to the item's worktree, so a cwd match
        silently dropped every work-item thread from the picker."""
        return self._spine.sessions_for_repo(ctx.id, resumable_only=True)

    def list(self, ctx: Context, mode: str | None = None) -> list[dict]:
        """Sessions that ran in this workspace (any surface), newest first. When `mode`
        ("core"|"dev") is given, only that mode's threads — the chat picker scopes by mode."""
        out = []
        for rec in self._workspace_rows(ctx):
            sid = rec["id"]
            sess_mode = rec.get("mode", "core")
            if mode and sess_mode != mode:
                continue
            # Build and vet are headless working memory the owner cannot open. `session_count` has
            # always excluded them; this reader must too, or the tile and the list disagree.
            if not is_conversation(rec.get("kind")):
                continue
            scan = self._scan(ctx, sid)
            if scan is None:
                continue
            item_id = rec.get("item_id") or None
            eff_kind = rec.get("kind") or ("work_item" if item_id else "general")
            override = rec.get("title")
            out.append({
                "id": sid,
                # Owner override wins, else the preset by kind.
                "title": override or _preset_title(eff_kind, item_id, rec.get("subject_run_id"), sid),
                # Internal: lets the router upgrade a non-overridden title without clobbering a rename.
                "has_override": bool(override),
                "surface": rec.get("surface", "web"),
                "mode": sess_mode,
                "updated_at": scan["updated_at"],
                "message_count": scan["message_count"],
                # Non-null ⇒ a work-item session. The router resolves its title.
                "item_id": item_id,
                # The durable kind; NULL reads as general. Labels the picker's category.
                "kind": rec.get("kind") or None,
            })
        out.sort(key=lambda s: s["updated_at"], reverse=True)
        return out

    def rename(self, ctx: Context, session_id: str, title: str | None) -> bool:
        """Set (or clear) a session's owner TITLE override, if it belongs to this workspace. A blank
        title reverts to the transcript-derived title. Returns True if a row was updated."""
        if session_id not in {r["id"] for r in self._workspace_rows(ctx)}:
            return False
        return self._spine.set_session_title(session_id, title)

    def read(self, ctx: Context, session_id: str, limit: int = 10) -> dict | None:
        """One session's title + its most recent `limit` bubbles (older ones skipped),
        or None if the session isn't in this workspace. The agent still resumes with
        full server-side context — we just don't replay the whole transcript in the UI.
        """
        recs = {r["id"]: r for r in self._workspace_rows(ctx)}
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
