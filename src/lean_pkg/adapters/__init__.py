# lean-pkg search adapters -- one module per public source
"""Network adapters for `lean-pkg search`, one module each: loogle, leansearch,
reservoir, palomar, github. Each exposes `NAME`, `search(term, timeout=10) -> dict`
and `revision() -> str | None`.

A result is {"state": "ok" | "skipped", "reason": str | None, "revision": str | None,
"hits": [{"name", "summary", "url", "extra"}]}. An unreachable source is `skipped`
with a reason, never zero hits. With state `ok`, `reason` may carry a note.

All I/O goes through `http_get` and `run_cmd` below, which call the module
attributes `urlopen` and `subprocess_run`; the test suite replaces both so no test
can reach the network or run `gh`.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "lean-pkg"
urlopen = urllib.request.urlopen
subprocess_run = subprocess.run
_WORD = re.compile(r"[^0-9a-z]+")


def http_get(url: str, body: bytes | None = None, timeout: float = 10) -> tuple[int, bytes] | None:
    """(status, body bytes); an HTTP error gives (status, b""); None when unreachable.
    With `body`, the request is a POST of JSON."""
    headers = {"User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read()
    except urllib.error.HTTPError as e:
        return int(e.code), b""
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def run_cmd(argv: list[str], timeout: float = 120) -> tuple[int, bytes, str] | None:
    """(returncode, stdout bytes, stderr text); None when the program is not installed."""
    if shutil.which(argv[0]) is None:
        return None
    try:
        p = subprocess_run(argv, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, b"", type(e).__name__
    return p.returncode, p.stdout, p.stderr.decode("utf-8", errors="replace")


def ok(revision: str | None, hits: list[dict], reason: str | None = None) -> dict:
    return {"state": "ok", "reason": reason, "revision": revision, "hits": hits}


def skipped(reason: str, revision: str | None = None) -> dict:
    return {"state": "skipped", "reason": reason, "revision": revision, "hits": []}


def one_line(s, limit: int = 200) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= limit else s[:limit - 3] + "..."


def hit(name, summary, url, **extra) -> dict:
    return {"name": str(name), "summary": one_line(summary), "url": url, "extra": extra}


def words(text) -> list[str]:
    return [w for w in _WORD.split(str(text or "").lower()) if w]


def matches(term: str, *texts) -> bool:
    """Every word of the term occurs in the texts (substring, case-insensitive), like a
    `grep -i` per word over descriptions: 'Fisher-Tippett' matches 'Fisher-Tippett-Gnedenko'."""
    hay = " ".join(str(t or "") for t in texts).lower()
    ws = words(term)
    return bool(ws) and all(w in hay for w in ws)


def cache_dir(*parts: str) -> pathlib.Path:
    """$XDG_CACHE_HOME/lean-pkg/<parts> (default ~/.cache/lean-pkg/<parts>)."""
    base = os.environ.get("XDG_CACHE_HOME", "").strip() or str(pathlib.Path.home() / ".cache")
    return pathlib.Path(base).expanduser().joinpath("lean-pkg", *parts)


def docs_url(decl: str) -> str:
    return "https://leanprover-community.github.io/mathlib4_docs/find/?pattern=" + \
        urllib.parse.quote(decl, safe="") + "#doc"
