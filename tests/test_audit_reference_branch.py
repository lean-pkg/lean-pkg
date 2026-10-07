#!/usr/bin/env python3
"""audit judges receipt drift against the package's reference branch, not the checkout's HEAD."""

from __future__ import annotations

import json

from .conftest import _git


def _on_main(repo):
    _git(repo, "branch", "-M", "main")


def _audit_json(lp, env, capsys) -> list[dict]:
    assert lp.main(["--registry", str(env.registry), "audit", "--json"]) == 0
    return json.loads(capsys.readouterr().out)["findings"]


def test_feature_branch_ahead_of_receipt_is_not_drift(lp, env, fake_gh, capsys):
    _on_main(env.repo_b)
    _git(env.repo_b, "checkout", "-q", "-b", "smoke")
    (env.repo_b / "Smoke.lean").write_text("-- smoke\n")
    _git(env.repo_b, "add", "Smoke.lean")
    _git(env.repo_b, "commit", "-q", "-m", "smoke change")
    findings = [f for f in _audit_json(lp, env, capsys) if f["name"] == "beta"]
    checks = {f["check"] for f in findings}
    assert "receipt-drift" not in checks and "comparator-drift" not in checks, findings
    notes = [f for f in findings if f["check"] == "checkout-branch"]
    assert len(notes) == 1 and notes[0]["detail"] == "checkout on smoke; judged against main"


def test_source_change_on_main_is_still_drift(lp, env, fake_gh, capsys):
    _on_main(env.repo_b)
    (env.repo_b / "Real.lean").write_text("-- a real change\n")
    _git(env.repo_b, "add", "Real.lean")
    _git(env.repo_b, "commit", "-q", "-m", "source change")
    findings = [f for f in _audit_json(lp, env, capsys) if f["name"] == "beta"]
    checks = {f["check"] for f in findings}
    assert "receipt-drift" in checks and "comparator-drift" in checks, findings
    assert "checkout-branch" not in checks


def test_drift_on_main_reported_while_checkout_on_feature(lp, env, fake_gh, capsys):
    _on_main(env.repo_b)
    (env.repo_b / "Real.lean").write_text("-- a real change\n")
    _git(env.repo_b, "add", "Real.lean")
    _git(env.repo_b, "commit", "-q", "-m", "source change")
    _git(env.repo_b, "checkout", "-q", "-b", "smoke")
    findings = [f for f in _audit_json(lp, env, capsys) if f["name"] == "beta"]
    drift = [f for f in findings if f["check"] == "receipt-drift"]
    assert len(drift) == 1 and "checkout on smoke; judged against main" in drift[0]["detail"]


def test_reference_branch_prefers_origin_head(lp, tmp_path):
    from .conftest import make_repo
    origin = make_repo(tmp_path / "origin")
    _git(origin, "branch", "-M", "trunk")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "checkout", "-q", "-b", "feature")
    branch, tip = lp.reference_branch(clone)
    assert branch == "trunk"
    assert tip == _git(clone, "rev-parse", "refs/remotes/origin/trunk").stdout.strip()


def test_reference_branch_falls_back_to_head(lp, tmp_path):
    from .conftest import make_repo
    repo = make_repo(tmp_path / "r")
    _git(repo, "branch", "-M", "work")
    branch, tip = lp.reference_branch(repo)
    assert branch is None
    assert tip == _git(repo, "rev-parse", "HEAD").stdout.strip()
