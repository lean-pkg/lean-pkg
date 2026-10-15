#!/usr/bin/env python3
"""search adapters: loogle, leansearch, reservoir, palomar, github. No test touches the network:
each patches its module's `fetch` or `runner` (conftest refuses any real I/O)."""

from __future__ import annotations

import importlib
import io
import json
import tarfile
import urllib.error
from types import SimpleNamespace

import pytest

from .conftest import ROOT

FIX = ROOT / "tests" / "fixtures"
SHA = "477b5e7c51ba4cf6199936c9f5e30c91dbe2c453"


def mod(name):
    return importlib.import_module(f"lean_pkg.adapters{'.' + name if name else ''}")


def answer(obj, status=200):
    return status, json.dumps(obj).encode("utf-8")


# --- the shared HTTP and subprocess layer -----------------------------------

class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_get_returns_status_and_bytes(monkeypatch):
    a = mod("")
    seen = {}

    def fake(req, timeout):
        seen.update(url=req.full_url, data=req.data, ua=req.get_header("User-agent"),
                    ctype=req.get_header("Content-type"), timeout=timeout)
        return FakeResponse(b"{}")
    monkeypatch.setattr(a, "urlopen", fake)
    assert a.http_get("https://example.invalid/x", timeout=3) == (200, b"{}")
    assert seen["ua"] == "lean-pkg" and seen["data"] is None and seen["timeout"] == 3
    assert a.http_get("https://example.invalid/y", body=b'{"q": 1}') == (200, b"{}")
    assert seen["data"] == b'{"q": 1}' and seen["ctype"] == "application/json"


@pytest.mark.parametrize("exc,expected", [
    (urllib.error.HTTPError("u", 404, "nf", {}, None), (404, b"")),
    (urllib.error.URLError("no route"), None),
    (TimeoutError(), None),
    (OSError("reset"), None),
])
def test_http_get_errors(monkeypatch, exc, expected):
    a = mod("")

    def fake(req, timeout):
        raise exc
    monkeypatch.setattr(a, "urlopen", fake)
    assert a.http_get("https://example.invalid/x") == expected


def test_run_cmd_missing_program_is_none(monkeypatch):
    a = mod("")
    monkeypatch.setattr(a.shutil, "which", lambda prog: None)
    assert a.run_cmd(["gh", "api", "x"]) is None


def test_run_cmd_returns_code_stdout_stderr(monkeypatch):
    a = mod("")
    monkeypatch.setattr(a.shutil, "which", lambda prog: "/usr/bin/" + prog)
    monkeypatch.setattr(a, "subprocess_run", lambda argv, **kw: SimpleNamespace(
        returncode=1, stdout=b"out", stderr=b"HTTP 403: rate limit\n"))
    assert a.run_cmd(["gh", "api", "x"]) == (1, b"out", "HTTP 403: rate limit\n")


def test_matches_is_every_word_as_a_substring():
    a = mod("")
    assert a.matches("Fisher-Tippett", "A project to formalize Fisher-Tippett-Gnedenko theorem")
    assert not a.matches("Gumbel", "A project to formalize Fisher-Tippett-Gnedenko theorem")
    assert not a.matches("!!!", "anything")


# --- loogle -------------------------------------------------------------------

LOOGLE = {"count": 641, "header": "Found 641 declarations mentioning Real.exp.",
          "hits": [{"name": "Real.exp", "module": "Mathlib.Analysis.Complex.Exponential",
                    "type": " (x : ℝ) : ℝ", "doc": "The real exponential function"},
                   {"name": "Real.exp_injective", "module": "Mathlib.Analysis.SpecialFunctions.Exp",
                    "type": " : Function.Injective Real.exp", "doc": None}]}


def test_loogle_hits(monkeypatch):
    lg = mod("loogle")
    urls = []
    monkeypatch.setattr(lg, "fetch", lambda url, timeout=10: urls.append(url) or answer(LOOGLE))
    r = lg.search("Real.exp _ ≤ _")
    assert urls == ["https://loogle.lean-lang.org/json?q=Real.exp%20_%20%E2%89%A4%20_"]
    assert r["state"] == "ok" and r["revision"] == "live (Mathlib master)"
    assert [h["name"] for h in r["hits"]] == ["Real.exp", "Real.exp_injective"]
    assert r["hits"][1]["summary"] == ": Function.Injective Real.exp"
    assert r["hits"][0]["url"].endswith("pattern=Real.exp#doc")
    assert r["reason"] == "641 found, first 20 kept"


