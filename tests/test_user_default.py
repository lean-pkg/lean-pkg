#!/usr/bin/env python3
"""The user-level default registry ~/.lean-pkg/registry (#12): resolution order and doctor's source report.

Hermetic: conftest points LEAN_PKG_HOME at an absent per-test directory; these tests build their own."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from .conftest import CLI, read_reg, subprocess_env


def _args(**kw):
    return SimpleNamespace(**{"registry": None, "events": None, **kw})


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A fresh LEAN_PKG_HOME (not yet holding `registry`), env and cwd neutral."""
    h = tmp_path / "lp-home"
    h.mkdir()
    monkeypatch.setenv("LEAN_PKG_HOME", str(h))
    monkeypatch.delenv("LEAN_PKG_REGISTRY", raising=False)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return h


def _reg_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": 3, "packages": {}}))
    return path


def test_directory_form(lp, home):
    f = _reg_file(home / "registry" / "lean-packages.yaml")
    assert lp.registry_source(_args()) == (f, "user-default")
    assert lp.registry_path(_args()) == f
    assert lp.events_path(_args()) == home / "registry" / "lean-package-events.jsonl"


def test_symlink_to_directory_form(lp, home, tmp_path):
    repo = tmp_path / "registry-repo"
    _reg_file(repo / "lean-packages.yaml")
    (home / "registry").symlink_to(repo)
    path, source = lp.registry_source(_args())
    assert source == "user-default"
    assert path == home / "registry" / "lean-packages.yaml"
    assert path.resolve() == (repo / "lean-packages.yaml").resolve()


def test_file_form(lp, home):
    f = _reg_file(home / "registry")
    assert lp.registry_source(_args()) == (f, "user-default")
    assert lp.events_path(_args()) == home / "lean-package-events.jsonl"


def test_symlink_to_file_form_resolves_to_target(lp, home, tmp_path):
    target = _reg_file(tmp_path / "shared" / "my-registry.yaml")
    (home / "registry").symlink_to(target)
    path, source = lp.registry_source(_args())
    assert source == "user-default" and path == target.resolve()
    # The event log sits beside the real file, and a save rewrites the target, not the link.
    assert lp.events_path(_args()) == target.resolve().parent / "lean-package-events.jsonl"


def test_directory_without_registry_file_falls_back_to_cwd(lp, home):
    (home / "registry").mkdir()
    path, source = lp.registry_source(_args())
    assert source == "cwd" and path.name == "lean-packages.yaml" and path.parent.name == "elsewhere"


def test_dangling_symlink_falls_back_to_cwd(lp, home, tmp_path):
    (home / "registry").symlink_to(tmp_path / "gone")
    assert lp.registry_source(_args())[1] == "cwd"
    assert "dangling" in lp.user_default_registry()[1]


def test_cwd_used_when_user_default_absent(lp, home, tmp_path):
    assert lp.registry_source(_args()) == (tmp_path / "elsewhere" / "lean-packages.yaml", "cwd")


def test_env_beats_user_default(lp, home, tmp_path, monkeypatch):
    _reg_file(home / "registry" / "lean-packages.yaml")
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(tmp_path / "env.yaml"))
    assert lp.registry_source(_args()) == (tmp_path / "env.yaml", "env")


def test_flag_beats_env_and_user_default(lp, home, tmp_path, monkeypatch):
    _reg_file(home / "registry" / "lean-packages.yaml")
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(tmp_path / "env.yaml"))
    assert lp.registry_source(_args(registry=str(tmp_path / "flag.yaml"))) == (tmp_path / "flag.yaml", "flag")


def test_home_dir_default_without_lean_pkg_home(lp, tmp_path, monkeypatch):
    fake_home = tmp_path / "fake-home"
    f = _reg_file(fake_home / ".lean-pkg" / "registry" / "lean-packages.yaml")
    monkeypatch.delenv("LEAN_PKG_HOME", raising=False)
    monkeypatch.delenv("LEAN_PKG_REGISTRY", raising=False)
    monkeypatch.setenv("HOME", str(fake_home))
    assert lp.lean_pkg_home() == fake_home / ".lean-pkg"
    assert lp.registry_source(_args()) == (f, "user-default")


