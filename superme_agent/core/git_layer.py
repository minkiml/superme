"""Git layer — per-item worktree lifecycle and merge machinery.

One work-item ⇄ one branch `item/<id>-<slug>` ⇄ one worktree under `~/.superme/worktrees/`.
Only the gated review decision merges, always behind a `refs/backup/` guardrail. Fail-loud.
"""

import json
import logging
import os
import re
import signal
import subprocess
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("superme-agent")


class GitError(RuntimeError):
    """A git command failed (command + stderr in the message)."""


class GitBusy(RuntimeError):
    """Another git operation is in flight for this repo (per-repo op lock)."""


# --- plumbing ---------------------------------------------------------------------

def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run one git command in `cwd`. check=True raises GitError on nonzero exit."""
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8")
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def _out(cwd: Path, *args: str) -> str:
    return _git(cwd, *args).stdout.strip()


def diff_numstat(worktree: Path, base: str) -> dict:
    """`git diff --numstat base...HEAD` → {files, insertions, deletions, by_file}. Best-effort:
    a bad base or non-repo returns zeros rather than raising."""
    proc = _git(worktree, "diff", "--numstat", f"{base}...HEAD", check=False)
    return _parse_numstat(proc.stdout if proc.returncode == 0 else "")


# Temporary instrumentation carries a tag so removal is a grep, not a memory.
DEBUG_TAG = re.compile(r"\[DEBUG-[0-9a-fA-F]{4,}\]")


def debug_tags(worktree: Path, base: str) -> list[dict]:
    """Tagged debug instrumentation still in the branch's ADDED lines. A tag already on
    trunk is not this item's."""
    proc = _git(worktree, "diff", f"{base}...HEAD", check=False)
    if proc.returncode != 0:
        return []
    hits: list[dict] = []
    seen: set[tuple[str, str]] = set()
    path = ""
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for tag in DEBUG_TAG.findall(line):
            key = (path, tag)
            if key not in seen:
                seen.add(key)
                hits.append({"path": path, "tag": tag, "line": line[1:].strip()[:120]})
    return hits


def _parse_numstat(text: str) -> dict:
    """`git --numstat` → {files, insertions, deletions, by_file}. Binary '-' counts
    read as 0, never dropped."""
    by_file, ins, dels = [], 0, 0
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        p, m, path = parts
        plus = int(p) if p.isdigit() else 0
        minus = int(m) if m.isdigit() else 0
        ins += plus
        dels += minus
        by_file.append({"path": path, "plus": plus, "minus": minus})
    return {"files": len(by_file), "insertions": ins, "deletions": dels, "by_file": by_file}


def is_git_repo(repo_dir: Path) -> bool:
    try:
        return _git(repo_dir, "rev-parse", "--git-dir", check=False).returncode == 0
    except (OSError, ValueError):
        return False


def slugify(title: str, cap: int = 24) -> str:
    """Branch-safe slug from a title (lowercase, hyphenated, capped). Empty-safe."""
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:cap].rstrip("-")


# Named once, so readers that tell a work branch from a real one cannot drift from the writer.
BRANCH_PREFIX = "item"


def branch_name(item_id: str, title: str = "") -> str:
    slug = slugify(title)
    stem = f"{BRANCH_PREFIX}/{item_id}"
    return f"{stem}-{slug}" if slug else stem


DEFAULT_WORKTREES_HOME = Path.home() / ".superme" / "worktrees"


def worktrees_home() -> Path:
    """SuperMe's OWNED worktree home, outside both the repo and its parent. In-repo
    would die to `git clean -fdx`."""
    env = os.environ.get("SUPERME_WORKTREES_HOME")
    return Path(env).expanduser() if env else DEFAULT_WORKTREES_HOME


def worktrees_root(repo_id: str) -> Path:
    """One repo's worktree home, keyed by REPO ID — two connected repos may share a
    folder name."""
    return worktrees_home() / repo_id


def worktree_dir(repo_id: str, item_id: str) -> Path:
    return worktrees_root(repo_id) / item_id


def default_branch(repo_dir: Path) -> str:
    """The trunk: origin/HEAD's target, else main/master, else whatever branch HEAD is on."""
    p = _git(repo_dir, "symbolic-ref", "refs/remotes/origin/HEAD", check=False)
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip().rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        if _git(repo_dir, "show-ref", "--verify", f"refs/heads/{cand}", check=False).returncode == 0:
            return cand
    return _out(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")


def resolve_anchor(repo_dir: Path, configured: str | None = None) -> str:
    """The ANCHOR every git site works against. A configured branch that does not
    exist RAISES rather than falling back."""
    if configured:
        if not branch_exists(repo_dir, configured):
            raise GitError(f"anchor branch '{configured}' does not exist in {repo_dir} — fix the "
                           "repo's anchor setting (SuperMe will not fall back to the default branch)")
        return configured
    return default_branch(repo_dir)


def branch_exists(repo_dir: Path, branch: str) -> bool:
    return _git(repo_dir, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0


def list_branches(repo_dir: Path) -> list[str]:
    """Local branches, newest commit first — the anchor picker's options. SuperMe's own
    item branches are excluded."""
    p = _git(repo_dir, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)",
             "refs/heads/", check=False)
    if p.returncode != 0:
        return []
    return [b for b in (ln.strip() for ln in p.stdout.splitlines())
            if b and not b.startswith(f"{BRANCH_PREFIX}/")]


def commit_exists(repo_dir: Path, sha: str) -> bool:
    """Is this sha a real commit here? A `merge_commit` that no longer resolves must
    read as NOT merged."""
    if not sha:
        return False
    return _git(repo_dir, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}",
                check=False).returncode == 0


