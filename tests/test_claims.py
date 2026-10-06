#!/usr/bin/env python3
"""claims/1: schema, `claims sync|list|check`, and the cached copy in the registry."""

from __future__ import annotations

import copy
import json

import pytest
import yaml

from .conftest import (CLAIMS_YAML, ROOT, _git, commit_claims, read_events, read_reg,
                       repo_head, write_reg)

FIXTURE = ROOT / "tests" / "fixtures" / "schemas" / "claims-1.yaml"


def doc():
    return yaml.safe_load(FIXTURE.read_text())


def sync(lp, env, name="beta", *extra):
    return lp.main(["--registry", str(env.registry), "claims", "sync", name, *extra])


# --- schema -----------------------------------------------------------------

def test_fixture_claims_file_is_valid(cl):
    assert cl.validate_claims(doc(), "she-rough-time-data") == []


def test_dump_orders_keys_and_round_trips(cl):
    text = FIXTURE.read_text()
    d = doc()
    assert cl.dump_claims(d) == text
    d["claims"] = [dict(reversed(list(c.items()))) for c in d["claims"]]
    assert cl.dump_claims(d) == text
    assert list(yaml.safe_load(cl.dump_claims(d))["claims"][0]) == list(cl.CLAIM_KEYS)


def test_optional_keys_default_when_absent(cl):
    d = doc()
    for k in ("aliases", "keywords", "citation", "depends_on"):
        del d["claims"][0][k]
    assert cl.validate_claims(d) == []
    n = cl.normalize_claim(d["claims"][0])
    assert n["aliases"] == [] and n["citation"] is None and list(n) == list(cl.CLAIM_KEYS)


def test_unknown_top_level_key_refused(cl):
    d = doc()
    d["registry"] = "lean-packages.yaml"
    assert any("unknown key" in x and "registry" in x for x in cl.validate_claims(d))


def test_trust_is_never_typed_into_a_claim(cl):
    d = doc()
    d["claims"][0]["trust"] = "comparator"
    assert any("unknown key" in x and "trust" in x for x in cl.validate_claims(d))


@pytest.mark.parametrize("key,value,needle", [
    ("id", "she-rough-time-data/Stochastic_Fubini", ".id"),
    ("id", "other-package/stochastic-fubini", "must start with the package name"),
    ("decl", "stochastic fubini", ".decl"),
    ("kind", "axiom", ".kind"),
    ("status", "done", ".status"),
    ("summary", "First line.\nSecond line.", ".summary"),
    ("summary", "x" * 301, ".summary"),
    ("aliases", ["ok", ""], ".aliases"),
    ("keywords", ["White Noise"], ".keywords"),
    ("file", "/abs/SheRough/Fubini.lean", ".file"),
    ("file", "SheRough/Fubini.md", ".file"),
    ("file", "../SheRough/Fubini.lean", ".file"),
    ("citation", "a\nb", ".citation"),
    ("depends_on", ["not an id"], ".depends_on"),
])
def test_bad_values_refused(cl, key, value, needle):
    d = doc()
    d["claims"][0][key] = value
    errs = cl.validate_claims(d)
    assert errs and any(needle in x for x in errs), errs


def test_missing_required_key_refused(cl):
    d = doc()
    del d["claims"][0]["decl"]
    assert any("missing key" in x and "decl" in x for x in cl.validate_claims(d))


def test_superseded_needs_superseded_by(cl):
    d = doc()
    d["claims"][1]["superseded_by"] = None
    assert any("needs superseded_by" in x for x in cl.validate_claims(d))


def test_superseded_by_needs_superseded_status(cl):
    d = doc()
    d["claims"][1]["status"] = "reusable"
    assert any("superseded_by is set" in x for x in cl.validate_claims(d))


def test_supersession_cycle_refused(cl):
    d = doc()
    d["claims"][0]["status"] = "superseded"
    d["claims"][0]["superseded_by"] = "she-rough-time-data/fubini-bounded"
    assert any("cycle" in x for x in cl.validate_claims(d))


