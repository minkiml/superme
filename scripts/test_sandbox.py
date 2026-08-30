"""The OS-level shell sandbox.

Reading a command string cannot tell where it writes: `cd /elsewhere && rm -rf` is invisible. The
kernel gets the write roots the permission layer already holds, because two boundaries drift.

Run: PYTHONPATH=. python -m scripts.test_sandbox
"""

from pathlib import Path

from superme_agent.core.vocab.sandbox import sandbox_options
from scripts.sources import src

PASS = 0
ROOT = Path(__file__).resolve().parents[1]


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


# ── the policy ──────────────────────────────────────────────────────────────────────────────────

def test_policy():
    p = sandbox_options([])["sandbox"]
    ok("the sandbox is on", p["enabled"] is True)
    ok("a command cannot opt itself out", p["allowUnsandboxedCommands"] is False)
    ok("the permission callback still decides every shell command",
       p["autoAllowBashIfSandboxed"] is False)
    ok("a check may bind a local port — a dev server is verification, not an escape",
       p["network"]["allowLocalBinding"] is True)
    ok("no domain is allowed by default", not p["network"].get("allowedDomains"))


def test_fragment_contract():
    ok("an unsandboxed run gets nothing to spread", sandbox_options(None) == {})
    empty = sandbox_options([])
    ok("an EMPTY list is a real answer, not 'off'", empty.get("sandbox") is not None)
    ok("…writable in its cwd and nowhere else", empty["add_dirs"] == [])

    a = ROOT / "scripts"
    frag = sandbox_options([a, a, ROOT / "scripts" / "."])
    ok("declared roots reach the kernel as add_dirs", frag["add_dirs"] == [str(a.resolve())])
    ok("…deduped, so one root is not granted twice", len(frag["add_dirs"]) == 1)


# ── the wiring ──────────────────────────────────────────────────────────────────────────────────

def test_one_choke_point():
    svc = src("superme_agent/core/agent_service.py")
    ok("options carry the sandbox fragment", "**sandbox_options(sandbox_writes)" in svc)
    ok("…and the seam is a run_turn argument, not a guess about the phase",
       "sandbox_writes: list[Path] | None = None" in svc)


def test_code_touching_runs_are_sandboxed():
    loop = src("superme_agent/daemon/services/loop.py")
    ok("build + vet each define one write boundary", loop.count("boundary = [wt, item_dir]") == 2)
    # The X-ray reads the turn's own kwargs instead of restating the list.
    ok("...and both sandbox the shell to it, off the one list the turn is sent",
       loop.count("sandbox_writes=boundary") == 2)
    ok("...and hold file writes to that list too",
       loop.count("write_boundary=boundary") == 2)
    ok("...with the X-ray reading the turn's own kwargs rather than a second declaration",
       loop.count("surface_from_turn(turn_kwargs") == 2
       and "surface=turn_surface(" not in loop)

    runs = src("superme_agent/daemon/services/runs.py")
    # Three turns, each naming the item folder once now that the X-ray reads the turn's kwargs.
    ok("the three item-folder runners are sandboxed",
       runs.count("sandbox_writes=[item_dir") == 3)
    # The shell boundary matches the write sandbox: without one, every unprovable command is
    # refused with no path to allow.
    ok("...and the phase runners hand the shell the same folder they sandbox",
       runs.count("\n        write_boundary=[item_dir]") == 2)
    # The one place the lists may differ, and only by ADDING: the sandbox gets the union, never
    # less.
    ok("the research worktree reaches the shell and the kernel together",
       "shell_roots=scratch_tree" in runs
       and "sandbox_writes=[item_dir, *scratch_tree]" in runs)
    ok("conflict resolution is sandboxed to its worktree",
       "sandbox_writes=[worktree]" in runs)

    ok("the deputy judge is sandboxed with nothing writable outside its cwd",
       "sandbox_writes=[]" in src("superme_agent/daemon/services/deputy.py"))