def test_loogle_query_error_is_skipped_not_zero(monkeypatch):
    lg = mod("loogle")
    monkeypatch.setattr(lg, "fetch", lambda url, timeout=10: answer({"error": "Unknown identifier `Gumbel`"}))
    r = lg.search("Gumbel")
    assert r["state"] == "skipped" and "Unknown identifier" in r["reason"] and r["hits"] == []


@pytest.mark.parametrize("got,reason", [(None, "unreachable"), ((500, b""), "HTTP 500"),
                                        ((200, b"<html>"), "answer is not JSON")])
def test_loogle_unavailable_is_skipped(monkeypatch, got, reason):
    lg = mod("loogle")
    monkeypatch.setattr(lg, "fetch", lambda url, timeout=10: got)
    assert lg.search("x") == {"state": "skipped", "reason": reason, "revision": None, "hits": []}


# --- leansearch ---------------------------------------------------------------

LEANSEARCH = [[{"result": {"name": ["MeasureTheory", "integral_prod"], "kind": "theorem",
                           "module_name": ["Mathlib", "MeasureTheory", "Integral", "Prod"],
                           "informal_name": "Fubini's theorem", "docstring": None},
                "distance": 0.21}]]


def test_leansearch_posts_the_query_and_parses_hits(monkeypatch):
    ls = mod("leansearch")
    calls = []
    monkeypatch.setattr(ls, "fetch", lambda url, body, timeout=10: calls.append((url, json.loads(body)))
                        or answer(LEANSEARCH))
    r = ls.search("stochastic fubini")
    assert calls == [("https://leansearch.net/search", {"query": ["stochastic fubini"], "num_results": 10})]
    assert r["state"] == "ok" and r["hits"][0]["name"] == "MeasureTheory.integral_prod"
    assert r["hits"][0]["summary"] == "Fubini's theorem"
    assert r["hits"][0]["extra"]["module"] == "Mathlib.MeasureTheory.Integral.Prod"


@pytest.mark.parametrize("got,reason", [
    ((404, b""), "HTTP 404: API moved (https://leansearch.net/search not found); the leansearch adapter needs "
                 "updating"),
    ((500, b"Internal Server Error"), "HTTP 500: service error at leansearch.net; retry later"),
    ((503, b""), "HTTP 503: service error at leansearch.net; retry later"),
    ((429, b""), "HTTP 429"),
    (None, "unreachable"),
    ((200, b'{"not": "a list"}'), "unexpected answer")])
def test_leansearch_moved_or_unreachable_is_skipped(monkeypatch, got, reason):
    ls = mod("leansearch")
    monkeypatch.setattr(ls, "fetch", lambda url, body, timeout=10: got)
    r = ls.search("x")
    assert r["state"] == "skipped" and r["reason"] == reason


# --- reservoir ----------------------------------------------------------------

def test_reservoir_searches_descriptions_not_only_names(monkeypatch):
    rv = mod("reservoir")
    monkeypatch.setenv("LEAN_PKG_RESERVOIR_INDEX", str(FIX / "reservoir-index"))
    r = rv.search("Fisher-Tippett")
    assert r["state"] == "ok" and r["revision"] == "reservoir-index"
    assert [h["name"] for h in r["hits"]] == ["kkytola/ExtremeValueProject"]
    h = r["hits"][0]
    assert h["url"] == "https://github.com/kkytola/ExtremeValueProject"
    assert h["extra"]["reservoir"] == "https://reservoir.lean-lang.org/@kkytola/ExtremeValueProject"
    assert r["reason"] == "3 packages searched"


def test_reservoir_keywords_and_repo_url_from_sources(monkeypatch):
    rv = mod("reservoir")
    monkeypatch.setenv("LEAN_PKG_RESERVOIR_INDEX", str(FIX / "reservoir-index"))
    r = rv.search("gumbel")
    assert [h["url"] for h in r["hits"]] == ["https://github.com/example/lean-gumbel-tools"]
    r = rv.search("matrix cookbook")
    assert r["hits"][0]["url"] == "https://github.com/example/lean-matrix-cookbook"


def _tarball(entries: dict, top=f"leanprover-reservoir-index-{SHA[:7]}") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, text in entries.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"{top}/{rel}" if not rel.startswith(("/", "..")) else rel)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def fake_gh(tar: bytes, calls: list, sha: str | None = SHA):
    def run(argv, timeout=120):
        calls.append(argv)
        if argv[:2] == ["gh", "api"] and argv[2].endswith("/commits/HEAD"):
            return (0, (sha + "\n").encode(), "") if sha else (1, b"", "error connecting")
        if argv[:2] == ["gh", "api"] and "/tarball/" in argv[2]:
            return 0, tar, ""
        raise AssertionError(argv)
    return run


