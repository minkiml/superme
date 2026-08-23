"""How to launch a console tool that is not necessarily a real executable.

On Windows, `npm`, `npx` and `claude` install as `.cmd` shims. `CreateProcess` appends `.exe`
when it searches PATH and cannot execute a batch file at all, so passing the bare name — or even
the resolved `.cmd` path — fails with WinError 193 rather than running the tool.
"""

import os
import shutil


def argv(tool: str, *args: str) -> list[str] | None:
    """The argv that runs `tool`, or None when it is not installed.

    Resolves through PATH first: `shutil.which` honours PATHEXT, so it is what finds a shim in the
    first place. A batch shim is then handed to the interpreter that can run one.
    """
    exe = shutil.which(tool)
    if not exe:
        return None
    if os.name == "nt" and os.path.splitext(exe)[1].lower() in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", exe, *args]
    return [exe, *args]
