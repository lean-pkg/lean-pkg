#!/usr/bin/env python3
"""search: local sources (registry claims, public index), deterministic scoring, fixed order."""

from __future__ import annotations

import json
import subprocess

import pytest
import yaml

from .conftest import CLI, read_events, read_reg, run_search, subprocess_env, write_reg

CLAIM = {"id": "p/stochastic-fubini", "decl": "SheRough.stochastic_fubini_ennreal",
         "summary": "Stochastic Fubini for nonnegative integrands against a white-noise integral.",
         "aliases": ["exchange of integrals", "Walsh integral"], "keywords": ["spde", "white-noise"],
         "citation": "Chen and Dalang 2015, Theorem 2.6", "status": "reusable"}


def test_tokens_and_norm(srch):
    assert srch.tokens("White-noise  Fubini's") == ["white", "noise", "fubini", "s"]
    assert srch.norm("  Exchange of   INTEGRALS ") == "exchange of integrals"


@pytest.mark.parametrize("term,expected", [
    ("Exchange of Integrals", (3, "alias")),
    ("white noise", (2, "keyword")),
    ("SPDE", (2, "keyword")),
    ("stochastic fubini", (1, "token")),
    ("dalang", (1, "token")),
    ("stochastic gumbel", (0, None)),
    ("!!!", (0, None)),
])
def test_score_claim(srch, term, expected):
    assert srch.score_claim(term, CLAIM) == expected


def test_rank_puts_experimental_and_superseded_last(srch):
    hits = [{"id": "a/old", "status": "superseded", "score": 3},
            {"id": "a/new", "status": "reusable", "score": 1},
            {"id": "a/try", "status": "experimental", "score": 2},
            {"id": "a/int", "status": "internal", "score": 2}]
    assert [h["id"] for h in srch.rank(hits)] == ["a/int", "a/new", "a/old", "a/try"]


def test_search_finds_registry_and_index_claims_in_fixed_order(lp, searchable, capsys):
    assert run_search(lp, searchable, "foo bound", "--offline") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith('lean-pkg search "foo bound" on ')
    assert out[1].startswith("registry: ok at sha256:") and out[1].endswith(", 1 hit")
    assert out[2].startswith("  [3 alias] beta/foo-le-bar  Demo.foo_le_bar  (theorem, reusable, trust comparator, pin ")
    assert out[4].startswith("index: ok at ") and out[4].endswith(", 0 hits")
    assert out[-1].startswith("consulted: registry (ok, 1 hit), index (ok, 0 hits)")


