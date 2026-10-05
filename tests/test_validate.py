#!/usr/bin/env python3
"""validate subcommand: schema + enum + history sanity."""

from __future__ import annotations

from .conftest import read_reg, write_reg


def test_validate_pass(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0
    assert "VALID" in capsys.readouterr().out


def test_validate_bad_enum(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["status"] = "bogus"
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 1
    out = capsys.readouterr().out
    assert "bad status" in out and "INVALID" in out


def test_validate_missing_field(lp, env, capsys):
    reg = read_reg(env)
    del reg["packages"]["alpha"]["repo"]
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 1
    assert "missing field 'repo'" in capsys.readouterr().out


def test_validate_accepts_comparator_null_or_string(lp, env):
    reg = read_reg(env)
    reg["packages"]["alpha"]["gate"]["comparator"] = None
    reg["packages"]["beta"]["gate"]["comparator"] = ".lean-receipts/x-comparator.json"
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_validate_rejects_non_string_comparator(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["gate"]["comparator"] = 42
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 1
    assert "comparator" in capsys.readouterr().out
