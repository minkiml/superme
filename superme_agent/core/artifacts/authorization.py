"""Authorization requests: the intent changes an agent may not settle alone, and the owner's answer."""

import re
from datetime import datetime
from pathlib import Path

from .text import atomic_write, _one_line

# the authorization ledger

# Reconciling a doc to what shipped is not a decision and never comes through here.
AUTH_SCOPES = (
    "prd-identity",       # project-prd identity / goals / deliverables
    "roadmap-scope",      # add / remove / re-scope a roadmap deliverable
    "new-decision",       # a decision that sets direction (incl. the public-contract naming choice)
    "doc-delete",         # delete / retire a doc
)

_AUTHORIZATION_FILE = "authorizations.md"
_AUTHORIZATION_HEAD = re.compile(r"^### (?P<id>\S+) — (?P<what>.*)$")


def record_authorization(item_dir: Path, *, what: str, why: str, doc: str, scope: str,
                         check: str = "", phase: str = "", cycle: int | None = None) -> dict:
    """Append one PENDING authorization request; the id is its timestamp. Append-only:
    a later decision rewrites only `status` and `by`."""
    def _one_line(s: str) -> str:
        return " ".join((s or "").split())
    what, why, doc, scope, check = (_one_line(x) for x in (what, why, doc, scope, check))
    if not (what and why and scope):
        raise ValueError("an authorization request needs what, why, and a scope")
    if scope not in AUTH_SCOPES:
        raise ValueError(f"unknown authorization scope {scope!r} — one of: {', '.join(AUTH_SCOPES)}")
    path = Path(item_dir) / "artifacts" / _AUTHORIZATION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "" if path.exists() else (
        "# Authorizations\n\nContract changes a work-item cannot self-authorize — each awaiting a "
        "grant or deny at review.\n")
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry = (f"\n### {ts} — {what}\n"
             f"- why: {why}\n"
             f"- doc: {doc}\n"
             f"- scope: {scope}\n"
             f"- check: {check}\n"
             f"- phase: {phase or 'unknown'}\n"
             f"- cycle: {cycle if cycle is not None else ''}\n"
             f"- status: pending\n"
             f"- by: \n")
    atomic_write(path, (path.read_text(encoding="utf-8") if path.exists() else head) + entry)
    return {"id": ts, "what": what, "scope": scope, "check": check, "status": "pending"}


def authorization_entries(item_dir: Path) -> list[dict]:
    """Parse the ledger: [{id, what, why, doc, scope, check, phase, cycle, status, by}] in order."""
    path = Path(item_dir) / "artifacts" / _AUTHORIZATION_FILE
    if not path.exists():
        return []
    entries: list[dict] = []
    cur: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _AUTHORIZATION_HEAD.match(line)
        if m:
            cur = {"id": m.group("id"), "what": m.group("what"), "status": "pending"}
            entries.append(cur)
        elif cur is not None:
            kv = re.match(r"^- (why|doc|scope|check|phase|cycle|status|by): (.*)$", line)
            if kv:
                cur[kv.group(1)] = kv.group(2).strip()
    return entries


def pending_authorizations(item_dir: Path) -> list[dict]:
    """Requests still owed a grant/deny — what the gate brief shows and the close gate refuses on."""
    return [a for a in authorization_entries(item_dir) if a.get("status") == "pending"]


def resolve_authorization(item_dir: Path, auth_id: str, *, decision: str, by: str) -> dict | None:
    """Grant or deny ONE pending request. Returns the updated entry, or None if the id
    is unknown or no longer pending."""
    if decision not in ("granted", "denied"):
        raise ValueError("decision must be granted or denied")
    path = Path(item_dir) / "artifacts" / _AUTHORIZATION_FILE
    if not path.exists():
        return None
    out: list[str] = []
    in_block = False
    changed = False
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        m = _AUTHORIZATION_HEAD.match(line.rstrip("\n"))
        if m:
            in_block = (m.group("id") == auth_id)
        elif in_block and line.startswith("- status: pending"):
            line = f"- status: {decision}\n"
            changed = True
        elif in_block and line.startswith("- by:"):
            line = f"- by: {by}\n"
        out.append(line)
    if not changed:
        return None
    atomic_write(path, "".join(out))
    return next((a for a in authorization_entries(item_dir) if a["id"] == auth_id), None)