def test_the_two_boundaries_cannot_drift():
    """Every sandboxed runner hands the sandbox the SAME roots it hands the permission layer."""
    loop = src("superme_agent/daemon/services/loop.py")
    for line in loop.splitlines():
        if "sandbox_writes=" in line:
            roots = line.split("sandbox_writes=")[1].split("#")[0].strip().rstrip(",")
            ok(f"the loop declares {roots} to both layers",
               f"write_boundary={roots}" in loop)

    runs = src("superme_agent/daemon/services/runs.py")
    ok("the item-folder runners scope the permission layer to that same folder",
       runs.count("scoped_writes_approve(item_dir, deny_all)") == 3)
    ok("…and resolution to that same worktree",
       "scoped_writes_approve(worktree, deny_all)" in runs)


def test_interactive_is_deliberately_not_sandboxed():
    ws = src("superme_agent/daemon/routers/ws.py")
    ok("a chat turn passes no sandbox — the human approving each command is the boundary",
       "sandbox_writes" not in ws)
    ok("…and the reason is written down where the policy lives",
       "Interactive turns are not sandboxed" in src("superme_agent/core/vocab/sandbox.py"))


def test_the_stale_claim_is_gone():
    perms = src("superme_agent/core/permissions.py")
    ok("permissions.py no longer claims the SDK has no sandbox",
       "no fs-sandbox mode" not in perms)
    ok("…and points at the layer that now holds the boundary", "core.vocab.sandbox" in perms)


def test_a_run_has_somewhere_to_put_a_temp_file():
    """A boundary saying only where you may NOT write is half a rule.

    The system temp dir is outside every granted root, so an item carries `scratch/`."""
    import asyncio
    import tempfile

    from superme_agent.core import permissions as perms
    from superme_agent.core.vocab.sandbox import SCRATCH_DIRNAME, ensure_scratch, prune_scratch

    item = Path(tempfile.mkdtemp()) / "item"
    item.mkdir()
    scratch = ensure_scratch(item)
    ok("the scratch dir lives inside the item folder, so the existing boundary already covers it",
       scratch.parent == item and scratch.is_dir())
    ok("…and calling it again is a no-op, which is what makes it safe on every run",
       ensure_scratch(item) == scratch)
    ok("…and it carries no marker file — the ignore rule lives once, at the knowledge root",
       not any(scratch.iterdir()))

    ok("an unused one is swept at run end, so being OFFERED the path costs the owner nothing",
       prune_scratch(item) and not scratch.exists())
    ensure_scratch(item)
    (scratch / "inventory.txt").write_text("x\n", encoding="utf-8")
    ok("…but a run that left files keeps them: a phase can be resumed, and rebuilding an "
       "inventory costs what building it cost",
       not prune_scratch(item) and (scratch / "inventory.txt").exists())
    ok("…and terminal takes it whole, which is what the preamble promises every phase",
       prune_scratch(item, only_if_empty=False) and not scratch.exists())

    ok("an item is NOT born with one — the tree that mints items never creates one, so nothing "
       "exists before a run could use it",
       "ensure_scratch" not in src("superme_agent/core/dev_knowledge.py"))
    ok("…the knowledge root ignores it, so no working file can reach a knowledge remote",
       "work-items/*/scratch/" in src("superme_agent/core/dev_knowledge.py"))
    ok("…a re-run clears it, so no attempt inherits the last one's half-built inventories",
       "sandbox.SCRATCH_DIRNAME" in src("superme_agent/core/dev_knowledge.py"))
    ok("…and going terminal removes it, so nothing survives the item that wrote it",
       "prune_scratch" in src("superme_agent/core/dev_knowledge.py"))
    ok("every run end sweeps an unused one, at the one place every run ends",
       "prune_scratch" in src("superme_agent/daemon/services/runs.py"))
    ok("every work-item turn is TOLD the path, by the block that owns the boundary rule",
       "ensure_scratch" in src("superme_agent/core/kernel_speech.py"))
    ensure_scratch(item)   # the boundary checks below need the directory back

    cut = perms.build_can_use_tool(perms.deny_all, write_boundary=[item],
                                   cwd=Path("/somewhere/else"))

    async def verdict(command: str):
        r = await cut("Bash", {"command": command}, None)
        return type(r).__name__ == "PermissionResultAllow", getattr(r, "message", "")

    allowed, _ = asyncio.run(verdict(f"cd {item} && ls | sort > {scratch}/x.txt"))
    ok("writing into it needs no approval — the whole point", allowed)

    refused, why = asyncio.run(verdict('sort -u > "$TMPDIR/names.txt"'))
    ok("reaching for $TMPDIR is still refused…", not refused)
    ok("…but the refusal names the legal alternative rather than just saying no",
       SCRATCH_DIRNAME in why)

    _, why_destructive = asyncio.run(verdict("rm -rf /Users/someone/real"))
    ok("a command reaching outside gets no such hint — advice that cannot be followed is worse "
       "than none", SCRATCH_DIRNAME not in why_destructive)


