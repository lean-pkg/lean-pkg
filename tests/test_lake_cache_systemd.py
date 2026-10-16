#!/usr/bin/env python3
"""`receipt systemd --run` isolates Lake's local artifact cache (lake_hygiene): the unit gets a fresh, empty
LAKE_CACHE_DIR (removed afterwards) and LAKE_ARTIFACT_CACHE=false unless --setenv names them; LAKE_CACHE_DIR=
(which does not disable the cache) and LAKE_ARTIFACT_CACHE=true are usage errors; a log showing a root-package
module restored instead of compiled is refused. Fake systemd-run / systemctl; no Lean."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import _git
from .test_receipt_systemd import (LOG, Unit, _fixed_identity, assert_nothing_written, fake_systemd,  # noqa: F401
                                   repo, run, run_live)

SEEN = "import os, json; open({path!r}, 'w').write(json.dumps({{k: os.environ.get(k) for k in " \
       "('LAKE_CACHE_DIR', 'LAKE_ARTIFACT_CACHE')}} | {{'empty': os.listdir(os.environ['LAKE_CACHE_DIR']) == []}}))"


def test_run_gives_the_unit_a_fresh_empty_lake_cache(lp, repo, tmp_path, fake_systemd, capsys):
    _props, calls_made = fake_systemd
    seen = tmp_path / "seen.json"
    rc, _d = run_live(lp, repo, tmp_path, touch=SEEN.format(path=str(seen)))
    assert rc == 0, capsys.readouterr().err
    got = json.loads(seen.read_text())
    assert got["LAKE_ARTIFACT_CACHE"] == "false" and got["empty"] is True
    assert got["LAKE_CACHE_DIR"] and not Path(got["LAKE_CACHE_DIR"]).exists()   # removed after the run
    start = calls_made()[0]
    assert f"--setenv=LAKE_CACHE_DIR={got['LAKE_CACHE_DIR']}" in start
    assert "--setenv=LAKE_ARTIFACT_CACHE=false" in start
    assert "✓ lake cache: a fresh empty LAKE_CACHE_DIR, LAKE_ARTIFACT_CACHE=false" in capsys.readouterr().out


def test_an_explicit_cache_dir_is_used_as_given(lp, repo, tmp_path, fake_systemd, capsys):
    mine = tmp_path / "my-cache"
    mine.mkdir()
    seen = tmp_path / "seen.json"
    rc, _d = run_live(lp, repo, tmp_path, "--setenv", f"LAKE_CACHE_DIR={mine}",
                      touch=SEEN.format(path=str(seen)))
    assert rc == 0, capsys.readouterr().err
    assert json.loads(seen.read_text())["LAKE_CACHE_DIR"] == str(mine) and mine.is_dir()


@pytest.mark.parametrize("setenv, needle", [
    ("LAKE_CACHE_DIR=", "does not disable Lake's artifact cache"),
    ("LAKE_ARTIFACT_CACHE=true", "needs LAKE_ARTIFACT_CACHE=false"),
])
def test_run_refuses_a_cache_setting_that_is_not_isolated(lp, repo, tmp_path, fake_systemd, capsys, setenv,
                                                          needle):
    _props, calls_made = fake_systemd
    rc, _d = run_live(lp, repo, tmp_path, "--setenv", setenv)
    assert rc == 2 and needle in capsys.readouterr().err
    assert calls_made() == []


def test_a_restored_root_module_in_a_unit_log_is_refused(lp, repo, tmp_path, capsys):
    (repo / "lakefile.toml").write_text('name = "demo"\n[[lean_lib]]\nname = "Demo"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "lib")
    assert run(lp, repo, Unit(tmp_path, LOG + "⚠ [4/9] Replayed Demo.Main\n")) == 1
    assert "Demo.Main (Replayed)" in capsys.readouterr().err
    assert_nothing_written(repo)
