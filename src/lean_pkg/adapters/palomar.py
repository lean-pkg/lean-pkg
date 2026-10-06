"""Palomar results API (https://data.palomar-registry.org), paged with `cursor`.

All pages are cached as one file per registry revision under
$XDG_CACHE_HOME/lean-pkg/palomar/<revision>.json, so a repeat search costs one
request; offline, the newest cached revision is searched and named as cached.
Titles, abstracts, theorem names and source repositories are searched.
"""

from __future__ import annotations

import json
import os
import urllib.parse

from . import cache_dir, hit, http_get, matches, ok, skipped

NAME = "palomar"
URL = "https://data.palomar-registry.org/api/v1/results?order=registered"
ENTRY_BASE = "https://data.palomar-registry.org/"
MAX_PAGES = 200
LIMIT = 30


def fetch(url: str, timeout: float = 10):
    return http_get(url, timeout=timeout)


def _page(url: str, timeout: float):
    got = fetch(url, timeout)
    if got is None or got[0] != 200:
        return None
    try:
        data = json.loads(got[1])
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("entries"), list) else None


def _cached() -> list[tuple[int, list]]:
    base = cache_dir("palomar")
    out = []
    if base.is_dir():
        for f in base.glob("*.json"):
            if f.stem.isdigit():
                try:
                    out.append((int(f.stem), json.loads(f.read_text())))
                except (ValueError, OSError):
                    continue
    return sorted(out, reverse=True)


def entries(timeout: float = 10) -> tuple[list | None, str | None, str | None]:
    """(entries, revision, reason when unavailable)."""
    first = _page(URL, timeout)
    if first is None:
        cached = _cached()
        if cached:
            return cached[0][1], f"{cached[0][0]} (cached)", None
        return None, None, "unreachable, and no cached results"
    rev = first.get("revision")
    f = cache_dir("palomar", f"{rev}.json")
    if isinstance(rev, int) and f.is_file():
        return json.loads(f.read_text()), str(rev), None
    rows, page, pages = list(first["entries"]), first, 1
    while page.get("next"):
        if pages >= MAX_PAGES:
            return None, str(rev), "too many pages"
        page = _page(URL + "&cursor=" + urllib.parse.quote(str(page["next"]), safe=""), timeout)
        if page is None:
            return None, str(rev), "paging interrupted"
        rows += page["entries"]
        pages += 1
    if isinstance(rev, int):
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(rows))
        os.replace(tmp, f)
    return rows, str(rev), None


def revision() -> str | None:
    cached = _cached()
    return str(cached[0][0]) if cached else None


def search(term: str, timeout: float = 10) -> dict:
    rows, rev, reason = entries(timeout)
    if rows is None:
        return skipped(reason, rev)
    found = []
    for e in rows:
        if not isinstance(e, dict):
            continue
        src = e.get("source") if isinstance(e.get("source"), dict) else {}
        thms = (e.get("formalization") or {}).get("theorem_names") if isinstance(e.get("formalization"), dict) else []
        if matches(term, e.get("title"), e.get("abstract"), " ".join(str(t) for t in thms or []),
                   src.get("repository")):
            found.append(hit(e.get("id"), e.get("title"), ENTRY_BASE + str(e.get("path") or ""),
                             repository=src.get("repository"), theorem_names=list(thms or [])))
    return ok(rev, found[:LIMIT], f"{len(rows)} results searched")
