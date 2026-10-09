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


# --- coverage: no claims synced is not "ok" (#24.1) ---------------------------

LEGACY_ROWS = [
    {"claim_id": "thm:main", "lean_decl": "Demo.foo_le_bar", "type": "theorem",
     "statement": "A trivial demonstration bound.", "lean_file": "Demo/Main.lean",
     "paper_ref": "Theorem 1.1", "tags": ["Demo", "bound"], "verified": True},
    {"claim_id": "lem:aux", "statement": "An auxiliary estimate\nover two lines.",
     "depends_on": ["thm:main", "missing"]},
    "not a row",
]


def write_legacy(repo, rows=None, rel="data/claims.json"):
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rows if rows is not None else {"claims": LEGACY_ROWS}))
    return rel


def test_check_named_package_without_claims_fails_with_coverage_none(lp, env, capsys):
    write_legacy(env.repo_a)
    assert lp.main(["--registry", str(env.registry), "claims", "check", "alpha"]) == 1
    out = capsys.readouterr().out
    assert "FAIL alpha (0 claim(s))" in out
    assert "alpha: coverage: none (cached_claims: 0, claims.yaml: absent)" in out
    assert "legacy: data/claims.json" in out
    assert "lean-pkg claims migrate alpha --from data/claims.json --dry-run" in out
    assert lp.main(["--registry", str(env.registry), "claims", "check", "alpha", "--json"]) == 1
    p = json.loads(capsys.readouterr().out)
    r = p["packages"][0]
    assert not p["ok"] and r["coverage"] == "none" and r["cached_claims"] == 0
    assert r["claims_yaml"] == "absent" and r["legacy_claims"] == ["data/claims.json"]
    assert "claims migrate alpha" in r["hint"]


def test_check_without_legacy_hints_writing_claims_yaml(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "claims", "check", "alpha"]) == 1
    out = capsys.readouterr().out
    assert "coverage: none" in out and "write claims.yaml (schema claims/1)" in out
    assert "legacy:" not in out


def test_check_committed_but_unsynced_claims_yaml_fails(lp, env, capsys):
    commit_claims(env.repo_b)
    assert lp.main(["--registry", str(env.registry), "claims", "check", "beta", "--json"]) == 1
    r = json.loads(capsys.readouterr().out)["packages"][0]
    assert r["coverage"] == "unsynced" and r["claims_yaml"] == "present"
    assert r["hint"].endswith("lean-pkg claims sync beta")


def test_check_all_packages_still_skips_packages_without_claims(lp, env, capsys):
    commit_claims(env.repo_b)
    sync(lp, env)
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "claims", "check"]) == 0
    assert "alpha" not in capsys.readouterr().out


def test_check_synced_package_reports_coverage_synced(lp, env, capsys):
    commit_claims(env.repo_b)
    sync(lp, env)
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "claims", "check", "beta", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)["packages"][0]
    assert r["coverage"] == "synced" and r["cached_claims"] == 1 and r["claims_yaml"] == "present"


def test_list_named_package_without_claims_states_coverage(lp, env, capsys):
    write_legacy(env.repo_a)
    assert lp.main(["--registry", str(env.registry), "claims", "list", "alpha"]) == 0
    out = capsys.readouterr().out
    assert "alpha: coverage: none (cached_claims: 0, claims.yaml: absent)" in out
    assert "legacy: data/claims.json" in out
    assert lp.main(["--registry", str(env.registry), "claims", "list", "alpha", "--json"]) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["claims"] == [] and p["coverage"] == "none" and p["cached_claims"] == 0


