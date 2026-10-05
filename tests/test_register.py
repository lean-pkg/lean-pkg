#!/usr/bin/env python3
"""register: the validated, deduping write path for new packages."""

from __future__ import annotations

from .conftest import (MATHLIB_MANIFEST, TOOLCHAIN, make_repo, read_events,
                       read_reg)


def register(lp, env, name, path, *extra):
    return lp.main(["--registry", str(env.registry), "register", name,
                    "--repo", f"git@github.com:example/{name}.git",
                    "--path", str(path), *extra])


def test_register_creates_entry_with_autodetect(lp, env, tmp_path):
    repo = make_repo(tmp_path / "gamma-repo",
                     extra_files={"lake-manifest.json": MATHLIB_MANIFEST})
    rc = register(lp, env, "gamma", repo)
    assert rc == 0
    pkg = read_reg(env)["packages"]["gamma"]
    assert pkg["lean_toolchain"] == TOOLCHAIN
    assert pkg["mathlib_pin"] == "81a5d257c8"
    assert pkg["status"] == "active"
    assert pkg["visibility"] == "private"
    assert pkg["lease"] is None
    assert pkg["gate"]["axioms_allowed"] == ["propext", "Quot.sound",
                                             "Classical.choice"]
    assert pkg["history"][-1]["event"] == "registered"
    events = read_events(env)
    assert events[-1]["event"] == "registered"
    assert events[-1]["name"] == "gamma"


def test_register_refuses_duplicate_name(lp, env, tmp_path, capsys):
    repo = make_repo(tmp_path / "other-repo")
    rc = register(lp, env, "alpha", repo)
    assert rc == 1
    err = capsys.readouterr().err
    assert "already registered" in err and "alpha" in err
    assert read_reg(env)["packages"]["alpha"]["local_path"] == str(env.repo_a)
    assert read_events(env) == []


def test_register_refuses_duplicate_local_path(lp, env, tmp_path, capsys):
    repo = make_repo(tmp_path / "gamma-repo")
    # a different spelling of alpha's path (trailing slash) must still dedupe
    rc = register(lp, env, "gamma", str(env.repo_a) + "/")
    assert rc == 1
    err = capsys.readouterr().err
    assert "local_path already registered" in err and "alpha" in err
    assert "gamma" not in read_reg(env)["packages"]
    assert repo.is_dir()  # untouched


def test_register_dry_run_writes_nothing(lp, env, tmp_path, capsys):
    before = env.registry.read_text()
    repo = make_repo(tmp_path / "gamma-repo",
                     extra_files={"lake-manifest.json": MATHLIB_MANIFEST})
    rc = register(lp, env, "gamma", repo, "--dry-run")
    assert rc == 0
    out = capsys.readouterr().out
    assert "gamma:" in out and "DRY RUN" in out
    assert env.registry.read_text() == before
    assert read_events(env) == []


def test_register_dry_run_still_refuses_duplicate_path(lp, env, capsys):
    rc = register(lp, env, "demo", str(env.repo_a), "--dry-run")
    assert rc == 1
    err = capsys.readouterr().err
    assert "local_path already registered" in err and "alpha" in err


def test_register_missing_mathlib_warns_but_registers(lp, env, tmp_path, capsys):
    repo = make_repo(tmp_path / "delta-repo")  # no lake-manifest.json
    rc = register(lp, env, "delta", repo)
    assert rc == 0
    out = capsys.readouterr().out
    assert "warn:" in out and "lake-manifest" in out
    pkg = read_reg(env)["packages"]["delta"]
    assert pkg["mathlib_pin"] is None
    assert pkg["lean_toolchain"] == TOOLCHAIN


def test_register_metadata_workspace_agent_is_not_a_claim(lp, env, tmp_path):
    repo = make_repo(tmp_path / "eps-repo")
    rc = register(lp, env, "epsilon", repo,
                  "--workspace", "w7", "--agent", "max",
                  "--owner-project", "Article-X", "--notes", "hello")
    assert rc == 0
    pkg = read_reg(env)["packages"]["epsilon"]
    assert pkg["workspace"] == "w7" and pkg["agent"] == "max"
    assert pkg["lease"] is None  # metadata only — claiming is a separate act
    assert pkg["owner_project"] == "Article-X" and pkg["notes"] == "hello"


def test_register_initialises_comparator_null(lp, env, tmp_path, capsys):
    repo = make_repo(tmp_path / "gamma-repo")
    rc = register(lp, env, "gamma", repo, "--dry-run")
    assert rc == 0
    assert "comparator: null" in capsys.readouterr().out
