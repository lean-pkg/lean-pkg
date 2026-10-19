"""`--json` keeps stdout for the JSON document alone (data on stdout, chatter on stderr).

A command that streams progress while it works (stage lines, judge output, warnings) runs that work
under `chatter_to_stderr()` and prints its JSON document after the block. Inside the block both the
Python-level `sys.stdout` and file descriptor 1 point at stderr, so a `print()` without
`file=sys.stderr`, a log echoed to `sys.stdout`, and a child process that inherits fd 1 all land on
stderr; `json.loads(stdout)` then parses what the caller captured.
"""

from __future__ import annotations

import contextlib
import os
import sys
from typing import Iterator


@contextlib.contextmanager
def chatter_to_stderr(active: bool = True) -> Iterator[None]:
    """While active, send everything written to stdout (Python and fd 1) to stderr; restore after."""
    if not active:
        yield
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    saved = None
    try:
        saved = os.dup(1)
        os.dup2(2, 1)
    except OSError:                    # no fd 1 or fd 2 (a closed descriptor): Python-level only
        if saved is not None:
            os.close(saved)
        saved = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except (OSError, ValueError):
                pass
        if saved is not None:
            os.dup2(saved, 1)
            os.close(saved)
