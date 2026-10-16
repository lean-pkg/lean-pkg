#!/usr/bin/env python3
"""`--repo DIR` on `next` and `gate --check` (field report): one call against another checkout than the
registered local_path, never saved to the registry."""

from __future__ import annotations

import json
import shutil

import pytest

from .conftest import _git, make_receipt, read_events, read_reg, write_reg, write_receipt


def run(lp, env, capsys, *argv):
    rc = lp.main(["--registry", str(env.registry), *argv])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def move_registered(env, tmp_path):
    """The registered path no longer holds alpha (another host's layout): point it at a missing dir."""
    reg = read_reg(env)
    reg["packages"]["alpha"]["local_path"] = str(tmp_path / "elsewhere")
    write_reg(env, reg)


@pytest.fixture(autouse=True)
def _no_gh(lp, monkeypatch):
    def refuse(*a):
        pytest.fail(f"called gh {a}")
    monkeypatch.setattr(lp, "gh", refuse)


def test_next_repo_evaluates_dir_and_writes_nothing(lp, env, capsys, tmp_path):
    move_registered(env, tmp_path)
    before = env.registry.read_bytes()
    rc, out, err = run(lp, env, capsys, "next", "alpha", "--repo", str(env.repo_a), "--json")
    data = json.loads(out)
    assert rc == 0 and data["blockers"] == []
    assert data["state"]["checkout"] == str(env.repo_a.resolve())
    # gate --check takes --repo too: it is carried into the recommended command
    assert data["command"] == f"lean-pkg gate alpha --check --receipt R --repo {env.repo_a.resolve()}"
    assert data["repo_override"] == {"path": str(env.repo_a.resolve()),
                                     "registered_path": str(tmp_path / "elsewhere"), "remote_match": None}
    assert f"using --repo {env.repo_a.resolve()} instead of the registered local_path" in err
    assert "the caller vouches" in err
    assert env.registry.read_bytes() == before and read_events(env) == []


def test_next_without_repo_has_a_null_override(lp, env, capsys):
    rc, out, err = run(lp, env, capsys, "next", "alpha", "--json")
    assert json.loads(out)["repo_override"] is None and "--repo" not in err


def test_next_repo_notes_that_other_commands_read_the_registered_path(lp, env, capsys):
    rc, out, _ = run(lp, env, capsys, "next", "beta", "--repo", str(env.repo_b), "--json")
    data = json.loads(out)
    assert rc == 0 and data["command"] == "lean-pkg publish beta --dry-run"
    assert any("reads the registered local_path, not --repo" in n for n in data["notes"])


def test_next_repo_refuses_a_directory_outside_git(lp, env, capsys, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    before = env.registry.read_bytes()
    rc, out, _ = run(lp, env, capsys, "next", "alpha", "--repo", str(plain), "--json")
    assert rc == 1 and "not inside a git work tree" in json.loads(out)["error"]
    rc, out, _ = run(lp, env, capsys, "next", "alpha", "--repo", str(tmp_path / "missing"), "--json")
    assert rc == 1 and "not a directory" in json.loads(out)["error"]
    assert env.registry.read_bytes() == before


def test_next_repo_checks_the_remote_when_both_sides_name_one(lp, env, capsys, tmp_path):
    clone = tmp_path / "alpha-clone"
    shutil.copytree(env.repo_a, clone)
    _git(clone, "remote", "add", "origin", "git@github.com:example/other.git")
    rc, out, _ = run(lp, env, capsys, "next", "alpha", "--repo", str(clone), "--json")
    assert rc == 1 and "no git remote of it is the package's repo" in json.loads(out)["error"]
    _git(clone, "remote", "set-url", "origin", "https://github.com/example/alpha")
    rc, out, err = run(lp, env, capsys, "next", "alpha", "--repo", str(clone), "--json")
    assert rc == 0 and json.loads(out)["repo_override"]["remote_match"] is True
    assert "vouches" not in err


def test_gate_check_repo_validates_against_dir(lp, env, capsys, tmp_path):
    move_registered(env, tmp_path)
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    before = env.registry.read_bytes()
    rc, out, err = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p), "--json")
    assert rc == 1                                      # the registered path is gone
    rc, out, err = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p),
                       "--repo", str(env.repo_a), "--json")
    data = json.loads(out)
    assert rc == 0 and data["repo_override"]["path"] == str(env.repo_a.resolve())
    assert "using --repo" in err
    assert env.registry.read_bytes() == before and read_events(env) == []


def test_gate_check_without_repo_has_a_null_override(lp, env, capsys, tmp_path):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    rc, out, _ = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p), "--json")
    assert rc == 0 and json.loads(out)["repo_override"] is None


def test_writing_gate_refuses_repo(lp, env, capsys, tmp_path):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    before = env.registry.read_bytes()
    rc, _, err = run(lp, env, capsys, "gate", "alpha", "--receipt", str(p), "--repo", str(env.repo_a))
    assert rc == 2 and "gate --repo goes with --check" in err
    assert env.registry.read_bytes() == before and read_events(env) == []
