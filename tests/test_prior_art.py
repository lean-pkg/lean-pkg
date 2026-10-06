#!/usr/bin/env python3
"""search --record: the citable record in the research repository plus one event line."""

from __future__ import annotations

import json

import pytest

from .conftest import read_events, read_reg, run_search, write_reg

NETWORK = ("loogle", "leansearch", "reservoir", "palomar", "github")


def record_file(repo, date, slug):
    return repo / "docs" / "prior-art" / f"{date}-{slug}.md"


def today(lp):
    return lp.now_iso()[:10]


def test_record_lands_in_the_package_checkout_with_every_source(lp, searchable, capsys):
    s = searchable
    assert run_search(lp, s, "foo bound", "--offline", "--package", "beta", "--record") == 0
    f = record_file(s.env.repo_b, today(lp), "foo-bound")
    text = f.read_text()
    assert text.startswith('# Prior-art search: "foo bound"\n')
    assert f"- date: {today(lp)}" in text and "- package: beta" in text
    for src in ("registry", "index", *NETWORK):
        assert f"| {src} |" in text, src
    assert "| loogle | - | skipped: offline | - |" in text
    assert "- `beta/foo-le-bar` (`Demo.foo_le_bar`): theorem, reusable, trust comparator" in text
    assert "\ndecision: pending\nreason:\n" in text
    assert "require | reuse | vendor | contribute | build" in text
    assert f"record written: {f}" in capsys.readouterr().out


def test_record_appends_one_prior_art_searched_event(lp, searchable):
    s = searchable
    n = len(read_events(s.env))
    assert run_search(lp, s, "foo bound", "--offline", "--package", "beta", "--record") == 0
    evs = read_events(s.env)
    assert len(evs) == n + 1
    e = evs[-1]
    assert e["event"] == "prior-art-searched" and e["name"] == "beta" and e["term"] == "foo bound"
    assert e["record"] == f"docs/prior-art/{today(lp)}-foo-bound.md" and e["decision"] == "pending"
    assert e["sources"]["registry"] == "ok" and e["sources"]["github"] == "skipped: offline"
    assert e["hits"] == {"registry": 1, "index": 0, "loogle": None, "leansearch": None,
                         "reservoir": None, "palomar": None, "github": None}


def test_zero_hits_are_worded_not_found_with_revisions(lp, searchable):
    s = searchable
    assert run_search(lp, s, "gumbel", "--offline", "--package", "beta", "--record") == 0
    text = record_file(s.env.repo_b, today(lp), "gumbel").read_text()
    reg_rev = [ln for ln in text.splitlines() if ln.startswith("| registry |")][0].split("|")[2].strip()
    assert f'Not found in registry at {reg_rev}, index at ' in text
    assert f' on {today(lp)} for "gumbel".' in text
    assert "loogle at" not in text                    # skipped sources are not zeros
    assert "## Candidates\n\nNone.\n" in text


def test_candidates_are_the_top_five_per_source(lp, srch, searchable, monkeypatch):
    many = [{"name": f"r{i}", "summary": "s", "url": f"https://example.invalid/{i}", "extra": {}} for i in range(7)]
    monkeypatch.setattr(srch.ADAPTERS["github"], "search", lambda term, timeout=10: {
        "state": "ok", "reason": None, "revision": "live", "hits": many})
    assert run_search(lp, searchable, "x", "--sources", "github", "--package", "beta", "--record") == 0
    text = record_file(searchable.env.repo_b, today(lp), "x").read_text()
    assert "[r4](https://example.invalid/4)" in text and "[r5]" not in text
    assert "- and 2 more not listed" in text


def test_a_record_is_never_overwritten(lp, searchable, capsys):
    s = searchable
    assert run_search(lp, s, "foo bound", "--offline", "--package", "beta", "--record") == 0
    f = record_file(s.env.repo_b, today(lp), "foo-bound")
    f.write_text(f.read_text().replace("decision: pending", "decision: reuse"))
    before = f.read_bytes()
    n = len(read_events(s.env))
    assert run_search(lp, s, "foo bound", "--offline", "--package", "beta", "--record") == 1
    assert "never overwritten" in capsys.readouterr().err
    assert f.read_bytes() == before and len(read_events(s.env)) == n


