"""Loogle (https://loogle.lean-lang.org): type-shaped search over Mathlib master."""

from __future__ import annotations

import json
import urllib.parse

from . import docs_url, hit, http_get, ok, skipped

NAME = "loogle"
URL = "https://loogle.lean-lang.org/json?q={q}"
LIMIT = 20


def fetch(url: str, timeout: float = 10):
    return http_get(url, timeout=timeout)


def revision() -> str:
    return "live (Mathlib master)"


def search(term: str, timeout: float = 10) -> dict:
    got = fetch(URL.format(q=urllib.parse.quote(term, safe="")), timeout)
    if got is None:
        return skipped("unreachable")
    status, body = got
    if status != 200:
        return skipped(f"HTTP {status}")
    try:
        data = json.loads(body)
    except ValueError:
        return skipped("answer is not JSON")
    if not isinstance(data, dict):
        return skipped("unexpected answer")
    if data.get("error"):
        return skipped("query not understood: " + str(data["error"]).splitlines()[0][:120])
    rows = [h for h in data.get("hits") or [] if isinstance(h, dict) and h.get("name")]
    hits = [hit(h["name"], h.get("doc") or h.get("type"), docs_url(h["name"]),
                module=h.get("module"), type=" ".join(str(h.get("type") or "").split()))
            for h in rows[:LIMIT]]
    count = data.get("count")
    total = count if isinstance(count, int) and not isinstance(count, bool) else len(rows)
    # More matches than listed (Loogle's count, or rows past LIMIT) is a capped answer, not a full one.
    return ok(revision(), hits, None, total=max(total, len(rows)))
