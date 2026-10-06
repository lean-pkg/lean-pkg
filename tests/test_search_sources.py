#!/usr/bin/env python3
"""search: network sources in a fixed order, --offline, and failures reported as skipped."""

from __future__ import annotations

import json

import pytest

from .conftest import run_search

NETWORK = ("loogle", "leansearch", "reservoir", "palomar", "github")


def stub_adapters(srch, monkeypatch, calls, results=None):
    for name in NETWORK:
        def search(term, timeout=10, _n=name):
            calls.append(_n)
            r = (results or {}).get(_n)
            if isinstance(r, Exception):
                raise r
            return r or {"state": "ok", "reason": None, "revision": f"rev-{_n}",
                         "hits": [{"name": f"{_n}-hit", "summary": "s", "url": "https://example.invalid", "extra": {}}]}
        monkeypatch.setattr(srch.ADAPTERS[name], "search", search)


def test_sources_order_is_fixed(srch):
    assert srch.SOURCES == ("registry", "index", "loogle", "leansearch", "reservoir", "palomar", "github")


def test_offline_skips_every_network_source_with_reason_offline(lp, srch, searchable, monkeypatch, capsys):
    calls = []
    stub_adapters(srch, monkeypatch, calls)
    assert run_search(lp, searchable, "foo bound", "--offline") == 0
    out = capsys.readouterr().out
    assert calls == []
    for name in NETWORK:
        assert f"{name}: skipped: offline" in out
    assert out.splitlines()[-1] == ("consulted: registry (ok, 1 hit), index (ok, 0 hits), "
                                    "loogle (skipped: offline), leansearch (skipped: offline), "
                                    "reservoir (skipped: offline), palomar (skipped: offline), "
                                    "github (skipped: offline)")


def test_network_sources_run_in_the_fixed_order(lp, srch, searchable, monkeypatch, capsys):
    calls = []
    stub_adapters(srch, monkeypatch, calls)
    assert run_search(lp, searchable, "foo bound", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert calls == list(NETWORK)
    assert [b["source"] for b in p["sources"]] == list(srch.SOURCES)
    assert p["sources"][2]["revision"] == "rev-loogle" and p["sources"][2]["hits"][0]["name"] == "loogle-hit"


def test_selected_sources_only(lp, srch, searchable, monkeypatch, capsys):
    calls = []
    stub_adapters(srch, monkeypatch, calls)
    assert run_search(lp, searchable, "x", "--sources", "github,registry") == 0
    out = capsys.readouterr().out
    assert calls == ["github"]
    assert "loogle: skipped: not selected" in out and "github: ok at rev-github, 1 hit" in out


def test_an_adapter_that_raises_is_skipped_and_the_rest_still_run(lp, srch, searchable, monkeypatch, capsys):
    calls = []
    stub_adapters(srch, monkeypatch, calls, {"leansearch": RuntimeError("boom /home/someone/x")})
    assert run_search(lp, searchable, "x") == 0
    out = capsys.readouterr().out
    assert "leansearch: skipped: adapter error: RuntimeError" in out and "/home/someone" not in out
    assert calls == list(NETWORK)


@pytest.mark.parametrize("bad", [None, {"state": "maybe"}, {"state": "ok", "hits": "many"}])
def test_a_malformed_adapter_result_is_skipped(lp, srch, searchable, monkeypatch, capsys, bad):
    calls = []
    stub_adapters(srch, monkeypatch, calls)
    monkeypatch.setattr(srch.ADAPTERS["palomar"], "search", lambda term, timeout=10: bad)
    assert run_search(lp, searchable, "x") == 0
    assert "palomar: skipped: adapter returned an unexpected result" in capsys.readouterr().out


def test_an_unreachable_source_is_never_counted_as_zero_hits(lp, srch, searchable, monkeypatch, capsys):
    calls = []
    stub_adapters(srch, monkeypatch, calls, {"loogle": {"state": "skipped", "reason": "unreachable",
                                                        "revision": None, "hits": []}})
    assert run_search(lp, searchable, "x", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    loogle = p["sources"][2]
    assert loogle["state"] == "skipped" and loogle["reason"] == "unreachable"
    assert p["total_hits"] == 4                    # four stubbed network hits; loogle adds none


def test_the_suite_cannot_reach_the_network(srch):
    """conftest replaces the adapters' urlopen: an unpatched adapter fails the test, never goes online."""
    with pytest.raises(pytest.fail.Exception):
        srch.ADAPTERS["loogle"].search("Real.exp")
