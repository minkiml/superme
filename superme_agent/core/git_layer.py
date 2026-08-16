"""Git layer — per-item worktree lifecycle + merge machinery (workspace-workflow S4 / D4).

Trunk-based, item-level: one work-item ⇄ one branch `item/<id>-<slug>` ⇄ one worktree under
SuperMe's own home, `~/.superme/worktrees/<repo-id>/<item-id>/` (see `worktrees_home`). Trees are
never shared across items. The repo's ANCHOR branch (`resolve_anchor` — the repo's `anchor_branch`
setting, else its own default branch) is the trunk; only the gated review decision merges into it,
always behind an ephemeral `refs/backup/<item>-<ts>` guardrail (revert always offered). Blocking
children branch FROM the parent's branch and merge BACK INTO it (the light path); parallel/spawn
branch from the anchor.

Everything here is a pure function over a repo directory — no daemon imports, no item-yaml
knowledge. Callers (routes/lifespan) own the item record; this module owns git. All mutations to
the MAIN repo run under a per-repo operation lock; a second concurrent op raises GitBusy rather
than corrupting state (nimbalyst punch-list). Fail-loud: any unexpected git failure raises
GitError with the command + stderr, so a half-truth never reads as success.
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
    return _parse_numstat(proc.stdout if proc.returncode == 0 else "")


# Temporary instrumentation carries a tag so its removal is one grep rather than a memory. The
# build skill asks for `[DEBUG-<4 hex>]` on every probe and a clean grep before the cycle ends;
# this is the reader that checks, at the review gate, whether any survived into the branch.
DEBUG_TAG = re.compile(r"\[DEBUG-[0-9a-fA-F]{4,}\]")


def debug_tags(worktree: Path, base: str) -> list[dict]:
    """Tagged debug instrumentation still present in the branch's ADDED lines.

    → [{path, tag, line}], one row per surviving probe, deduped by (path, tag).

    Reads `git diff base...HEAD` and scans only `+` lines, because what matters is what THIS branch
    adds: a tag that already lived on the trunk is not this item's to clean up, and flagging it would
    make the row fire forever on a repo that has one. Best-effort — a bad base or a non-repo returns
    an empty list, the same contract as `diff_numstat`, so the gate row goes quiet rather than red
    when git cannot answer."""
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
    """`git --numstat` output → {files, insertions, deletions, by_file}. Binary files report '-'
    for both counts — read as 0 rather than dropped, so the file still appears in `by_file`."""
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


# Every work-item branch lives under this prefix. Named once so the readers that need to tell a
# work branch from a real one (`list_branches`) can't drift from the writer.
BRANCH_PREFIX = "item"


def branch_name(item_id: str, title: str = "") -> str:
    slug = slugify(title)
    stem = f"{BRANCH_PREFIX}/{item_id}"
    return f"{stem}-{slug}" if slug else stem


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


def resolve_anchor(repo_dir: Path, configured: str | None = None) -> str:
    """The ANCHOR: the branch every git site works against — branch-from base, sync source, merge
    target (workflow-renovation-v2 §2.3). `configured` is the repo's `anchor_branch` setting; absent
    → the repo's own default branch.

    A configured branch that does not exist RAISES rather than falling back: the setting exists for
    repos where landing on the default branch is forbidden, so silently retargeting there is the one
    failure that matters."""
    if configured:
        if not branch_exists(repo_dir, configured):
            raise GitError(f"anchor branch '{configured}' does not exist in {repo_dir} — fix the "
                           "repo's anchor setting (SuperMe will not fall back to the default branch)")
        return configured
    return default_branch(repo_dir)


def branch_exists(repo_dir: Path, branch: str) -> bool:
    return _git(repo_dir, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0


def list_branches(repo_dir: Path) -> list[str]:
    """This repo's local branches, sorted by most recent commit. The anchor picker's option set:
    the anchor names a branch that must exist (`resolve_anchor` REFUSES rather than falling back),
    so offering the real list is what stops a typo becoming a merge-time failure.

    SuperMe's own per-item worktree branches are excluded — they are transient, one per work-item,
    and a repo mid-cohort would bury its trunk under a dozen of them. Never raises: a non-repo or a
    broken git returns [], and the caller shows no picker rather than an error."""
    p = _git(repo_dir, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)",
             "refs/heads/", check=False)
    if p.returncode != 0:
        return []
    return [b for b in (ln.strip() for ln in p.stdout.splitlines())
            if b and not b.startswith(f"{BRANCH_PREFIX}/")]


def commit_exists(repo_dir: Path, sha: str) -> bool:
    """Is this sha a real commit in this repo? The recorded `merge_commit` is the authoritative
    answer to 'was this item merged' — but a recorded sha that no longer resolves (a reverted or
    surgically-rewritten anchor) must read as NOT merged, or the item is stranded forever."""
    if not sha:
        return False
    return _git(repo_dir, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}",
                check=False).returncode == 0


def _is_merged(repo_dir: Path, branch: str, target: str, merge_commit: str | None) -> bool:
    """Has this branch already landed on `target`?

    The RECORDED merge commit is authoritative, because a SQUASH commit is not an ancestor of the
    branch it squashed — ancestry, which was the whole test before slice 4c, silently reads every
    squash-merged item as unmerged. Ancestry survives only as the fallback for items merged with
    the old `--no-ff` before a merge commit was ever recorded; it can produce a false NO under
    squash but never a false YES, so it is safe to keep and useless to rely on."""
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


# --- the commit gate --------------------------------------------------------------------

# Version marker: what tells an install "this file is mine to rewrite" apart from "someone else's
# hook, leave it alone". Bump it when the script below changes.
COMMIT_HOOK_MARKER = "superme-commit-msg v1"

# The mechanical half of the commit contract. Prose in build/SKILL.md got the trailer onto commits
# only after two live builds had already written none — a skill is a probability, and the review
# page's task walkthrough is not recoverable after the fact from a commit that never carried its
# task. So the rule is enforced where it can only be true or false.
#
# Deliberately ONE rule. Subject shape, wrapping, capitalization all stay prose: they degrade a
# reader's experience, while a missing trailer silently destroys a whole surface.
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
    """Install (or refresh) the commit-msg gate in `repo_dir`. Returns {installed, reason[, detail]}.

    Best-effort and honest about it — it REFUSES rather than breaking a project:

    - `foreign` — a `commit-msg` hook that isn't ours already exists. Clobbering it would silently
      disable a test gate or a team's convention, which is a far worse failure than the one this
      guards. The owner is told; chaining is theirs to decide.
    - `hooks_path_override` — the project sets `core.hooksPath` (husky and friends), so
      `.git/hooks` is never consulted. Installing there would LOOK enforced and do nothing, which
      is the one outcome worth refusing loudly.

    Worktrees share the main repo's hooks (`--git-common-dir`), so one install covers every item.
    """
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
        if hook.exists() and COMMIT_HOOK_MARKER not in hook.read_text():
            return {"installed": False, "reason": "foreign", "detail": str(hook)}
        hooks.mkdir(parents=True, exist_ok=True)
        hook.write_text(_COMMIT_MSG_HOOK)
        hook.chmod(0o755)
    except OSError as e:
        log.warning("commit hook not installed in %s: %s", repo_dir, e)
        return {"installed": False, "reason": "error", "detail": str(e)}
    return {"installed": True, "reason": "ok", "detail": str(hook)}


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
        # Refresh the commit gate every time, not once at connect: `.git/hooks` is untracked, so a
        # re-clone, a `git init` redo or a hand-deleted file would leave enforcement quietly absent.
        # Never fatal — a repo we can't gate still builds; the caller reports why it isn't gated.
        hook = install_commit_hook(repo_dir)
        return {"branch": branch, "worktree": str(wt), "base": base, "base_sha": base_sha,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "commit_hook": hook}


def create_scratch_worktree(repo_dir: Path, repo_id: str, item_id: str, *,
                            base: str | None = None) -> dict:
    """A DETACHED throwaway checkout for a read-only phase — no branch, nothing to merge.

    Why it exists (2026-08-14 security review): a research run reads real code, so its cwd was the
    real repository — and the OS sandbox makes a run's own cwd writable by design (`core.sandbox`:
    "a shell write inside the run's cwd succeeds"). The permission layer was therefore the ONLY
    thing between an investigation and the working tree, where a build phase has two walls: the
    same classifier PLUS a worktree nobody merges. This gives the read-only phases the second wall.

    Detached on purpose. A branch is the durable half of an implementation worktree — the thing a
    merge lands and a reconcile rebuilds from — and research merges nothing, so a branch here would
    be a promise of a landing that never comes (and would show up in `list_branches` as one). The
    record carries `branch: None`, which is how every downstream reader tells the two apart.

    Cheap to lose: if the dir is gone, the next run makes another. Existing dir → returned as is,
    so a re-entered phase keeps whatever the last one left in it."""
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
    """Every LISTENING process whose working directory is `wt` — the vet-env servers this worktree
    owns, identified by the one fact that survives everything else.

    Not by a remembered pid: a live run showed a worktree accumulating TWO servers (build started
    one and never stopped it; vet's `start` could not see it, took the next free port, and stacked a
    second), while the state file named only the last. Both outlived the deleted worktree. cwd is
    unforgeable and needs no bookkeeping to stay true."""
    try:
        out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=15)
        pids = sorted({int(x) for x in out.stdout.split() if x.isdigit()})
    except Exception:  # noqa: BLE001 — no lsof, or it failed; the caller falls back
        return []
    target = Path(wt).resolve()
    here: list[int] = []
    for pid in pids:
        try:
            r = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                               capture_output=True, text=True, timeout=10)
            cwds = [ln[1:] for ln in r.stdout.splitlines() if ln.startswith("n")]
            if cwds and Path(cwds[-1]).resolve() == target:
                here.append(pid)
        except Exception:  # noqa: BLE001 — the process died mid-scan, or is not ours to inspect
            continue
    return here


def stop_vet_env(wt: Path) -> list[int]:
    """Kill every vet-env server running in this worktree. Returns the pids signalled.

    Removing the dir takes the DB, the log and the `.env` symlink with it — untracked files and all.
    It does NOT take the PROCESS: a server left listening keeps its port and an open fd on a file
    that no longer has a name, and nothing else will ever reap it. This is the last line of defence,
    so it must run BEFORE the dir goes: afterwards, cwd no longer resolves and the servers become
    unfindable.

    Signals the process GROUP — `vet_env.sh` spawns with start_new_session, and a server that runs
    its worker in a child would otherwise keep the port. The state file is only a fallback for a
    host without `lsof`.

    Best-effort and never fatal: cleanup must not be able to block a terminal item from closing."""
    pids = servers_in(wt)
    if not pids:
        try:
            pid = int(json.loads((Path(wt) / ".vet-env.json").read_text()).get("pid") or 0)
            pids = [pid] if pid > 0 else []
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []
    stopped: list[int] = []
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                continue      # already gone
        stopped.append(pid)
    if stopped:
        log.info("stopped vet-env server(s) %s in %s", stopped, wt)
    return stopped


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
            stop_vet_env(wt)          # the dir takes the files; only this takes the process
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


def worktree_health(repo_dir: Path, repo_id: str, item_id: str, branch: str | None = None,
                    *, trunk: str | None = None, merge_commit: str | None = None) -> dict:
    """Health check for one item's git state: does the branch exist, does the dir exist, does
    git still register it, is the tree dirty, how far is the branch from trunk, is it merged.
    `trunk` is the repo's anchor (default: its own default branch) — ahead/behind and the diff
    shape are only meaningful against the branch this item will actually merge into.
    `merge_commit` is the item's RECORDED merge sha; pass it or `merged` is wrong for every
    squash-merged item (see `_is_merged`)."""
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
            health["ahead"] = int(ahead)    # branch commits not on trunk
            health["behind"] = int(behind)  # trunk commits not on branch (freshness debt)
        # Diff shape vs the merge base — the review brief's `diff` fact. Read from GIT, never from
        # a doc: the pre-demolition source was an agent-typed Stats yaml inside readiness.md, so
        # the number on the owner's merge-decision surface was whatever the agent typed and nothing
        # compared it to the tree. `--shortstat` over `trunk...branch` (three dots = merge base) is
        # the same range the merge will squash.
        stat = _git(repo_dir, "diff", "--shortstat", f"{trunk}...{branch}", check=False)
        if stat.returncode == 0:
            line = stat.stdout.strip()
            health["files"] = int(m.group(1)) if (m := re.search(r"(\d+) files? changed", line)) else 0
            health["insertions"] = int(m.group(1)) if (m := re.search(r"(\d+) insertion", line)) else 0
            health["deletions"] = int(m.group(1)) if (m := re.search(r"(\d+) deletion", line)) else 0
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
            wt = Path(rec.get("worktree") or worktree_dir(repo_id, item_id))
            # A branchless record is a SCRATCH tree (`create_scratch_worktree`): detached, nothing
            # to rebuild from and nothing that needs rebuilding — its next run makes another. Only
            # the registration is worth checking; the branch tests below would call every one of
            # them broken, on a repo where they are all perfectly healthy.
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
    """The ONE commit-message shape SuperMe writes, at every site that commits.

        <subject>
                                    ← blank line
        <body, wrapped at 72>
                                    ← blank line
        SuperMe-Item: 4f2a1b9c0d3e  ← the trailer block

    **The main message is for the project; the trailer block is for SuperMe.** Nothing
    SuperMe-shaped — no item ids, no task numbers, no phase names — belongs above the trailers: a
    reader of the project's history has never heard of this workspace. Everything SuperMe needs to
    join a commit back to its item goes below, in git's own `Key: value` trailer form, which
    `git interpret-trailers` and every forge already understand.

    Wrapping at 72 is what keeps the body readable in `git log` on an 80-column terminal."""
    import textwrap
    blocks = [subject.strip()]
    body = (body or "").strip()
    if body:
        blocks.append("\n".join(
            "\n".join(textwrap.wrap(para, 72)) if para.strip() else ""
            for para in body.split("\n")))
    # An absent fact is omitted, never written as "none". The trailers form ONE uninterrupted run
    # at the very end — git only recognizes them there.
    tail = "\n".join(f"{k}: {v}" for k, v in (trailers or {}).items() if v)
    if tail:
        blocks.append(tail)
    return "\n\n".join(blocks) + "\n"


# --- reading a branch back (the PR walkthrough) ------------------------------------------

# Field/record separators for the one `git log` call the walkthrough costs. ASCII's own record
# (0x1e) and unit (0x1f) separators: a commit body may contain anything printable, so a separator
# that cannot appear in one is the only parse that can't be broken by a commit message.
_REC, _FLD = "\x1e", "\x1f"

_TRAILER = re.compile(r"(?m)^([A-Za-z][A-Za-z0-9-]*):[ \t]*(.+?)[ \t]*$")


def fork_point(repo_dir: Path, branch: str, base: str) -> str:
    """Where `branch` left `base` — the walkthrough's floor. `merge-base`, not `base` itself,
    because the anchor keeps moving: diffing against today's anchor head would show every sibling
    item's work as though this branch had done it. Survives the squash merge, which is not an
    ancestor of the branch it squashed, so the fork point stays put after landing."""
    proc = _git(repo_dir, "merge-base", base, branch, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def branch_commits(repo_dir: Path, branch: str, base: str) -> list[dict]:
    """Every commit `branch` added over `base`, oldest first, with its trailers parsed and its
    per-file churn — the raw material of the task-grouped PR walkthrough.

    `--no-merges` drops the freshness syncs: a `sync_from_main` merge commit carries the anchor's
    work, which this item did not do, and its numstat would attribute it here.

    Returns [{sha, short, subject, body, trailers, files:[{path, plus, minus}]}]. Best-effort — a
    missing branch or a non-repo returns [], because a walkthrough is a view, never a gate."""
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
    """What this branch actually LANDS — the net diff from the fork point, not the sum of its
    commits' churn (a line added in t1 and rewritten in t3 lands once). Same shape as
    `diff_numstat`, computed from the bare repo so it still answers after the worktree is gone."""
    point = fork_point(repo_dir, branch, base)
    if not point:
        return {"files": 0, "insertions": 0, "deletions": 0, "by_file": []}
    proc = _git(repo_dir, "diff", "--numstat", f"{point}..{branch}", check=False)
    return _parse_numstat(proc.stdout if proc.returncode == 0 else "")


def commit_trailers(body: str) -> dict:
    """The `Key: value` block at the END of a commit body, as a dict. Only the final uninterrupted
    run counts — that is git's own rule, and it is what stops a `Note: see X` line in the middle of
    a paragraph from being read as metadata."""
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
    """The patches `shas` applied to ONE file, in order — the diff a walkthrough row expands to.

    Per-commit rather than one range diff: the commits of a task are not guaranteed to be
    contiguous on the branch, and a `first^..last` range would silently swallow whatever landed
    between them. Reading each commit's own patch is exact, and it reads better anyway — the file's
    story in the order it happened. `cap` truncates the total (a generated file can be enormous);
    the truncation is REPORTED, never silent."""
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
    """A UNIQUE pre-merge restore point. Microseconds, not seconds: two merge attempts inside the
    same second used to produce the SAME ref name, and the second `update-ref` overwrote the first
    backup with the post-merge head — silently destroying the only route back. Reachable since the
    merge became a squash, because a retry no longer short-circuits on ancestry."""
    return f"refs/backup/{item_id}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"


def recut_branch(repo_dir: Path, item_id: str, branch: str, base: str) -> dict:
    """Reset an item's branch back onto its BASE — the re-run's "start clean" in GIT (owner,
    2026-07-31). Returns {recut, backup_ref, from_sha, to_sha}.

    `base` is the ref the branch was originally cut from (the item's `git_base`: the trunk for a
    normal item, the parent's branch for a blocking child), and it is resolved to that ref's
    CURRENT tip — not to some sha recorded weeks ago. A re-run means "do this work again, starting
    now", so it should start where a brand-new item would; replaying from a stale point would hand
    the fresh attempt the same trunk debt the old one carried.

    Soft-deleting rows cleans what the owner SEES. This is the half that decides what the next
    attempt BUILDS ON: without it a re-run checks out a branch that still carries the discarded
    attempt's commits and continues from there, which is the definition of building on top of the
    failed version.

    NOTHING IS LOST. The old tip is written to `refs/backup/<item>-<ts>` FIRST — the same guardrail
    the merge uses — so every discarded commit stays reachable by sha and by ref. The branch ref
    itself is not deleted, only moved; the worktree must already be gone (the caller removes it),
    because resetting a branch that is checked out somewhere would leave that tree lying about its
    own history.

    Returns `recut: False` (never raises) when there is nothing to do — no branch, no base sha, or
    the branch is already sitting on the base. A re-run must not fail because git had nothing to
    undo."""
    repo_dir = Path(repo_dir)
    if not branch:
        return {"recut": False, "reason": "this item has no branch"}
    if not branch_exists(repo_dir, branch):
        return {"recut": False, "reason": f"branch {branch} does not exist"}
    base = base or resolve_anchor(repo_dir, None)   # pre-`git_base` items fall back to the anchor
    if _git(repo_dir, "rev-parse", "--verify", f"{base}^{{commit}}", check=False).returncode != 0:
        # The base ref is gone (renamed trunk, a parent branch deleted). Leaving the branch where
        # it is beats resetting it onto something we cannot name.
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
    """Pre-flight overlap detection: files dirty in the main tree AND touched by the branch —
    an auto-stash would hide a collision, so the merge refuses early on any intersection."""
    dirty = set(_dirty_files(repo_dir))
    if not dirty:
        return []
    touched = set(_out(repo_dir, "diff", "--name-only", f"{target}...{branch}").splitlines())
    return sorted(dirty & touched)


def merge_freshness(repo_dir: Path, worktree: Path, branch: str, *,
                    target: str | None = None) -> dict:
    """**The merge act owns freshness** (§2.3) — not review, which would guarantee it hours before
    it is used. One comparison, at the instant that matters: is the anchor already contained in this
    branch?

        anchor unmoved                        → {"action": "merge"}
        anchor moved → sync it into the branch
            conflict                          → {"action": "park", "conflicts": [...]}
            clean, anchor delta touches paths
                 this item also touched       → {"action": "revet", "paths": [...]}
            clean, no overlap                 → {"action": "merge", "synced": <sha>}

    Path overlap is the cheap mechanical middle between merging blind and re-vetting every open item
    after every sibling merge (which makes a cohort never converge). It knowingly MISSES
    action-at-a-distance — a shared config, an implicit contract two files agree on. Accepted.

    Conflicts are never auto-resolved: the item parks and pages, which under overnight autopilot is
    exactly the thing that should wake the owner."""
    repo_dir, worktree = Path(repo_dir), Path(worktree)
    target = resolve_anchor(repo_dir, target)
    if not worktree.is_dir():
        # No tree to sync into — the merge itself will report any conflict. Never silently skip.
        return {"action": "merge", "reason": "no live worktree to sync"}
    if _git(repo_dir, "merge-base", "--is-ancestor", target, branch, check=False).returncode == 0:
        return {"action": "merge"}
    # Both path sets are measured from the merge base BEFORE the sync — afterwards the branch
    # contains the anchor's commits and the two sets would no longer be distinguishable.
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
    """The ONE heavy merge: item branch → the anchor, in the MAIN repo, under the op lock.

    **SQUASH** (§2.3, owner-locked): the branch's per-task commits collapse into ONE commit on the
    anchor. They are never rewritten — the branch is kept after merge, so task granularity survives
    as trace while the project log carries one commit per item. `message` is that commit's message,
    composed by the caller (this module knows nothing about items); a default is used if absent.

    Pre-flights (D4, nimbalyst): both sides not mid-merge/rebase · never-merge-twice (the recorded
    `merged_commit`, see `_is_merged`) · overlap refusal · main's uncommitted changes auto-stashed
    unique-tagged and verify-popped (failure surfaced as `stash_warning`, never silent) · backup
    ref `refs/backup/<item>-<ts>` written BEFORE the merge (revert always offered).

    Conflict → fully unwound to the backup (a squash merge leaves no MERGE_HEAD, so `merge --abort`
    is not available — the reset to the backup ref is the unwind) and the conflicted paths returned;
    the Resolve-with-Agent path goes through sync_from_main in the WORKTREE instead (freshness rule:
    after the anchor is merged into the branch, this merge is trivial).

    Returns {merged, merge_commit, backup_ref, stash_warning?} | {already_merged: True}
    | {conflicts: [...]}. Raises GitError/GitBusy on refusals."""
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
        # Never merge twice. The caller passes the item's RECORDED merge sha; ancestry is only the
        # legacy fallback (a squash commit is not an ancestor of its branch — see `_is_merged`).
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
                # `merge --squash` records no MERGE_HEAD, so `merge --abort` cannot unwind it. The
                # backup ref was written one line ago and the tree was made clean by the stash, so
                # a hard reset to it restores the exact pre-merge state — including removing files
                # the squash staged into the index.
                _git(repo_dir, "reset", "--hard", backup, check=False)
                result = {"merged": False, "conflicts": conflicts, "backup_ref": backup}
            elif _git(repo_dir, "diff", "--cached", "--quiet", check=False).returncode == 0:
                # A clean squash that staged NOTHING means the branch's content is already on the
                # anchor — the crash-between-merge-and-record window, retried. Report it as the
                # no-op it is instead of failing on an empty commit, and drop the backup ref we
                # just wrote: nothing happened, so there is nothing to restore to.
                _git(repo_dir, "update-ref", "-d", backup, check=False)
                result = {"already_merged": True, "merged": False}
            else:
                _git(repo_dir, "commit", "-m", message or f"Merge branch {branch}")
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
    """Restore the anchor to its pre-merge state via the backup ref. SAFE-ONLY: refuses unless
    the backup commit is the first parent of the CURRENT head (i.e. nothing has landed on top) and
    the tree is clean — beyond that window, un-merging needs a human. Unaffected by the switch to
    squash: a squash commit's single parent IS the pre-merge anchor head, so the same check holds."""
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
        # Ancestry is CORRECT here and must stay: the light path is a real `--no-ff` merge into
        # the parent's BRANCH, not a squash into the anchor. Only the anchor merge squashes, so
        # only the anchor merge needed the recorded-sha migration (§2.3).
        if _git(repo_dir, "merge-base", "--is-ancestor", child_branch, state["branch"],
                check=False).returncode == 0:
            return {"already_merged": True, "merged": False}
        # The child's landing message follows the same shape as the anchor's — clean main message,
        # SuperMe facts in the trailer block. It reaches the PARENT's branch, which review and the
        # PR walkthrough both read, so branch names in the subject were workspace vocabulary in a
        # place a human reads.
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
    """Freshness rule (replaces rebasing ceremony): merge the trunk INTO the item branch, inside
    the worktree. Run agent-side during long builds and ALWAYS at review time — after this, the
    merge back to main is trivial. Conflicts: default aborts + reports; `leave_conflicts=True`
    leaves the conflicted merge IN the tree for the Resolve-with-Agent session (the human never
    hand-edits conflict markers; finish with `finish_merge`)."""
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
