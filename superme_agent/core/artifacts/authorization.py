"""Authorization requests: the scopes an agent may ask for, and the owner's answer."""

import re
from datetime import datetime
from pathlib import Path

from .text import atomic_write, _one_line

# the authorization ledger

# A work-item may PROPOSE a contract change, but changes that DEFINE intent are owner-reserved.
# Build requests and DEFERS.
AUTH_SCOPES = (
    # DELEGABLE by default — "sync the contract to shipped reality":
    "doc-sync",           # reconcile a descriptive doc (architecture/capabilities/resources) to merged reality
    "rename-to-shipped",  # rename doc references to match already-shipped code
    "roadmap-mark-done",  # mark a roadmap item done that IS done
    # RESERVED by default — "define or alter intent" (the floor holds; the deputy escalates):
    "prd-identity",       # project-prd identity / goals / deliverables
    "roadmap-scope",      # add / remove / re-scope a roadmap deliverable
    "new-decision",       # a decision that sets direction (incl. the public-contract naming choice)
    "doc-delete",         # delete / retire a doc
)

# The DEFAULT sync-to-reality set. The live delegated set is a per-system setting; this only
# informs the review brief.
DELEGABLE_SCOPES = ("doc-sync", "rename-to-shipped", "roadmap-mark-done")

_AUTHORIZATION_FILE = "authorizations.md"
_AUTHORIZATION_HEAD = re.compile(r"^### (?P<id>\S+) — (?P<what>.*)$")


# Which STAGED OPS make a declared scope a lie: the split is declared by the agent it constrains.
_INTENT_SECTIONS = {
    "project-prd": ("deliverables", "success signals", "non-goals", "users", "problem"),
    "roadmap":     ("wave", "deliverable"),
}


def intent_ops(ops: list) -> list[str]:
    """The staged ops that DEFINE intent rather than record what shipped. Empty when nothing
    intent-defining is staged."""
    out: list[str] = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        doc = str(op.get("doc") or "").strip().lower()
        section = str(op.get("section") or "").strip().lower()
        for marker in _INTENT_SECTIONS.get(doc, ()):
            if marker in section:
                out.append(f"{doc} § {op.get('section')}")
                break
    return out


def scope_mismatch(scope: str, ops: list) -> str:
    """'' when the declared scope matches the staged ops, else the refusal message. Only
    DELEGABLE scopes are checked — a reserved one already goes to the owner."""
    if scope not in DELEGABLE_SCOPES:
        return ""
    hits = intent_ops(ops)
    if not hits:
        return ""
    return (f"scope {scope!r} is the delegable 'sync the docs to shipped reality' kind, but the "
            f"staged ops change what the project IS: {', '.join(sorted(set(hits))[:3])}. That is "
            f"an intent change — use `roadmap-scope` (add/remove/re-scope a deliverable), "
            f"`prd-identity` (identity/goals) or `new-decision`. Those are owner-reserved: they "
            f"reach the owner instead of a delegated deputy, which is the point.")


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
