"""A completed search must never be a corrupt cache or a mixed registry snapshot."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from lean_pkg.adapters import palomar as pm


def page(revision=168, *, next_cursor=None, entries=None):
    return 200, json.dumps({"revision": revision, "next": next_cursor,
                            "entries": entries if entries is not None else
                            [{"id": "entry", "title": "Wick normalization"}]}).encode()


@pytest.mark.parametrize("body", ["{broken", "{}", "[]", '["not an entry"]'])
def test_corrupt_or_legacy_cache_refetched_online(monkeypatch, body):
    path = pm._cache_file(168)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    calls = []
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: calls.append(url) or page())
    result = pm.search("Wick")
    assert result["state"] == "ok" and len(result["hits"]) == 1
    saved = json.loads(path.read_text())
    assert saved["schema"] == pm.CACHE_SCHEMA and saved["complete"] is True
    assert pm._read_cache(path) == saved["entries"]
    assert len(calls) == 1


@pytest.mark.parametrize("revision", [None, True, -1, "168"])
def test_invalid_revision_is_skipped(monkeypatch, revision):
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page(revision))
    result = pm.search("Wick")
    assert result["state"] == "skipped" and result["reason"] == "invalid registry revision"


@pytest.mark.parametrize("rows", [[1], [{}], [{"id": None}], [{"id": 1}], [{"id": ""}]])
def test_unusable_live_entries_are_not_a_successful_zero(monkeypatch, rows):
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page(entries=rows))
    assert pm.search("Wick")["state"] == "skipped"


def test_corrupt_cache_offline_is_not_zero_hits(monkeypatch):
    path = pm._cache_file(168)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: None)
    result = pm.search("Wick")
    assert result["state"] == "skipped" and "no cached results" in result["reason"]


@pytest.mark.parametrize("change", [
    {"schema": "unknown"}, {"revision": True}, {"revision": 169},
    {"pages": 0}, {"pages": True}, {"complete": False}, {"entries": ["bad"]},
])
def test_invalid_snapshot_metadata_cannot_be_offline_evidence(monkeypatch, change):
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page())
    pm.search("Wick")
    path = pm._cache_file(168)
    saved = json.loads(path.read_text())
    saved.update(change)
    path.write_text(json.dumps(saved))
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: None)
    assert pm.search("Wick")["state"] == "skipped"


def test_empty_complete_snapshot_is_a_valid_zero(monkeypatch):
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page(entries=[]))
    assert pm.search("Wick")["state"] == "ok"
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: None)
    result = pm.search("Wick")
    assert result["state"] == "ok" and result["revision"] == "168 (cached)"
    assert result["hits"] == []


def test_changed_revision_never_cached(monkeypatch):
    answers = iter([page(next_cursor="next"), page(169)])
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: next(answers))
    result = pm.search("Wick")
    assert result["state"] == "skipped"
    assert result["reason"] == "registry revision changed while paging"
    assert not pm._cache_file(168).exists()


@pytest.mark.parametrize("cursor", [True, False, 0, [], {}, "", "  ", ["not a cursor"]])
def test_invalid_cursor_refused(monkeypatch, cursor):
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page(next_cursor=cursor))
    result = pm.search("Wick")
    assert result["state"] == "skipped" and "paging cursor" in result["reason"]


def test_bad_cursor_checked_before_warm_cache(monkeypatch):
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page())
    pm.search("Wick")
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page(next_cursor=False))
    assert pm.search("Wick")["state"] == "skipped"


def test_missing_cursor_does_not_mean_complete(monkeypatch):
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10:
                        (200, json.dumps({"revision": 168, "entries": []}).encode()))
    assert pm.search("Wick")["state"] == "skipped"


def test_later_page_revision_type_checked(monkeypatch):
    answers = iter([page(next_cursor="next"), page(168.0)])
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: next(answers))
    assert pm.search("Wick")["state"] == "skipped"


def test_later_page_cursor_type_checked(monkeypatch):
    answers = iter([page(next_cursor="next"), page(next_cursor=0)])
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: next(answers))
    assert pm.search("Wick")["state"] == "skipped"


def test_cursor_cycle_refused_before_max_pages(monkeypatch):
    calls = []
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10:
                        calls.append(url) or page(next_cursor="same"))
    result = pm.search("Wick")
    assert result["state"] == "skipped" and "repeated" in result["reason"]
    assert len(calls) == 2


def test_cached_entries_checksum_and_prefix_checked(monkeypatch):
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page())
    pm.search("Wick")
    path = pm._cache_file(168)
    saved = json.loads(path.read_text())
    saved["entries"] = []             # valid JSON, incomplete content
    path.write_text(json.dumps(saved))
    assert pm.search("Wick")["hits"]
    saved = json.loads(path.read_text())
    saved["entries"][0]["title"] = "different"
    saved["entries_sha256"] = pm._rows_hash(saved["entries"])
    path.write_text(json.dumps(saved))
    assert pm.search("Wick")["hits"]  # checksum alone is not agreement with the live prefix


def test_concurrent_cache_writes_have_unique_temporary_files(monkeypatch):
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page())
    replace = pm.os.replace
    barrier = Barrier(2)
    names = []

    def overlapping(source, target):
        names.append(source.name)
        barrier.wait(timeout=5)
        replace(source, target)

    monkeypatch.setattr(pm.os, "replace", overlapping)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: pm.search("Wick"), range(2)))
    assert all(r["state"] == "ok" and r["hits"] for r in results)
    assert len(set(names)) == 2
    assert pm._read_cache(pm._cache_file(168))


def test_cache_write_failure_keeps_complete_live_result(monkeypatch):
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page())
    monkeypatch.setattr(pm.os, "replace", lambda *args: (_ for _ in ()).throw(PermissionError()))
    assert pm.search("Wick")["hits"]
    assert not list(pm.cache_dir("palomar", "snapshots-v1").glob("*.tmp"))


def test_legacy_cache_namespace_is_not_overwritten(monkeypatch):
    legacy = pm.cache_dir("palomar", "168.json")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('[{"id":"old","title":"Wick"}]')
    before = legacy.read_bytes()
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: page())
    assert pm.search("Wick")["hits"]
    assert legacy.read_bytes() == before
    assert pm._cache_file(168).is_file()