def test_record_needs_a_target(lp, searchable):
    assert run_search(lp, searchable, "foo bound", "--offline", "--record") == 2


def test_repo_dir_without_record_is_a_usage_error(lp, searchable, tmp_path):
    assert run_search(lp, searchable, "x", "--offline", "--repo-dir", str(tmp_path)) == 2


def test_record_never_lands_in_the_registry_directory(lp, searchable, capsys):
    s = searchable
    reg_dir = s.env.registry.parent
    assert run_search(lp, s, "foo bound", "--offline", "--repo-dir", str(reg_dir), "--record") == 1
    assert "registry directory" in capsys.readouterr().err
    assert not (reg_dir / "docs").exists()


def test_record_text_holds_no_local_paths(lp, searchable, tmp_path):
    s = searchable
    assert run_search(lp, s, "foo bound", "--offline", "--package", "beta", "--record") == 0
    text = record_file(s.env.repo_b, today(lp), "foo-bound").read_text()
    assert str(tmp_path) not in text and "lean-packages.yaml" not in text and "/home/" not in text


def test_other_private_claims_are_withheld_from_a_public_or_unknown_target(lp, searchable, tmp_path):
    s = searchable
    research = tmp_path / "research"
    research.mkdir()
    assert run_search(lp, s, "foo bound", "--offline", "--repo-dir", str(research), "--record") == 0
    text = record_file(research, today(lp), "foo-bound").read_text()
    assert "beta/foo-le-bar" not in text
    assert "1 registry claim(s) of other private packages matched and are not listed here." in text
    assert "- private-claims: no" in text
    assert read_events(s.env)[-1]["name"] is None


def test_a_private_target_lists_private_claims_and_says_so(lp, searchable):
    s = searchable
    reg = read_reg(s.env)
    assert reg["packages"]["alpha"]["visibility"] == "private"
    assert run_search(lp, s, "foo bound", "--offline", "--package", "alpha", "--record") == 0
    text = record_file(s.env.repo_a, today(lp), "foo-bound").read_text()
    assert "- private-claims: yes" in text and "beta/foo-le-bar" in text


def test_a_public_target_withholds_other_private_claims(lp, searchable):
    s = searchable
    reg = read_reg(s.env)
    reg["packages"]["alpha"]["visibility"] = "public"
    write_reg(s.env, reg)
    assert run_search(lp, s, "foo bound", "--offline", "--package", "alpha", "--record") == 0
    text = record_file(s.env.repo_a, today(lp), "foo-bound").read_text()
    assert "beta/foo-le-bar" not in text and "- private-claims: no" in text


def test_record_never_writes_the_registry(lp, searchable):
    before = searchable.env.registry.read_bytes()
    assert run_search(lp, searchable, "foo bound", "--offline", "--package", "beta", "--record") == 0
    assert searchable.env.registry.read_bytes() == before


def test_without_a_registry_the_record_is_written_and_the_event_is_not(lp, searchable, tmp_path, capsys):
    research = tmp_path / "research"
    research.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert lp.main(["--registry", str(elsewhere / "none.yaml"), "search", "foo bound", "--offline",
                    "--index", str(searchable.index), "--repo-dir", str(research), "--record"]) == 0
    assert record_file(research, today(lp), "foo-bound").is_file()
    assert "prior-art-searched not recorded" in capsys.readouterr().out
    assert list(elsewhere.iterdir()) == []


def test_json_reports_the_record(lp, searchable, capsys):
    assert run_search(lp, searchable, "foo bound", "--offline", "--package", "beta", "--record", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["record"] == {"path": f"docs/prior-art/{today(lp)}-foo-bound.md", "event": "prior-art-searched",
                           "withheld": 0, "private_claims": False}


def test_record_slug_truncation_keeps_distinct_terms_distinct():
    from lean_pkg.prior_art import record_slug
    base = "stochastic fubini theorem for the walsh dalang integral with a time dependent kernel"
    a, b = record_slug(base + " alpha"), record_slug(base + " beta")
    assert a != b and len(a) <= 70 and len(b) <= 70
    assert record_slug("stochastic fubini") == "stochastic-fubini"   # short terms are unchanged
    assert record_slug(base + " alpha") == a                          # deterministic
