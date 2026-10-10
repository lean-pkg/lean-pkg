#!/usr/bin/env python3
"""#58: `gate` and `gate --check` run the claims/1 schema check and `claims sync`'s declaration check
on claims.yaml as tracked at the gated commit (never the working tree); `claims check --file PATH`
schema-checks a working copy."""

from __future__ import annotations

import json

from .conftest import CLAIMS_YAML, commit_claims, make_receipt, read_events, read_reg, write_receipt

ALPHA_CLAIMS = CLAIMS_YAML.replace("beta", "alpha")
LONG = "x" * 311
BAD_CLAIMS = ALPHA_CLAIMS.replace(
    "summary: A trivial demonstration bound used by the white-noise examples.", f"summary: {LONG}")


def gate(lp, env, tmp_path, *extra):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    return lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p), *extra])


def test_gate_check_valid_claims_prints_ok_line(lp, env, tmp_path, capsys):
    head = commit_claims(env.repo_a, ALPHA_CLAIMS)
    assert gate(lp, env, tmp_path, "--check") == 0
    assert f"✓ claims.yaml at {head[:12]}: schema ok (1 claims), declarations found" in capsys.readouterr().out
    assert gate(lp, env, tmp_path, "--check", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and "checks" in data and "head" in data
    assert data["claims_schema"] == {"checked": True, "commit": head, "file": "claims.yaml", "ok": True,
                                     "claims": 1, "errors": [],
                                     "declarations": {"checked": True, "ok": True, "problems": []}}


def test_gate_check_overlong_summary_fails(lp, env, tmp_path, capsys):
    head = commit_claims(env.repo_a, BAD_CLAIMS)
    before = env.registry.read_bytes()
    assert gate(lp, env, tmp_path, "--check") == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("alpha: receipt valid") and "FAILS the claims schema" in out
    assert f"✗ claims.yaml at {head[:12]}: claims[0].summary" in out
    assert gate(lp, env, tmp_path, "--check", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    cs = data["claims_schema"]
    assert data["ok"] is False and cs["checked"] is True and cs["ok"] is False and cs["claims"] == 1
    assert any("claims[0].summary" in e for e in cs["errors"])
    assert env.registry.read_bytes() == before and read_events(env) == []


def test_gate_refuses_overlong_summary_and_records_nothing(lp, env, tmp_path, capsys):
    commit_claims(env.repo_a, BAD_CLAIMS)
    before = env.registry.read_bytes()
    assert gate(lp, env, tmp_path) == 1
    err = capsys.readouterr().err
    assert "claims[0].summary" in err and "claims check --file claims.yaml" in err
    assert "new receipt" in err and "Nothing copied" in err
    assert not (env.repo_a / ".lean-receipts").exists()
    assert env.registry.read_bytes() == before and read_events(env) == []
    assert gate(lp, env, tmp_path, "--json") == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_gate_with_valid_claims_records_and_reports(lp, env, tmp_path, capsys):
    head = commit_claims(env.repo_a, ALPHA_CLAIMS)
    assert gate(lp, env, tmp_path, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "gate-passed" and data["claims_schema"]["ok"] is True
    assert data["claims_schema"]["commit"] == head
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


def test_no_claims_yaml_tracked_is_neutral(lp, env, tmp_path, capsys):
    (env.repo_a / "claims.yaml").write_text(BAD_CLAIMS)    # untracked: does not count
    assert gate(lp, env, tmp_path, "--check") == 0
    out = capsys.readouterr().out
    assert "· no claims.yaml tracked at" in out and "✗" not in out
    assert gate(lp, env, tmp_path, "--check", "--json") == 0
    cs = json.loads(capsys.readouterr().out)["claims_schema"]
    assert cs["checked"] is False and cs["ok"] is None and cs["errors"] == []


def test_the_gated_commit_is_judged_not_the_working_tree(lp, env, tmp_path, capsys):
    commit_claims(env.repo_a, ALPHA_CLAIMS)
    (env.repo_a / "claims.yaml").write_text(BAD_CLAIMS)    # dirty working copy, valid at the commit
    assert gate(lp, env, tmp_path, "--check") == 0
    assert "schema ok" in capsys.readouterr().out
    commit_claims(env.repo_a, BAD_CLAIMS)
    (env.repo_a / "claims.yaml").write_text(ALPHA_CLAIMS)  # fixed only in the working tree
    assert gate(lp, env, tmp_path, "--check") == 1
    assert "✗ claims.yaml at" in capsys.readouterr().out


NO_DECL_LEAN = "namespace Demo\n\ntheorem other_bound : True := trivial\n\nend Demo\n"
MISSING = "alpha/foo-le-bar: 'foo_le_bar' does not occur in Demo/Main.lean"


def test_gate_check_missing_declaration_fails(lp, env, tmp_path, capsys):
    head = commit_claims(env.repo_a, ALPHA_CLAIMS, lean=NO_DECL_LEAN)
    before = env.registry.read_bytes()
    assert gate(lp, env, tmp_path, "--check") == 1
    out = capsys.readouterr().out
    assert "FAILS the claims declaration check" in out
    assert f"✗ claims.yaml at {head[:12]}: {MISSING}" in out
    assert gate(lp, env, tmp_path, "--check", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    cs = data["claims_schema"]
    assert data["ok"] is False and cs["ok"] is False and cs["errors"] == []
    assert cs["declarations"] == {"checked": True, "ok": False, "problems": [MISSING]}
    assert env.registry.read_bytes() == before and read_events(env) == []


def test_gate_refuses_missing_declaration_and_records_nothing(lp, env, tmp_path, capsys):
    commit_claims(env.repo_a, ALPHA_CLAIMS, lean=NO_DECL_LEAN)
    before = env.registry.read_bytes()
    assert gate(lp, env, tmp_path) == 1
    err = capsys.readouterr().err
    assert MISSING in err and "declarations missing" in err and "claims sync alpha" in err
    assert "Nothing copied" in err and "new receipt" in err
    assert not (env.repo_a / ".lean-receipts").exists()
    assert env.registry.read_bytes() == before and read_events(env) == []


def test_declaration_removed_only_in_working_tree_still_passes(lp, env, tmp_path, capsys):
    head = commit_claims(env.repo_a, ALPHA_CLAIMS)
    (env.repo_a / "Demo" / "Main.lean").write_text(NO_DECL_LEAN)    # the commit is judged
    assert gate(lp, env, tmp_path, "--check") == 0
    assert f"✓ claims.yaml at {head[:12]}: schema ok (1 claims), declarations found" in capsys.readouterr().out


def test_schema_error_reported_before_declaration_problem(lp, env, tmp_path, capsys):
    head = commit_claims(env.repo_a, BAD_CLAIMS, lean=NO_DECL_LEAN)
    assert gate(lp, env, tmp_path, "--check") == 1
    out = capsys.readouterr().out
    assert f"✗ claims.yaml at {head[:12]}: claims[0].summary" in out and "(+1 more)" in out
    assert "FAILS the claims schema" in out
    assert gate(lp, env, tmp_path, "--check", "--json") == 1
    cs = json.loads(capsys.readouterr().out)["claims_schema"]
    assert cs["errors"] and cs["declarations"]["problems"] == [MISSING]
    assert gate(lp, env, tmp_path) == 1
    err = capsys.readouterr().err
    assert err.index("claims[0].summary") < err.index(MISSING) and "claims check --file claims.yaml" in err


def test_claims_check_file(lp, tmp_path, capsys):
    good, bad = tmp_path / "good.yaml", tmp_path / "bad.yaml"
    good.write_text(ALPHA_CLAIMS)
    bad.write_text(BAD_CLAIMS)
    reg = tmp_path / "absent" / "lean-packages.yaml"    # no registry is read
    assert lp.main(["--registry", str(reg), "claims", "check", "--file", str(good)]) == 0
    assert capsys.readouterr().out.startswith(f"PASS {good}: schema ok (1 claim(s))")
    assert lp.main(["--registry", str(reg), "claims", "check", "--file", str(bad)]) == 1
    out = capsys.readouterr().out
    assert out.startswith(f"FAIL {bad}") and "claims[0].summary" in out
    assert lp.main(["--registry", str(reg), "claims", "check", "--file", str(bad), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["path"] == str(bad) and data["claims"] == 1
    assert any("claims[0].summary" in e for e in data["errors"])
    assert lp.main(["--registry", str(reg), "claims", "check", "--file", str(good), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "path": str(good), "claims": 1, "errors": []}
    assert lp.main(["--registry", str(reg), "claims", "check", "beta", "--file", str(good)]) == 1
    assert "differs from 'beta'" in capsys.readouterr().out
    (tmp_path / "notyaml.yaml").write_text("a: [\n")
    assert lp.main(["--registry", str(reg), "claims", "check", "--file", str(tmp_path / "notyaml.yaml")]) == 1
    assert "not valid YAML" in capsys.readouterr().out
    assert not reg.parent.exists()