def test_superseded_by_unknown_claim_of_this_package_refused(cl):
    d = doc()
    d["claims"][1]["superseded_by"] = "she-rough-time-data/no-such-claim"
    assert any("not a claim in this file" in x for x in cl.validate_claims(d))


def test_duplicate_ids_refused(cl):
    d = doc()
    d["claims"][1]["id"] = d["claims"][0]["id"]
    assert any("duplicate claim id" in x for x in cl.validate_claims(d))


def test_local_path_strings_refused(cl):
    d = doc()
    d["claims"][0]["aliases"] = ["see /home/someone/checkout"]
    assert any("private local path" in x for x in cl.validate_claims(d))


def test_package_mismatch_reported(cl):
    assert any("differs" in x for x in cl.validate_claims(doc(), "beta"))


def test_slug_of_decl(cl):
    assert cl.slug_of_decl("Demo.foo_le_bar") == "demo-foo-le-bar"
    assert cl.slug_of_decl("MarkovProcessPilot.integral_eval_exitTimeTrunc_Ioo") == \
        "markovprocesspilot-integral-eval-exittimetrunc-ioo"
    assert cl.slug_of_decl("«weird name»") == "weird-name"


@pytest.mark.parametrize("gate,palomar,expected", [
    ({"receipt": None}, [], "none"),
    ({"receipt": "r.json"}, [], "lower-tier"),
    ({"receipt": "r.json", "comparator": "c.json"}, [], "comparator"),
    ({"receipt": "r.json"}, [{"id": "PALOMAR-2026-10-05-000008", "version": 1}], "palomar"),
    ({"receipt": "r.json"}, [{"id": "PALOMAR-2026-10-05-000008", "version": 1,
                              "withdrawn_at": "2026-10-06"}], "lower-tier"),
])
def test_package_trust(cl, gate, palomar, expected):
    assert cl.package_trust({"gate": gate, "palomar": palomar}) == expected


# --- claims sync ---------------------------------------------------------------

def test_sync_caches_claims_with_commit_and_event(lp, env, capsys):
    head = commit_claims(env.repo_b)
    assert sync(lp, env) == 0
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["claims_synced_commit"] == head and pkg["claims_file"] == "claims.yaml"
    assert [c["id"] for c in pkg["claims"]] == ["beta/foo-le-bar"]
    assert pkg["claims"][0]["superseded_by"] is None
    ev = read_events(env)[-1]
    assert ev["event"] == "claims-synced" and ev["commit"] == head and ev["claims"] == 1
    assert pkg["history"][-1]["event"] == "claims-synced"
    assert "1 claim(s) synced" in capsys.readouterr().out


def test_sync_is_idempotent(lp, env, capsys):
    commit_claims(env.repo_b)
    assert sync(lp, env) == 0
    before = env.registry.read_bytes()
    n = len(read_events(env))
    assert sync(lp, env) == 0
    assert env.registry.read_bytes() == before and len(read_events(env)) == n
    assert "already synced" in capsys.readouterr().out


def test_sync_refuses_a_file_naming_another_package(lp, env, capsys):
    commit_claims(env.repo_b, CLAIMS_YAML.replace("package: beta", "package: alpha"))
    assert sync(lp, env) == 1
    assert "declares package 'alpha'" in capsys.readouterr().err
    assert read_reg(env)["packages"]["beta"].get("claims") in (None, [])


def test_sync_refuses_decl_missing_from_its_file(lp, env, capsys):
    commit_claims(env.repo_b, lean="theorem something_else : True := trivial\n")
    assert sync(lp, env) == 1
    assert "'foo_le_bar' does not occur in Demo/Main.lean" in capsys.readouterr().err
    assert read_events(env) == []


def test_sync_refuses_an_uncommitted_file(lp, env, capsys):
    (env.repo_b / "claims.yaml").write_text(CLAIMS_YAML)       # on disk, never committed
    assert sync(lp, env) == 1
    assert "not committed at HEAD" in capsys.readouterr().err