def test_the_shell_may_name_what_the_write_tools_may_not():
    """A sweep reads one tree and writes to another, so its honest commands name both.

    `shell_roots` widens what the SHELL may name without widening what any write tool may touch."""
    import asyncio
    import tempfile

    from superme_agent.core import permissions as perms
    from superme_agent.core.vocab.sandbox import ensure_scratch

    item = Path(tempfile.mkdtemp()) / "item"
    item.mkdir()
    scratch = ensure_scratch(item)
    wt = Path(tempfile.mkdtemp()) / "wt"
    wt.mkdir()
    # The shape that was refused: make the scratch dir, enter the tree, pipe an inventory out.
    both_trees = f"mkdir -p {scratch}\ncd {wt}\ngrep -rnE '^(def|class)' . | sort > {scratch}/s.txt"

    def gate(roots):
        return perms.build_can_use_tool(perms.deny_all, write_boundary=[item],
                                        shell_roots=roots, cwd=wt)

    async def verdicts():
        without = await gate(None)("Bash", {"command": both_trees}, None)
        with_ = await gate([wt])("Bash", {"command": both_trees}, None)
        outside = await gate([wt])("Bash", {"command": "rm -rf /Users/someone/real"}, None)
        into_tree = await gate([wt])("Write", {"file_path": str(wt / "x.py"), "content": "x"}, None)
        into_item = await gate([wt])("Write", {"file_path": str(item / "a.md"), "content": "x"},
                                     None)
        return [type(r).__name__ for r in (without, with_, outside, into_tree, into_item)]

    without, with_, outside, into_tree, into_item = asyncio.run(verdicts())
    ok("naming both trees is refused without a shell root — the live failure",
       without == "PermissionResultDeny")
    ok("…and allowed with one, which is the whole point", with_ == "PermissionResultAllow")
    ok("a path outside both is still refused — this widens, it does not open",
       outside == "PermissionResultDeny")
    ok("the write TOOLS never follow the shell into it: research changes no code",
       into_tree == "PermissionResultDeny")
    ok("…while the item folder stays theirs", into_item == "PermissionResultAllow")


def test_a_search_pattern_is_not_a_place():
    """The boundary reads a command's `/…` tokens as paths, and a regex is spelled like one.

    A sweep whose files were in bounds was refused for its own grep pattern."""
    import asyncio
    import tempfile

    from superme_agent.core import permissions as perms
    from superme_agent.core.vocab.sandbox import ensure_scratch

    item = Path(tempfile.mkdtemp()) / "item"
    item.mkdir()
    s = ensure_scratch(item)
    wt = Path(tempfile.mkdtemp()) / "wt"
    wt.mkdir()
    gate = perms.build_can_use_tool(perms.deny_all, write_boundary=[item],
                                    shell_roots=[wt], cwd=wt)

    async def verdicts():
        cases = [
            # Every FILE is in bounds; only the pattern looks like a path.
            f"grep -v '/generated/' {s}/all.txt | grep -E '\\.(py|ts)$' > {s}/src.txt",
            f"sed -n '/^def /p' {s}/all.txt > {s}/defs.txt",
            f"awk '/^import/ {{print $2}}' {s}/all.txt > {s}/imports.txt",
            f"grep -e '/x/' {s}/all.txt > {s}/hits.txt",
            # A pattern argument is dropped, never a real one: these still name a path outside.
            f"grep -v x /etc/passwd > {s}/leak.txt",
            f"grep -v x {s}/all.txt > /etc/evil",
        ]
        return [type(await gate("Bash", {"command": c}, None)).__name__ for c in cases]

    v = asyncio.run(verdicts())
    ok("a slash-bearing grep/sed/awk pattern no longer reads as an out-of-bounds path",
       v[:4] == ["PermissionResultAllow"] * 4)
    ok("the file arguments are still checked — an outside READ target is refused",
       v[4] == "PermissionResultDeny")
    ok("…and so is an outside WRITE target", v[5] == "PermissionResultDeny")


