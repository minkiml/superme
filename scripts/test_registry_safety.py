"""The registry is the one record with no history, so losing it loses every connected repo.

A snapshot before each write and once per boot, and a check for work on disk with no entry.

Run: PYTHONPATH=. python -m scripts.test_registry_safety
"""

import tempfile
from pathlib import Path

from superme_agent.core.spine import RepoConfig, SystemSpine
from superme_agent.core.spine.base import REGISTRY_BACKUPS

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def backups(d: Path) -> list[Path]:
    return sorted(d.glob("repos-*.yaml"))


def spine_at(tmp: Path, backup_dir: Path) -> SystemSpine:
    """A spine on a throwaway registry, with the backup dir redirected out of the install."""
    import superme_agent.core.spine.base as base
    base.REPOS_BACKUP_DIR = backup_dir
    return SystemSpine(db_path=tmp / "s.db", system_config=tmp / "system.yaml",
                       repos_config=tmp / "repos.yaml")


def seed_registry(tmp: Path) -> Path:
    p = tmp / "repos.yaml"
    p.write_text("repos:\n  global:\n    label: Hub\n    cwd: '.'\n    layer: global\n",
                 encoding="utf-8")
    return p


def test_snapshots(tmp: Path) -> None:
    print("\nsnapshots — the registry has no git history to fall back on")
    bdir = tmp / "backups"
    reg = seed_registry(tmp)
    s = spine_at(tmp, bdir)
    ok("boot writes a snapshot", len(backups(bdir)) == 1, str(backups(bdir)))
    ok("…named for the reason", backups(bdir)[0].name.endswith("-boot.yaml"))
    ok("…holding the registry verbatim",
       backups(bdir)[0].read_text(encoding="utf-8") == reg.read_text(encoding="utf-8"))

    spine_at(tmp, bdir)
    ok("an unchanged registry is not copied again", len(backups(bdir)) == 1,
       "restarts would otherwise evict the older copies that hold what a boot copy cannot")

    # The pre-write state being on disk is the property, not a file per write.
    s.add_repo(RepoConfig(id="alpha", label="alpha", cwd=str(tmp / "alpha")))
    ok("after add_repo, the registry without it is still recoverable",
       any("alpha" not in b.read_text(encoding="utf-8") for b in backups(bdir)))

    s.update_repo("alpha", label="Alpha")
    ok("update_repo copies the pre-edit registry", backups(bdir)[-1].name.endswith("-update.yaml"))
    ok("…and that copy holds the value being overwritten",
       "label: alpha" in backups(bdir)[-1].read_text(encoding="utf-8"))

    s.remove_repo("alpha")
    ok("remove_repo copies first", backups(bdir)[-1].name.endswith("-remove.yaml"))
    ok("…so the entry it drops survives the disconnect",
       "alpha" in backups(bdir)[-1].read_text(encoding="utf-8"),
       "this is the copy a mistaken disconnect is restored from")
    ok("every copy is uniquely named, so none overwrites another",
       len({b.name for b in backups(bdir)}) == len(backups(bdir)))

    for i in range(REGISTRY_BACKUPS + 4):
        reg.write_text(reg.read_text(encoding="utf-8") + f"# {i}\n", encoding="utf-8")
        s.snapshot_registry("boot")
    ok(f"pruned to the newest {REGISTRY_BACKUPS}", len(backups(bdir)) == REGISTRY_BACKUPS,
       str(len(backups(bdir))))

    missing = tmp / "gone"
    s2 = SystemSpine(db_path=tmp / "s2.db", system_config=tmp / "system.yaml",
                     repos_config=missing / "repos.yaml")
    ok("no registry yet → nothing to snapshot and no crash", s2.snapshot_registry("boot") is None)


def test_orphans(tmp: Path) -> None:
    print("\norphaned repos — work on disk that the registry does not list")
    import superme_agent.paths as paths

    import superme_agent.core.git_layer as git_layer

    know = tmp / "knowledge"
    (know / "global-knowledge").mkdir(parents=True)
    (know / "alpha-knowledge").mkdir()
    (know / "ghost-knowledge").mkdir()
    (know / "buried-knowledge").mkdir()
    (know / "loose-file.txt").write_text("x", encoding="utf-8")
    paths.KNOWLEDGE_REPO_DIR = know
    git_layer.DEFAULT_WORKTREES_HOME = tmp / "wt"

    seed_registry(tmp)
    s = spine_at(tmp, tmp / "backups2")
    s.add_repo(RepoConfig(id="alpha", label="alpha", cwd=str(tmp / "alpha")))
    s.add_repo(RepoConfig(id="buried", label="buried", cwd=str(tmp / "buried")))
    s.remove_repo("buried")           # a real disconnect leaves a tombstone

    found = {r["repo_id"] for r in s.orphaned_repos()}
    ok("a knowledge home with no entry is an orphan", found == {"ghost"}, str(found))
    ok("a registered repo is not", "alpha" not in found)
    ok("a DISCONNECTED repo is not — its tombstone explains it", "buried" not in found,
       "otherwise every deliberate disconnect would page the owner forever")
    ok("the hub is not", "global" not in found)
    ok("a loose file is not a repo", "loose-file.txt" not in found)

    ok("the orphan carries the evidence that proves it existed",
       s.orphaned_repos()[0]["evidence"] == [str(know / "ghost-knowledge")])

    # An empty repo dir is what `remove_worktree` leaves behind.
    (git_layer.DEFAULT_WORKTREES_HOME / "spent").mkdir(parents=True, exist_ok=True)
    ok("an empty worktree home is not stranded work",
       "spent" not in {r["repo_id"] for r in s.orphaned_repos()})
    (git_layer.DEFAULT_WORKTREES_HOME / "spent" / "item-x").mkdir(parents=True, exist_ok=True)
    ok("...but one that still holds an item is",
       "spent" in {r["repo_id"] for r in s.orphaned_repos()})

    paths.KNOWLEDGE_REPO_DIR = tmp / "nope"
    git_layer.DEFAULT_WORKTREES_HOME = tmp / "nope2"
    ok("no knowledge repo at all → no orphans and no crash", s.orphaned_repos() == [])



def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a"; a.mkdir()
        test_snapshots(a)
        b = Path(td) / "b"; b.mkdir()
        test_orphans(b)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