def _is_merged(repo_dir: Path, branch: str, target: str, merge_commit: str | None) -> bool:
    """Has this branch landed on `target`? The RECORDED merge commit is authoritative:
    a squash is not an ancestor."""
    if commit_exists(repo_dir, merge_commit or ""):
        return True
    return _git(repo_dir, "merge-base", "--is-ancestor", branch, target,
                check=False).returncode == 0


def _current_branch(cwd: Path) -> str:
    return _out(cwd, "rev-parse", "--abbrev-ref", "HEAD")


def _dirty_files(cwd: Path) -> list[str]:
    """Paths with uncommitted changes (staged, unstaged, or untracked)."""
    out = _git(cwd, "status", "--porcelain", check=False).stdout
    files = []
    for line in out.splitlines():
        if len(line) > 3:
            # rename lines are "R  old -> new" — the live path is the right-hand side.
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            if path:
                files.append(path)
    return files


def check_git_state(cwd: Path) -> dict:
    """Pre-flight state probe: refuse to operate mid-merge or mid-rebase.
    Returns {ok, branch, head, dirty, in_merge, in_rebase, reason}."""
    if not is_git_repo(cwd):
        return {"ok": False, "reason": f"not a git repository: {cwd}", "dirty": [],
                "in_merge": False, "in_rebase": False, "branch": None, "head": None}
    git_dir = Path(_out(cwd, "rev-parse", "--absolute-git-dir"))
    in_merge = (git_dir / "MERGE_HEAD").exists()
    in_rebase = (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()
    state = {
        "ok": not (in_merge or in_rebase),
        "branch": _current_branch(cwd),
        "head": _out(cwd, "rev-parse", "HEAD") if _git(cwd, "rev-parse", "HEAD", check=False).returncode == 0 else None,
        "dirty": _dirty_files(cwd),
        "in_merge": in_merge,
        "in_rebase": in_rebase,
        "reason": None,
    }
    if in_merge:
        state["reason"] = "a merge is in progress"
    elif in_rebase:
        state["reason"] = "a rebase is in progress"
    return state


# --- per-repo operation lock (nimbalyst: never two mutating ops on one repo) ------------

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(repo_dir: Path) -> threading.Lock:
    key = str(Path(repo_dir).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class repo_lock:
    """`with repo_lock(repo_dir):` — non-blocking; raises GitBusy if another op holds it."""

    def __init__(self, repo_dir: Path):
        self._lock = _lock_for(repo_dir)

    def __enter__(self):
        if not self._lock.acquire(blocking=False):
            raise GitBusy("another git operation is in progress for this repo")
        return self

    def __exit__(self, *exc):
        self._lock.release()
        return False


# --- the commit gate --------------------------------------------------------------------

# Tells an install "mine to rewrite" from "someone else's hook". Bump when the script changes.
COMMIT_HOOK_MARKER = "superme-commit-msg v1"

# The mechanical half of the commit contract. Deliberately ONE rule: a missing trailer destroys a
# whole surface.
_COMMIT_MSG_HOOK = """#!/bin/sh
# """ + COMMIT_HOOK_MARKER + """ — written by SuperMe when this repo's item worktree was created.
# Rejects a commit on an `item/*` branch that carries no task trailer. Delete this file to disable.

branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null) || exit 0
case "$branch" in
  item/*) ;;
  *) exit 0 ;;
esac

# Merges belong to no single task, and SuperMe writes their messages itself (the freshness sync, a
# sub-item landing, a resolved conflict finishing). MERGE_HEAD is how git says one is in progress.
if git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1; then
  exit 0
fi

if grep -qE '^SuperMe-Task:[[:space:]]*t[0-9]+' "$1"; then
  exit 0
fi

cat >&2 <<'SUPERME_MSG'
SuperMe: this commit has no task trailer, so it was not made.

Every commit on an item branch ends with the task it belongs to, on its own final line:

    SuperMe-Task: t3

The ids are the `## Tasks` entries in this item's artifacts/plan.md — use the one you are
committing. A work-in-progress commit between tasks marks itself: `SuperMe-Task: t3 (wip)`.
A task id never belongs in the subject.

Add the trailer and commit again. If the rejection above is NOT what this message describes —
a check this project owns refused your commit — then stop: do not retry, do not pass
--no-verify. Leave the work staged and end your run with
report_completion(machine.outcome='needs_user'), quoting the refusal verbatim in the question
and naming what you think the owner should do about it.
SUPERME_MSG
exit 1
"""


def install_commit_hook(repo_dir: Path) -> dict:
    """Install or refresh the commit-msg gate. REFUSES rather than clobbering a
    foreign hook or a hooks-path override."""
    repo_dir = Path(repo_dir)
    if not is_git_repo(repo_dir):
        return {"installed": False, "reason": "not_a_repo"}
    override = _git(repo_dir, "config", "--get", "core.hooksPath", check=False).stdout.strip()
    if override:
        return {"installed": False, "reason": "hooks_path_override", "detail": override}
    common = _git(repo_dir, "rev-parse", "--git-common-dir", check=False).stdout.strip() or ".git"
    hooks = (repo_dir / common) / "hooks"   # absolute `common` wins; relative resolves in-repo
    hook = hooks / "commit-msg"
    try:
        if hook.exists() and COMMIT_HOOK_MARKER not in hook.read_text(encoding="utf-8"):
            return {"installed": False, "reason": "foreign", "detail": str(hook)}
        hooks.mkdir(parents=True, exist_ok=True)
        hook.write_text(_COMMIT_MSG_HOOK, encoding="utf-8")
        hook.chmod(0o755)
    except OSError as e:
        log.warning("commit hook not installed in %s: %s", repo_dir, e)
        return {"installed": False, "reason": "error", "detail": str(e)}
    return {"installed": True, "reason": "ok", "detail": str(hook)}


# --- worktree lifecycle -----------------------------------------------------------------

def create_worktree(repo_dir: Path, repo_id: str, item_id: str, title: str = "", *,
                    base: str | None = None) -> dict:
    """TRANSACTIONAL create on build entry: branch from `base` plus a worktree.
    Any failure undoes every step."""
    repo_dir = Path(repo_dir)
    if not is_git_repo(repo_dir):
        raise GitError(f"not a git repository: {repo_dir}")
    branch = branch_name(item_id, title)
    wt = worktree_dir(repo_id, item_id)
    with repo_lock(repo_dir):
        # A stale registration for this path (dir deleted out from under git) blocks re-add — prune first.
        _git(repo_dir, "worktree", "prune", check=False)
        if wt.exists():
            raise GitError(f"worktree dir already exists: {wt}")
        base = resolve_anchor(repo_dir, base)
        reattach = branch_exists(repo_dir, branch)
        if not reattach:
            base_sha = _out(repo_dir, "rev-parse", base)
            _git(repo_dir, "branch", branch, base)
        else:
            base_sha = _out(repo_dir, "merge-base", branch, base)
        wt.parent.mkdir(parents=True, exist_ok=True)
        try:
            _git(repo_dir, "worktree", "add", str(wt), branch)
        except GitError:
            # Undo: remove any partial dir + registration, and the branch if this call created it.
            _git(repo_dir, "worktree", "remove", "--force", str(wt), check=False)
            _git(repo_dir, "worktree", "prune", check=False)
            if not reattach:
                _git(repo_dir, "branch", "-D", branch, check=False)
            raise
        # Every time, not once at connect: `.git/hooks` is untracked, so a re-clone would leave
        # enforcement quietly absent. Never fatal.
        hook = install_commit_hook(repo_dir)
        return {"branch": branch, "worktree": str(wt), "base": base, "base_sha": base_sha,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "commit_hook": hook}


def create_scratch_worktree(repo_dir: Path, repo_id: str, item_id: str, *,
                            base: str | None = None) -> dict:
    """A DETACHED throwaway checkout for a read-only phase — the second wall
    behind the permission classifier.

    Detached on purpose: research merges nothing. The record carries `branch: None`."""
    repo_dir = Path(repo_dir)
    if not is_git_repo(repo_dir):
        raise GitError(f"not a git repository: {repo_dir}")
    wt = worktree_dir(repo_id, item_id)
    with repo_lock(repo_dir):
        _git(repo_dir, "worktree", "prune", check=False)
        base = resolve_anchor(repo_dir, base)
        base_sha = _out(repo_dir, "rev-parse", base)
        if wt.is_dir():
            return {"branch": None, "worktree": str(wt), "base": base, "base_sha": base_sha,
                    "created_at": datetime.now().isoformat(timespec="seconds"), "reused": True}
        wt.parent.mkdir(parents=True, exist_ok=True)
        try:
            _git(repo_dir, "worktree", "add", "--detach", str(wt), base)
        except GitError:
            _git(repo_dir, "worktree", "remove", "--force", str(wt), check=False)
            _git(repo_dir, "worktree", "prune", check=False)
            raise
        return {"branch": None, "worktree": str(wt), "base": base, "base_sha": base_sha,
                "created_at": datetime.now().isoformat(timespec="seconds"), "reused": False}


def servers_in(wt: Path) -> list[int]:
    """Every LISTENING process whose cwd is `wt`. Not by remembered pid: cwd is
    unforgeable and needs no bookkeeping."""
    try:
        out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=15, encoding="utf-8")
        pids = sorted({int(x) for x in out.stdout.split() if x.isdigit()})
    except Exception:  # noqa: BLE001 — no lsof, or it failed; the caller falls back
        return []
    target = Path(wt).resolve()
    here: list[int] = []
    for pid in pids:
        try:
            r = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                               capture_output=True, text=True, timeout=10, encoding="utf-8")
            cwds = [ln[1:] for ln in r.stdout.splitlines() if ln.startswith("n")]
            if cwds and Path(cwds[-1]).resolve() == target:
                here.append(pid)
        except Exception:  # noqa: BLE001 — the process died mid-scan, or is not ours to inspect
            continue
    return here


