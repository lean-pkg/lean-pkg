#!/usr/bin/env python3
"""--json: machine-readable stdout, exit codes unchanged."""

from __future__ import annotations

import json

from .conftest import publish_evidence, read_reg, set_lease


def test_list_json_parses_and_contains_both(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "--json", "list"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["packages"]) == {"alpha", "beta"}


def test_board_json_works_off_tty(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "--json", "board"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "alpha" in data["packages"] and "beta" in data["packages"]


def test_status_json_returns_full_record(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "--json", "status", "alpha"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["package"]["name"] == "alpha"
    assert data["package"]["status"] == "gate-pending"


def test_claim_json_returns_lease(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "--json", "claim", "beta",
                  "--workspace", "w4", "--agent", "max"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["event"] == "claimed"
    lease = data["lease"]
    assert lease["agent"] == "max"
    assert lease["workspace"] == "w4"
    assert lease["ttl_hours"] == 24
    assert lease["claimed_at"] and "expires_at" in data
    # registry on disk agrees
    assert read_reg(env)["packages"]["beta"]["lease"]["agent"] == "max"


def test_audit_json_returns_findings_array(lp, env, fake_gh, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=25, ttl=24)
    rc = lp.main(["--registry", str(env.registry), "--json", "audit"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert isinstance(data["findings"], list)
    assert data["fix"] is False and data["fixed"] == 0
    assert any(f["check"] == "lease-expired" and f["name"] == "alpha" and f["fixed"] is False
               for f in data["findings"])
    # the lease is still on disk: without --fix, audit is read-only
    assert read_reg(env)["packages"]["alpha"]["lease"]["agent"] == "vera"


def test_audit_fix_json_counts_what_it_wrote(lp, env, fake_gh, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=25, ttl=24)
    rc = lp.main(["--registry", str(env.registry), "--json", "audit", "--fix"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["fix"] is True and data["fixed"] == 1
    assert read_reg(env)["packages"]["alpha"]["lease"] is None


def test_promote_dry_run_json_checklist(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    rc = lp.main(["--registry", str(env.registry), "--json", "promote", "beta"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True
    assert data["refused"] is False
    assert isinstance(data["checklist"], list) and data["checklist"]
    assert all(item["status"] in ("pass", "fail", "warn")
               for item in data["checklist"])
    # dry-run still mutated nothing
    assert read_reg(env)["packages"]["beta"]["status"] == "verified-private"


def test_validate_json(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "--json", "validate"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["valid"] is True and data["errors"] == []


def test_json_refusal_is_machine_readable(lp, env, capsys):
    set_lease(env, "alpha", agent="vera")
    rc = lp.main(["--registry", str(env.registry), "--json", "claim", "alpha",
                  "--workspace", "w4", "--agent", "max"])
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert "vera" in data["error"]


def test_json_flag_after_subcommand(lp, env, capsys):
    # `lean-pkg list --json` must work as well as `lean-pkg --json list`
    rc = lp.main(["--registry", str(env.registry), "list", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["packages"]) == {"alpha", "beta"}
    rc = lp.main(["--registry", str(env.registry), "claim", "beta", "--json",
                  "--workspace", "w4", "--agent", "max"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["lease"]["agent"] == "max"


def test_json_flag_before_subcommand_still_wins(lp, env, capsys):
    rc = lp.main(["--json", "--registry", str(env.registry), "validate"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
