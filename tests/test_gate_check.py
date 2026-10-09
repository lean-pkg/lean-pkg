#!/usr/bin/env python3
"""#26.2: `gate --check` validates a receipt against HEAD like `gate`, writes nothing and records no
event; it mirrors `comparator --check`, and both print the same ✓ lines for the shared binding checks."""

from __future__ import annotations

import json

from .conftest import (add_comparator_config, make_comparator_receipt, make_receipt, read_events, read_reg,
                       write_receipt)


def check(lp, env, tmp_path, receipt, *extra, name="alpha"):
    p = write_receipt(tmp_path, receipt)
    return lp.main(["--registry", str(env.registry), "gate", name, "--receipt", str(p), "--check", *extra])


def test_gate_check_passes_prints_checks_and_records_nothing(lp, env, tmp_path, capsys):
    before = env.registry.read_bytes()
    assert check(lp, env, tmp_path, make_receipt(env.repo_a)) == 0
    out = capsys.readouterr().out
    assert "receipt valid at commit" in out and "not recorded (--check)" in out
    for needle in ("✓ binding: commit", "✓ lean_toolchain", "✓ sorry_count 0", "✓ axioms"):
        assert needle in out, needle
    assert env.registry.read_bytes() == before
    assert read_events(env) == []
    assert not (env.repo_a / ".lean-receipts").exists()


def test_gate_check_json_payload(lp, env, tmp_path, capsys):
    assert check(lp, env, tmp_path, make_receipt(env.repo_a), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "gate-checked" and data["recorded"] is False
    assert data["comparator"] is None and data["checks"][0].startswith("✓ binding")


def test_gate_check_refuses_like_gate(lp, env, tmp_path, capsys):
    assert check(lp, env, tmp_path, make_receipt(env.repo_a, sorry_count=2)) == 1
    assert "sorry_count must be 0" in capsys.readouterr().err
    assert check(lp, env, tmp_path, make_receipt(env.repo_a, commit="0" * 40)) == 1
    assert "evidence-only descendant" in capsys.readouterr().err
    assert check(lp, env, tmp_path, make_receipt(env.repo_a, lean_toolchain="leanprover/lean4:v4.0.0")) == 1
    assert "toolchain mismatch" in capsys.readouterr().err
    assert read_events(env) == []


def test_gate_check_works_at_any_status_and_says_so(lp, env, tmp_path, capsys):
    # beta is verified-private: `gate` would refuse on status; --check still validates
    assert check(lp, env, tmp_path, make_receipt(env.repo_b, job_id="slurm-77"), name="beta") == 0
    assert "· status verified-private" in capsys.readouterr().out


def test_gate_check_with_comparator_validates_both_tiers(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    r = make_receipt(env.repo_a)
    cp = write_receipt(tmp_path, make_comparator_receipt(env.repo_a), name="comp.json")
    assert check(lp, env, tmp_path, r, "--comparator", str(cp)) == 0
    out = capsys.readouterr().out
    assert "with comparator (1 config(s))" in out and "✓ comparator: 1 config(s)" in out
    assert "✓ comparator commit = lower-tier receipt commit" in out
    assert read_reg(env)["packages"]["alpha"]["gate"].get("comparator") is None


def test_gate_check_with_failing_comparator_refuses(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    cp = write_receipt(tmp_path, make_comparator_receipt(env.repo_a, result="fail"), name="comp.json")
    assert check(lp, env, tmp_path, make_receipt(env.repo_a), "--comparator", str(cp)) == 1
    assert "result" in capsys.readouterr().err


def test_gate_and_comparator_check_share_the_binding_lines(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    assert check(lp, env, tmp_path, make_receipt(env.repo_a), "--json") == 0
    g = json.loads(capsys.readouterr().out)
    cp = write_receipt(tmp_path, make_comparator_receipt(env.repo_a), name="comp.json")
    assert lp.main(["--registry", str(env.registry), "comparator", "alpha", "--receipt", str(cp),
                    "--check", "--json"]) == 0
    c = json.loads(capsys.readouterr().out)
    shared = [x for x in g["checks"] if x.startswith(("✓ binding", "✓ lean_toolchain", "✓ formalization"))]
    assert shared and all(x in c["checks"] for x in shared)


def test_gate_check_rejects_write_flags(lp, env, tmp_path, capsys):
    assert check(lp, env, tmp_path, make_receipt(env.repo_a), "--refresh-formalization") == 2
    assert "writes nothing" in capsys.readouterr().err


def test_gate_check_is_not_a_write_invocation(lp):
    import argparse
    assert lp.is_write_invocation(argparse.Namespace(cmd="gate", check=True, dry_run=False)) is False
    assert lp.is_write_invocation(argparse.Namespace(cmd="gate", check=False, dry_run=False)) is True
