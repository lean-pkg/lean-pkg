#!/usr/bin/env python3
"""#20 R17: record validates evidence fields (each refusal names its rule; warnings never refuse)."""

from __future__ import annotations

import json

import pytest

from .conftest import _git, read_events, read_reg, repo_head, write_reg

C = "0123456789abcdef0123456789abcdef01234567"


def rec(lp, env, name, event, *fields, json_out=False):
    argv = ["--registry", str(env.registry)] + (["--json"] if json_out else []) + ["record", name, event]
    for f in fields:
        argv += ["--field", f]
    return lp.main(argv)


def set_status(env, name, status):
    reg = read_reg(env)
    reg["packages"][name]["status"] = status
    reg["packages"][name]["visibility"] = "public" if status in ("public", "upstreamed") else "private"
    write_reg(env, reg)


def refused(lp, env, capsys, rule, *args):
    before = env.registry.read_text()
    assert rec(lp, env, *args) == 1
    err = capsys.readouterr().err
    assert f"[rule {rule}]" in err, err
    assert env.registry.read_text() == before and read_events(env) == []
    return err


# ---- doi-format ----------------------------------------------------------------------------------

@pytest.mark.parametrize("doi", ["notadoi", "10.12/x", "doi:10.5281/zenodo.1", "10.5281/"])
def test_doi_must_look_like_a_doi(lp, env, capsys, doi):
    refused(lp, env, capsys, "doi-format", "alpha", "doi-minted", f"doi={doi}", "tag=v1", f"commit={C}")


def test_doi_concept_must_look_like_a_doi(lp, env, capsys):
    refused(lp, env, capsys, "doi-format", "alpha", "doi-minted", "doi=10.5281/zenodo.2", "tag=v1",
            f"commit={C}", "concept=zenodo-1")


def test_doi_pattern_is_case_insensitive(lp, env):
    assert rec(lp, env, "alpha", "palomar-preflight-pass", f"commit={C}", "run=https://example.invalid/r/1") == 0
    assert rec(lp, env, "alpha", "doi-minted", "doi=10.5281/ZENODO.ABC", "tag=v1", f"commit={C}") == 0


# ---- mathlib-pr-url ------------------------------------------------------------------------------

@pytest.mark.parametrize("event", ["mathlib-pr-opened", "mathlib-pr-merged"])
@pytest.mark.parametrize("url", ["notaurl", "http://github.com/leanprover-community/mathlib4/pull/1",
                                 "https://gitlab.com/x/y/pull/1", "https://github.com/onlyowner"])
def test_mathlib_pr_url_must_be_https_github(lp, env, capsys, event, url):
    refused(lp, env, capsys, "mathlib-pr-url", "beta", event, f"url={url}")


def test_mathlib_pr_github_url_accepted(lp, env):
    assert rec(lp, env, "beta", "mathlib-pr-opened",
               "url=https://github.com/leanprover-community/mathlib4/pull/12345") == 0


# ---- reservoir-listed: a url not naming this package warns, never refuses ---------------------

def test_reservoir_url_for_another_package_warns(lp, env, monkeypatch, capsys):
    monkeypatch.setattr(lp, "reservoir_listing_status", lambda owner, name: 200)
    assert rec(lp, env, "beta", "reservoir-listed", "url=https://reservoir.lean-lang.org/@someone/other",
               json_out=True) == 0
    cap = capsys.readouterr()
    p = json.loads(cap.out)
    assert p["ok"] and any("names neither the package" in n for n in p["notes"])
    assert "WARN: reservoir url names neither" in cap.err
    assert read_reg(env)["packages"]["beta"]["reservoir"]["url"].endswith("/other")


def test_reservoir_url_naming_the_repo_does_not_warn(lp, env, monkeypatch, capsys):
    monkeypatch.setattr(lp, "reservoir_listing_status", lambda owner, name: 200)
    assert rec(lp, env, "beta", "reservoir-listed", "url=https://reservoir.lean-lang.org/@example/beta",
               json_out=True) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["notes"] == [] and "WARN" not in cap.err


# ---- palomar-registered: public code, tracked comparator config --------------------------------

@pytest.mark.parametrize("status", ["verified-private", "gate-pending", "scratch"])
def test_palomar_registered_refused_unless_public(lp, env, capsys, status):
    set_status(env, "beta", status)
    refused(lp, env, capsys, "palomar-registered-public", "beta", "palomar-registered",
            f"commit={repo_head(env.repo_b)}", "id=P-1", "version=1", "comparator_config=comparator.json")


def test_palomar_registered_refused_for_untracked_config(lp, env, capsys):
    set_status(env, "beta", "public")
    (env.repo_b / "untracked.json").write_text("{}\n")          # present, but not committed
    err = refused(lp, env, capsys, "palomar-config-tracked", "beta", "palomar-registered",
                  f"commit={repo_head(env.repo_b)}", "id=P-1", "version=1",
                  "comparator_config=untracked.json")
    assert "not tracked" in err