def _terminate(pid: int) -> bool:
    """Stop a vet-env server and whatever it spawned, reporting whether anything was signalled.

    The group is the target where the OS has groups: a server that forked a worker leaves the
    worker holding the port. Windows has no `killpg` at all — an AttributeError, which is not an
    OSError, so it has to be caught by name or it escapes every handler around it.
    """
    killpg = getattr(os, "killpg", None)
    if killpg is not None:
        try:
            killpg(pid, signal.SIGTERM)
            return True
        except OSError:
            pass                      # not a group leader, or already gone — try the process
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def stop_vet_env(wt: Path) -> list[int]:
    """Kill every vet-env server here. Must run BEFORE the dir goes, or cwd no longer
    resolves and they are unfindable."""
    pids = servers_in(wt)
    if not pids:
        try:
            pid = int(json.loads((Path(wt) / ".vet-env.json").read_text(encoding="utf-8")).get("pid") or 0)
            pids = [pid] if pid > 0 else []
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []
    stopped: list[int] = []
    for pid in pids:
        if _terminate(pid):
            stopped.append(pid)
    if stopped:
        log.info("stopped vet-env server(s) %s in %s", stopped, wt)
    return stopped


def remove_worktree(repo_dir: Path, repo_id: str, item_id: str) -> dict:
    """Terminal cleanup: remove the worktree DIR, KEEP the branch ref. Force-removes,
    so stray junk cannot block closure."""
    repo_dir = Path(repo_dir)
    wt = worktree_dir(repo_id, item_id)
    removed = False
    with repo_lock(repo_dir):
        if wt.exists():
            stop_vet_env(wt)          # the dir takes the files; only this takes the process
            _git(repo_dir, "worktree", "remove", "--force", str(wt))
            removed = True
        _git(repo_dir, "worktree", "prune", check=False)
    verified = not wt.exists()
    if not verified:
        log.error("worktree dir survived removal: %s", wt)
    return {"removed": removed, "verified": verified}