def test_research_cannot_reach_the_codebase():
    """A research sweep reads a throwaway checkout and writes only its own folder.

    Every way out is closed at the callback, and the kernel holds the same two roots."""
    import asyncio
    import tempfile

    from superme_agent.core import permissions as perms
    from superme_agent.core.vocab.sandbox import sandbox_options

    item = Path(tempfile.mkdtemp()) / "item"
    item.mkdir()
    wt = Path(tempfile.mkdtemp()) / "wt"
    wt.mkdir()
    REPO = "/Users/someone/project"
    gate = perms.build_can_use_tool(perms.deny_all, write_boundary=[item], shell_roots=[wt], cwd=wt)

    async def verdicts():
        cases = [
            ("Write", {"file_path": f"{REPO}/src/core.py", "content": "x"}),
            ("Write", {"file_path": str(wt / "src/core.py"), "content": "x"}),
            ("Edit", {"file_path": f"{REPO}/src/api.py", "old_string": "a", "new_string": "b"}),
            ("Bash", {"command": f'sqlite3 {REPO}/state.db "DELETE FROM run"'}),
            ("Bash", {"command": f"rm -rf {REPO}/src"}),
            ("Bash", {"command": f"echo bad > {REPO}/config.yaml"}),
            ("Bash", {"command": "cd " + str(wt) + " && git push origin HEAD"}),
            ("Bash", {"command": "cd " + str(wt) + " && git commit --no-verify -m x"}),
        ]
        out = [type(await gate(t, a, None)).__name__ for t, a in cases]
        allowed = await gate("Write", {"file_path": str(item / "record.md"), "content": "ok"}, None)
        return out, type(allowed).__name__

    denied, own_folder = asyncio.run(verdicts())
    ok("no write tool reaches real source, the read checkout, or anything outside the item folder",
       denied[:3] == ["PermissionResultDeny"] * 3)
    ok("no shell command reaches a database, a delete, or a redirect outside the boundary",
       denied[3:6] == ["PermissionResultDeny"] * 3)
    ok("a push is refused — the one mutation that escapes a filesystem boundary by not touching it",
       denied[6] == "PermissionResultDeny")
    ok("…and the commit gate cannot be skipped", denied[7] == "PermissionResultDeny")
    ok("what it CAN do is write its own record", own_folder == "PermissionResultAllow")
    ok("and the kernel is handed those same two roots, nothing wider",
       sandbox_options([item, wt])["add_dirs"] == [str(item.resolve()), str(wt.resolve())])


def test_the_kernel_counts_what_it_refused():
    """A report cannot claim no tool was unavailable when calls were refused.

    Each refusal is read once, hundreds of calls earlier, so the count is what the agent cannot
    reconstruct."""
    import asyncio
    import re

    from superme_agent.core import permissions as perms

    cut = perms.build_can_use_tool(perms.deny_all, write_boundary=[Path("/tmp/nowhere")],
                                    cwd=Path("/somewhere/else"))

    async def tallies():
        out = []
        for _ in range(3):
            r = await cut("Bash", {"command": "rm -rf /Users/someone/real"}, None)
            m = re.search(r"refused so far this run: (\d+)", getattr(r, "message", ""))
            out.append(int(m.group(1)) if m else None)
        allowed = await cut("Bash", {"command": "ls"}, None)
        return out, type(allowed).__name__

    counts, allowed_kind = asyncio.run(tallies())
    ok("the tally rises with each refusal, so the last one carries the run's total",
       counts == [1, 2, 3])
    ok("an allowed call is untouched — this counts refusals, it does not narrate every decision",
       allowed_kind == "PermissionResultAllow")
    ok("the report template asks for that number rather than the agent's recollection",
       "refused" in src("superme_agent/harness/plugins/superme-dev/skills/investigate/"
                        "templates/report-investigate-template.md"))


