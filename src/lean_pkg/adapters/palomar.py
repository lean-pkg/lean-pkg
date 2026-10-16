"""Palomar results API (https://data.palomar-registry.org), paged with `cursor`.

All pages are cached as one file per registry revision under
$XDG_CACHE_HOME/lean-pkg/palomar/snapshots-v1/<revision>.json, so a repeat search costs one
request; offline, the newest cached revision is searched and named as cached.
Titles, abstracts, theorem names and source repositories are searched.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
import urllib.parse

from . import cache_dir, hit, http_get, matches, note_cache_write, ok, skipped

NAME = "palomar"
URL = "https://data.palomar-registry.org/api/v1/results?order=registered"
ENTRY_BASE = "https://data.palomar-registry.org/"
MAX_PAGES = 200
LIMIT = 30
CACHE_SCHEMA = "lean-pkg-palomar-cache/1"


def _cache_file(revision: int) -> pathlib.Path:
    # Keep envelopes separate from old bare-list caches, including old running clients.
    return cache_dir("palomar", "snapshots-v1", f"{revision}.json")


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
    return data if isinstance(data, dict) and _valid_rows(data.get("entries")) else None


def _valid_rows(rows) -> bool:
    return isinstance(rows, list) and all(
        isinstance(row, dict) and isinstance(row.get("id"), str) and bool(row["id"].strip())
        for row in rows)


def _valid_revision(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_cursor(page: dict) -> bool:
    return "next" in page and (page["next"] is None or
                                isinstance(page["next"], str) and bool(page["next"].strip()))


def _read_cache(path: pathlib.Path) -> list | None:
    try:
        payload = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA:
        return None                       # old bare lists have no snapshot-consistency evidence
    rev, pages, rows = payload.get("revision"), payload.get("pages"), payload.get("entries")
    if not _valid_revision(rev) or str(rev) != path.stem or \
            not isinstance(pages, int) or isinstance(pages, bool) or pages < 1 or \
            payload.get("complete") is not True or not _valid_rows(rows):
        return None
    return rows if payload.get("entries_sha256") == _rows_hash(rows) else None


def _rows_hash(rows: list) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()


def _cached() -> list[tuple[int, list]]:
    base = cache_dir("palomar", "snapshots-v1")
    out = []
    if base.is_dir():
        for f in base.glob("*.json"):
            if f.stem.isdigit():
                rows = _read_cache(f)
                if rows is not None:
                    out.append((int(f.stem), rows))
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
    if not _valid_revision(rev):
        return None, None, "invalid registry revision"
    if not _valid_cursor(first):
        return None, str(rev), "invalid paging cursor"
    f = _cache_file(rev)
    cached = _read_cache(f) if f.is_file() else None
    if cached is not None and cached[:len(first["entries"])] == first["entries"]:
        return cached, str(rev), None
    rows, page, pages = list(first["entries"]), first, 1
    cursors = set()
    while page["next"] is not None:
        if pages >= MAX_PAGES:
            return None, str(rev), "too many pages"
        cursor = page["next"]
        if not isinstance(cursor, str) or cursor in cursors:
            return None, str(rev), "invalid or repeated paging cursor"
        cursors.add(cursor)
        page = _page(URL + "&cursor=" + urllib.parse.quote(cursor, safe=""), timeout)
        if page is None:
            return None, str(rev), "paging interrupted"
        if not _valid_revision(page.get("revision")) or page["revision"] != rev:
            return None, str(rev), "registry revision changed while paging"
        if not _valid_cursor(page):
            return None, str(rev), "invalid paging cursor"
        rows += page["entries"]
        pages += 1
    tmp = None
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=f.parent,
                                         prefix=f.name + ".", suffix=".tmp", delete=False) as handle:
            tmp = pathlib.Path(handle.name)
            json.dump({"schema": CACHE_SCHEMA, "revision": rev, "pages": pages,
                       "complete": True, "entries": rows, "entries_sha256": _rows_hash(rows)}, handle)
        os.replace(tmp, f)
        note_cache_write("palomar", "snapshots-v1", f.name)
    except OSError:
        # A complete live result remains useful even when the cache is unwritable.
        pass
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
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
    return ok(rev, found[:LIMIT], f"{len(rows)} results searched", total=len(found))
