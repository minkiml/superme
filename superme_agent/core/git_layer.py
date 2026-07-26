"""Git layer — per-item worktree lifecycle + merge machinery (workspace-workflow S4 / D4).

Trunk-based, item-level: one work-item ⇄ one branch `item/<id>-<slug>` ⇄ one worktree under
SuperMe's own home, `~/.superme/worktrees/<repo-id>/<item-id>/` (see `worktrees_home`). Trees are
never shared across items. `main` is the
trunk; only the human-gated review decision merges into it, always behind an ephemeral
`refs/backup/<item>-<ts>` guardrail (revert always offered). Blocking children branch FROM the
parent's branch and merge BACK INTO it (the light path); parallel/spawn branch from main.

Everything here is a pure function over a repo directory — no daemon imports, no item-yaml
knowledge. Callers (routes/lifespan) own the item record; this module owns git. All mutations to
the MAIN repo run under a per-repo operation lock; a second concurrent op raises GitBusy rather
than corrupting state (nimbalyst punch-list). Fail-loud: any unexpected git failure raises
GitError with the command + stderr, so a half-truth never reads as success.
"""

import logging
import os
import re
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
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def _out(cwd: Path, *args: str) -> str:
    return _git(cwd, *args).stdout.strip()


def diff_numstat(worktree: Path, base: str) -> dict:
    """`git diff --numstat base...HEAD` in the worktree → {files, insertions, deletions, by_file}
    (by_file = [{path, plus, minus}]). The mechanical merge-diff summary — feeds readiness.md's
    `## Stats` (and later a PR body). Best-effort: a bad base / non-repo returns zeros, never raises
    (binary files report '-' for plus/minus in numstat → counted as 0)."""
    proc = _git(worktree, "diff", "--numstat", f"{base}...HEAD", check=False)
    if proc.returncode != 0:
        return {"files": 0, "insertions": 0, "deletions": 0, "by_file": []}
    by_file, ins, dels = [], 0, 0
    for line in proc.stdout.splitlines():
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


def branch_name(item_id: str, title: str = "") -> str:
    slug = slugify(title)
    return f"item/{item_id}-{slug}" if slug else f"item/{item_id}"


DEFAULT_WORKTREES_HOME = Path.home() / ".superme" / "worktrees"


def worktrees_home() -> Path:
    """SuperMe's OWNED worktree home, `~/.superme/worktrees/` (override: $SUPERME_WORKTREES_HOME,
    read per call so tests can point it at a temp dir).

    Deliberately outside both the repo and its parent. In-repo was considered and rejected: the
    tree would have to be git-ignored on the trunk AND on every branch base before its first use
    (a bootstrap hazard), being ignored makes a `git clean -fdx` in the main repo silently destroy
    every in-flight item's uncommitted work, and recursive tooling (grep/pytest/linters/watchers)
    would descend into N copies of the repo. A sibling dir avoided all that but polluted the
    owner's parent directory looking like deletable junk. An owned home keeps the isolation and
    makes ownership obvious."""
    env = os.environ.get("SUPERME_WORKTREES_HOME")
    return Path(env).expanduser() if env else DEFAULT_WORKTREES_HOME


def worktrees_root(repo_id: str) -> Path:
    """One repo's worktree home. Keyed by REPO ID (unique by construction in repos.yaml), not by
    repo dir name — two connected repos may share a folder name."""
    return worktrees_home() / repo_id


def worktree_dir(repo_id: str, item_id: str) -> Path:
    return worktrees_root(repo_id) / item_id


