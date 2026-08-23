"""This process's own output streams, in UTF-8.

Windows encodes stdout as the locale when it is a pipe, which is cp1252 on a Western install,
and every surface here prints box glyphs. Entry points call this before writing anything.
"""

import sys


def utf8_streams() -> None:
    """Re-encode stdout and stderr as UTF-8. A stream a test has replaced is left alone."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
