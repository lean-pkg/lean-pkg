#!/usr/bin/env python3
"""#19: refusals that name the fix, the one UTC time policy, and output cosmetics."""

from __future__ import annotations

import json
import re

import pytest

from .conftest import (drop_claims, make_receipt, read_reg, repo_head, set_lease, write_receipt,
                       write_reg)


# --- refusals that name the fix -------------------------------------------------------------

def test_registry_not_found_names_source_and_fix_flag(lp, tmp_path, capsys):
    missing = tmp_path / "nowhere" / "lean-packages.yaml"
    assert lp.main(["--registry", str(missing), "list"]) == 1
    err = capsys.readouterr().err
    assert f"registry not found: {missing}" in err
    assert "resolved from flag" in err
    assert "lean-pkg init" in err and "LEAN_PKG_REGISTRY" in err and "~/.lean-pkg/registry" in err


def test_registry_not_found_names_env_source(lp, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(tmp_path / "gone.yaml"))
    assert lp.main(["validate"]) == 1
    assert "resolved from env" in capsys.readouterr().err


def test_registry_not_found_names_cwd_source(lp, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LEAN_PKG_REGISTRY", raising=False)
    monkeypatch.chdir(tmp_path)
    assert lp.main(["leases"]) == 1
    assert "resolved from cwd" in capsys.readouterr().err


def test_gate_at_verified_private_points_to_the_regate_path(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_b, job_id="slurm-77777")
    p = write_receipt(tmp_path, r)
    assert lp.main(["--registry", str(env.registry), "gate", "beta", "--receipt", str(p)]) == 1
    err = capsys.readouterr().err
    assert "lean-pkg gate beta --receipt R --regate" in err   # #67: one step, not update + gate


def test_gate_at_active_has_no_regate_hint(lp, env, tmp_path, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["status"] = "active"
    write_reg(env, reg)
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p)]) == 1
    assert "re-gate" not in capsys.readouterr().err


def test_differing_comparator_receipt_names_the_file_to_pass(lp, env, tmp_path, capsys):
    comp = dict(env.comp_b, job_id="slurm-newer")
    p = tmp_path / "newer-comparator.json"
    p.write_text(json.dumps(comp))
    assert lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt", str(p)]) == 1
    err = capsys.readouterr().err
    dest = env.repo_b / ".lean-receipts" / f"{repo_head(env.repo_b)[:12]}-comparator.json"
    assert f"{dest} already holds a different comparator receipt" in err
    assert f"--receipt {dest}" in err and "--replace" in err
    assert "remove it or pass that file instead" not in err


def test_claims_sync_missing_file_says_missing(lp, env, capsys):
    drop_claims(env.repo_b)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 1
    err = capsys.readouterr().err
    assert "claims.yaml is missing in" in err and str(env.repo_b) in err
    assert "untracked" not in err


def test_leases_expiring_refuses_a_negative_window(lp, env, capsys):
    with pytest.raises(SystemExit) as e:
        lp.main(["--registry", str(env.registry), "leases", "--expiring", "-5"])
    assert e.value.code == 2
    assert "hours must be >= 0" in capsys.readouterr().err


def test_register_ttl_help_says_ignored(lp, capsys):
    with pytest.raises(SystemExit):
        lp.main(["register", "--help"])
    assert "accepted and ignored" in " ".join(capsys.readouterr().out.split())


# --- the time policy -------------------------------------------------------------------------

def test_leases_json_now_has_second_precision(lp, env, capsys):
    set_lease(env, "alpha")
    assert lp.main(["--registry", str(env.registry), "--json", "leases"]) == 0
    now = json.loads(capsys.readouterr().out)["now"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", now), now


def test_wiki_updated_is_the_utc_date(lp, env, monkeypatch, capsys):
    monkeypatch.setattr(lp, "utc_today", lambda: "2031-01-02")
    assert lp.main(["--registry", str(env.registry), "wiki"]) == 0
    assert "updated: '2031-01-02'" in capsys.readouterr().out


def test_doctor_warns_on_naive_timestamps_and_validate_accepts_them(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["history"] = [{"ts": "2026-01-01T00:00:00", "event": "registered",
                                            "name": "alpha"}]
    write_reg(env, reg)
    set_lease(env, "beta")
    reg = read_reg(env)
    reg["packages"]["beta"]["lease"]["claimed_at"] = reg["packages"]["beta"]["lease"]["claimed_at"][:19]
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "doctor"]) == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if " time:" in x)
    assert line == "warn time: 2 naive timestamp(s) in leases/history (legacy; treated as UTC)"


def test_doctor_time_ok_when_every_timestamp_has_an_offset(lp, env, capsys):
    set_lease(env, "alpha")
    assert lp.main(["--registry", str(env.registry), "doctor"]) == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if " time:" in x)
    assert line.startswith("ok time:")


# --- cosmetics -------------------------------------------------------------------------------

def test_audit_findings_align_to_the_longest_check(lp):
    findings = [{"check": "x", "name": "a", "detail": "d"},
                {"check": "a-very-long-check-name-here", "name": "b", "detail": "e"}]
    lines = lp.render_findings(findings)[1:]      # [0] is the count line (#44: verdict first)
    assert lines[0].index("a: d") == lines[1].index("b: e")


def test_board_and_list_rows_have_no_trailing_spaces(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(line == line.rstrip() for line in lines)
