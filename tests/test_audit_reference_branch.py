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
    branch, tip, _ = lp.reference_branch(clone)
    assert branch == "trunk"
    assert tip == _git(clone, "rev-parse", "refs/remotes/origin/trunk").stdout.strip()


def test_reference_branch_falls_back_to_head(lp, tmp_path):
    from .conftest import make_repo
    repo = make_repo(tmp_path / "r")
    _git(repo, "branch", "-M", "work")
    branch, tip, _ = lp.reference_branch(repo)
    assert branch is None
    assert tip == _git(repo, "rev-parse", "HEAD").stdout.strip()


# --- review pass: stale local branch vs fetched origin, master fallback

import yaml

from .conftest import base_pkg, make_repo


def _clone_pair(tmp_path):
    """A bare origin on main, the package checkout A, and a second clone B."""
    seed = make_repo(tmp_path / "seed")
    _git(seed, "branch", "-M", "main")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    a, b = tmp_path / "a", tmp_path / "b"
    _git(tmp_path, "clone", "-q", str(origin), str(a))
    _git(tmp_path, "clone", "-q", str(origin), str(b))
    return origin, a, b


def _registry_for(tmp_path, repo, commit):
    rel = ".lean-receipts/r.json"
    (repo / ".lean-receipts").mkdir(exist_ok=True)
    (repo / rel).write_text(json.dumps({"commit": commit}))
    reg = {"version": 2, "packages": {"pkg": base_pkg("pkg", repo, "verified-private",
                                                      gate={"receipt": rel, "comparator": None})}}
    p = tmp_path / "reg.yaml"
    p.write_text(yaml.safe_dump(reg, sort_keys=False))
    return p


def test_fetched_origin_newer_than_stale_local_main_is_the_tip(lp, tmp_path, fake_gh, capsys):
    origin, a, b = _clone_pair(tmp_path)
    (b / "New.lean").write_text("-- new\n")
    _git(b, "add", "New.lean")
    _git(b, "commit", "-q", "-m", "new on main")
    _git(b, "push", "-q", "origin", "main")
    _git(a, "fetch", "-q", "origin")                       # local main stays behind
    new_tip = _git(a, "rev-parse", "refs/remotes/origin/main").stdout.strip()
    assert _git(a, "rev-parse", "refs/heads/main").stdout.strip() != new_tip
    _git(a, "checkout", "-q", "-b", "feature", "origin/main")
    reg = _registry_for(tmp_path, a, new_tip)
    assert lp.main(["--registry", str(reg), "audit", "--json"]) == 0
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert not [f for f in findings if f["check"] == "receipt-drift"], findings
    branch, tip, note = lp.reference_branch(a)
    assert (branch, tip, note) == ("main", new_tip, "")


def test_diverged_local_and_origin_prefers_origin_with_a_note(lp, tmp_path):
    origin, a, b = _clone_pair(tmp_path)
    for repo, f in ((b, "B.lean"), (a, "A.lean")):
        (repo / f).write_text("-- x\n")
        _git(repo, "add", f)
        _git(repo, "commit", "-q", "-m", f)
    _git(b, "push", "-q", "origin", "main")
    _git(a, "fetch", "-q", "origin")
    branch, tip, note = lp.reference_branch(a)
    assert branch == "main" and tip == _git(a, "rev-parse", "refs/remotes/origin/main").stdout.strip()
    assert "diverged" in note


def test_master_only_repo_resolves_to_master(lp, tmp_path):
    repo = make_repo(tmp_path / "m")
    _git(repo, "branch", "-M", "master")
    _git(repo, "checkout", "-q", "-b", "feature")
    branch, tip, _ = lp.reference_branch(repo)
    assert branch == "master" and tip == _git(repo, "rev-parse", "refs/heads/master").stdout.strip()


def test_detached_head_at_the_reference_tip_is_not_off_branch(lp, env, fake_gh, capsys):
    _on_main(env.repo_b)
    _git(env.repo_b, "checkout", "-q", "--detach", "main")
    findings = [f for f in _audit_json(lp, env, capsys) if f["name"] == "beta"]
    assert not [f for f in findings if f["check"] == "checkout-branch"], findings


def test_detached_head_elsewhere_is_still_reported(lp, env, fake_gh, capsys):
    _on_main(env.repo_b)
    (env.repo_b / "Smoke.lean").write_text("-- smoke\n")
    _git(env.repo_b, "add", "Smoke.lean")
    _git(env.repo_b, "commit", "-q", "-m", "smoke")
    _git(env.repo_b, "checkout", "-q", "--detach", "HEAD")
    _git(env.repo_b, "branch", "-f", "main", "HEAD~1")
    notes = [f for f in _audit_json(lp, env, capsys) if f["check"] == "checkout-branch"]
    assert [n["detail"] for n in notes] == ["checkout on detached HEAD; judged against main"]
