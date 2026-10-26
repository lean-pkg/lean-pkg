#!/usr/bin/env python3
"""#48: `next`, `status`, `gate` and `comparator` say when the package checkout is not on its default
branch (refs/remotes/origin/HEAD, else main/master); HEAD is still what is evaluated."""

from __future__ import annotations

import json

import pytest

from .conftest import _git, make_receipt, read_reg, repo_head, write_receipt, write_reg, patch_core


@pytest.fixture(autouse=True)
def _no_gh(lp, monkeypatch):
    def refuse(*a):
        pytest.fail(f"called gh {a}")
    patch_core(monkeypatch, "gh", refuse)


def on(repo, default="main", branch=None, detach=False):
    """Put repo on `default` (the only local branch), then on `branch` or a detached HEAD."""
    _git(repo, "branch", "-M", default)
    if branch:
        _git(repo, "checkout", "-q", "-b", branch)
    if detach:
        _git(repo, "checkout", "-q", "--detach")
    return repo


def next_json(lp, env, capsys, name="beta"):
    lp.main(["--registry", str(env.registry), "next", name, "--json"])
    return json.loads(capsys.readouterr().out)


def next_text(lp, env, capsys, name="beta"):
    lp.main(["--registry", str(env.registry), "next", name])
    return capsys.readouterr().out.splitlines()


def status_json(lp, env, capsys, name="beta"):
    assert lp.main(["--registry", str(env.registry), "status", name, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def status_text(lp, env, capsys, name="beta"):
    assert lp.main(["--registry", str(env.registry), "status", name]) == 0
    return capsys.readouterr().out.splitlines()


def branch_notes(notes):
    return [n for n in notes if n.startswith("evaluating ")]


def test_default_branch_has_no_note(lp, env, capsys):
    on(env.repo_b)
    assert branch_notes(next_json(lp, env, capsys)["notes"]) == []
    assert not any("evaluating" in ln for ln in next_text(lp, env, capsys))
    data = status_json(lp, env, capsys)
    assert data["branch"] == {"current": "main", "default": "main", "detached": False, "is_default": True}
    assert {"ok", "name", "package", "history_count"} <= set(data)
    assert not any(ln.startswith("branch:") for ln in status_text(lp, env, capsys))


def test_feature_branch_is_noted(lp, env, capsys):
    on(env.repo_b, branch="feature/x")
    msg = "evaluating branch feature/x, not the default main"
    data = next_json(lp, env, capsys)
    assert branch_notes(data["notes"]) == [msg]
    assert data["state"]["head"] == repo_head(env.repo_b)        # HEAD is still what is judged
    assert data["command"] == "lean-pkg publish beta --dry-run"
    assert f"  note: {msg}" in next_text(lp, env, capsys)
    assert status_json(lp, env, capsys)["branch"] == {"current": "feature/x", "default": "main",
                                                      "detached": False, "is_default": False}
    assert f"branch: {msg}" in status_text(lp, env, capsys)


def test_detached_head_is_noted(lp, env, capsys):
    on(env.repo_b, detach=True)
    msg = f"evaluating a detached HEAD {repo_head(env.repo_b)[:12]}, not the default main"
    assert branch_notes(next_json(lp, env, capsys)["notes"]) == [msg]
    assert f"  note: {msg}" in next_text(lp, env, capsys)
    assert status_json(lp, env, capsys)["branch"] == {"current": None, "default": "main",
                                                      "detached": True, "is_default": False}
    assert f"branch: {msg}" in status_text(lp, env, capsys)


def test_local_master_is_the_fallback_default(lp, env, capsys):
    on(env.repo_b, default="master", branch="upgrade/toolchain")
    msg = "evaluating branch upgrade/toolchain, not the default master"
    assert branch_notes(next_json(lp, env, capsys)["notes"]) == [msg]
    assert status_json(lp, env, capsys)["branch"]["default"] == "master"
    assert f"branch: {msg}" in status_text(lp, env, capsys)


def test_origin_head_wins_over_main(lp, env, capsys):
    on(env.repo_b)
    _git(env.repo_b, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    _git(env.repo_b, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert lp.default_branch(env.repo_b) == "trunk"
    assert branch_notes(next_json(lp, env, capsys)["notes"]) == [
        "evaluating branch main, not the default trunk"]


def test_no_default_branch_means_no_note(lp, env, capsys):
    on(env.repo_b, default="trunk", branch="feature/x")
    assert lp.default_branch(env.repo_b) is None
    assert branch_notes(next_json(lp, env, capsys)["notes"]) == []
    assert status_json(lp, env, capsys)["branch"] == {"current": "feature/x", "default": None,
                                                      "detached": False, "is_default": False}
    assert not any(ln.startswith("branch:") for ln in status_text(lp, env, capsys))


def test_status_json_branch_for_a_missing_checkout(lp, env, capsys, tmp_path):
    reg = read_reg(env)
    reg["packages"]["alpha"]["local_path"] = str(tmp_path / "gone")
    write_reg(env, reg)
    assert status_json(lp, env, capsys, "alpha")["branch"] == {"current": None, "default": None,
                                                               "detached": False, "is_default": False}


def test_gate_warns_off_the_default_branch_and_still_gates(lp, env, tmp_path, capsys):
    on(env.repo_a, branch="feature/x")
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p)]) == 0
    err = capsys.readouterr().err
    assert "WARN: evaluating branch feature/x, not the default main" in err
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


def test_gate_on_the_default_branch_has_no_branch_warn(lp, env, tmp_path, capsys):
    on(env.repo_a)
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p), "--check"]) == 0
    assert "evaluating" not in capsys.readouterr().err