def test_find_legacy_claims_prunes_build_and_vcs_dirs(cl, tmp_path):
    for rel in ("data/claims.json", "paper-claims.json", ".lake/x/claims.json", "build/claims.json"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("[]")
    assert cl.find_legacy_claims(tmp_path) == ["data/claims.json", "paper-claims.json"]


# --- claims migrate (#24.1) -----------------------------------------------------

def migrate(lp, env, *extra, name="alpha"):
    return lp.main(["--registry", str(env.registry), "claims", "migrate", name, *extra])


def test_migrate_dry_run_prints_a_skeleton_and_writes_nothing(lp, env, capsys):
    write_legacy(env.repo_a)
    before = env.registry.read_bytes()
    assert migrate(lp, env, "--from", "data/claims.json", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "alpha: 2 claim(s) from data/claims.json, 2 with TODO markers, 1 non-mapping row(s) skipped" in out
    assert "schema: claims/1" in out and "- id: alpha/thm-main" in out
    assert "# TODO alpha/lem-aux: kind (defaulted to theorem), decl, file, status (defaulted to experimental)" in out
    assert "# TODO alpha/thm-main: status (defaulted to experimental)" in out
    assert "# legacy keys not mapped: verified" in out
    assert not (env.repo_a / "claims.yaml").exists()
    assert env.registry.read_bytes() == before


def test_migrate_writes_claims_yaml_but_never_the_registry(lp, env, cl, capsys):
    write_legacy(env.repo_a)
    before = env.registry.read_bytes()
    assert migrate(lp, env, "--from", "data/claims.json", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["written"] and p["claims"] == 2 and p["todo"] == 2 and p["out"] == "claims.yaml"
    assert env.registry.read_bytes() == before
    assert not env.events.exists()
    doc = yaml.safe_load((env.repo_a / "claims.yaml").read_text())
    main, aux = doc["claims"]
    assert main["decl"] == "Demo.foo_le_bar" and main["file"] == "Demo/Main.lean"
    assert main["citation"] == "Theorem 1.1" and main["keywords"] == ["bound", "demo"]
    assert main["status"] == "experimental"
    assert aux["decl"] == "TODO" and aux["file"] == "TODO.lean"
    assert aux["summary"] == "An auxiliary estimate over two lines."
    assert aux["depends_on"] == ["alpha/thm-main"]
    assert cl.validate_claims(doc, "alpha") == []        # schema-valid skeleton; sync refuses TODO.lean


def test_migrate_skeleton_is_refused_by_sync_until_todos_are_fixed(lp, env, capsys):
    write_legacy(env.repo_a)
    assert migrate(lp, env, "--from", "data/claims.json") == 0
    _git(env.repo_a, "add", "-A")
    _git(env.repo_a, "commit", "-m", "skeleton")
    capsys.readouterr()
    assert sync(lp, env, "alpha") == 1
    assert "TODO.lean is not tracked" in capsys.readouterr().err


def test_migrate_never_overwrites(lp, env, capsys):
    write_legacy(env.repo_a)
    (env.repo_a / "claims.yaml").write_text("keep\n")
    assert migrate(lp, env, "--from", "data/claims.json") == 1
    assert "already exists" in capsys.readouterr().err
    assert (env.repo_a / "claims.yaml").read_text() == "keep\n"
    assert migrate(lp, env, "--from", "data/claims.json", "--out", "claims.new.yaml") == 0
    assert (env.repo_a / "claims.new.yaml").is_file()


@pytest.mark.parametrize("payload, needle", [("[1, 2", "is not JSON"), ('{"x": 1}', "expected a list")])
def test_migrate_refuses_unreadable_legacy(lp, env, capsys, payload, needle):
    (env.repo_a / "bad.json").write_text(payload)
    assert migrate(lp, env, "--from", "bad.json") == 1
    assert needle in capsys.readouterr().err


def test_migrate_refuses_missing_source_and_absolute_out(lp, env, capsys):
    assert migrate(lp, env, "--from", "nope.json") == 1
    assert "is not a file" in capsys.readouterr().err
    write_legacy(env.repo_a)
    assert migrate(lp, env, "--from", "data/claims.json", "--out", "/tmp/x.yaml") == 2


def test_migrate_drops_local_paths(cl):
    doc, todos, _, _ = cl.migrate_rows("alpha", [{"id": "a", "statement": "see /home/someone/notes.txt"}])
    assert doc["claims"][0]["summary"] == "TODO: one sentence."
    assert cl.validate_claims(doc, "alpha") == []
