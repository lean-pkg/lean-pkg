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
_WORD = re.compile(r"[\W_]+", re.UNICODE)


def fold(text) -> str:
    """NFKD, strip combining marks, casefold: Itô -> ito, Black–Scholes keeps its words. The one folding
    used by every search source (lean_pkg.search imports it), applied to the term and the text alike."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()


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


def ok(revision: str | None, hits: list[dict], reason: str | None = None,
       partial: list[str] | None = None, capped: list[str] | None = None,
       cap_limit: int | None = None, total: int | None = None) -> dict:
    """`partial` names the parts of the source that failed (GitHub's `code` search): the source answered,
    but a zero from it is not a negative result. `capped` names the parts that returned exactly their
    result limit `cap_limit`: more matches may exist beyond the ones listed. `total` is how many matches the
    source found when it lists fewer: more than len(hits) marks the `results` part capped at len(hits)."""
    r = {"state": "ok", "reason": reason, "revision": revision, "hits": hits}
    if partial:
        r["partial"] = list(partial)
    capped = list(capped or [])
    if isinstance(total, int) and total > len(hits):
        capped.append("results")
        cap_limit = cap_limit or len(hits)
        r["total"] = total
    if capped:
        r["capped"], r["cap_limit"] = capped, cap_limit
    return r


def skipped(reason: str, revision: str | None = None) -> dict:
    return {"state": "skipped", "reason": reason, "revision": revision, "hits": []}


def one_line(s, limit: int = 200) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= limit else s[:limit - 3] + "..."


def hit(name, summary, url, **extra) -> dict:
    return {"name": str(name), "summary": one_line(summary), "url": url, "extra": extra}


def words(text) -> list[str]:
    return [w for w in _WORD.split(fold(text)) if w]


def matches(term: str, *texts) -> bool:
    """Every word of the term occurs in the texts (substring, case- and accent-insensitive: both sides
    folded), like a `grep -i` per word over descriptions: 'Fisher-Tippett' matches 'Fisher-Tippett-Gnedenko',
    'Godel' matches 'Gödel'."""
    hay = fold(" ".join(str(t or "") for t in texts))
    ws = words(term)
    return bool(ws) and all(w in hay for w in ws)


def cache_dir(*parts: str) -> pathlib.Path:
    """$XDG_CACHE_HOME/lean-pkg/<parts> (default ~/.cache/lean-pkg/<parts>)."""
    base = os.environ.get("XDG_CACHE_HOME", "").strip() or str(pathlib.Path.home() / ".cache")
    return pathlib.Path(base).expanduser().joinpath("lean-pkg", *parts)


def docs_url(decl: str) -> str:
    return "https://leanprover-community.github.io/mathlib4_docs/find/?pattern=" + \
        urllib.parse.quote(decl, safe="") + "#doc"
