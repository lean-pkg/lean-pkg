#!/usr/bin/env python3
"""#20 R17: record validates evidence fields (each refusal names its rule; warnings never refuse)."""

from __future__ import annotations

import json

import pytest

from .conftest import _git, read_events, read_reg, repo_head, write_reg, patch_core

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
    patch_core(monkeypatch, "reservoir_listing_status", lambda owner, name: 200)
    assert rec(lp, env, "beta", "reservoir-listed", "url=https://reservoir.lean-lang.org/@someone/other",
               json_out=True) == 0
    cap = capsys.readouterr()
    p = json.loads(cap.out)
    assert p["ok"] and any("names neither the package" in n for n in p["notes"])
    assert "WARN: reservoir url names neither" in cap.err
    assert read_reg(env)["packages"]["beta"]["reservoir"]["url"].endswith("/other")


def test_reservoir_url_naming_the_repo_does_not_warn(lp, env, monkeypatch, capsys):
    patch_core(monkeypatch, "reservoir_listing_status", lambda owner, name: 200)
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


# ---- DOI-only track (#51) ------------------------------------------------------------------------

DOI = ("doi=10.5281/zenodo.7", "tag=v1", f"commit={C}")


def test_palomar_track_doi_without_palomar_still_refused(lp, env, capsys):
    set_status(env, "alpha", "public")
    before = env.registry.read_text()
    assert rec(lp, env, "alpha", "doi-minted", *DOI) == 1
    assert "rule doi-in-palomar" in capsys.readouterr().err
    assert env.registry.read_text() == before and read_events(env) == []


def test_doi_only_via_record_field(lp, env, capsys):
    set_status(env, "alpha", "public")
    assert lp.main(["--registry", str(env.registry), "update", "alpha", "--track", "doi-only",
                    "--reason", "no headline theorem"]) == 0
    hist = read_reg(env)["packages"]["alpha"]["history"][-1]
    assert hist["event"] == "updated" and "track" in hist["fields"]
    capsys.readouterr()
    assert rec(lp, env, "alpha", "doi-minted", *DOI, json_out=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["track"] == "doi-only"
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["track"] == "doi-only"
    assert pkg["doi"]["versions"][-1]["track"] == "doi-only"
    assert pkg["history"][-1]["event"] == "doi-minted" and pkg["history"][-1]["track"] == "doi-only"
    assert read_events(env)[-1]["track"] == "doi-only"
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0
    # switching back to palomar keeps the recorded doi-only version valid
    assert lp.main(["--registry", str(env.registry), "update", "alpha", "--track", "palomar",
                    "--reason", "back"]) == 0
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_doi_only_via_track_flag(lp, env, capsys):
    set_status(env, "alpha", "public")
    argv = ["--registry", str(env.registry), "record", "alpha", "doi-minted", "--track", "doi-only"]
    for f in DOI:
        argv += ["--field", f]
    assert lp.main(argv) == 0
    pkg = read_reg(env)["packages"]["alpha"]
    assert "track" not in pkg
    assert pkg["history"][-1]["track"] == "doi-only"
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_palomar_event_records_palomar_track(lp, env):
    assert rec(lp, env, "alpha", "palomar-preflight-pass", f"commit={C}", "run=https://example.invalid/r/1") == 0
    assert rec(lp, env, "alpha", "doi-minted", *DOI) == 0
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["history"][-1]["track"] == "palomar" and "track" not in pkg["doi"]["versions"][-1]


@pytest.mark.parametrize("via_field", [True, False])
def test_doi_only_refused_unless_public(lp, env, capsys, via_field):
    if via_field:
        reg = read_reg(env)
        reg["packages"]["alpha"]["track"] = "doi-only"
        write_reg(env, reg)
        err = refused(lp, env, capsys, "doi-only-public", "alpha", "doi-minted", *DOI)
    else:
        before = env.registry.read_text()
        argv = ["--registry", str(env.registry), "record", "alpha", "doi-minted", "--track", "doi-only"]
        for f in DOI:
            argv += ["--field", f]
        assert lp.main(argv) == 1
        err = capsys.readouterr().err
        assert "[rule doi-only-public]" in err and env.registry.read_text() == before
    assert "status public" in err and "gate-pending" in err


def test_track_flag_with_another_event_is_a_usage_error(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "record", "alpha", "palomar-preflight-pass",
                    "--track", "doi-only", "--field", f"commit={C}", "--field", "run=r"]) == 2


def test_update_track_needs_reason_and_a_known_value(lp, env, capsys):
    before = env.registry.read_text()
    assert lp.main(["--registry", str(env.registry), "update", "alpha", "--track", "doi-only"]) == 1
    assert "--track requires --reason" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        lp.main(["--registry", str(env.registry), "update", "alpha", "--track", "zenodo", "--reason", "x"])
    assert e.value.code == 2
    assert env.registry.read_text() == before


def test_validate_rejects_unknown_track(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["track"] = "zenodo"
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 1
    assert "bad track 'zenodo'" in capsys.readouterr().out


def test_status_fields_track(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "--json", "status", "alpha", "--fields", "track"]) == 0
    assert json.loads(capsys.readouterr().out)["package"] == {"track": None}
    assert lp.main(["--registry", str(env.registry), "update", "alpha", "--track", "doi-only",
                    "--reason", "r"]) == 0
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "--json", "status", "alpha", "--fields", "track"]) == 0
    assert json.loads(capsys.readouterr().out)["package"] == {"track": "doi-only"}
    assert lp.main(["--registry", str(env.registry), "status", "alpha"]) == 0
    assert "doi-only" in capsys.readouterr().out