def test_a_kernel_device_is_not_a_place():
    """`/dev/null` names a device, not a directory anyone can be inside. Asking whether it sits in
    a write boundary is a category error, and answering `no` refused real work."""
    from superme_agent.core import permissions as perms

    roots = [Path("/tmp/boundary")]
    ok("discarding output does not make a command escape its boundary",
       not perms._bash_escapes_boundary("make test 2>/dev/null", roots))
    ok("…nor does it count as the in-boundary path that scopes one",
       not perms._bash_scoped_into_boundary("echo x > /dev/null", roots))
    ok("a real path outside still escapes — the exemption is for devices only",
       perms._bash_escapes_boundary("cat /etc/hosts", roots))


def test_an_escaping_command_names_the_path_that_escaped():
    """A Bash command naming ONE path outside the boundary is told which one.

    Live 2026-08-30: a vet probe mistyped an item id in a `mkdir`, so an otherwise-legal command
    escaped. The refusal it got was the approval fallback — "not a judgment on this particular
    call" — which was false, and vet gave up a check rather than fixing one character."""
    from superme_agent.core.permissions import _bash_escaping_paths
    wt = Path("/tmp/wt/item-a")
    item = Path("/tmp/knowledge/work-items/item-a")
    roots = [wt, item]

    typo = (f'cd {wt}\n'
            f'mkdir -p /tmp/knowledge/work-items/item-b/scratch 2>/dev/null\n'
            f'mkdir -p {item}/scratch')
    out = _bash_escaping_paths(typo, roots)
    ok("the escaping path is identified", len(out) == 1)
    ok("...and it is the mistyped sibling, not the legal paths",
       out and out[0].endswith("item-b/scratch"))
    ok("the same command with the typo fixed does not escape",
       not _bash_escaping_paths(typo.replace("item-b", "item-a"), roots))
    ok("a pseudo-device is not an escape",
       not _bash_escaping_paths("echo x 2>/dev/null", roots))
    ok("a relative path is not an escape",
       not _bash_escaping_paths("python ./probe.py", roots))
    ok("every escaping path is reported, not just the first",
       len(_bash_escaping_paths("cp /etc/a /var/b", roots)) == 2)

    # The message has to reach the agent, or naming the path changes nothing.
    from superme_agent.core import permissions as _p
    msg = _p._BASH_ESCAPES_BOUNDARY.format(paths="`/tmp/x`", roots="`/tmp/wt`",
                                           wall=_p._BOUNDARY_WALL)
    ok("the message quotes the offending path", "/tmp/x" in msg)
    ok("...and says one path refuses the whole command", "whole command" in msg)
    ok("...and the wall rule is stated ONCE, formatted into both refusals",
       _p._BOUNDARY_WALL in msg
       and _p._BOUNDARY_WALL not in _p._BASH_ESCAPES_BOUNDARY
       and "{wall}" in _p._BASH_OUTSIDE_BOUNDARY)