def test_reservoir_downloads_the_tarball_once_and_caches_it_by_commit(monkeypatch, tmp_path):
    rv = mod("reservoir")
    meta = (FIX / "reservoir-index" / "kkytola" / "extremevalueproject" / "metadata.json").read_text()
    calls = []
    monkeypatch.setattr(rv, "runner", fake_gh(_tarball({"kkytola/extremevalueproject/metadata.json": meta}), calls))
    r = rv.search("Gnedenko")
    assert r["state"] == "ok" and r["revision"] == SHA[:8] and len(r["hits"]) == 1
    cached = tmp_path / "xdg-cache" / "lean-pkg" / "reservoir-index" / SHA
    assert (cached / "kkytola" / "extremevalueproject" / "metadata.json").is_file()
    assert rv.search("Gnedenko")["hits"] and sum("/tarball/" in c[2] for c in calls) == 1
    assert rv.revision() == SHA[:8]


def test_reservoir_offline_uses_the_newest_cache_and_says_so(monkeypatch, tmp_path):
    rv = mod("reservoir")
    d = tmp_path / "xdg-cache" / "lean-pkg" / "reservoir-index" / SHA / "example" / "gumbel-tools"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text((FIX / "reservoir-index" / "example" / "gumbel-tools" / "metadata.json").read_text())
    monkeypatch.setattr(rv, "runner", fake_gh(b"", [], sha=None))
    r = rv.search("gumbel")
    assert r["state"] == "ok" and r["revision"] == f"{SHA[:8]} (cached)"


def test_reservoir_without_gh_or_cache_is_skipped(monkeypatch):
    rv = mod("reservoir")
    monkeypatch.setattr(rv, "runner", lambda argv, timeout=120: None)
    r = rv.search("gumbel")
    assert r["state"] == "skipped" and "no cached index" in r["reason"]
    assert rv.revision() is None


def test_reservoir_refuses_an_unsafe_tarball(monkeypatch, tmp_path):
    rv = mod("reservoir")
    tar = _tarball({"ok/metadata.json": "{}", "../../escape.txt": "x"})
    monkeypatch.setattr(rv, "runner", fake_gh(tar, []))
    r = rv.search("gumbel")
    assert r["state"] == "skipped" and r["reason"] == "index tarball refused: ValueError"
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "xdg-cache" / "lean-pkg" / "reservoir-index" / SHA).exists()


# --- palomar ------------------------------------------------------------------

PAGES = {None: (FIX / "palomar" / "page-1.json").read_bytes(),
         "Y3Vyc29yIDI=": (FIX / "palomar" / "page-2.json").read_bytes()}


def fake_palomar(urls: list, fail_second=False):
    def fetch(url, timeout=10):
        urls.append(url)
        cursor = url.split("&cursor=", 1)[1] if "&cursor=" in url else None
        import urllib.parse
        cursor = urllib.parse.unquote(cursor) if cursor else None
        if cursor and fail_second:
            return None
        return 200, PAGES[cursor]
    return fetch


def test_palomar_pages_with_the_cursor_and_searches_abstracts(monkeypatch):
    pm = mod("palomar")
    urls = []
    monkeypatch.setattr(pm, "fetch", fake_palomar(urls))
    r = pm.search("brownian exit")
    assert urls[1].endswith("&cursor=Y3Vyc29yIDI%3D")
    assert r["state"] == "ok" and r["revision"] == "168" and r["reason"] == "2 results searched"
    assert [h["name"] for h in r["hits"]] == ["PALOMAR-2026-10-05-000008"]
    assert r["hits"][0]["url"] == "https://data.palomar-registry.org/entries/PALOMAR-2026-10-05-000008-v1.json"


def test_palomar_matches_theorem_names(monkeypatch):
    pm = mod("palomar")
    monkeypatch.setattr(pm, "fetch", fake_palomar([]))
    assert [h["name"] for h in pm.search("exists_monotone_light_factorization")["hits"]] == \
        ["PALOMAR-2026-10-04-000006"]


def test_palomar_caches_by_revision(monkeypatch):
    pm = mod("palomar")
    urls = []
    monkeypatch.setattr(pm, "fetch", fake_palomar(urls))
    pm.search("brownian")
    n = len(urls)
    assert pm.search("brownian")["hits"] and len(urls) == n + 1      # only the first page again
    assert pm.revision() == "168"


def test_palomar_offline_uses_the_cache_and_says_so(monkeypatch):
    pm = mod("palomar")
    monkeypatch.setattr(pm, "fetch", fake_palomar([]))
    pm.search("brownian")
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: None)
    r = pm.search("brownian")
    assert r["state"] == "ok" and r["revision"] == "168 (cached)" and r["hits"]