def default_branch(repo_dir: Path) -> str:
    """The trunk: origin/HEAD's target when a remote exists, else main/master if present,
    else whatever branch HEAD is on (single-branch scratch repos)."""
    p = _git(repo_dir, "symbolic-ref", "refs/remotes/origin/HEAD", check=False)
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip().rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        if _git(repo_dir, "show-ref", "--verify", f"refs/heads/{cand}", check=False).returncode == 0:
            return cand
    return _out(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")


def branch_exists(repo_dir: Path, branch: str) -> bool:
    return _git(repo_dir, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0


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
    """Pre-flight state probe (D4 merge mechanics): refuse to operate mid-merge/mid-rebase.
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


# --- worktree lifecycle -----------------------------------------------------------------

def create_worktree(repo_dir: Path, repo_id: str, item_id: str, title: str = "", *,
                    base: str | None = None) -> dict:
    """TRANSACTIONAL create on build entry: branch `item/<id>-<slug>` from `base` (default: the
    trunk; a blocking child passes its parent's branch) + worktree under the owned home. On any
    failure every step already taken is undone (branch deleted, worktree removed) and GitError
    raised — no half-created state. Reattach case: if OUR branch already exists but the dir is
    gone (a previous create died after branching, or a deleted dir), the worktree is re-added
    from the existing branch instead of failing. Returns the git record for the item yaml:
    {branch, worktree, base, base_sha, created_at}."""
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
        base = base or default_branch(repo_dir)
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
        return {"branch": branch, "worktree": str(wt), "base": base, "base_sha": base_sha,
                "created_at": datetime.now().isoformat(timespec="seconds")}


def remove_worktree(repo_dir: Path, repo_id: str, item_id: str) -> dict:
    """Terminal cleanup: remove the worktree DIR, KEEP the branch ref (near-free, is trace —
    never-delete holds). Force-removes (a terminal item's stray uncommitted junk must not block
    closure; anything real was merged or lives on the branch). Verify-after-delete: confirms the
    dir is actually gone. Returns {removed, verified}."""
    repo_dir = Path(repo_dir)
    wt = worktree_dir(repo_id, item_id)
    removed = False
    with repo_lock(repo_dir):
        if wt.exists():
            _git(repo_dir, "worktree", "remove", "--force", str(wt))
            removed = True
        _git(repo_dir, "worktree", "prune", check=False)
    verified = not wt.exists()
    if not verified:
        log.error("worktree dir survived removal: %s", wt)
    return {"removed": removed, "verified": verified}


def delete_branch(repo_dir: Path, branch: str) -> dict:
    """Force-delete a branch ref (`git branch -D`). Unlike `remove_worktree`, this DOES drop the
    branch — used ONLY for a throwaway prompt-extraction probe, whose branch is disposable scaffolding
    (never merged, never trace), NOT a real work-item branch (those are kept, never-delete-logs).
    Best-effort: a missing branch is a no-op. Returns {deleted}."""
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


def worktree_health(repo_dir: Path, repo_id: str, item_id: str, branch: str | None = None) -> dict:
    """Health check for one item's git state: does the branch exist, does the dir exist, does
    git still register it, is the tree dirty, how far is the branch from trunk, is it merged."""
    repo_dir = Path(repo_dir)
    wt = worktree_dir(repo_id, item_id)
    branch = branch or branch_name(item_id)
    if not is_git_repo(repo_dir):
        return {"ok": False, "reason": "not a git repository"}
    b_exists = branch_exists(repo_dir, branch)
    # Resolve both sides — git reports real paths (/private/var/…) where callers may hold the
    # symlinked form (/var/… on macOS).
    registered = any(_same_path(e["path"], wt) for e in list_worktrees(repo_dir))
    trunk = default_branch(repo_dir)
    health = {
        "ok": True, "branch": branch, "worktree": str(wt), "trunk": trunk,
        "branch_exists": b_exists, "dir_exists": wt.is_dir(), "registered": registered,
        "dirty": _dirty_files(wt) if wt.is_dir() else [],
        "merged": bool(b_exists) and _git(
            repo_dir, "merge-base", "--is-ancestor", branch, trunk, check=False).returncode == 0,
    }
    if b_exists:
        counts = _git(repo_dir, "rev-list", "--left-right", "--count",
                      f"{trunk}...{branch}", check=False)
        if counts.returncode == 0 and counts.stdout.strip():
            behind, ahead = counts.stdout.split()
            health["ahead"] = int(ahead)    # branch commits not on trunk
            health["behind"] = int(behind)  # trunk commits not on branch (freshness debt)
    health["ok"] = b_exists and (wt.is_dir() == registered)
    return health


def reconcile(repo_dir: Path, repo_id: str, records: dict[str, dict]) -> list[dict]:
    """Startup reconciliation (nimbalyst punch-list): recorded worktrees vs disk vs branches.
    `records` = {item_id: {branch, worktree}} for LIVE (non-terminal, build+) items. Heals what
    it safely can, reports the rest:
      · record + branch + dir MISSING  → re-add the worktree from the branch (kill-mid-create /
        deleted dir — the branch is the durable state).
      · record + branch MISSING        → report broken (nothing durable to rebuild from; the
        caller surfaces it — we never guess).
      · dir on disk git no longer registers → prune the registration, report the orphan dir
        (report-only: a stray dir is never deleted here — accident-prevention, not cleanup).
    Returns a list of {item_id?, action, detail} entries (empty = all consistent)."""
    repo_dir = Path(repo_dir)
    actions: list[dict] = []
    if not is_git_repo(repo_dir):
        return [{"action": "skipped", "detail": f"not a git repository: {repo_dir}"}]
    with repo_lock(repo_dir):
        _git(repo_dir, "worktree", "prune", check=False)
        registered = [e["path"] for e in list_worktrees(repo_dir)]
        for item_id, rec in records.items():
            branch = rec.get("branch") or branch_name(item_id)
            wt = Path(rec.get("worktree") or worktree_dir(repo_id, item_id))
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


def _backup_ref(item_id: str) -> str:
    return f"refs/backup/{item_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def overlap(repo_dir: Path, branch: str, target: str) -> list[str]:
    """Pre-flight overlap detection: files dirty in the main tree AND touched by the branch —
    an auto-stash would hide a collision, so the merge refuses early on any intersection."""
    dirty = set(_dirty_files(repo_dir))
    if not dirty:
        return []
    touched = set(_out(repo_dir, "diff", "--name-only", f"{target}...{branch}").splitlines())
    return sorted(dirty & touched)


def merge_to_main(repo_dir: Path, repo_id: str, item_id: str, branch: str, *,
                  target: str | None = None) -> dict:
    """The ONE heavy merge: item branch → trunk, in the MAIN repo, under the op lock.

    Pre-flights (D4, nimbalyst): both sides not mid-merge/rebase · never-merge-twice (re-query
    authoritative ancestry) · overlap refusal · main's uncommitted changes auto-stashed
    unique-tagged and verify-popped (failure surfaced as `stash_warning`, never silent) · backup
    ref `refs/backup/<item>-<ts>` written BEFORE the merge (revert always offered).

    Conflict → the merge is fully unwound (abort, stash pop, checkout restored) and the conflicted
    paths returned; the Resolve-with-Agent path goes through sync_from_main in the WORKTREE
    instead (freshness rule: after main is merged into the branch, this merge is trivial).

    Returns {merged, merge_commit, backup_ref, stash_warning?} | {already_merged: True}
    | {conflicts: [...]}. Raises GitError/GitBusy on refusals."""
    repo_dir = Path(repo_dir)
    with repo_lock(repo_dir):
        target = target or default_branch(repo_dir)
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
        # Never merge twice — authoritative ancestry, not a stored flag.
        if _git(repo_dir, "merge-base", "--is-ancestor", branch, target, check=False).returncode == 0:
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
            merge = _git(repo_dir, "merge", "--no-ff", branch,
                         "-m", f"Merge {branch} (work-item {item_id})", check=False)
            if merge.returncode != 0:
                conflicts = _out(repo_dir, "diff", "--name-only", "--diff-filter=U").splitlines()
                _git(repo_dir, "merge", "--abort", check=False)
                result = {"merged": False, "conflicts": conflicts, "backup_ref": backup}
            else:
                result = {"merged": True, "merge_commit": _out(repo_dir, "rev-parse", "HEAD"),
                          "backup_ref": backup, "target": target}
        finally:
            # Cleanup runs on success, conflict, AND exception — the result (when there is one)
            # picks the stash warning up AFTER the pop attempt, never before.
            if switched:
                _git(repo_dir, "checkout", prev_branch, check=False)
            stash_warning = None
            if stash_tag:
                # Verified pop: find OUR stash by its unique tag; a failed pop is surfaced loud
                # (stash_warning) — the changes are safe in the stash, never lost silently.
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
    """Restore the trunk to its pre-merge state via the backup ref. SAFE-ONLY: refuses unless
    the backup commit is the merge's direct first parent of the CURRENT head (i.e. nothing has
    landed on top) and the tree is clean — beyond that window, un-merging needs a human."""
    repo_dir = Path(repo_dir)
    with repo_lock(repo_dir):
        target = target or default_branch(repo_dir)
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


def merge_into_parent(repo_dir: Path, child_branch: str, parent_worktree: Path) -> dict:
    """The LIGHT path (D4): a blocking child's branch merges into its parent's branch, executed
    INSIDE the parent's worktree. No backup ref, no stash ceremony — no main risk; the parent
    re-vets the family before its own main merge. Conflict → abort + report. Runs under the
    per-repo op lock: the merge commit mutates the SHARED object store, and the parent's own
    trunk merge must never interleave with a child landing into it."""
    parent_worktree = Path(parent_worktree)
    with repo_lock(repo_dir):
        state = check_git_state(parent_worktree)
        if not state["ok"]:
            raise GitError(f"parent worktree not mergeable: {state['reason']}")
        if state["dirty"]:
            raise GitError("parent worktree has uncommitted changes — commit them first")
        if _git(repo_dir, "merge-base", "--is-ancestor", child_branch, state["branch"],
                check=False).returncode == 0:
            return {"already_merged": True, "merged": False}
        merge = _git(parent_worktree, "merge", "--no-ff", child_branch,
                     "-m", f"Merge {child_branch} into {state['branch']}", check=False)
        if merge.returncode != 0:
            conflicts = _out(parent_worktree, "diff", "--name-only", "--diff-filter=U").splitlines()
            _git(parent_worktree, "merge", "--abort", check=False)
            return {"merged": False, "conflicts": conflicts}
        return {"merged": True, "merge_commit": _out(parent_worktree, "rev-parse", "HEAD"),
                "target": state["branch"]}


def sync_from_main(repo_dir: Path, worktree: Path, *, target: str | None = None,
                   leave_conflicts: bool = False) -> dict:
    """Freshness rule (replaces rebasing ceremony): merge the trunk INTO the item branch, inside
    the worktree. Run agent-side during long builds and ALWAYS at review time — after this, the
    merge back to main is trivial. Conflicts: default aborts + reports; `leave_conflicts=True`
    leaves the conflicted merge IN the tree for the Resolve-with-Agent session (the human never
    hand-edits conflict markers; finish with `finish_merge`)."""
    repo_dir = Path(repo_dir)
    worktree = Path(worktree)
    target = target or default_branch(repo_dir)
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
    """Complete an in-tree conflicted merge after the files were resolved. Ground-truth checks,
    not claims: refuses while any file still carries conflict markers (`git grep` — the index
    stays 'unmerged' until add, so markers are the real signal), then stages everything, verifies
    nothing is left unmerged, and commits with the merge's prepared message."""
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