def test_lean_pkg_home_override(lp, tmp_path, monkeypatch):
    fake_home = tmp_path / "fake-home"
    _reg_file(fake_home / ".lean-pkg" / "registry" / "lean-packages.yaml")
    other = _reg_file(tmp_path / "other-base" / "registry" / "lean-packages.yaml")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("LEAN_PKG_HOME", str(tmp_path / "other-base"))
    monkeypatch.delenv("LEAN_PKG_REGISTRY", raising=False)
    assert lp.registry_source(_args()) == (other, "user-default")


def test_cli_mutation_through_symlinked_user_default(env, tmp_path):
    """The lab layout: ~/.lean-pkg/registry -> the registry repo; a claim from another directory lands there."""
    base = tmp_path / "lp-home"
    base.mkdir()
    (base / "registry").symlink_to(env.registry.parent)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    p = subprocess.run([*CLI, "claim", "alpha", "--workspace", "w", "--agent", "a", "--reason", "r"],
                       cwd=elsewhere, env=subprocess_env({"LEAN_PKG_HOME": str(base)}),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert read_reg(env)["packages"]["alpha"]["agent"] == "a"
    assert env.registry.is_file() and not env.registry.is_symlink()
    assert env.events.is_file()


# doctor --------------------------------------------------------------------

@pytest.fixture()
def no_install_probe(tmp_path, lp, monkeypatch):
    d = tmp_path / "site-packages" / "lean_pkg"
    d.mkdir(parents=True)
    monkeypatch.setattr(lp, "package_dir", lambda: d)


def _doctor_json(lp, capsys, *pre):
    rc = lp.main([*pre, "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    return rc, {c["item"]: c for c in data["checks"]}


def _doctor_text(lp, capsys, *pre):
    lp.main([*pre, "doctor"])
    lines = capsys.readouterr().out.splitlines()
    return {ln.split(" ", 1)[1].split(":", 1)[0]: ln for ln in lines}


@pytest.mark.parametrize("source", ["flag", "env", "user-default", "cwd"])
def test_doctor_reports_source(lp, home, tmp_path, monkeypatch, capsys, no_install_probe, source):
    pre: list[str] = []
    if source == "flag":
        f = _reg_file(tmp_path / "flag" / "lean-packages.yaml")
        pre = ["--registry", str(f)]
    elif source == "env":
        f = _reg_file(tmp_path / "env" / "lean-packages.yaml")
        monkeypatch.setenv("LEAN_PKG_REGISTRY", str(f))
    elif source == "user-default":
        f = _reg_file(home / "registry" / "lean-packages.yaml")
    else:
        f = _reg_file(tmp_path / "elsewhere" / "lean-packages.yaml")
    lines = _doctor_text(lp, capsys, *pre)
    assert lines["registry"].startswith("ok registry:")
    assert lines["registry"].endswith(f"[source: {source}]") and str(f) in lines["registry"]
    rc, checks = _doctor_json(lp, capsys, *pre)
    assert rc == 0
    assert checks["registry"]["source"] == source
    assert set(checks["registry"]) == {"level", "item", "detail", "source"}


def test_doctor_user_default_line(lp, home, tmp_path, capsys, no_install_probe):
    lines = _doctor_text(lp, capsys)
    assert lines["user-default"].startswith("n/a user-default:") and "not configured" in lines["user-default"]
    assert lines["registry"].startswith("warn registry:") and lines["registry"].endswith("[source: cwd]")
    repo = tmp_path / "registry-repo"
    _reg_file(repo / "lean-packages.yaml")
    (home / "registry").symlink_to(repo)
    lines = _doctor_text(lp, capsys)
    assert lines["user-default"].startswith("ok user-default:")
    assert str(home / "registry" / "lean-packages.yaml") in lines["user-default"]


def test_doctor_json_missing_registry_still_has_source(lp, home, capsys, no_install_probe):
    rc, checks = _doctor_json(lp, capsys)
    assert rc == 1
    assert checks["registry"]["level"] == "warn" and checks["registry"]["source"] == "cwd"
    assert checks["user-default"]["level"] == "n/a"
    assert checks["events"]["source"] == "sibling"   # #18: the events line names its source too
