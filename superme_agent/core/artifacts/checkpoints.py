"""Checkpoints: what a session banks before it is compacted, and what the next one reads."""

import hashlib
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .text import _atomic_write
from .ledger import evidence_entries

# convergence guard

# Normalize the failure signature so incidental variation — case, punctuation, timestamps, ids —
# cannot hide a no-progress cycle.
_SIG_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{7,40}\b")
_SIG_NUM = re.compile(r"\d{2,}")
_SIG_JUNK = re.compile(r"[^a-z0-9 ]+")


def _normalize_signature(s: str) -> str:
    s = _SIG_HEX.sub("", (s or "").lower())
    s = _SIG_NUM.sub("", s)
    s = _SIG_JUNK.sub(" ", s)
    return " ".join(s.split())


def convergence_fingerprint(item_dir: Path, *, extra: list[str] | None = None) -> str:
    """The cycle's failure fingerprint: sha1 over the sorted (check, normalized
    result) pairs. Empty when nothing is failing.

    `extra` carries failure signatures that are not ledger checks — a wall the loop keeps hitting
    should exit `not_converging`."""
    latest: dict[str, dict] = {}
    for e in evidence_entries(item_dir):
        latest[e["check"]] = e
    failing = sorted((c, _normalize_signature(str(e.get("result") or "")))
                     for c, e in latest.items() if not e.get("passed"))
    failing += sorted(("lens", _normalize_signature(t)) for t in (extra or []) if t)
    if not failing:
        return ""
    return hashlib.sha1("\n".join(f"{c}|{sig}" for c, sig in failing).encode()).hexdigest()[:12]


# --------------------------------------------------------------------------- checkpoints

def write_checkpoint(item_dir: Path, repo_dir: Path | None, *, working_on: str, decisions: str,
                     remaining: str, notes: str = "", role: str | None = None) -> str:
    """Bank one continuity checkpoint. APPEND-ONLY and atomic; the filename IS the order.
    Reference artifacts BY PATH.

    `role` is the SESSION ROLE that banked it: unstamped, a compacted intake thread gets the build
    thread's checkpoint and reads it as its own."""
    if not (working_on.strip() and remaining.strip()):
        raise ValueError("a checkpoint needs at least working_on and remaining")
    cdir = Path(item_dir) / "checkpoints"
    cdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = cdir / f"{ts}.md"
    n = 1
    while path.exists():            # same-second collision → suffix, never overwrite
        path = cdir / f"{ts}-{n}.md"
        n += 1
    git_line = "(no git state)"
    if repo_dir and Path(repo_dir).is_dir():
        try:
            r = subprocess.run(["git", "log", "-1", "--format=%h %s", "HEAD"], cwd=repo_dir,
                               capture_output=True, text=True, timeout=10)
            b = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir,
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                git_line = f"{b.stdout.strip()} @ {r.stdout.strip()}"
        except (OSError, subprocess.SubprocessError):
            pass
    text = (f"---\ncheckpoint: {ts}\ngit: {git_line}\nreader: agent\n"
            + (f"role: {role}\n" if role else "")
            + f"---\n"
            f"## Working on\n{working_on.strip()}\n\n"
            f"## Decisions\n{(decisions or '').strip() or '—'}\n\n"
            f"## Remaining\n{remaining.strip()}\n\n"
            f"## Notes\n{(notes or '').strip() or '—'}\n")
    _atomic_write(path, text)
    return str(path)


def checkpoint_feed(item_dir: Path, *, limit: int = 30) -> list[dict]:
    """The drilldown's continuity feed: newest-first checkpoint stubs. Full text stays
    behind the path, one click deeper."""
    cdir = Path(item_dir) / "checkpoints"
    if not cdir.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(cdir.glob("*.md"), key=lambda p: p.stem, reverse=True)[:limit]:
        text = p.read_text()
        git = None
        m = re.search(r"(?m)^git: (.+)$", text)
        if m:
            git = m.group(1).strip()
        body = re.sub(r"(?s)\A---\n.*?\n---\n", "", text)
        headline = next((ln.strip() for ln in body.splitlines()
                         if ln.strip() and not ln.startswith("#")), "")
        out.append({"ts": p.stem, "path": str(p), "headline": headline[:200], "git": git})
    return out


def latest_checkpoint(item_dir: Path, *, char_cap: int = 6000,
                      role: str | None = None) -> dict | None:
    """The newest checkpoint by filename, char-capped. None when none exist.

    `role=None` answers "what is this ITEM's latest state"; a named role answers "what was THIS
    THREAD doing", and also takes unstamped checkpoints, which are role-agnostic."""
    cdir = Path(item_dir) / "checkpoints"
    if not cdir.is_dir():
        return None
    # Sort by STEM: with `.md` attached, `-` < `.` would put a collision file `<ts>-1` before
    # `<ts>`.
    files = sorted(cdir.glob("*.md"), key=lambda p: p.stem)
    if not files:
        return None
    if role:
        # Two other stamps match: an UNSTAMPED checkpoint, and a legacy `intake` one. Both widen,
        # never narrow.
        from ..vocab.kind_profiles import INTAKE_PHASES, LEGACY_INTAKE_SLOT
        wants = [f"\nrole: {role}\n"]
        if role in INTAKE_PHASES:
            wants.append(f"\nrole: {LEGACY_INTAKE_SLOT}\n")
        files = [p for p in files
                 if (t := p.read_text())
                 and (any(w in t for w in wants) or "\nrole: " not in t)]
        if not files:
            return None
    text = files[-1].read_text()
    return {"path": str(files[-1]), "text": text[:char_cap],
            "truncated": len(text) > char_cap}


# session memory: the non-work-item thread's checkpoint

# A general session has no item folder, so a compaction loses the conversation outright. ONE file
# per session, overwritten.

def session_memory_path(root_dir: Path, session_id: str) -> Path:
    """Where this session's memory lives. `root_dir` is the MODE root (`…/dev` or `…/core`)."""
    return Path(root_dir) / "session-memory" / f"{session_id}.md"


def read_session_memory(root_dir: Path, session_id: str, *,
                        char_cap: int = 6000) -> dict | None:
    """This session's banked memory, char-capped. None when it has never banked one."""
    path = session_memory_path(root_dir, session_id)
    if not path.is_file():
        return None
    text = path.read_text()
    return {"path": str(path), "text": text[:char_cap], "truncated": len(text) > char_cap}