def test_sync_reads_head_not_the_working_tree(lp, env):
    head = commit_claims(env.repo_b)
    (env.repo_b / "claims.yaml").write_text("schema: claims/1\npackage: beta\nclaims: [oops]\n")
    assert sync(lp, env) == 0
    assert read_reg(env)["packages"]["beta"]["claims_synced_commit"] == head


def test_sync_refuses_unknown_keys_in_the_file(lp, env, capsys):
    commit_claims(env.repo_b, CLAIMS_YAML + "  trust: comparator\n")
    assert sync(lp, env) == 1
    assert "unknown key" in capsys.readouterr().err


def test_sync_honours_a_custom_claims_file(lp, env):
    reg = read_reg(env)
    reg["packages"]["beta"]["claims_file"] = "meta/claims.yaml"
    write_reg(env, reg)
    head = commit_claims(env.repo_b, rel="meta/claims.yaml")
    assert sync(lp, env) == 0
    assert read_reg(env)["packages"]["beta"]["claims_synced_commit"] == head


def test_sync_on_a_v2_registry_keeps_it_valid(lp, env):
    assert read_reg(env)["version"] == 2
    commit_claims(env.repo_b)
    assert sync(lp, env) == 0
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_sync_unknown_package_refused(lp, env, capsys):
    assert sync(lp, env, "nope") == 1
    assert "unknown package" in capsys.readouterr().err


def test_claims_without_action_is_usage_error(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "claims"]) == 2


# --- claims list ---------------------------------------------------------------

def test_list_shows_status_and_trust(lp, env, capsys):
    commit_claims(env.repo_b)
    sync(lp, env)
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "claims", "list"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["ID", "KIND", "STATUS", "TRUST", "PIN", "DECL"]
    row = out.splitlines()[1].split()
    assert row[:4] == ["beta/foo-le-bar", "theorem", "reusable", "comparator"]
    assert row[-1] == "Demo.foo_le_bar"


def test_list_json(lp, env, capsys):
    head = commit_claims(env.repo_b)
    sync(lp, env)
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "claims", "list", "beta", "--json"]) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["ok"] and p["claims"][0]["pin"] == head and p["claims"][0]["trust"] == "comparator"


def test_list_without_cached_claims_says_how_to_sync(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "claims", "list"]) == 0
    assert "lean-pkg claims sync NAME" in capsys.readouterr().out


# --- claims check --------------------------------------------------------------

def test_check_passes_after_sync(lp, env, capsys):
    commit_claims(env.repo_b)
    sync(lp, env)
    assert lp.main(["--registry", str(env.registry), "claims", "check"]) == 0
    assert "PASS beta (1 claim(s))" in capsys.readouterr().out


def test_check_fails_on_a_hand_edited_cache(lp, env, capsys):
    commit_claims(env.repo_b)
    sync(lp, env)
    reg = read_reg(env)
    reg["packages"]["beta"]["claims"][0]["kind"] = "axiom"
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "claims", "check", "beta"]) == 1
    assert "FAIL beta" in capsys.readouterr().out


def test_check_notes_a_claims_file_changed_since_sync(lp, env, capsys):
    commit_claims(env.repo_b)
    sync(lp, env)
    commit_claims(env.repo_b, CLAIMS_YAML.replace("A trivial", "A plain"))
    assert lp.main(["--registry", str(env.registry), "claims", "check", "beta"]) == 0
    assert "changed since" in capsys.readouterr().out


def test_check_skips_decl_check_without_a_checkout(lp, env, capsys):
    commit_claims(env.repo_b)
    sync(lp, env)
    reg = read_reg(env)
    reg["packages"]["beta"]["local_path"] = str(env.repo_b) + "-moved"
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "claims", "check", "beta"]) == 0
    assert "decl check skipped" in capsys.readouterr().out
