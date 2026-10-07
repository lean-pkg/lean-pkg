#!/usr/bin/env python3
"""lean-pkg --version and lean-pkg doctor (no dependence on the real machine)."""

from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

from .conftest import _git, make_repo

TOOLS = ("lake", "elan", "git", "gh", "reuse", "bwrap", "comparator", "nanoda")


def test_version_flag(lp, capsys):
    import lean_pkg
    with pytest.raises(SystemExit) as e:
        lp.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"lean-pkg {lean_pkg.__version__}"


@pytest.fixture()
def fake_path(tmp_path, monkeypatch):
    """PATH holds only git (real, symlinked) and fake lake + bwrap."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "git").symlink_to(shutil.which("git"))
    for t in ("lake", "bwrap"):
        f = bindir / t
        f.write_text("#!/bin/sh\nexit 0\n")
        f.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return bindir


@pytest.fixture()
def installed(tmp_path, lp, monkeypatch):
    """The package appears installed outside any git checkout."""
    d = tmp_path / "site-packages" / "lean_pkg"
    d.mkdir(parents=True)
    monkeypatch.setattr(lp, "package_dir", lambda: d)
    return d


def _doctor(lp, env, capsys, *extra):
    rc = lp.main(["--registry", str(env.registry), "doctor", *extra])
    return rc, capsys.readouterr()


def test_doctor_lines_and_prefixes(lp, env, fake_path, installed, tmp_path, monkeypatch, capsys):
    idx = tmp_path / "index"
    for n in ("a", "b"):
        (idx / n).mkdir(parents=True)
        (idx / n / "entry.yaml").write_text("schema: index-entry/2\n")
    monkeypatch.setenv("LEAN_PKG_INDEX", str(idx))
    rc, cap = _doctor(lp, env, capsys)
    assert rc == 0, cap.err
    lines = cap.out.splitlines()
    assert all(line.split(" ", 1)[0] in ("ok", "warn", "n/a") for line in lines), lines
    text = cap.out
    import lean_pkg
    assert f"version: lean-pkg {lean_pkg.__version__}" in text
    assert "install: installed " in text and str(installed) in text
    assert f"registry: {env.registry} (schema version 2" in text
    assert "events: " in text and "index: " in text and "2 entries" in text
    for t in TOOLS:
        assert sum(1 for line in lines if f"tool {t}:" in line) == 1, t
    assert any(line.startswith("ok") and "tool lake:" in line for line in lines)
    assert any(line.startswith("ok") and "tool bwrap:" in line for line in lines)
    assert any(not line.startswith("ok") and "tool gh:" in line and "not on PATH" in line for line in lines)
    assert f"python: {sys.version.split()[0]}" in text


def test_doctor_editable_install_reports_branch_and_counts_without_fetching(lp, env, fake_path, tmp_path,
                                                                           monkeypatch, capsys):
    repo = make_repo(tmp_path / "checkout")
    _git(repo, "branch", "-M", "main")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    (repo / "x.txt").write_text("x\n")
    _git(repo, "add", "x.txt")
    _git(repo, "commit", "-q", "-m", "local work")
    pkgdir = repo / "src" / "lean_pkg"
    pkgdir.mkdir(parents=True)
    monkeypatch.setattr(lp, "package_dir", lambda: pkgdir)
    calls = []
    real_git = lp.git
    monkeypatch.setattr(lp, "git", lambda r, *a: (calls.append(a), real_git(r, *a))[1])
    rc, cap = _doctor(lp, env, capsys)
    assert rc == 0
    line = next(x for x in cap.out.splitlines() if "install:" in x)
    short = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    assert "editable" in line and short in line and "main" in line
    assert "0 behind, 1 ahead of origin/main" in line and "fetch to be sure" in line
    assert not any("fetch" in a for a in calls)


def test_doctor_json(lp, env, fake_path, installed, capsys):
    rc, cap = _doctor(lp, env, capsys, "--json")
    assert rc == 0
    data = json.loads(cap.out)
    assert data["ok"] is True
    assert {c["level"] for c in data["checks"]} <= {"ok", "warn", "n/a"}
    assert {c["item"] for c in data["checks"]} >= {"version", "install", "registry", "events", "index", "python",
                                                   *(f"tool {t}" for t in TOOLS)}


def test_doctor_exits_1_on_unreadable_registry(lp, env, fake_path, installed, capsys):
    env.registry.write_text("packages: [unclosed\n")
    rc, cap = _doctor(lp, env, capsys)
    assert rc == 1
    assert any(line.startswith("warn registry:") for line in cap.out.splitlines())


# --- review pass

def test_doctor_without_git_on_path_does_not_crash(lp, env, tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    rc, cap = _doctor(lp, env, capsys)
    assert rc == 0, cap.err
    lines = cap.out.splitlines()
    assert "warn tool git: not on PATH" in lines
    install = next(x for x in lines if " install:" in x)
    assert install.startswith("warn") and "git not on PATH" in install
