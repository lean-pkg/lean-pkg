#!/usr/bin/env python3
"""scripts/bootstrap-collaborator.sh, run offline under a fake HOME.

GitHub is replaced by local bare repositories (LEAN_PKG_GIT_BASE=file://...),
and gh, pipx, elan, go, cargo and jq by stubs; lean-pkg is the code under test.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from .conftest import ROOT

SCRIPT = ROOT / "scripts" / "bootstrap-collaborator.sh"

GH_STUB = """#!/bin/sh
# gh stub: logged in; `gh api repos/X` fails for X listed in GH_DENY
case "$1 $2" in
  "auth status"|"auth setup-git") exit 0 ;;
esac
if [ "$1" = api ]; then
  for d in $GH_DENY; do [ "$2" = "repos/$d" ] && exit 1; done
  exit 0
fi
exit 0
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _world(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _exe(stubs / "gh", GH_STUB)
    for t in ("pipx", "elan", "go", "cargo", "jq"):
        _exe(stubs / t, "#!/bin/sh\nexit 0\n")
    (stubs / "python3").symlink_to(sys.executable)
    # the code under test, except that FAKE_VERSION replaces its --version line (an older install) and
    # a register call is logged to REGISTER_LOG
    _exe(stubs / "lean-pkg",
         "#!/bin/sh\n"
         'if [ "$1" = --version ] && [ -n "$FAKE_VERSION" ]; then echo "lean-pkg $FAKE_VERSION"; exit 0; fi\n'
         'if [ "$1" = register ] && [ -n "$REGISTER_LOG" ]; then echo "$*" >> "$REGISTER_LOG"; fi\n'
         f"exec {sys.executable} -c 'import sys; from lean_pkg.cli import main; sys.exit(main())' \"$@\"\n")

    base = tmp_path / "gitbase"
    (base / "lean-pkg").mkdir(parents=True)
    _git("clone", "-q", "--bare", str(ROOT), str(base / "lean-pkg" / "lean-pkg.git"))
    toy = tmp_path / "toy"
    toy.mkdir()
    (toy / "lean-toolchain").write_text("leanprover/lean4:v4.32.2\n")
    (toy / "lakefile.toml").write_text('name = "toy"\n')
    env = {
        "HOME": str(home),
        "PATH": f"{stubs}:/usr/bin:/bin",
        "LEAN_PKG_GIT_BASE": f"file://{base}",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        "PYTHONPATH": str(ROOT / "src"),
    }
    _git("init", "-q", cwd=toy)
    _git("add", "-A", cwd=toy)
    subprocess.run(["git", "commit", "-qm", "toy"], cwd=toy, check=True, capture_output=True, env={**os.environ, **env})
    (base / "owner").mkdir()
    _git("clone", "-q", "--bare", str(toy), str(base / "owner" / "toy.git"))
    return {"home": home, "env": env}


def _run(w: dict, *args, **extra):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
                          env={**w["env"], **extra})


def test_bootstrap_requires_owner_slash_package(tmp_path):
    w = _world(tmp_path)
    r = _run(w)
    assert r.returncode == 2 and "usage" in r.stderr
    assert _run(w, "toy").returncode == 2


def test_bootstrap_sets_up_registry_checkouts_and_skills_idempotently(tmp_path):
    w = _world(tmp_path)
    home = w["home"]
    r = _run(w, "owner/toy")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "READY" in r.stdout and "NOT READY" not in r.stdout
    assert (home / "src" / "lean-pkg" / "scripts" / "install_skills.py").is_file()
    assert (home / "src" / "toy" / "lean-toolchain").is_file()
    reg = home / ".lean-pkg" / "registry"
    log = subprocess.run(["git", "-C", str(reg), "log", "--format=%s"], capture_output=True, text=True).stdout.split("\n")
    assert [s for s in log if s] == ["registry: register toy", "registry: initialize lean-pkg"]
    assert subprocess.run(["git", "-C", str(reg), "status", "--porcelain"], capture_output=True, text=True).stdout == ""
    assert "toy:" in (reg / "lean-packages.yaml").read_text()
    assert any((home / ".agents" / "skills").iterdir())

    again = _run(w, "owner/toy")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "toy already registered" in again.stdout
    log2 = subprocess.run(["git", "-C", str(reg), "log", "--format=%s"], capture_output=True, text=True).stdout
    assert log2.count("registry:") == 2


def test_bootstrap_stops_at_missing_access_and_names_the_human_step(tmp_path):
    w = _world(tmp_path)
    r = _run(w, "owner/toy", GH_DENY="owner/toy")
    assert r.returncode == 1
    assert "no access to owner/toy" in r.stdout and "accept the invitation" in r.stdout
    assert "NOT READY" in r.stdout
    assert not (w["home"] / ".lean-pkg").exists()


def test_bootstrap_registers_with_url_on_a_current_lean_pkg(tmp_path):
    w = _world(tmp_path)
    log = tmp_path / "register.log"
    r = _run(w, "owner/toy", REGISTER_LOG=str(log))
    assert r.returncode == 0, r.stdout + r.stderr
    assert log.read_text().startswith("register toy --url git@github.com:owner/toy.git --path ")


def test_bootstrap_falls_back_to_repo_before_0_11_0(tmp_path):
    """--url exists from lean-pkg 0.11.0; an older install (pipx upgrade failed) gets --repo, its old spelling."""
    w = _world(tmp_path)
    log = tmp_path / "register.log"
    r = _run(w, "owner/toy", REGISTER_LOG=str(log), FAKE_VERSION="0.10.9")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "lean-pkg 0.10.9" in r.stdout
    assert log.read_text().startswith("register toy --repo git@github.com:owner/toy.git --path ")
    assert "toy:" in (w["home"] / ".lean-pkg" / "registry" / "lean-packages.yaml").read_text()


def test_bootstrap_uses_repo_when_the_version_is_unreadable(tmp_path):
    w = _world(tmp_path)
    log = tmp_path / "register.log"
    r = _run(w, "owner/toy", REGISTER_LOG=str(log), FAKE_VERSION="dev")
    assert r.returncode == 0, r.stdout + r.stderr
    assert log.read_text().startswith("register toy --repo ")
