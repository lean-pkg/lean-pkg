#!/usr/bin/env python3
"""audit: a failed gh query says why (gh missing, not authenticated, no access, not found), and
--no-gh skips the GitHub checks (#32). A fake `gh` script on PATH produces each error shape."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from .conftest import read_reg, write_reg

FAKE_GH = """#!/bin/sh
# fake gh: FAKE_GH_VIEW is the stderr of `gh repo view` (exit 1); FAKE_GH_AUTH the exit of `gh auth status`
if [ "$1" = auth ]; then exit "${FAKE_GH_AUTH:-0}"; fi
if [ "$1" = repo ] && [ "$2" = view ]; then echo "$FAKE_GH_VIEW" >&2; exit 1; fi
exit 1
"""

RESOLVE = "GraphQL: Could not resolve to a Repository with the name 'example/{n}'. (repository)"


@pytest.fixture()
def gh_bin(tmp_path, monkeypatch):
    d = tmp_path / "fakebin"
    d.mkdir()
    (d / "gh").write_text(FAKE_GH)
    (d / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    return d


def _audit(lp, env, capsys, *extra) -> dict[str, dict]:
    assert lp.main(["--registry", str(env.registry), "audit", "--json", *extra]) == 0
    return {f["name"]: f for f in json.loads(capsys.readouterr().out)["findings"]
            if f["check"] in ("warn", "gh-skipped")}


def _remote_that_works(repo, n):
    """A remote whose configured URL names example/N on GitHub but resolves (insteadOf) to the checkout."""
    url = f"git@github.com:example/{n}.git"
    subprocess.run(["git", "-C", str(repo), "remote", "add", "gh", url], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", f"url.{repo}.insteadOf", url], check=True, capture_output=True)


def test_not_found_with_a_reachable_remote_is_no_access(lp, env, gh_bin, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_GH_VIEW", RESOLVE.format(n="alpha"))
    _remote_that_works(env.repo_a, "alpha")
    f = _audit(lp, env, capsys)
    assert f["alpha"]["gh"] == "no-access" and f["alpha"]["level"] == "warn"
    assert f["alpha"]["detail"].startswith("token lacks access to example/alpha: re-run `gh auth login` "
                                           "with a token that can read it")
    assert f["beta"]["gh"] == "not-found"                # no remote names it: cannot tell
    assert "not found on GitHub, or the token lacks access" in f["beta"]["detail"]


def test_not_found_when_the_remote_also_fails(lp, env, gh_bin, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_GH_VIEW", RESOLVE.format(n="alpha"))
    url = "git@github.com:example/alpha.git"
    subprocess.run(["git", "-C", str(env.repo_a), "remote", "add", "gh", url], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(env.repo_a), "config", f"url.{tmp_path / 'gone'}.insteadOf", url],
                   check=True, capture_output=True)
    f = _audit(lp, env, capsys)
    assert f["alpha"]["gh"] == "not-found" and f["alpha"]["level"] == "warn"
    assert f["alpha"]["detail"] == "repository example/alpha not found (gh and the checkout's git remote both fail)"


def test_archived_package_with_a_vanished_upstream_is_info(lp, env, gh_bin, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_GH_VIEW", RESOLVE.format(n="alpha"))
    reg = read_reg(env)
    reg["packages"]["alpha"]["status"] = "archived"
    write_reg(env, reg)
    url = "git@github.com:example/alpha.git"
    subprocess.run(["git", "-C", str(env.repo_a), "remote", "add", "gh", url], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(env.repo_a), "config", f"url.{tmp_path / 'gone'}.insteadOf", url],
                   check=True, capture_output=True)
    f = _audit(lp, env, capsys)
    assert f["alpha"]["gh"] == "not-found" and f["alpha"]["level"] == "info"
    assert f["alpha"]["detail"].startswith("archived; upstream repo not found — expected if the owner "
                                           "removed it; see notes")
    assert f["beta"]["level"] == "warn"                  # every other status still warns


def test_not_authenticated(lp, env, gh_bin, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_GH_VIEW", RESOLVE.format(n="alpha"))
    monkeypatch.setenv("FAKE_GH_AUTH", "1")
    f = _audit(lp, env, capsys)
    assert f["alpha"]["gh"] == "not-authenticated"
    assert f["alpha"]["detail"] == "gh is not authenticated: run `gh auth login` (or pass --no-gh)"
    monkeypatch.setenv("FAKE_GH_VIEW", "HTTP 401: Bad credentials (https://api.github.com/graphql)")
    monkeypatch.setenv("FAKE_GH_AUTH", "0")
    assert _audit(lp, env, capsys)["alpha"]["gh"] == "not-authenticated"


def test_forbidden_text_is_no_access(lp, env, gh_bin, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_GH_VIEW", "GraphQL: Resource not accessible by personal access token (repository)")
    f = _audit(lp, env, capsys)
    assert f["alpha"]["gh"] == "no-access" and "token lacks access to example/alpha" in f["alpha"]["detail"]


def test_other_errors_keep_the_raw_text(lp, env, gh_bin, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_GH_VIEW", "HTTP 502: bad gateway")
    f = _audit(lp, env, capsys)
    assert f["alpha"]["gh"] == "error" and f["alpha"]["detail"] == "gh query failed: HTTP 502: bad gateway"


def test_gh_missing(lp, env, capsys, monkeypatch, tmp_path):
    empty = tmp_path / "nogh"
    empty.mkdir()
    for tool in ("git",):                                # keep git, drop gh
        real = subprocess.run(["sh", "-c", f"command -v {tool}"], capture_output=True, text=True).stdout.strip()
        (empty / tool).symlink_to(real)
    monkeypatch.setenv("PATH", str(empty))
    f = _audit(lp, env, capsys)
    assert f["alpha"]["gh"] == "gh-missing"
    assert f["alpha"]["detail"] == "gh is not installed: gh checks skipped (install gh, or pass --no-gh)"


def test_no_gh_skips_every_query(lp, env, gh_bin, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_GH_VIEW", RESOLVE.format(n="alpha"))
    f = _audit(lp, env, capsys, "--no-gh")
    assert {n: (x["check"], x["level"], x["detail"]) for n, x in f.items()} == {
        "alpha": ("gh-skipped", "info", "gh checks skipped (--no-gh)"),
        "beta": ("gh-skipped", "info", "gh checks skipped (--no-gh)")}
    assert lp.main(["--registry", str(env.registry), "audit", "--no-gh"]) == 0
    assert "2 package(s): gh checks skipped (--no-gh): alpha, beta" in capsys.readouterr().out