def test_a_literal_path_shortcut_is_seen_through():
    """`ITEM=/abs/path; cat "$ITEM/f"` is judged on the path, not refused for using a variable.

    Live 2026-08-30: 4 of one run's 5 refusals were an agent reading its OWN folder through a
    shortcut it had just assigned. The same command with the path spelled out twice is allowed.

    ONLY a plain literal assignment is followed. The controls below are the point: a shortcut can
    hide a command (`X=$(...)`) or walk back out (`$X/../..`), and following either would flip the
    error from 'refuses too much' to 'allows outside the boundary'."""
    from superme_agent.core.permissions import (_expand_literal_assignments,
                                                _bash_escaping_paths,
                                                _bash_scoped_into_boundary, is_read_only_bash)
    item = Path("/tmp/knowledge/work-items/item-a")
    roots = [item]

    # THE CASE THIS EXISTS FOR — the command that was refused live.
    real = (f'ITEM={item}; echo "--item--"; cat "$ITEM/item.md" 2>/dev/null; '
            f'ls "$ITEM/artifacts"')
    ok("the shortcut is followed to its path", str(item) in _expand_literal_assignments(real))
    ok("...so the read reads as read-only", is_read_only_bash(_expand_literal_assignments(real)))
    ok("...and as scoped into the boundary",
       _bash_scoped_into_boundary(_expand_literal_assignments(real), roots))
    ok("...and it does not escape", not _bash_escaping_paths(_expand_literal_assignments(real), roots))

    # CONTROL 1 — walking back out of the boundary must still escape.
    out = f'ITEM={item}; cat "$ITEM/../../../etc/passwd"'
    ok("a shortcut walked back OUT of the boundary still escapes",
       bool(_bash_escaping_paths(_expand_literal_assignments(out), roots)))
    ok("...and is not read as scoped in",
       not _bash_scoped_into_boundary(_expand_literal_assignments(out), roots))

    # CONTROL 2 — a shortcut holding a COMMAND is never followed.
    for hidden in (f'ITEM=$(cat /etc/secret); cat "$ITEM/f"',
                   f'ITEM=`cat /etc/secret`; cat "$ITEM/f"',
                   f'ITEM=${{OTHER}}/x; cat "$ITEM/f"'):
        ok(f"a shortcut hiding a command is left alone: {hidden[:22]}",
           "$ITEM" in _expand_literal_assignments(hidden))

    # CONTROL 3 — an assignment to a RELATIVE path proves nothing about where it lands.
    rel = 'ITEM=../elsewhere; cat "$ITEM/f"'
    ok("a relative shortcut is not followed", "$ITEM" in _expand_literal_assignments(rel))

    # CONTROL 4 — the last assignment wins, and an unassigned name stays unexpanded.
    two = f'ITEM=/tmp/other; ITEM={item}; cat "$ITEM/f"'
    ok("a reassigned shortcut takes its LAST value",
       _expand_literal_assignments(two).rstrip().endswith(f'cat "{item}/f"'))
    ok("a name that was never assigned is left alone",
       "$NOPE" in _expand_literal_assignments('cat "$NOPE/f"'))

    # CONTROL 5 — expansion is for JUDGING only; a command outside stays outside.
    outside = 'P=/etc; cat "$P/passwd"'
    ok("expanding does not smuggle an outside path in",
       bool(_bash_escaping_paths(_expand_literal_assignments(outside), roots)))

    # CONTROL 6 — the one place this LOOSENS: an assignment naming an outside path that the
    # command never uses. Naming a path is not touching it, so this is allowed on purpose.
    unused = f'P=/etc; cat {item}/f'
    ok("an outside path that is assigned but never used no longer blocks the command",
       not _bash_escaping_paths(_expand_literal_assignments(unused), roots))
    ok("...but the moment it IS used, it escapes again",
       bool(_bash_escaping_paths(_expand_literal_assignments(f'P=/etc; cat "$P/passwd"'), roots)))

    # CONTROL 7 — `${ITEM}` is the same shortcut as `$ITEM`.
    braced = f'ITEM={item}; cat "${{ITEM}}/item.md"'
    ok("the braced form is followed too", str(item) in _expand_literal_assignments(braced))


def main() -> None:
    test_policy()
    test_fragment_contract()
    test_one_choke_point()
    test_code_touching_runs_are_sandboxed()
    test_the_two_boundaries_cannot_drift()
    test_interactive_is_deliberately_not_sandboxed()
    test_the_stale_claim_is_gone()
    test_a_run_has_somewhere_to_put_a_temp_file()
    test_the_shell_may_name_what_the_write_tools_may_not()
    test_research_cannot_reach_the_codebase()
    test_the_kernel_counts_what_it_refused()
    test_a_kernel_device_is_not_a_place()
    test_an_escaping_command_names_the_path_that_escaped()
    test_a_literal_path_shortcut_is_seen_through()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
