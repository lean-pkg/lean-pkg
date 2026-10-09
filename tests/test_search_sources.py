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
    assert run_search(lp, searchable, "foo bound", "--json", "--loogle") == 0
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
    assert run_search(lp, searchable, "x", "--loogle") == 0
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
    assert run_search(lp, searchable, "?a + ?b", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    loogle = p["sources"][2]
    assert loogle["state"] == "skipped" and loogle["reason"] == "unreachable"
    assert p["total_hits"] == 4                    # four stubbed network hits; loogle adds none


def test_the_suite_cannot_reach_the_network(srch):
    """conftest replaces the adapters' urlopen: an unpatched adapter fails the test, never goes online."""
    with pytest.raises(pytest.fail.Exception):
        srch.ADAPTERS["loogle"].search("Real.exp")


# --- routing by query shape (#24.3) ---------------------------------------------

NATURAL = ("stochastic fubini", "Gumbel", "Fisher-Tippett theorem", "Itô formula for white noise",
           "law of large numbers", "Tracy–Widom distribution", "semicircle law for Wigner matrices")
SIGNATURE = ("?a + ?b = ?b + ?a", "Real.exp", "(n : ℕ) → n ≤ n + 1")


class FakeLoogle:
    """A Loogle adapter that parses like the service: prose is a parser error, never a hit."""
    def __init__(self, route_of):
        self.terms, self.route_of = [], route_of

    def search(self, term, timeout=10):
        self.terms.append(term)
        if self.route_of(term) != "signature":
            return {"state": "skipped", "reason": "query not understood: parse error", "revision": None,
                    "hits": []}
        return {"state": "ok", "reason": None, "revision": "live (Mathlib master)",
                "hits": [{"name": "add_comm", "summary": "s", "url": "https://example.invalid", "extra": {}}]}


@pytest.mark.parametrize("term", NATURAL)
def test_route_of_natural_language(srch, term):
    assert srch.route_of(term) == "natural-language"


@pytest.mark.parametrize("term", SIGNATURE + ("∀ x, f x = 0", "fun x => x", "_ ⊢ _", "Mathlib.Order",
                                              "a -> b", '"comm"', "x ∈ s", "List.map"))
def test_route_of_signature(srch, term):
    assert srch.route_of(term) == "signature"


def _fake(srch, monkeypatch):
    calls, fake = [], FakeLoogle(srch.route_of)
    stub_adapters(srch, monkeypatch, calls)
    monkeypatch.setattr(srch.ADAPTERS["loogle"], "search", fake.search)
    return calls, fake


def test_natural_language_queries_skip_loogle_with_the_reason(lp, srch, searchable, monkeypatch, capsys):
    calls, fake = _fake(srch, monkeypatch)
    for term in NATURAL:
        assert run_search(lp, searchable, term, "--json") == 0
        p = json.loads(capsys.readouterr().out)
        loogle = p["sources"][2]
        assert p["route"] == "natural-language" and {b["route"] for b in p["sources"]} == {"natural-language"}
        assert loogle["state"] == "skipped" and loogle["reason"] == srch.LOOGLE_NL_REASON
        assert "?a + ?b = ?b + ?a" in loogle["reason"]
        assert all("parse" not in str(b["reason"]) for b in p["sources"])
        assert p["sources"][3]["state"] == "ok"            # leansearch still takes prose
    assert fake.terms == []
    assert calls == [s for _ in NATURAL for s in NETWORK if s != "loogle"]


def test_natural_language_skip_is_in_the_text_and_closing_line(lp, srch, searchable, monkeypatch, capsys):
    _fake(srch, monkeypatch)
    assert run_search(lp, searchable, "stochastic fubini") == 0
    out = capsys.readouterr().out
    assert f"loogle: skipped: {srch.LOOGLE_NL_REASON}" in out
    assert f"loogle (skipped: {srch.LOOGLE_NL_REASON})" in out.splitlines()[-1]


def test_signature_queries_call_loogle(lp, srch, searchable, monkeypatch, capsys):
    _, fake = _fake(srch, monkeypatch)
    for term in SIGNATURE:
        assert run_search(lp, searchable, term, "--json") == 0
        p = json.loads(capsys.readouterr().out)
        assert p["route"] == "signature" and p["sources"][2]["route"] == "signature"
        assert p["sources"][2]["state"] == "ok" and p["sources"][2]["hits"][0]["name"] == "add_comm"
    assert fake.terms == list(SIGNATURE)


def test_loogle_flag_forces_a_natural_language_query(lp, srch, searchable, monkeypatch, capsys):
    _, fake = _fake(srch, monkeypatch)
    assert run_search(lp, searchable, "stochastic fubini", "--loogle", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert fake.terms == ["stochastic fubini"]
    assert p["route"] == "natural-language"
    assert p["sources"][2]["state"] == "skipped" and "query not understood" in p["sources"][2]["reason"]


def test_offline_still_wins_over_routing(lp, srch, searchable, monkeypatch, capsys):
    _, fake = _fake(srch, monkeypatch)
    assert run_search(lp, searchable, "stochastic fubini", "--offline") == 0
    assert "loogle: skipped: offline" in capsys.readouterr().out and fake.terms == []


def test_not_selected_still_wins_over_routing(lp, srch, searchable, monkeypatch, capsys):
    _fake(srch, monkeypatch)
    assert run_search(lp, searchable, "stochastic fubini", "--sources", "registry") == 0
    assert "loogle: skipped: not selected" in capsys.readouterr().out