def delete_branch(repo_dir: Path, branch: str) -> dict:
    """Force-delete a branch ref. Used only for a throwaway probe, whose branch is
    scaffolding rather than trace."""
    repo_dir = Path(repo_dir)
    if not branch:
        return {"deleted": False}
    with repo_lock(repo_dir):
        r = _git(repo_dir, "branch", "-D", branch, check=False)
    return {"deleted": r.returncode == 0}


def _same_path(a, b) -> bool:
    """Symlink-tolerant path equality (macOS: /var ↔ /private/var)."""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, ValueError):
        return str(a) == str(b)


def list_worktrees(repo_dir: Path) -> list[dict]:
    """Parse `git worktree list --porcelain` → [{path, head, branch}]."""
    out = _git(repo_dir, "worktree", "list", "--porcelain", check=False).stdout
    entries: list[dict] = []
    cur: dict = {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                entries.append(cur)
            cur = {"path": line[len("worktree "):].strip()}
        elif line.startswith("HEAD "):
            cur["head"] = line[5:].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    if cur:
        entries.append(cur)
    return entries


def worktree_health(repo_dir: Path, repo_id: str, item_id: str, branch: str | None = None,
                    *, trunk: str | None = None, merge_commit: str | None = None) -> dict:
    """One item's git state. Pass `merge_commit`, or `merged` is wrong for every
    squash-merged item."""
    repo_dir = Path(repo_dir)
    wt = worktree_dir(repo_id, item_id)
    branch = branch or branch_name(item_id)
    if not is_git_repo(repo_dir):
        return {"ok": False, "reason": "not a git repository"}
    try:
        trunk = resolve_anchor(repo_dir, trunk)
    except GitError as e:   # misconfigured anchor — report it here rather than crash the Git tab
        return {"ok": False, "reason": str(e)}
    b_exists = branch_exists(repo_dir, branch)
    # Resolve both sides — git reports real paths (/private/var/…) where callers may hold the
    # symlinked form (/var/… on macOS).
    registered = any(_same_path(e["path"], wt) for e in list_worktrees(repo_dir))
    health = {
        "ok": True, "branch": branch, "worktree": str(wt), "trunk": trunk,
        "branch_exists": b_exists, "dir_exists": wt.is_dir(), "registered": registered,
        "dirty": _dirty_files(wt) if wt.is_dir() else [],
        "merged": bool(b_exists) and _is_merged(repo_dir, branch, trunk, merge_commit),
    }
    if b_exists:
        counts = _git(repo_dir, "rev-list", "--left-right", "--count",
                      f"{trunk}...{branch}", check=False)
        if counts.returncode == 0 and counts.stdout.strip():
            behind, ahead = counts.stdout.split()
            health["ahead"] = int(ahead)
            health["behind"] = int(behind)
        # Read from GIT, never a doc. Three dots = merge base, the same range the merge will
        # squash.
        stat = _git(repo_dir, "diff", "--shortstat", f"{trunk}...{branch}", check=False)
        if stat.returncode == 0:
            line = stat.stdout.strip()
            health["files"] = int(m.group(1)) if (m := re.search(r"(\d+) files? changed", line)) else 0
            health["insertions"] = int(m.group(1)) if (m := re.search(r"(\d+) insertion", line)) else 0
            health["deletions"] = int(m.group(1)) if (m := re.search(r"(\d+) deletion", line)) else 0
    health["ok"] = b_exists and (wt.is_dir() == registered)
    return health


def reconcile(repo_dir: Path, repo_id: str, records: dict[str, dict]) -> list[dict]:
    """Startup reconciliation: recorded worktrees vs disk vs branches. Heals what it safely
    can, reports the rest. Never deletes."""
    repo_dir = Path(repo_dir)
    actions: list[dict] = []
    if not is_git_repo(repo_dir):
        return [{"action": "skipped", "detail": f"not a git repository: {repo_dir}"}]
    with repo_lock(repo_dir):
        _git(repo_dir, "worktree", "prune", check=False)
        registered = [e["path"] for e in list_worktrees(repo_dir)]
        for item_id, rec in records.items():
            wt = Path(rec.get("worktree") or worktree_dir(repo_id, item_id))
            # A branchless record is a SCRATCH tree: nothing to rebuild from and nothing that
            # needs it.
            if not rec.get("branch"):
                if wt.is_dir() and not any(_same_path(r, wt) for r in registered):
                    actions.append({"item_id": item_id, "action": "orphan-dir",
                                    "detail": f"{wt} exists but git does not register it"})
                continue
            branch = rec["branch"]
            b = branch_exists(repo_dir, branch)
            if b and not wt.is_dir():
                try:
                    wt.parent.mkdir(parents=True, exist_ok=True)
                    _git(repo_dir, "worktree", "add", str(wt), branch)
                    actions.append({"item_id": item_id, "action": "recreated",
                                    "detail": f"re-added {wt} from {branch}"})
                except GitError as e:
                    actions.append({"item_id": item_id, "action": "broken", "detail": str(e)})
            elif not b:
                actions.append({"item_id": item_id, "action": "broken",
                                "detail": f"recorded branch {branch} no longer exists"})
            elif wt.is_dir() and not any(_same_path(r, wt) for r in registered):
                actions.append({"item_id": item_id, "action": "orphan-dir",
                                "detail": f"{wt} exists but git does not register it"})
    # Orphan dirs under the worktrees root that belong to NO live record → report only.
    root = worktrees_root(repo_id)
    if root.is_dir():
        known = {Path(r.get("worktree") or worktree_dir(repo_id, i)).name
                 for i, r in records.items()}
        for child in root.iterdir():
            if child.is_dir() and child.name not in known:
                actions.append({"action": "orphan-dir",
                                "detail": f"{child} has no live work-item record"})
    return actions


# --- merge machinery --------------------------------------------------------------------

_STASH_PREFIX = "superme-automerge"


def compose_commit(subject: str, body: str = "", trailers: dict | None = None) -> str:
    """The ONE commit-message shape SuperMe writes. The main message is for the
    PROJECT, the trailer block for SuperMe.

    Nothing SuperMe-shaped belongs above the trailers: the project's history has never heard of
    this workspace."""
    import textwrap
    blocks = [subject.strip()]
    body = (body or "").strip()
    if body:
        blocks.append("\n".join(
            "\n".join(textwrap.wrap(para, 72)) if para.strip() else ""
            for para in body.split("\n")))
    # An absent fact is omitted, never written as "none". Git reads trailers as one uninterrupted
    # run.
    tail = "\n".join(f"{k}: {v}" for k, v in (trailers or {}).items() if v)
    if tail:
        blocks.append(tail)
    return "\n\n".join(blocks) + "\n"


# --- reading a branch back (the PR walkthrough) ------------------------------------------

# ASCII record and unit separators: a commit body may hold anything printable, so only these are
# unbreakable.
_REC, _FLD = "\x1e", "\x1f"

_TRAILER = re.compile(r"(?m)^([A-Za-z][A-Za-z0-9-]*):[ \t]*(.+?)[ \t]*$")


def fork_point(repo_dir: Path, branch: str, base: str) -> str:
    """Where `branch` left `base`. `merge-base`, not `base` itself: the anchor keeps moving."""
    proc = _git(repo_dir, "merge-base", base, branch, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def branch_commits(repo_dir: Path, branch: str, base: str) -> list[dict]:
    """Every commit `branch` added over `base`, oldest first. `--no-merges` drops
    syncs, whose churn is the anchor's."""
    point = fork_point(repo_dir, branch, base)
    if not point:
        return []
    proc = _git(repo_dir, "log", "--reverse", "--no-merges", "--numstat",
                f"--format={_REC}%H{_FLD}%s{_FLD}%b{_FLD}", f"{point}..{branch}", check=False)
    if proc.returncode != 0:
        return []
    out: list[dict] = []
    for chunk in proc.stdout.split(_REC):
        if not chunk.strip():
            continue
        parts = chunk.split(_FLD)
        if len(parts) < 4:
            continue
        sha, subject, body, tail = parts[0].strip(), parts[1], parts[2], parts[3]
        files = []
        for line in tail.splitlines():
            cols = line.split("\t")
            if len(cols) != 3:
                continue
            p, m, path = cols
            files.append({"path": path, "plus": int(p) if p.isdigit() else 0,
                          "minus": int(m) if m.isdigit() else 0})
        out.append({"sha": sha, "short": sha[:10], "subject": subject.strip(),
                    "body": body.strip(), "trailers": commit_trailers(body), "files": files})
    return out


def branch_stat(repo_dir: Path, branch: str, base: str) -> dict:
    """What this branch actually LANDS — the net diff from the fork point, not the sum
    of its commits."""
    point = fork_point(repo_dir, branch, base)
    if not point:
        return {"files": 0, "insertions": 0, "deletions": 0, "by_file": []}
    proc = _git(repo_dir, "diff", "--numstat", f"{point}..{branch}", check=False)
    return _parse_numstat(proc.stdout if proc.returncode == 0 else "")


def commit_trailers(body: str) -> dict:
    """The `Key: value` block at the END of a body. Only the final uninterrupted run
    counts, per git."""
    lines = (body or "").rstrip().splitlines()
    block: list[str] = []
    for line in reversed(lines):
        if not line.strip():
            break
        if not _TRAILER.fullmatch(line.strip()):
            return {}          # the last block isn't a trailer block at all
        block.append(line.strip())
    out = {}
    for line in reversed(block):
        m = _TRAILER.fullmatch(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def commit_patches(repo_dir: Path, shas: list[str], path: str, *, cap: int = 200_000) -> list[dict]:
    """The patches `shas` applied to ONE file, in order. Per-commit, not a range —
    a task's commits need not be contiguous."""
    out: list[dict] = []
    used = 0
    for sha in shas:
        if used >= cap:
            out.append({"sha": sha, "patch": "", "truncated": True})
            continue
        proc = _git(repo_dir, "show", "--format=", "--patch", sha, "--", path, check=False)
        patch = proc.stdout if proc.returncode == 0 else ""
        if not patch.strip():
            continue      # this commit didn't touch the file — an empty row is not a diff
        clipped = patch[: cap - used]
        used += len(clipped)
        out.append({"sha": sha, "patch": clipped, "truncated": len(clipped) < len(patch)})
    return out


def _backup_ref(item_id: str) -> str:
    """A UNIQUE pre-merge restore point. Microseconds, not seconds: two attempts in one
    second collided."""
    return f"refs/backup/{item_id}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"


def recut_branch(repo_dir: Path, item_id: str, branch: str, base: str) -> dict:
    """Reset an item's branch back onto its BASE — the re-run's "start clean". `base`
    resolves to its CURRENT tip.

    NOTHING IS LOST: the old tip goes to a backup ref and the branch is moved, not deleted."""
    repo_dir = Path(repo_dir)
    if not branch:
        return {"recut": False, "reason": "this item has no branch"}
    if not branch_exists(repo_dir, branch):
        return {"recut": False, "reason": f"branch {branch} does not exist"}
    base = base or resolve_anchor(repo_dir, None)   # pre-`git_base` items fall back to the anchor
    if _git(repo_dir, "rev-parse", "--verify", f"{base}^{{commit}}", check=False).returncode != 0:
        # The base ref is gone. Leaving the branch put beats resetting onto something unnameable.
        return {"recut": False, "reason": f"base `{base}` does not resolve in this repo"}
    tip = _out(repo_dir, "rev-parse", branch).strip()
    target = _out(repo_dir, "rev-parse", f"{base}^{{commit}}").strip()
    if tip == target:
        return {"recut": False, "reason": "branch is already at its base", "from_sha": tip}
    ref = _backup_ref(item_id)
    _git(repo_dir, "update-ref", ref, tip)          # guardrail BEFORE the move, never after
    _git(repo_dir, "branch", "-f", branch, target)
    return {"recut": True, "backup_ref": ref, "from_sha": tip, "base": base,
            "to_sha": _out(repo_dir, "rev-parse", branch).strip()}


def overlap(repo_dir: Path, branch: str, target: str) -> list[str]:
    """Files dirty in the main tree AND touched by the branch. An auto-stash would hide the
    collision."""
    dirty = set(_dirty_files(repo_dir))
    if not dirty:
        return []
    touched = set(_out(repo_dir, "diff", "--name-only", f"{target}...{branch}").splitlines())
    return sorted(dirty & touched)


def merge_freshness(repo_dir: Path, worktree: Path, branch: str, *,
                    target: str | None = None) -> dict:
    """The merge act owns freshness, not review — one comparison at the instant that
    matters.

    Anchor unmoved → merge · sync conflicts → park · clean but overlapping our paths → revet ·
    else merge. Conflicts are never auto-resolved."""
    repo_dir, worktree = Path(repo_dir), Path(worktree)
    target = resolve_anchor(repo_dir, target)
    if not worktree.is_dir():
        # No tree to sync into — the merge itself will report any conflict. Never silently skip.
        return {"action": "merge", "reason": "no live worktree to sync"}
    if _git(repo_dir, "merge-base", "--is-ancestor", target, branch, check=False).returncode == 0:
        return {"action": "merge"}
    # Measured from the merge base BEFORE the sync: afterwards the two sets are indistinguishable.
    base = _out(repo_dir, "merge-base", target, branch)
    anchor_paths = set(_out(repo_dir, "diff", "--name-only", base, target).splitlines())
    item_paths = set(_out(repo_dir, "diff", "--name-only", base, branch).splitlines())
    res = sync_from_main(repo_dir, worktree, target=target)
    if res.get("conflicts"):
        return {"action": "park", "conflicts": res["conflicts"]}
    both = sorted(anchor_paths & item_paths)
    if both:
        return {"action": "revet", "paths": both, "synced": res.get("commit")}
    return {"action": "merge", "synced": res.get("commit")}


def merge_to_main(repo_dir: Path, repo_id: str, item_id: str, branch: str, *,
                  target: str | None = None, merged_commit: str | None = None,
                  message: str | None = None) -> dict:
    """The ONE heavy merge: item branch → the anchor, under the op lock.

    SQUASH — the branch is kept, so task granularity survives as trace. A conflict fully unwinds
    to the backup ref, written BEFORE the merge."""
    repo_dir = Path(repo_dir)
    with repo_lock(repo_dir):
        target = resolve_anchor(repo_dir, target)
        state = check_git_state(repo_dir)
        if not state["ok"]:
            raise GitError(f"main repo not mergeable: {state['reason']}")
        wt = worktree_dir(repo_id, item_id)
        if wt.is_dir():
            wstate = check_git_state(wt)
            if not wstate["ok"]:
                raise GitError(f"item worktree not mergeable: {wstate['reason']}")
            if wstate["dirty"]:
                raise GitError("item worktree has uncommitted changes — commit or discard them first")
        if not branch_exists(repo_dir, branch):
            raise GitError(f"branch {branch} does not exist")
        # Never merge twice. The recorded sha decides; ancestry is only the legacy fallback.
        if _is_merged(repo_dir, branch, target, merged_commit):
            return {"already_merged": True, "merged": False}
        prev_branch = state["branch"]
        dirty = state["dirty"]
        if prev_branch != target and dirty:
            raise GitError(f"main repo is on {prev_branch} with uncommitted changes — "
                           f"switch to {target} or commit first")
        clash = overlap(repo_dir, branch, target)
        if clash:
            raise GitError("uncommitted changes in the main tree overlap files this branch "
                           "touches: " + ", ".join(clash))
        stash_tag = None
        if dirty:
            stash_tag = f"{_STASH_PREFIX}-{item_id}-{datetime.now().strftime('%H%M%S')}"
            _git(repo_dir, "stash", "push", "-u", "-m", stash_tag)
        switched = False
        result: dict
        try:
            if prev_branch != target:
                _git(repo_dir, "checkout", target)
                switched = True
            backup = _backup_ref(item_id)
            _git(repo_dir, "update-ref", backup, _out(repo_dir, "rev-parse", target))
            merge = _git(repo_dir, "merge", "--squash", branch, check=False)
            if merge.returncode != 0:
                conflicts = _out(repo_dir, "diff", "--name-only", "--diff-filter=U").splitlines()
                # A squash records no MERGE_HEAD, so `merge --abort` cannot unwind it. The backup
                # ref can.
                _git(repo_dir, "reset", "--hard", backup, check=False)
                result = {"merged": False, "conflicts": conflicts, "backup_ref": backup}
            elif _git(repo_dir, "diff", "--cached", "--quiet", check=False).returncode == 0:
                # Staging NOTHING means the content is already on the anchor. Report the no-op and
                # drop the backup.
                _git(repo_dir, "update-ref", "-d", backup, check=False)
                result = {"already_merged": True, "merged": False}
            else:
                _git(repo_dir, "commit", "-m", message or f"Merge branch {branch}")
                result = {"merged": True, "merge_commit": _out(repo_dir, "rev-parse", "HEAD"),
                          "backup_ref": backup, "target": target}
        finally:
            # Runs on success, conflict AND exception. The result picks up the stash warning after the
            # pop attempt, never before.
            if switched:
                _git(repo_dir, "checkout", prev_branch, check=False)
            stash_warning = None
            if stash_tag:
                # Find OUR stash by its unique tag. A failed pop is surfaced loud — never lost
                # silently.
                entry = None
                for line in _git(repo_dir, "stash", "list", check=False).stdout.splitlines():
                    if stash_tag in line:
                        entry = line.split(":", 1)[0]
                        break
                popped = entry and _git(repo_dir, "stash", "pop", entry, check=False).returncode == 0
                if not popped:
                    stash_warning = (f"auto-stash '{stash_tag}' could not be popped cleanly — "
                                     f"your uncommitted changes are preserved in the stash; "
                                     f"run `git stash list` to recover them")
                    log.error("stash pop failed in %s: %s", repo_dir, stash_tag)
        if stash_warning:
            result["stash_warning"] = stash_warning
        return result


def revert_merge(repo_dir: Path, backup_ref: str, *, target: str | None = None) -> dict:
    """Restore the anchor to its pre-merge state. SAFE-ONLY: refuses unless the backup
    is head's first parent."""
    repo_dir = Path(repo_dir)
    with repo_lock(repo_dir):
        target = resolve_anchor(repo_dir, target)
        if _git(repo_dir, "rev-parse", "--verify", backup_ref, check=False).returncode != 0:
            raise GitError(f"backup ref {backup_ref} not found")
        backup_sha = _out(repo_dir, "rev-parse", backup_ref)
        head_sha = _out(repo_dir, "rev-parse", target)
        first_parent = _out(repo_dir, "rev-parse", f"{target}^1") \
            if _git(repo_dir, "rev-parse", f"{target}^1", check=False).returncode == 0 else None
        if first_parent != backup_sha:
            raise GitError("cannot revert: commits landed on top of the merge (or this backup "
                           "does not match the last merge) — manual git surgery required")
        state = check_git_state(repo_dir)
        if not state["ok"] or (state["branch"] == target and state["dirty"]):
            raise GitError("cannot revert: the main tree is mid-operation or has uncommitted changes")
        if state["branch"] == target:
            _git(repo_dir, "reset", "--hard", backup_sha)
        else:
            _git(repo_dir, "branch", "-f", target, backup_sha)
        return {"reverted": True, "target": target, "head": backup_sha}


def merge_into_parent(repo_dir: Path, child_branch: str, parent_worktree: Path, *,
                      message: str | None = None) -> dict:
    """The LIGHT path: a blocking child merges into its parent's branch, inside the
    parent's worktree. No main risk."""
    parent_worktree = Path(parent_worktree)
    with repo_lock(repo_dir):
        state = check_git_state(parent_worktree)
        if not state["ok"]:
            raise GitError(f"parent worktree not mergeable: {state['reason']}")
        if state["dirty"]:
            raise GitError("parent worktree has uncommitted changes — commit them first")
        # Ancestry is CORRECT here: the light path is a real `--no-ff` merge, not a squash.
        if _git(repo_dir, "merge-base", "--is-ancestor", child_branch, state["branch"],
                check=False).returncode == 0:
            return {"already_merged": True, "merged": False}
        # Same shape as the anchor's: clean subject, SuperMe facts in the trailers. A human reads
        # this.
        merge = _git(parent_worktree, "merge", "--no-ff", child_branch,
                     "-m", message or compose_commit("Merge a completed sub-item"), check=False)
        if merge.returncode != 0:
            conflicts = _out(parent_worktree, "diff", "--name-only", "--diff-filter=U").splitlines()
            _git(parent_worktree, "merge", "--abort", check=False)
            return {"merged": False, "conflicts": conflicts}
        return {"merged": True, "merge_commit": _out(parent_worktree, "rev-parse", "HEAD"),
                "target": state["branch"]}


def sync_from_main(repo_dir: Path, worktree: Path, *, target: str | None = None,
                   leave_conflicts: bool = False) -> dict:
    """Merge the trunk INTO the item branch, inside the worktree. `leave_conflicts=True`
    leaves them for Resolve-with-Agent."""
    repo_dir = Path(repo_dir)
    worktree = Path(worktree)
    target = resolve_anchor(repo_dir, target)
    state = check_git_state(worktree)
    if not state["ok"]:
        raise GitError(f"worktree not syncable: {state['reason']}")
    if state["dirty"]:
        raise GitError("worktree has uncommitted changes — commit them before syncing")
    if _git(repo_dir, "merge-base", "--is-ancestor", target, state["branch"],
            check=False).returncode == 0:
        return {"merged": False, "up_to_date": True}
    merge = _git(worktree, "merge", target,
                 "-m", f"Sync {target} into {state['branch']}", check=False)
    if merge.returncode != 0:
        conflicts = _out(worktree, "diff", "--name-only", "--diff-filter=U").splitlines()
        if not leave_conflicts:
            _git(worktree, "merge", "--abort", check=False)
        return {"merged": False, "conflicts": conflicts, "in_tree": leave_conflicts}
    return {"merged": True, "commit": _out(worktree, "rev-parse", "HEAD")}


def conflicted_files(worktree: Path) -> list[str]:
    """Paths still carrying conflict markers per git's index (unmerged entries)."""
    return _out(Path(worktree), "diff", "--name-only", "--diff-filter=U").splitlines()


def finish_merge(worktree: Path) -> dict:
    """Complete an in-tree conflicted merge. Refuses while any file still carries conflict
    markers."""
    worktree = Path(worktree)
    if not (Path(_out(worktree, "rev-parse", "--absolute-git-dir")) / "MERGE_HEAD").exists():
        raise GitError("no merge in progress to finish")
    marked = _git(worktree, "grep", "-l", "^<<<<<<< ", "--", ".", check=False)
    if marked.returncode == 0 and marked.stdout.strip():
        raise GitError("conflict markers remain in: " + ", ".join(marked.stdout.split()))
    _git(worktree, "add", "-A")
    remaining = conflicted_files(worktree)
    if remaining:
        raise GitError("unresolved paths remain in the index: " + ", ".join(remaining))
    _git(worktree, "commit", "--no-edit")
    return {"merged": True, "commit": _out(worktree, "rev-parse", "HEAD")}
