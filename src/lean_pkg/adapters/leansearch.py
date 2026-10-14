"""LeanSearch (https://leansearch.net): natural-language search over Mathlib.

Checked 2026-10-06: `POST https://leansearch.net/search` with
{"query": [term], "num_results": N} answers a list (one per query) of
{"result": {...}, "distance": x}; `GET /api/search` answers 404. A 404 or any
other non-200 answer is `skipped`, so a moved API never reads as zero hits. The
reason says what to do: a 5xx is the service failing (2026-10-14: every query
answered 500 while the site's own page sent this exact request), a 404 means the
endpoint moved and this adapter needs updating.
"""

from __future__ import annotations

import json

from . import docs_url, hit, http_get, ok, skipped

NAME = "leansearch"
URL = "https://leansearch.net/search"
LIMIT = 10


def fetch(url: str, body: bytes, timeout: float = 10):
    return http_get(url, body=body, timeout=timeout)


def revision() -> str:
    return "live"


def status_reason(status: int) -> str:
    """The skip reason for a non-200 answer, keeping `HTTP <status>` first for anything matching on it."""
    if 500 <= status <= 599:
        return f"HTTP {status}: service error at leansearch.net; retry later"
    if status == 404:
        return f"HTTP {status}: API moved ({URL} not found); the leansearch adapter needs updating"
    return f"HTTP {status}"


def _name(v) -> str:
    return ".".join(str(x) for x in v) if isinstance(v, list) else str(v or "")


def search(term: str, timeout: float = 10) -> dict:
    body = json.dumps({"query": [term], "num_results": LIMIT}).encode("utf-8")
    got = fetch(URL, body, timeout)
    if got is None:
        return skipped("unreachable")
    status, raw = got
    if status != 200:
        return skipped(status_reason(status))
    try:
        data = json.loads(raw)
        rows = data[0] if isinstance(data, list) and data and isinstance(data[0], list) else None
    except ValueError:
        rows = None
    if rows is None:
        return skipped("unexpected answer")
    hits = []
    for row in rows[:LIMIT]:
        r = row.get("result") if isinstance(row, dict) else None
        if not isinstance(r, dict) or not _name(r.get("name")):
            continue
        name = _name(r["name"])
        hits.append(hit(name, r.get("informal_name") or r.get("docstring") or r.get("signature"),
                        docs_url(name), kind=r.get("kind"), module=_name(r.get("module_name")),
                        distance=row.get("distance")))
    return ok(revision(), hits)