def test_index_headlines_are_claims_with_entry_trust_and_pin(lp, searchable, capsys):
    assert run_search(lp, searchable, "trivial demonstration", "--offline", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    index = [b for b in p["sources"] if b["source"] == "index"][0]
    h = index["hits"][0]
    assert h["id"] == "demo/demo-foo-le-bar" and h["decl"] == "Demo.foo_le_bar"
    assert h["trust"] == "comparator" and h["pin"] == searchable.pkg.pin
    assert h["file"] == "Demo/Main.lean" and h["kind"] == "theorem" and h["score"] == 1


def test_json_hit_shape(lp, srch, searchable, capsys):
    assert run_search(lp, searchable, "demo", "--offline", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["ok"] and p["term"] == "demo" and p["total_hits"] >= 1
    assert [b["source"] for b in p["sources"]][:2] == ["registry", "index"]
    for b in p["sources"]:
        assert set(b) == {"source", "state", "reason", "revision", "hits", "route", "partial"}
        assert b["partial"] == []
        assert b["route"] == p["route"] == "natural-language"
        for h in b["hits"]:
            if b["source"] in ("registry", "index"):
                assert set(h) == set(srch.HIT_KEYS)


def test_index_fields_act_as_keywords(lp, searchable, capsys):
    e = yaml.safe_load((searchable.index / "demo" / "entry.yaml").read_text())
    e["fields"] = ["probability"]
    (searchable.index / "demo" / "entry.yaml").write_text(yaml.safe_dump(e, sort_keys=False))
    assert run_search(lp, searchable, "Probability", "--offline", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["sources"][1]["hits"][0]["where"] == "keyword"


def test_zero_hits_is_success(lp, searchable, capsys):
    assert run_search(lp, searchable, "gumbel distribution", "--offline") == 0
    assert "consulted: registry (ok, 0 hits), index (ok, 0 hits)" in capsys.readouterr().out


def test_package_names_who_the_search_is_for_and_still_searches_everything(lp, searchable, capsys):
    assert run_search(lp, searchable, "foo bound", "--offline", "--package", "alpha") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].endswith(" for package alpha") and "beta/foo-le-bar" in out


def test_package_must_be_registered(lp, searchable, capsys):
    assert run_search(lp, searchable, "foo bound", "--offline", "--package", "nope") == 2
    assert "not in the registry" in capsys.readouterr().err


def test_sources_subset_marks_the_rest_not_selected(lp, searchable, capsys):
    assert run_search(lp, searchable, "demo", "--offline", "--sources", "index") == 0
    out = capsys.readouterr().out
    assert "registry: skipped: not selected" in out
    assert "consulted: registry (skipped: not selected), index (ok, " in out


@pytest.mark.parametrize("argv", [["demo", "--sources", "registry,bogus"], ["demo", "--sources", ","],
                                  ["!!!"]])
def test_usage_errors_exit_2(lp, searchable, argv):
    assert run_search(lp, searchable, *argv, "--offline") == 2


def test_missing_registry_is_skipped_not_zero(lp, searchable, tmp_path, capsys):
    assert lp.main(["--registry", str(tmp_path / "none.yaml"), "search", "demo", "--offline",
                    "--index", str(searchable.index)]) == 0
    out = capsys.readouterr().out
    assert "registry: skipped: no registry file" in out and str(tmp_path) not in out


def test_missing_index_is_skipped_not_zero(lp, searchable, tmp_path, capsys):
    assert lp.main(["--registry", str(searchable.env.registry), "search", "demo", "--offline",
                    "--index", str(tmp_path / "no-index")]) == 0
    assert "index: skipped: no index directory" in capsys.readouterr().out


def test_unreadable_index_entry_is_reported_and_others_searched(lp, searchable, capsys):
    (searchable.index / "broken").mkdir()
    (searchable.index / "broken" / "entry.yaml").write_text("- not\n- a mapping\n")
    assert run_search(lp, searchable, "trivial demonstration", "--offline") == 0
    out = capsys.readouterr().out
    assert "index: ok at" in out and "1 hit (1 unreadable entry not searched)" in out


def test_search_never_writes_the_registry_or_the_event_log(lp, searchable):
    before = searchable.env.registry.read_bytes()
    n = len(read_events(searchable.env))
    assert run_search(lp, searchable, "demo", "--offline") == 0
    assert searchable.env.registry.read_bytes() == before and len(read_events(searchable.env)) == n


def test_registry_revision_follows_its_content(lp, searchable, srch):
    a = srch.registry_revision(searchable.env.registry)
    reg = read_reg(searchable.env)
    reg["packages"]["beta"]["notes"] = "changed"
    write_reg(searchable.env, reg)
    assert srch.registry_revision(searchable.env.registry) != a and a.startswith("sha256:")


def test_superseded_registry_claim_listed_after_reusable(lp, searchable, capsys):
    reg = read_reg(searchable.env)
    old = dict(reg["packages"]["beta"]["claims"][0], id="beta/old-bound", status="superseded",
               superseded_by="beta/foo-le-bar")
    reg["packages"]["beta"]["claims"].insert(0, old)
    write_reg(searchable.env, reg)
    assert run_search(lp, searchable, "trivial bound", "--offline", "--json") == 0
    hits = json.loads(capsys.readouterr().out)["sources"][0]["hits"]
    assert [h["id"] for h in hits] == ["beta/foo-le-bar", "beta/old-bound"]
    assert hits[1]["status"] == "superseded"


def test_module_entry_point(searchable):
    p = subprocess.run([*CLI, "--registry", str(searchable.env.registry), "search", "foo bound",
                        "--offline", "--index", str(searchable.index)],
                       capture_output=True, text=True, env=subprocess_env())
    assert p.returncode == 0, p.stderr
    assert "beta/foo-le-bar" in p.stdout


# ---- #20 R18: the registry source scores packages, not only their cached claims ----------------

def test_search_finds_a_package_by_name_offline(lp, env, capsys):
    """alpha has no cached claims: before #20 this was "registry: ok, 0 hits"."""
    assert lp.main(["--registry", str(env.registry), "search", "alpha", "--offline", "--sources",
                    "registry"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("registry: ok at sha256:") and out[1].endswith(", 1 hit"), out
    assert out[2] == "  [3 name] package: alpha (gate-pending) — git@github.com:example/alpha.git"


def test_package_hits_score_notes_owner_project_and_repo(lp, env, srch, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["notes"] = "Stochastic heat equation moments"
    reg["packages"]["beta"]["owner_project"] = "Article-heat-moments"
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "--json", "search", "heat moments", "--offline",
                    "--sources", "registry"]) == 0
    hits = json.loads(capsys.readouterr().out)["sources"][0]["hits"]
    assert [(h["package"], h["score"], h["where"], h["hit_kind"]) for h in hits] == [
        ("alpha", 1, "token", "package"), ("beta", 1, "token", "package")]
    assert hits[0]["summary"] == "Stochastic heat equation moments" and hits[0]["status"] == "gate-pending"
    for h in hits:
        assert set(h) == set(srch.HIT_KEYS)
    assert lp.main(["--registry", str(env.registry), "--json", "search", "example", "--offline",
                    "--sources", "registry"]) == 0                       # the repo slug's owner
    assert len(json.loads(capsys.readouterr().out)["sources"][0]["hits"]) == 2


def test_claim_hits_are_marked_as_claims(lp, searchable, capsys):
    assert run_search(lp, searchable, "foo bound", "--offline", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    for b in p["sources"][:2]:
        assert all(h["hit_kind"] == "claim" for h in b["hits"])


def test_score_package(srch):
    pkg = {"notes": "Itô calculus", "owner_project": "Article-x", "repo": "https://github.com/o/heat-lean"}
    assert srch.score_package("heat-lean", "heat-lean", pkg) == (3, "name")
    assert srch.score_package("ito", "heat-lean", pkg) == (1, "token")
    assert srch.score_package("heat lean", "other", pkg) == (1, "token")
    assert srch.score_package("gumbel", "heat-lean", pkg) == (0, None)
