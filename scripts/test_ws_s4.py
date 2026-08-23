"""The git layer, driven end to end against a throwaway repo.

Transactional worktree create and its unwind, a kill mid-create healed by reconciliation,
freshness merge, backup-ref revert, a manufactured conflict resolved in-tree, and the freeze
boundary refusing an outside write.

Run: PYTHONPATH=. python -m scripts.test_ws_s4
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from superme_agent.core import git_layer as G
from superme_agent.core.permissions import build_can_use_tool

PASS = 0
RID = "proj"   # the repo ID keying the worktree home (repos.yaml id, not the folder name)


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True, encoding="utf-8").stdout


def make_repo(tmp: Path) -> Path:
    repo = tmp / "proj"
    repo.mkdir()
    sh(repo, "git", "init", "-b", "main")
    sh(repo, "git", "config", "user.email", "t@t")
    sh(repo, "git", "config", "user.name", "t")
    (repo / "app.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-m", "init")
    return repo


def commit_all(cwd: Path, msg: str, task: str = "t1") -> None:
    """Commit as a BUILD AGENT does, with the task trailer.

    A fresh worktree installs the commit-msg gate, so a bare message is refused by the repo it
    just made."""
    sh(cwd, "git", "add", "-A")
    sh(cwd, "git", "commit", "-m", f"{msg}\n\nSuperMe-Task: {task}")


def test_lifecycle(repo: Path) -> None:
    print("worktree lifecycle")
    rec = G.create_worktree(repo, RID, "aaa111", "My Feature!")
    wt = Path(rec["worktree"])
    ok("branch name slugged", rec["branch"] == "item/aaa111-my-feature", rec["branch"])
    ok("worktree under the owned home keyed by repo id",
       wt.is_dir() and wt.parent == G.worktrees_home() / RID, str(wt))
    # Not inside the repo, which `git clean -fdx` would eat, and not the owner's parent dir.
    ok("worktree neither in-repo nor a repo sibling",
       repo not in wt.parents and wt.parent.parent != repo.parent, str(wt))
    ok("record has base sha", rec["base"] == "main" and len(rec["base_sha"]) == 40)
    # Transactional unwind: same item again → dir exists → refuse, and NO stray second branch.
    try:
        G.create_worktree(repo, RID, "aaa111", "My Feature!")
        ok("duplicate create refused", False)
    except G.GitError:
        ok("duplicate create refused", True)
    # Commits in the worktree land on the item branch only.
    (wt / "feature.py").write_text("new\n", encoding="utf-8")
    commit_all(wt, "feat")
    ok("worktree commit on branch, not main",
       "feature.py" in sh(repo, "git", "ls-tree", "--name-only", rec["branch"])
       and "feature.py" not in sh(repo, "git", "ls-tree", "--name-only", "main"))
    h = G.worktree_health(repo, RID, "aaa111", rec["branch"])
    ok("health: ok · ahead 1 · not merged",
       h["ok"] and h["ahead"] == 1 and not h["merged"] and h["registered"], str(h))
    # Kill-mid-create / deleted dir: branch survives, dir gone → reconcile re-adds it.
    import shutil
    shutil.rmtree(wt)
    acts = G.reconcile(repo, RID, {"aaa111": {"branch": rec["branch"], "worktree": rec["worktree"]}})
    ok("reconcile recreated missing dir",
       any(a["action"] == "recreated" for a in acts) and wt.is_dir(), str(acts))
    # Broken record (branch deleted) → reported, never guessed.
    G.remove_worktree(repo, RID, "aaa111")
    sh(repo, "git", "branch", "-D", rec["branch"])
    acts = G.reconcile(repo, RID, {"aaa111": {"branch": rec["branch"], "worktree": rec["worktree"]}})
    ok("reconcile reports missing branch as broken",
       any(a["action"] == "broken" for a in acts), str(acts))


def test_merge_flow(repo: Path) -> None:
    print("merge machinery (heavy path)")
    rec = G.create_worktree(repo, RID, "bbb222", "merge me")
    wt = Path(rec["worktree"])
    (wt / "merged.py").write_text("payload\n", encoding="utf-8")
    commit_all(wt, "work")
    # Freshness: land an unrelated commit on main, then sync it INTO the branch.
    (repo / "other.txt").write_text("trunk moved\n", encoding="utf-8")
    commit_all(repo, "trunk work")
    res = G.sync_from_main(repo, wt)
    ok("freshness merge brings trunk in",
       res["merged"] and (wt / "other.txt").exists(), str(res))
    ok("second sync is up-to-date noop", G.sync_from_main(repo, wt).get("up_to_date") is True)
    # Merge to main with an UNRELATED dirty file in main → auto-stash + verified pop.
    (repo / "scratchpad.txt").write_text("uncommitted note\n", encoding="utf-8")
    res = G.merge_to_main(repo, RID, "bbb222", rec["branch"])
    ok("merged to main with backup ref",
       res["merged"] and res["backup_ref"].startswith("refs/backup/bbb222-"), str(res))
    ok("stash pop verified (dirty file survived, no warning)",
       (repo / "scratchpad.txt").exists() and not res.get("stash_warning"))
    ok("merge commit on main", "merged.py" in sh(repo, "git", "ls-tree", "--name-only", "main"))
    ok("never-merge-twice", G.merge_to_main(repo, RID, "bbb222", rec["branch"])["already_merged"] is True)
    # Revert restores the pre-merge head…
    pre = sh(repo, "git", "rev-parse", res["backup_ref"]).strip()
    (repo / "scratchpad.txt").unlink()  # clean tree for the revert
    rev = G.revert_merge(repo, res["backup_ref"])
    ok("revert restored pre-merge head",
       rev["reverted"] and sh(repo, "git", "rev-parse", "main").strip() == pre)
    # …re-merge, land a commit ON TOP → revert must refuse (safe-only window).
    res2 = G.merge_to_main(repo, RID, "bbb222", rec["branch"])
    (repo / "later.txt").write_text("landed after\n", encoding="utf-8")
    commit_all(repo, "after merge")
    try:
        G.revert_merge(repo, res2["backup_ref"])
        ok("revert refused once commits landed on top", False)
    except G.GitError:
        ok("revert refused once commits landed on top", True)
    # Overlap refusal: dirty file in main that the NEXT branch also touches.
    rec2 = G.create_worktree(repo, RID, "ccc333", "overlap")
    wt2 = Path(rec2["worktree"])
    (wt2 / "app.py").write_text("line1\nbranch change\nline3\n", encoding="utf-8")
    commit_all(wt2, "branch touches app.py")
    (repo / "app.py").write_text("line1\nline2\nline3\ndirty main edit\n", encoding="utf-8")
    try:
        G.merge_to_main(repo, RID, "ccc333", rec2["branch"])
        ok("overlap detected → merge refused", False)
    except G.GitError as e:
        ok("overlap detected → merge refused", "app.py" in str(e), str(e))
    sh(repo, "git", "checkout", "--", "app.py")
    G.remove_worktree(repo, RID, "ccc333")


def test_conflict_resolution(repo: Path) -> None:
    print("conflict → in-tree resolution (the Resolve-with-Agent mechanical path)")
    rec = G.create_worktree(repo, RID, "ddd444", "conflict")
    wt = Path(rec["worktree"])
    (wt / "app.py").write_text("line1\nBRANCH version\nline3\n", encoding="utf-8")
    commit_all(wt, "branch side")
    (repo / "app.py").write_text("line1\nMAIN version\nline3\n", encoding="utf-8")
    commit_all(repo, "main side")
    # Default: conflict aborts + reports, tree left clean.
    res = G.sync_from_main(repo, wt)
    ok("conflict aborted + reported",
       not res["merged"] and res["conflicts"] == ["app.py"] and not res["in_tree"], str(res))
    ok("worktree clean after abort", G.check_git_state(wt)["ok"] and not G.check_git_state(wt)["dirty"])
    # Resolve path: leave in tree → finish_merge refuses on markers → resolve → finishes.
    res = G.sync_from_main(repo, wt, leave_conflicts=True)
    ok("conflict left in tree for resolution", res["in_tree"] and G.conflicted_files(wt) == ["app.py"])
    try:
        G.finish_merge(wt)
        ok("finish refused while markers remain", False)
    except G.GitError as e:
        ok("finish refused while markers remain", "app.py" in str(e), str(e))
    (wt / "app.py").write_text("line1\nMERGED version\nline3\n", encoding="utf-8")   # the agent's resolution
    fin = G.finish_merge(wt)
    ok("finish completed the merge", fin["merged"] and not G.check_git_state(wt)["in_merge"])
    # Freshness rule payoff: the merge back to main is now trivial.
    res = G.merge_to_main(repo, RID, "ddd444", rec["branch"])
    ok("post-resolution main merge trivial", res["merged"], str(res))
    G.remove_worktree(repo, RID, "ddd444")


def test_family(repo: Path) -> None:
    print("blocking-child family (light path)")
    prec = G.create_worktree(repo, RID, "eee555", "parent")
    pwt = Path(prec["worktree"])
    (pwt / "parent.py").write_text("parent work\n", encoding="utf-8")
    commit_all(pwt, "parent work")
    # Blocking child branches FROM the parent's branch → it sees parent's unmerged work.
    crec = G.create_worktree(repo, RID, "fff666", "child", base=prec["branch"])
    cwt = Path(crec["worktree"])
    ok("child based on parent branch", (cwt / "parent.py").exists() and crec["base"] == prec["branch"])
    (cwt / "child.py").write_text("child work\n", encoding="utf-8")
    commit_all(cwt, "child work")
    # Light merge into the parent's branch — main untouched, no backup ceremony.
    res = G.merge_into_parent(repo, crec["branch"], pwt)
    ok("child merged into parent branch",
       res["merged"] and res["target"] == prec["branch"] and "backup_ref" not in res, str(res))
    ok("main untouched by light merge",
       "child.py" not in sh(repo, "git", "ls-tree", "--name-only", "main"))
    # Family merges to main ONCE, through the parent.
    res = G.merge_to_main(repo, RID, "eee555", prec["branch"])
    names = sh(repo, "git", "ls-tree", "--name-only", "main")
    ok("family merged to main once", res["merged"] and "parent.py" in names and "child.py" in names)
    G.remove_worktree(repo, RID, "fff666")
    # Terminal: dir removed + verified, branch ref SURVIVES.
    out = G.remove_worktree(repo, RID, "eee555")
    ok("terminal removed dir (verified)", out["removed"] and out["verified"] and not pwt.exists())
    ok("branch ref survives terminal", G.branch_exists(repo, prec["branch"]))


def test_freeze_boundary(repo: Path, tmp: Path) -> None:
    print("freeze boundary (build-phase write confinement)")
    rec = G.create_worktree(repo, RID, "ggg777", "freeze")
    wt = Path(rec["worktree"])
    item_dir = tmp / "devroot" / "work-items" / "ggg777"
    item_dir.mkdir(parents=True)

    async def never(_t, _i):  # the surface ApproveFn must NOT be consulted during a freeze
        raise AssertionError("freeze boundary escalated to approval")

    can = build_can_use_tool(never, write_boundary=[wt, item_dir])

    async def check(tool, inp):
        return await can(tool, inp, None)

    r = asyncio.run(check("Write", {"file_path": str(wt / "inside.py"), "content": "x"}))
    ok("write inside worktree auto-allows", type(r).__name__ == "PermissionResultAllow")
    r = asyncio.run(check("Edit", {"file_path": str(item_dir / "artifacts" / "plan.md")}))
    ok("write inside item dir auto-allows", type(r).__name__ == "PermissionResultAllow")
    r = asyncio.run(check("Write", {"file_path": str(repo / "app.py"), "content": "x"}))
    ok("write into MAIN repo hard-denied",
       type(r).__name__ == "PermissionResultDeny" and "boundary" in r.message)
    r = asyncio.run(check("Write", {"file_path": "/tmp/elsewhere.txt", "content": "x"}))
    ok("write anywhere else hard-denied", type(r).__name__ == "PermissionResultDeny")
    r = asyncio.run(check("Write", {"content": "no path at all"}))
    ok("pathless write fails closed", type(r).__name__ == "PermissionResultDeny")

    # A phase agent owns its shell, or tests and commits park on a human and the loop stalls.
    can_sh = build_can_use_tool(never, cwd=wt, write_boundary=[wt, item_dir])

    async def shell(cmd):
        return await can_sh("Bash", {"command": cmd}, None)

    for cmd in ("pytest -q", "python -m pytest tests/ -v", "git add -A && git commit -m 'x'",
                "npm install", "mkdir -p src/pkg", f"pytest {wt}/tests"):
        r = asyncio.run(shell(cmd))
        ok(f"shell auto-allows in-boundary: {cmd[:28]}", type(r).__name__ == "PermissionResultAllow")

    # An absolute path outside the boundary must escalate, not auto-allow.
    for cmd in (f"rm -rf {repo}", "cat /etc/passwd > /tmp/leak.txt", "rm -rf /"):
        try:
            asyncio.run(shell(cmd))
            ok(f"out-of-boundary shell escalates: {cmd[:28]}", False, "auto-allowed!")
        except AssertionError as e:
            ok(f"out-of-boundary shell escalates: {cmd[:28]}", "escalated to approval" in str(e))

    # No boundary (a plain chat session) → unchanged: a mutating command still escalates.
    can_plain = build_can_use_tool(never, cwd=wt)
    try:
        asyncio.run(can_plain("Bash", {"command": "pytest -q"}, None))
        ok("no boundary → mutating shell still escalates", False, "auto-allowed!")
    except AssertionError as e:
        ok("no boundary → mutating shell still escalates", "escalated to approval" in str(e))

    # cwd OUTSIDE the boundary → the boundary grants nothing (the worktree is the whole guarantee).
    can_out = build_can_use_tool(never, cwd=repo, write_boundary=[wt, item_dir])
    try:
        asyncio.run(can_out("Bash", {"command": "pytest -q"}, None))
        ok("cwd outside boundary → shell escalates", False, "auto-allowed!")
    except AssertionError as e:
        ok("cwd outside boundary → shell escalates", "escalated to approval" in str(e))

    # Read-only shell is allowed everywhere, including the redirect agents reach for on ordinary
    # reads.
    r = asyncio.run(can_plain("Bash", {"command": "grep -rn foo . 2>&1"}, None))
    ok("read-only shell with 2>&1 auto-allows", type(r).__name__ == "PermissionResultAllow")


    # Stopping the host is denied everywhere: a run is the daemon's child, so a restart is fatal
    # from inside one.
    from superme_agent.core.permissions import kills_the_host
    for cmd, want in [
        ("kill $(lsof -ti:8787 -sTCP:LISTEN)", True),
        ("kill $(lsof -ti:8787) $(lsof -ti:8000)", True),
        ("pkill -f superme_agent.daemon", True),
        ("pkill -f web.bff", True),
        ("kill $(lsof -ti:8801)", False),          # a vet env's own teardown must stay allowed
        ("kill -9 12345", False),
        ("kill $(lsof -ti:18787)", False),         # digit-delimited: not the host port
        ("lsof -ti:8787", False),                  # looking is not killing
        ("curl -s http://127.0.0.1:8787/openapi.json", False),
    ]:
        ok(f"host-kill {'denied' if want else 'allowed'}: {cmd[:34]}", kills_the_host(cmd) is want)
    G.remove_worktree(repo, RID, "ggg777")


def test_locks_and_guards(repo: Path) -> None:
    print("op lock + state guards")
    lock = G._lock_for(repo)
    lock.acquire()
    try:
        G.merge_to_main(repo, RID, "zzz", "item/zzz")
        ok("busy repo raises GitBusy", False)
    except G.GitBusy:
        ok("busy repo raises GitBusy", True)
    finally:
        lock.release()
    ok("non-repo health fails soft", G.worktree_health(Path("/tmp"), RID, "x")["ok"] is False)
    try:
        G.create_worktree(Path("/tmp"), RID, "x", "t")
        ok("create on non-repo refused", False)
    except G.GitError:
        ok("create on non-repo refused", True)


def _spawn_sleeper() -> subprocess.Popen:
    """A stand-in for a vet-env daemon: its own session (so it is a process-group leader, exactly
    what `vet_env.sh` spawns) and long enough to still be alive when we go to kill it."""
    return subprocess.Popen(["sleep", "60"], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _settle(probe, want: int = 1, tries: int = 60):
    """Poll until a spawned listener is actually bound. Reading the port table once races the
    process's own startup and reports a leak that is only a millisecond of lag."""
    out = []
    for _ in range(tries):
        out = probe()
        if len(out) >= want:
            return out
        time.sleep(0.05)
    return out