def test_palomar_unreachable_without_cache_is_skipped(monkeypatch):
    pm = mod("palomar")
    monkeypatch.setattr(pm, "fetch", lambda url, timeout=10: None)
    r = pm.search("brownian")
    assert r["state"] == "skipped" and "no cached results" in r["reason"]


def test_palomar_interrupted_paging_is_skipped_and_not_cached(monkeypatch, tmp_path):
    pm = mod("palomar")
    monkeypatch.setattr(pm, "fetch", fake_palomar([], fail_second=True))
    r = pm.search("brownian")
    assert r["state"] == "skipped" and r["reason"] == "paging interrupted" and r["revision"] == "168"
    assert not (tmp_path / "xdg-cache" / "lean-pkg" / "palomar" / "168.json").exists()


# --- github -------------------------------------------------------------------

REPOS = [{"fullName": "kkytola/ExtremeValueProject", "description": "Fisher-Tippett-Gnedenko",
          "stargazersCount": 2, "license": {"key": "apache-2.0"}, "updatedAt": "2026-07-25T14:15:02Z"}]
CODE = [{"repository": {"nameWithOwner": "example/linglib"}, "path": "Linglib/Gumbel.lean",
         "url": "https://github.com/example/linglib/blob/abc/Linglib/Gumbel.lean"}]


def fake_search(calls, repos=(0, REPOS), code=(0, CODE)):
    def run(argv, timeout=60):
        calls.append(argv)
        rc, data = repos if argv[2] == "repos" else code
        return (rc, json.dumps(data).encode(), "") if rc == 0 else (rc, b"", "HTTP 401: Requires authentication\n")
    return run


def test_github_repos_and_code_hits(monkeypatch):
    gh = mod("github")
    calls = []
    monkeypatch.setattr(gh, "runner", fake_search(calls))
    r = gh.search("Gumbel")
    assert r["state"] == "ok" and r["revision"] == "live" and r["reason"] is None
    assert [h["name"] for h in r["hits"]] == ["kkytola/ExtremeValueProject", "example/linglib:Linglib/Gumbel.lean"]
    assert r["hits"][0]["extra"] == {"kind": "repo", "stars": 2, "license": "apache-2.0"}
    assert all(c[-2:] == ["--", "Gumbel"] and "--language" in c for c in calls)


def test_github_term_with_a_leading_dash_is_never_a_flag(monkeypatch):
    gh = mod("github")
    calls = []
    monkeypatch.setattr(gh, "runner", fake_search(calls))
    gh.search("--help")
    assert all(c.index("--") == len(c) - 2 and c[-1] == "--help" for c in calls)


def test_github_without_gh_is_skipped(monkeypatch):
    gh = mod("github")
    monkeypatch.setattr(gh, "runner", lambda argv, timeout=60: None)
    assert gh.search("x") == {"state": "skipped", "reason": "gh not installed", "revision": None, "hits": []}


def test_github_code_search_failure_is_a_partial_ok(monkeypatch):
    gh = mod("github")
    monkeypatch.setattr(gh, "runner", fake_search([], code=(1, None)))
    r = gh.search("Gumbel")
    assert r["state"] == "ok" and len(r["hits"]) == 1
    assert r["reason"] == "code search failed: HTTP 401: Requires authentication"
    assert r["partial"] == ["code"]


def test_github_repository_search_failure_is_partial_too(monkeypatch):
    gh = mod("github")
    monkeypatch.setattr(gh, "runner", fake_search([], repos=(1, None)))
    r = gh.search("Gumbel")
    assert r["state"] == "ok" and r["partial"] == ["repository"]
    assert r["reason"].startswith("repository search failed: HTTP 401")


def test_github_with_both_searches_answering_is_not_partial(monkeypatch):
    gh = mod("github")
    monkeypatch.setattr(gh, "runner", fake_search([]))
    assert "partial" not in gh.search("Gumbel")


def test_github_both_failing_is_skipped(monkeypatch):
    gh = mod("github")
    monkeypatch.setattr(gh, "runner", fake_search([], repos=(1, None), code=(1, None)))
    r = gh.search("Gumbel")
    assert r["state"] == "skipped" and r["reason"].startswith("gh search failed: HTTP 401")


def test_live_revisions():
    assert mod("loogle").revision() == "live (Mathlib master)"
    assert mod("leansearch").revision() == "live" and mod("github").revision() == "live"


def test_the_adapters_package_ships_in_the_wheel():
    setuptools = pytest.importorskip("setuptools")
    assert "lean_pkg.adapters" in setuptools.find_packages(str(ROOT / "src"))
