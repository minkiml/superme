"""How to launch a console tool that is not a real executable.

On Windows `npm` and `claude` are `.cmd` shims, which only a shell can run.
"""

import os
import shutil


def argv(tool: str, *args: str) -> list[str] | None:
    """The argv that runs `tool`, or None when it is not installed.

    A `.cmd` or `.bat` goes through COMSPEC, since exec cannot run one directly."""
    exe = shutil.which(tool)
    if not exe:
        return None
    if os.name == "nt" and os.path.splitext(exe)[1].lower() in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", exe, *args]
    return [exe, *args]