def _reaped(p: subprocess.Popen, tries: int = 60) -> bool:
    """SIGTERM is asynchronous — poll rather than read the pid table once and call it a leak."""
    for _ in range(tries):
        if p.poll() is not None:
            return True
        time.sleep(0.05)
    return p.poll() is not None


def _spawn_listener(cwd: Path) -> subprocess.Popen:
    """A stand-in vet-env SERVER: listens on an ephemeral port with its working directory set to
    `cwd`, in its own session — the shape `servers_in` identifies by."""
    code = ("import http.server,socketserver;"
            "socketserver.TCPServer(('127.0.0.1',0),http.server.BaseHTTPRequestHandler)"
            ".serve_forever()")
    return subprocess.Popen([sys.executable, "-c", code], cwd=str(cwd), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_general_session_shell() -> None:
    """A general session's shell ASKS; it does not refuse.

    The classifier can prove read-only or prove nothing, so refusing the unprovable refused reading
    the project's own data. The file-WRITE tools still hard-deny: they name their target."""
    print("general-session shell (ask, don't refuse)")
    asked: list[str] = []

    async def approve(_tool, inp):
        asked.append(inp.get("command", ""))
        return True

    async def never(_tool, _inp):
        raise AssertionError("a provably read-only command escalated to approval")

    root = Path("/tmp/gen-write-root")
    can_ask = build_can_use_tool(approve, gate_general_mutations=True, general_write_root=root)
    can_never = build_can_use_tool(never, gate_general_mutations=True, general_write_root=root)

    # Provably read-only → straight through, no prompt (`never` raises if it escalates).
    for cmd in ("ls -la", "grep -rn TODO .", "git log --oneline -5", "cat data.db"):
        r = asyncio.run(can_never("Bash", {"command": cmd}, None))
        ok(f"read-only runs unprompted: {cmd[:28]}", type(r).__name__ == "PermissionResultAllow")

    # Unprovable means ASK: a SELECT is exactly as unprovable as a DROP, so refusing both refuses
    # to read.
    for cmd in ('sqlite3 app.db "SELECT count(*) FROM run"', 'psql -c "SELECT 1"',
                'python -c "print(1)"'):
        asked.clear()
        r = asyncio.run(can_ask("Bash", {"command": cmd}, None))
        ok(f"unprovable asks the owner: {cmd[:28]}",
           type(r).__name__ == "PermissionResultAllow" and asked == [cmd])

    # The owner's NO is still a no.
    async def refuse(_tool, _inp):
        return False
    can_no = build_can_use_tool(refuse, gate_general_mutations=True, general_write_root=root)
    r = asyncio.run(can_no("Bash", {"command": "rm -rf build/"}, None))
    ok("owner declining is a deny", type(r).__name__ == "PermissionResultDeny")

    # Write TOOLS keep refusing outright — no prompt can grant one, and the nudge says itemize.
    r = asyncio.run(can_never("Write", {"file_path": "/repo/app.py", "content": "x"}, None))
    ok("write tool still hard-denied in a general session",
       type(r).__name__ == "PermissionResultDeny" and "itemiz" in r.message.lower())
    # …except into the general/ memory home, which stays auto-allowed.
    r = asyncio.run(can_never("Write", {"file_path": str(root / "architecture.md"), "content": "x"}, None))
    ok("general/ memory write still auto-allows", type(r).__name__ == "PermissionResultAllow")

    # The two standing refusals outrank the prompt: neither may be approved into existence.
    for cmd, label in (("git commit --no-verify -m x", "hook bypass"),
                       ("kill $(lsof -t -i:8787)", "host kill")):
        r = asyncio.run(can_ask("Bash", {"command": cmd}, None))
        ok(f"{label} denied even in a session that asks", type(r).__name__ == "PermissionResultDeny")


def test_denial_truth() -> None:
    """A denial is the agent's only account of what happened.

    The three ways an ask can end must not share one sentence: a timed-out card reporting a
    refusal invites the agent to invent a rule. Pins the SHAPES."""
    print("denial messages — three endings, three facts")
    from superme_agent.core.permissions import (APPROVAL_UNANSWERED, NO_HUMAN_TO_ASK, deny_all,
                                                learning_write_approve)

    async def refuse(_t, _i):
        return False

    async def silence(_t, _i):
        return APPROVAL_UNANSWERED

    cmd = {"command": "rm -rf build/"}
    owner = asyncio.run(build_can_use_tool(refuse)("Bash", cmd, None)).message
    unanswered = asyncio.run(build_can_use_tool(silence)("Bash", cmd, None)).message
    nobody = asyncio.run(build_can_use_tool(deny_all)("Bash", cmd, None)).message

    ok("three endings, three different messages", len({owner, unanswered, nobody}) == 3)
    ok("the owner's refusal names the owner", "owner" in owner.lower())
    # Silence must not read as somebody's decision, and must head off the hunt for a cause.
    ok("an unanswered ask denies nothing and blames nobody",
       "nobody refused" in unanswered.lower() and "unanswered" in unanswered.lower())
    ok("an unanswered ask forbids inventing a blocker",
       all(w in unanswered.lower() for w in ("rule", "don't go looking")))
    ok("a background run is told the gate is shut for the whole run",
       "background run" in nobody.lower() and "whole run" in nobody.lower())
    # Each denial caps the reply too: the essay after it was the visible damage.
    for label, msg in (("owner refusal", owner), ("unanswered ask", unanswered)):
        ok(f"{label} asks for one line back", "one line" in msg.lower())
    ok("the owner's refusal bans the alternatives menu and the theory",
       "no list of other things" in owner.lower() and "no theory" in owner.lower())

    # A scoped background policy speaks for ITSELF rather than borrowing the owner's voice.
    scoped = asyncio.run(build_can_use_tool(learning_write_approve(Path("/tmp/forge-ws")))(
        "Write", {"file_path": "/repo/app.py", "content": "x"}, None)).message
    ok("an out-of-scope background write says scope, not refusal",
       "scratch workspace" in scoped and "owner" not in scoped.lower())


def test_vet_env(repo: Path, tmp: Path) -> None:
    """`stop_vet_env` and its call site: removing the worktree takes everything but the PROCESS.

    A server belongs to a worktree because its CWD is that worktree, never because a state file
    remembers its pid."""
    wt = tmp / "novet"
    wt.mkdir()
    ok("empty dir → no servers found", G.servers_in(wt) == [])
    ok("no state file → nothing signalled", G.stop_vet_env(wt) == [])
    (wt / ".vet-env.json").write_text("{not json", encoding="utf-8")
    ok("unreadable state → nothing signalled", G.stop_vet_env(wt) == [])
    (wt / ".vet-env.json").write_text('{"port": 8800}', encoding="utf-8")
    ok("state without a pid → nothing signalled", G.stop_vet_env(wt) == [])
    (wt / ".vet-env.json").write_text('{"pid": 0, "port": 8800}', encoding="utf-8")
    ok("pid 0 → nothing signalled", G.stop_vet_env(wt) == [])

    dead = _spawn_sleeper()
    dead.kill(); dead.wait()
    (wt / ".vet-env.json").write_text(f'{{"pid": {dead.pid}, "port": 8800}}', encoding="utf-8")
    ok("already-dead pid → nothing signalled", G.stop_vet_env(wt) == [])

    live = _spawn_sleeper()
    (wt / ".vet-env.json").write_text(f'{{"pid": {live.pid}, "port": 8800}}', encoding="utf-8")
    ok("state-file fallback signals the recorded pid", G.stop_vet_env(wt) == [live.pid])
    ok("state-file fallback actually kills it", _reaped(live))

    # Found by CWD with no state file at all — the case the state file cannot cover.
    srv = _spawn_listener(wt)
    (wt / ".vet-env.json").unlink(missing_ok=True)
    found = _settle(lambda: G.servers_in(wt))
    ok("listener found by cwd, with no state file", found == [srv.pid], f"{found} vs {srv.pid}")
    ok("stopped by cwd", G.stop_vet_env(wt) == [srv.pid])
    ok("stopped by cwd → actually dead", _reaped(srv))

    # TWO servers in one worktree — the live failure. The state file can only ever name one.
    a, b = _spawn_listener(wt), _spawn_listener(wt)
    (wt / ".vet-env.json").write_text(f'{{"pid": {a.pid}, "port": 8800}}', encoding="utf-8")
    found = _settle(lambda: G.servers_in(wt), want=2)
    ok("both servers found", sorted(found) == sorted([a.pid, b.pid]), str(found))
    ok("both signalled", sorted(G.stop_vet_env(wt)) == sorted([a.pid, b.pid]))
    ok("both actually dead", _reaped(a) and _reaped(b),
       "the one the state file did not name is exactly the one that leaked live")

    # The wiring: terminal cleanup must stop it, not just delete the directory around it.
    G.create_worktree(repo, RID, "vetenv", "vet env")
    real = G.worktree_dir(RID, "vetenv")
    server = _spawn_listener(real)
    _settle(lambda: G.servers_in(real))
    res = G.remove_worktree(repo, RID, "vetenv")
    ok("remove_worktree removed the dir", res["removed"] and res["verified"])
    ok("remove_worktree killed the vet-env server", _reaped(server),
       "a server outliving its worktree keeps the port and an fd on an unlinked DB")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Point the worktree home at the throwaway dir, so a run never touches the real one.
        os.environ["SUPERME_WORKTREES_HOME"] = str(tmp / "worktrees-home")
        repo = make_repo(tmp)
        test_lifecycle(repo)
        test_merge_flow(repo)
        test_conflict_resolution(repo)
        test_family(repo)
        test_freeze_boundary(repo, tmp)
        test_locks_and_guards(repo)
        test_general_session_shell()
        test_denial_truth()
        test_vet_env(repo, tmp)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
