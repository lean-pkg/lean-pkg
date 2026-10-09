#!/usr/bin/env python3
"""#20 R29: audit flags receipts not tracked at the reference branch tip and two packages sharing
one checkout. Both are informational: fixed is always false and nothing is written."""

from __future__ import annotations

import json

from .conftest import _git, base_pkg, read_events, read_reg, write_reg


def _findings(lp, env, capsys, *extra) -> list[dict]:
    assert lp.main(["--registry", str(env.registry), "audit", "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)["findings"]


def _of(findings, check, name=None):
    return [f for f in findings if f["check"] == check and (name is None or f["name"] == name)]


def test_untracked_gate_and_comparator_receipts_are_flagged(lp, env, fake_gh, capsys):
    # the env fixture writes beta's receipts into .lean-receipts/ without committing them
    _git(env.repo_b, "branch", "-M", "main")
    found = _of(_findings(lp, env, capsys), "receipt-untracked", "beta")
    assert len(found) == 2, found
    gate = read_reg(env)["packages"]["beta"]["gate"]
    details = " ".join(f["detail"] for f in found)
    assert gate["receipt"] in details and gate["comparator"] in details and "at main " in details
    assert all(f["fixed"] is False for f in found)


def test_committed_receipts_are_not_flagged(lp, env, fake_gh, capsys):
    _git(env.repo_b, "branch", "-M", "main")
    _git(env.repo_b, "add", "-f", ".lean-receipts")
    _git(env.repo_b, "commit", "-q", "-m", "evidence")
    assert _of(_findings(lp, env, capsys), "receipt-untracked") == []


def test_receipt_committed_only_off_the_reference_branch_is_flagged(lp, env, fake_gh, capsys):
    _git(env.repo_b, "branch", "-M", "main")
    _git(env.repo_b, "checkout", "-q", "-b", "side")
    _git(env.repo_b, "add", "-f", ".lean-receipts")
    _git(env.repo_b, "commit", "-q", "-m", "evidence on a side branch")
    assert len(_of(_findings(lp, env, capsys), "receipt-untracked", "beta")) == 2


def test_audit_fix_never_touches_untracked_receipts(lp, env, fake_gh, capsys):
    before = env.registry.read_text()
    found = _of(_findings(lp, env, capsys, "--fix"), "receipt-untracked", "beta")
    assert found and all(f["fixed"] is False for f in found)
    assert env.registry.read_text() == before and read_events(env) == []
    assert _git(env.repo_b, "status", "--porcelain").stdout.count(".lean-receipts") == 1   # still untracked


def test_duplicate_local_paths_are_flagged_on_both_packages(lp, env, fake_gh, capsys):
    reg = read_reg(env)
    reg["packages"]["gamma"] = base_pkg("gamma", env.repo_a, "scratch",
                                        local_path=str(env.repo_a) + "/./")   # normalises to alpha's
    write_reg(env, reg)
    before = env.registry.read_text()
    found = _of(_findings(lp, env, capsys, "--fix"), "duplicate-path")
    assert sorted(f["name"] for f in found) == ["alpha", "gamma"]
    by = {f["name"]: f for f in found}
    assert "gamma" in by["alpha"]["detail"] and "alpha" in by["gamma"]["detail"]
    assert all(f["fixed"] is False and str(env.repo_a) not in f["detail"] for f in found)   # no path echo
    assert env.registry.read_text() == before


def test_distinct_local_paths_are_not_flagged(lp, env, fake_gh, capsys):
    assert _of(_findings(lp, env, capsys), "duplicate-path") == []