def test_palomar_registered_refused_for_absolute_config(lp, env, capsys):
    set_status(env, "beta", "public")
    refused(lp, env, capsys, "palomar-config-tracked", "beta", "palomar-registered",
            f"commit={repo_head(env.repo_b)}", "id=P-1", "version=1",
            f"comparator_config={env.repo_b / 'comparator.json'}")


def test_palomar_registered_refused_when_checkout_missing(lp, env, capsys):
    set_status(env, "beta", "upstreamed")
    reg = read_reg(env)
    reg["packages"]["beta"]["local_path"] = str(env.repo_b) + "-gone"
    write_reg(env, reg)
    refused(lp, env, capsys, "palomar-config-tracked", "beta", "palomar-registered",
            f"commit={C}", "id=P-1", "version=1", "comparator_config=comparator.json")


def test_palomar_registered_accepted_when_upstreamed_and_tracked(lp, env):
    set_status(env, "beta", "upstreamed")
    assert rec(lp, env, "beta", "palomar-registered", f"commit={C}", "id=P-1", "version=1",
               "comparator_config=comparator.json") == 0
    assert read_reg(env)["packages"]["beta"]["palomar"][0]["version"] == 1


def test_palomar_registered_tracked_config_in_a_subdirectory(lp, env):
    set_status(env, "beta", "public")
    (env.repo_b / "comparator").mkdir()
    (env.repo_b / "comparator" / "main.json").write_text("{}\n")
    _git(env.repo_b, "add", "comparator/main.json")
    _git(env.repo_b, "commit", "-q", "-m", "config")
    assert rec(lp, env, "beta", "palomar-registered", f"commit={C}", "id=P-1", "version=1",
               "comparator_config=comparator/main.json") == 0


# ---- palomar-version: integers, compared as integers ------------------------------------------

def test_palomar_registered_non_integer_version_refused(lp, env, capsys):
    set_status(env, "beta", "public")
    refused(lp, env, capsys, "palomar-version", "beta", "palomar-registered", f"commit={C}", "id=P-1",
            "version=v1", "comparator_config=comparator.json")


def test_palomar_withdrawn_normalises_version(lp, env):
    set_status(env, "beta", "public")
    assert rec(lp, env, "beta", "palomar-registered", f"commit={C}", "id=P-1", "version=1",
               "comparator_config=comparator.json") == 0
    assert rec(lp, env, "beta", "palomar-withdrawn", "id=P-1", "version=01") == 0
    assert read_reg(env)["packages"]["beta"]["palomar"][0]["withdrawn_at"]


def test_palomar_withdrawn_non_integer_version_refused(lp, env, capsys):
    refused(lp, env, capsys, "palomar-version", "beta", "palomar-withdrawn", "id=P-1", "version=one")


# ---- an identical palomar-preflight-pass is a no-op --------------------------------------------

def test_identical_preflight_pass_is_a_noop(lp, env, capsys):
    f = (f"commit={C}", "run=https://example.invalid/runs/7")
    assert rec(lp, env, "beta", "palomar-preflight-pass", *f) == 0
    capsys.readouterr()
    before, events = env.registry.read_text(), read_events(env)
    assert rec(lp, env, "beta", "palomar-preflight-pass", *f, json_out=True) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["ok"] and p["already_recorded"] is True and p["event"] == "palomar-preflight-pass"
    assert env.registry.read_text() == before and read_events(env) == events
    assert rec(lp, env, "beta", "palomar-preflight-pass", *f) == 0
    assert "already recorded" in capsys.readouterr().out


def test_preflight_pass_with_a_new_run_is_recorded(lp, env, capsys):
    assert rec(lp, env, "beta", "palomar-preflight-pass", f"commit={C}", "run=https://example.invalid/runs/7") == 0
    capsys.readouterr()
    assert rec(lp, env, "beta", "palomar-preflight-pass", f"commit={C}", "run=https://example.invalid/runs/8",
               json_out=True) == 0
    assert json.loads(capsys.readouterr().out)["already_recorded"] is False
    assert len(read_events(env)) == 2


# ---- private paths in evidence warn ------------------------------------------------------------

@pytest.mark.parametrize("pointer", ["/home/someone/private/consent.md", "/Users/someone/consent.md",
                                     "~/consent.md"])
def test_consent_pointer_with_a_home_path_warns(lp, env, capsys, pointer):
    assert rec(lp, env, "beta", "consent-recorded", "who=coauthor A", "role=coauthor",
               f"pointer={pointer}", json_out=True) == 0
    cap = capsys.readouterr()
    p = json.loads(cap.out)
    assert p["private_strings"] == [{"field": "field.pointer", "kind": "private local path"}]
    assert "private path in evidence; use a URL or repo-relative pointer" in cap.err
    assert pointer not in cap.err                          # the private text is never echoed


def test_relative_or_url_consent_pointer_does_not_warn(lp, env, capsys):
    for pointer in ("consent/2026-10-05-a.md", "https://example.invalid/consent/1"):
        assert rec(lp, env, "beta", "consent-recorded", "who=A", "role=coauthor",
                   f"pointer={pointer}", json_out=True) == 0
        cap = capsys.readouterr()
        assert json.loads(cap.out)["private_strings"] == [] and "WARN" not in cap.err
