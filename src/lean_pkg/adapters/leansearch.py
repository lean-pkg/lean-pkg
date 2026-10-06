"""LeanSearch (https://leansearch.net): natural-language search over Mathlib.

Checked 2026-10-06: `POST https://leansearch.net/search` with
{"query": [term], "num_results": N} answers a list (one per query) of
{"result": {...}, "distance": x}; `GET /api/search` answers 404. A 404 or any
other non-200 answer is `skipped`, so a moved API never reads as zero hits.
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


def _name(v) -> str:
    return ".".join(str(x) for x in v) if isinstance(v, list) else str(v or "")


def search(term: str, timeout: float = 10) -> dict:
    body = json.dumps({"query": [term], "num_results": LIMIT}).encode("utf-8")
    got = fetch(URL, body, timeout)
    if got is None:
        return skipped("unreachable")
    status, raw = got
    if status != 200:
        return skipped(f"HTTP {status}")
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
