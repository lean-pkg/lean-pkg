#!/usr/bin/env python3
"""audit: packages whose repo is not on GitHub get one quiet no-github-mirror finding each."""

from __future__ import annotations

import json
from types import SimpleNamespace

from .conftest import read_reg, write_reg, patch_core

DETAIL = "repo is not on GitHub; visibility not mirrored"


def _off_github(env, *names):
    reg = read_reg(env)
    for n in names:
        reg["packages"][n]["repo"] = f"/srv/git/{n}.git"     # a bare path: no GitHub owner/repo
    write_reg(env, reg)


def test_json_keeps_one_finding_per_package_without_path_echo(lp, env, fake_gh, capsys):
    _off_github(env, "alpha", "beta")
    assert lp.main(["--registry", str(env.registry), "audit", "--json"]) == 0
    findings = json.loads(capsys.readouterr().out)["findings"]
    mirror = [f for f in findings if f["check"] == "no-github-mirror"]
    assert sorted(f["name"] for f in mirror) == ["alpha", "beta"]
    assert all(f["detail"] == DETAIL for f in mirror)
    assert not any("cannot parse" in f["detail"] or "/srv/git" in f["detail"] for f in findings)
    assert not [f for f in findings if f["check"].startswith("gh-")]


def test_human_output_prints_one_summary_line(lp, env, fake_gh, capsys):
    _off_github(env, "alpha", "beta")
    assert lp.main(["--registry", str(env.registry), "audit"]) == 0
    out = capsys.readouterr().out
    assert "2 package(s) without a GitHub mirror (visibility not checked): alpha, beta" in out
    assert out.count("no-github-mirror") == 0 and "cannot parse" not in out and "/srv/git" not in out


def test_gh_query_failures_stay_individual_warnings(lp, env, monkeypatch, capsys):
    _off_github(env, "alpha")
    patch_core(monkeypatch, "gh", lambda *a: SimpleNamespace(returncode=1, stdout="", stderr="HTTP 502"))
    assert lp.main(["--registry", str(env.registry), "audit"]) == 0
    out = capsys.readouterr().out
    assert "gh query failed: HTTP 502" in out and "beta" in out
    assert "1 package(s) without a GitHub mirror (visibility not checked): alpha" in out


def test_long_check_names_stay_separated_from_the_package_name(lp):
    line = lp.render_finding({"check": "below-toolchain-floor", "name": "alpha", "detail": "d", "fixed": False})
    assert line.startswith("warn  below-toolchain-floor alpha: d")
    assert lp.render_finding({"check": "gh-error", "name": "a", "detail": "d", "level": "warn"}) \
        == "warn  " + "gh-error".ljust(20) + "a: d"
    assert lp.render_finding({"check": "receipt-untracked", "name": "a", "detail": "d", "level": "info"},
                             width=19) == "info  receipt-untracked  a: d"
