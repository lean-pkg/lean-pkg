#!/usr/bin/env python3
"""search field report: --loogle-query, --require-complete, scan-error causes, cache writes and --cache-dir."""

from __future__ import annotations

import json
import subprocess

from .conftest import base_pkg, read_reg, run_search, write_reg
from .test_search_declarations import unclaimed  # noqa: F401  (fixture)

NETWORK = ("loogle", "leansearch", "reservoir", "palomar", "github")


def stub(srch, monkeypatch, calls, keep=()):
    """Every network adapter except `keep` answers one hit and records (source, term)."""
    for name in NETWORK:
        if name in keep:
            continue

        def search(term, timeout=10, _n=name):
            calls.append((_n, term))
            return {"state": "ok", "reason": None, "revision": f"rev-{_n}",
                    "hits": [{"name": f"{_n}-hit", "summary": "s", "url": "https://example.invalid", "extra": {}}]}
        monkeypatch.setattr(srch.ADAPTERS[name], "search", search)


# --- Loogle syntax (item 1.i) ------------------------------------------------------

def test_loogle_query_goes_to_loogle_and_the_term_to_the_rest(lp, srch, searchable, monkeypatch, capsys):
    calls = []
    stub(srch, monkeypatch, calls)
    assert run_search(lp, searchable, "exponential martingale", "--loogle-query", "Real.exp", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert ("loogle", "Real.exp") in calls
    assert all(t == "exponential martingale" for s, t in calls if s != "loogle")
    assert p["loogle_query"] == "Real.exp" and p["route"] == "natural-language"
    assert p["sources"][2]["state"] == "ok" and p["complete"] is True


def test_loogle_query_needs_loogle_selected_and_a_pattern(lp, srch, searchable, monkeypatch, capsys):
    stub(srch, monkeypatch, [])
    assert run_search(lp, searchable, "x", "--loogle-query", "Real.exp", "--sources", "registry") == 2
    assert run_search(lp, searchable, "x", "--loogle-query", "  ") == 2


def test_a_loogle_parse_error_says_how_to_send_lean_syntax(lp, srch, searchable, monkeypatch, capsys):
    stub(srch, monkeypatch, [], keep=("loogle",))
    monkeypatch.setattr(srch.ADAPTERS["loogle"], "fetch",
                        lambda url, timeout=10: (200, b'{"error": "Unknown identifier exponential"}'))
    assert run_search(lp, searchable, "exponential Real.exp", "--json") == 0
    b = json.loads(capsys.readouterr().out)["sources"][2]
    assert b["state"] == "skipped"
    assert b["reason"].startswith("query not understood: Unknown identifier exponential; Loogle takes Lean names")
    assert "--loogle-query" in b["reason"]


def test_the_natural_language_skip_names_loogle_query(lp, srch, searchable, monkeypatch, capsys):
    stub(srch, monkeypatch, [])
    assert run_search(lp, searchable, "law of large numbers") == 0
    assert "--loogle-query PATTERN" in capsys.readouterr().out


# --- --require-complete (item 1.ii) --------------------------------------------------

def test_require_complete_exits_1_on_an_incomplete_search(lp, srch, searchable, monkeypatch, capsys):
    stub(srch, monkeypatch, [])
    assert run_search(lp, searchable, "law of large numbers", "--json") == 0      # default unchanged
    assert json.loads(capsys.readouterr().out)["complete"] is False
    assert run_search(lp, searchable, "law of large numbers", "--json", "--require-complete") == 1
    cap = capsys.readouterr()
    p = json.loads(cap.out)
    assert p["ok"] is False and p["complete"] is False and p["sources"]      # full payload still printed
    assert p["error"] == "search incomplete (--require-complete): skipped: loogle"
    assert "--require-complete" in cap.err


def test_require_complete_names_a_truncated_source(lp, srch, searchable, monkeypatch, capsys):
    stub(srch, monkeypatch, [])
    monkeypatch.setattr(srch.ADAPTERS["github"], "search", lambda term, timeout=10: {
        "state": "ok", "reason": None, "revision": "live", "hits": [], "partial": ["code"]})
    assert run_search(lp, searchable, "x", "--loogle", "--require-complete") == 1
    assert "github: code search failed" in capsys.readouterr().err


def test_require_complete_passes_a_complete_search(lp, srch, searchable, monkeypatch, capsys):
    stub(srch, monkeypatch, [])
    assert run_search(lp, searchable, "x", "--loogle-query", "Real.exp", "--require-complete", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["ok"] is True and p["complete"] is True and "error" not in p


# --- scan-error causes (item 2) -------------------------------------------------------

def _scan_error(lp, srch, unclaimed, monkeypatch, capsys, rc, stderr):
    real = srch.git_bytes

    def git(repo, *args, **kw):
        if args and args[0] == "grep":
            return subprocess.CompletedProcess(["git", *args], rc, b"", stderr)
        return real(repo, *args, **kw)
    monkeypatch.setattr(srch, "git_bytes", git)
    assert lp.main(["--registry", str(unclaimed.env.registry), "search", "gumbel", "--offline", "--json"]) == 0
    out = capsys.readouterr().out
    return out, {r["package"]: r for r in json.loads(out)["coverage"]["not_searched_reasons"]}


def test_a_scan_error_names_its_cause_never_the_stderr(lp, srch, unclaimed, monkeypatch, capsys):
    out, rs = _scan_error(lp, srch, unclaimed, monkeypatch, capsys, 128,
                          b"fatal: unable to read 0123abcd in /home/someone/secret-repo\n")
    assert rs["alpha"]["code"] == "scan-error"
    assert rs["alpha"]["detail"] == ("git grep exit 128: object missing or unreadable "
                                     "(a partial or damaged clone; try `git fsck`)")
    assert "/home/someone" not in out and "secret-repo" not in out
    assert rs["demo-c"]["detail"] is None                        # other codes carry no detail


def test_a_crashed_git_grep_is_named_as_a_signal(lp, srch, unclaimed, monkeypatch, capsys):
    _, rs = _scan_error(lp, srch, unclaimed, monkeypatch, capsys, -11, b"")
    assert rs["alpha"]["detail"].startswith("git grep killed by signal 11")


def test_the_coverage_line_carries_each_scan_error_cause(lp, srch, unclaimed, monkeypatch, capsys):
    real = srch.git_bytes
    monkeypatch.setattr(srch, "git_bytes", lambda repo, *a, **kw: subprocess.CompletedProcess(
        ["git"], 128, b"", b"fatal: detected dubious ownership") if a and a[0] == "grep" else real(repo, *a, **kw))
    assert lp.main(["--registry", str(unclaimed.env.registry), "search", "gumbel", "--offline"]) == 0
    assert ("scan error: alpha (git grep exit 128: repository owned by another user (git safe.directory))"
            in capsys.readouterr().out)


# --- cache writes (item 3) --------------------------------------------------------------

PAGE = json.dumps({"revision": 7, "next": None,
                   "entries": [{"id": "e1", "title": "Gumbel law", "path": "e1"}]}).encode()


def test_a_palomar_refresh_is_disclosed(lp, srch, searchable, monkeypatch, capsys, tmp_path):
    stub(srch, monkeypatch, [], keep=("palomar",))
    monkeypatch.setattr(srch.ADAPTERS["palomar"], "fetch", lambda url, timeout=10: (200, PAGE))
    assert run_search(lp, searchable, "gumbel", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["cache"] == {"root": "XDG_CACHE_HOME", "writes": ["palomar/snapshots-v1/7.json"]}
    assert p["sources"][5]["cache_writes"] == ["palomar/snapshots-v1/7.json"]
    assert (tmp_path / "xdg-cache" / "lean-pkg" / "palomar" / "snapshots-v1" / "7.json").is_file()
    assert run_search(lp, searchable, "gumbel") == 0                   # cached now: no write, no line
    assert "cache: wrote" not in capsys.readouterr().out


def test_cache_dir_keeps_the_refresh_private(lp, srch, searchable, monkeypatch, capsys, tmp_path):
    stub(srch, monkeypatch, [], keep=("palomar",))
    monkeypatch.setattr(srch.ADAPTERS["palomar"], "fetch", lambda url, timeout=10: (200, PAGE))
    private = tmp_path / "private-cache"
    assert run_search(lp, searchable, "gumbel", "--cache-dir", str(private)) == 0
    out = capsys.readouterr().out
    assert (private / "palomar" / "snapshots-v1" / "7.json").is_file()
    assert not (tmp_path / "xdg-cache" / "lean-pkg").exists()
    assert ("cache: wrote palomar/snapshots-v1/7.json under the --cache-dir directory (a cache refresh; "
            "the registry was not written; --cache-dir DIR keeps a search's cache private)") in out
    assert str(private) not in out
    assert srch.adapters.CACHE_ROOT is None                              # restored after the run


def test_offline_writes_nothing(lp, srch, searchable, capsys):
    assert run_search(lp, searchable, "gumbel", "--offline", "--json") == 0
    assert json.loads(capsys.readouterr().out)["cache"]["writes"] == []


def test_require_complete_fails_once_a_registered_package_is_not_searched(lp, srch, searchable, monkeypatch,
                                                                         tmp_path, capsys):
    """#376: a registered package the registry could not search (here: no checkout) makes a search that was
    complete incomplete; before the fix `complete` stayed true and --require-complete passed."""
    stub(srch, monkeypatch, [])
    assert run_search(lp, searchable, "x", "--loogle-query", "Real.exp", "--require-complete", "--json") == 0
    assert json.loads(capsys.readouterr().out)["complete"] is True
    reg = read_reg(searchable.env)
    reg["packages"]["gone"] = base_pkg("gone", tmp_path / "no-such-checkout", "gate-pending")
    write_reg(searchable.env, reg)
    assert run_search(lp, searchable, "x", "--loogle-query", "Real.exp", "--require-complete", "--json") == 1
    cap = capsys.readouterr()
    p = json.loads(cap.out)
    assert p["complete"] is False and p["truncated_sources"] == ["registry"] and p["ok"] is False
    assert p["coverage"]["not_searched"] == ["gone"]
    assert "registry: 1 registered package(s) not searched (no checkout)" in p["error"]
