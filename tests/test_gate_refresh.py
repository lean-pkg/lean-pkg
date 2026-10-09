#!/usr/bin/env python3
"""#22.4: `gate NAME --receipt R --refresh-formalization` gates and regenerates formalization.yaml in
one step; refuses (writing nothing) when there is nothing to refresh."""

from __future__ import annotations

import json

import yaml

from .conftest import make_receipt, read_events, read_reg, set_lease, write_receipt


def gate(lp, env, receipt_path, *extra):
    return lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(receipt_path),
                    "--refresh-formalization", *extra])


def test_gate_then_regenerate_in_one_step(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    p = write_receipt(tmp_path, r)
    assert gate(lp, env, p, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "gate-passed" and data["status"] == "verified-private"
    assert data["formalization"]["written"] is True and data["formalization"]["registry_updated"] is True
    doc = yaml.safe_load((env.repo_a / "formalization.yaml").read_text())
    assert doc["status"]["evidence"]["commit"] == r["commit"]
    assert doc["status"]["evidence"]["gate_receipt"] == data["receipt"]
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["formalization_yaml"] == "formalization.yaml"
    assert [e["event"] for e in read_events(env)] == ["gate-passed", "updated"]


def test_nothing_to_refresh_is_refused_and_writes_nothing(lp, env, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert gate(lp, env, p) == 0
    capsys.readouterr()
    before_reg, before_ev = env.registry.read_text(), read_events(env)
    assert gate(lp, env, p) == 1
    assert "nothing to refresh" in capsys.readouterr().err
    assert env.registry.read_text() == before_reg and read_events(env) == before_ev


def test_stale_evidence_is_regenerated_on_an_identical_regate(lp, env, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert gate(lp, env, p) == 0
    target = env.repo_a / "formalization.yaml"
    doc = yaml.safe_load(target.read_text())
    doc["status"]["evidence"]["commit"] = "0" * 40             # a stale evidence key
    target.write_text(yaml.safe_dump(doc, sort_keys=False))
    capsys.readouterr()
    assert gate(lp, env, p, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "already-gated" and data["formalization"]["written"] is True
    assert yaml.safe_load(target.read_text())["status"]["evidence"]["commit"] != "0" * 40


def test_foreign_lease_refuses_before_the_gate(lp, env, tmp_path, capsys):
    set_lease(env, "alpha", agent="vera")
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert gate(lp, env, p, "--agent", "otto") == 1
    assert "lease held by vera" in capsys.readouterr().err
    assert read_reg(env)["packages"]["alpha"]["status"] == "gate-pending"
    assert not (env.repo_a / "formalization.yaml").exists()
    assert gate(lp, env, p, "--agent", "vera") == 0


def test_invalid_kept_keys_refuse_before_the_gate(lp, env, tmp_path, capsys):
    (env.repo_a / "formalization.yaml").write_text("sources: [{title: ok, relationship: nonsense}]\n")
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert gate(lp, env, p) == 1
    assert "sources/0/relationship" in capsys.readouterr().err
    assert read_reg(env)["packages"]["alpha"]["status"] == "gate-pending" and read_events(env) == []


def test_agent_without_refresh_is_a_usage_error(lp, env, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    rc = lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p), "--agent", "x"])
    assert rc == 2 and "only with --refresh-formalization" in capsys.readouterr().err
