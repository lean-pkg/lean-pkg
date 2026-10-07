#!/usr/bin/env python3
"""audit: below-toolchain-floor, and the Lean toolchain version parser behind it."""

from __future__ import annotations

import json

import pytest

from .conftest import read_reg, write_reg

ORDER = [   # strictly increasing
    "leanprover/lean4:v4.9.0",
    "leanprover/lean4:v4.10.0-rc1",
    "leanprover/lean4:v4.10.0-rc2",
    "leanprover/lean4:v4.10.0-rc10",
    "leanprover/lean4:v4.10.0",
    "leanprover/lean4:v4.10.1",
    "leanprover/lean4:v4.34.0",
    "leanprover/lean4:v4.35.0-rc1",
    "leanprover/lean4:v4.35.0-rc2",
    "leanprover/lean4:v4.35.0",
    "leanprover/lean4:v5.0.0-rc1",
]


def test_parser_orders_the_table(lp):
    keys = [lp.parse_lean_toolchain(t) for t in ORDER]
    assert all(k is not None for k in keys)
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


@pytest.mark.parametrize("older,newer", list(zip(ORDER, ORDER[1:])))
def test_parser_pairwise(lp, older, newer):
    assert lp.parse_lean_toolchain(older) < lp.parse_lean_toolchain(newer)


def test_rc_is_older_than_its_release_and_bare_version_parses(lp):
    assert lp.parse_lean_toolchain("v4.35.0-rc2") < lp.parse_lean_toolchain("v4.35.0")
    assert lp.parse_lean_toolchain("  leanprover/lean4:v4.35.0\n") == lp.parse_lean_toolchain("v4.35.0")


@pytest.mark.parametrize("text", ["leanprover/lean4:nightly-2026-09-01", "leanprover/lean4-nightly:nightly-2026-09-01",
                                  "leanprover/lean4:stable", "v4.35", "leanprover/lean4:v4.35.0-beta1", "", None])
def test_parser_refuses_unparsed(lp, text):
    assert lp.parse_lean_toolchain(text) is None


def _audit(lp, env, capsys, *extra) -> list[dict]:
    assert lp.main(["--registry", str(env.registry), "audit", "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)["findings"]


def _set(env, name, **kw):
    reg = read_reg(env)
    reg["packages"][name].update(kw)
    write_reg(env, reg)


def test_audit_reports_below_default_floor(lp, env, fake_gh, capsys, monkeypatch):
    monkeypatch.delenv("LEAN_PKG_TOOLCHAIN_FLOOR", raising=False)
    found = [f for f in _audit(lp, env, capsys) if f["check"] == "below-toolchain-floor"]
    assert {f["name"] for f in found} == {"alpha", "beta"}       # fixtures pin v4.32.0
    assert "leanprover/lean4:v4.35.0-rc2" in found[0]["detail"]


def test_audit_floor_flag_and_env(lp, env, fake_gh, capsys, monkeypatch):
    monkeypatch.setenv("LEAN_PKG_TOOLCHAIN_FLOOR", "leanprover/lean4:v4.30.0")
    assert not [f for f in _audit(lp, env, capsys) if f["check"] == "below-toolchain-floor"]
    found = [f for f in _audit(lp, env, capsys, "--toolchain-floor", "v4.32.0")
             if f["check"] == "below-toolchain-floor"]
    assert found == []                                           # equal is not below
    found = [f for f in _audit(lp, env, capsys, "--toolchain-floor", "v4.32.1")
             if f["check"] == "below-toolchain-floor"]
    assert {f["name"] for f in found} == {"alpha", "beta"}


def test_audit_exempts_scratch_and_archived_and_flags_unparsed(lp, env, fake_gh, capsys, monkeypatch):
    monkeypatch.delenv("LEAN_PKG_TOOLCHAIN_FLOOR", raising=False)
    _set(env, "alpha", status="scratch")
    _set(env, "beta", lean_toolchain="leanprover/lean4:nightly-2026-09-01")
    findings = _audit(lp, env, capsys)
    assert not [f for f in findings if f["check"] == "below-toolchain-floor"]
    unparsed = [f for f in findings if f["check"] == "toolchain-unparsed"]
    assert len(unparsed) == 1 and unparsed[0]["name"] == "beta"
    _set(env, "alpha", status="archived")
    assert not [f for f in _audit(lp, env, capsys) if f["check"] == "below-toolchain-floor"]


def test_audit_refuses_an_unparsable_floor(lp, env, fake_gh, capsys):
    assert lp.main(["--registry", str(env.registry), "audit", "--toolchain-floor", "nightly"]) == 2
    assert "toolchain floor" in capsys.readouterr().err
