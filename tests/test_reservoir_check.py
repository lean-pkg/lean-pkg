#!/usr/bin/env python3
"""#22.2: `reservoir check NAME`, read-only static checks of Reservoir's listing criteria."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from .conftest import _git, read_events, read_reg, write_reg

LAKEFILE = ('import Lake\nopen Lake DSL\n\npackage «beta» where\n  description := "x"\n'
            '  keywords := #["math"]\n  versionTags := optRegex "v.*"\n')
MIT = "MIT License\n\nPermission is hereby granted, free of charge\n"


@pytest.fixture()
def gh_repo(lp, monkeypatch):
    info = {"visibility": "PUBLIC", "isFork": False, "stargazerCount": 3}

    def fake(*args):
        if "view" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps(info), stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")
    monkeypatch.setattr(lp, "gh", fake)
    monkeypatch.setattr(lp, "reservoir_listing_status", lambda owner, name: 404)
    return info


def ready(env):
    (env.repo_b / "lakefile.lean").write_text(LAKEFILE)
    (env.repo_b / "LICENSE").write_text(MIT)
    _git(env.repo_b, "add", "lakefile.lean", "LICENSE")
    _git(env.repo_b, "commit", "-q", "-m", "lakefile")
    _git(env.repo_b, "tag", "v1.0.0")
    reg = read_reg(env)
    reg["packages"]["beta"]["license_spdx"] = "MIT"
    write_reg(env, reg)


def check(lp, env, capsys, *extra):
    rc = lp.main(["--registry", str(env.registry), "reservoir", "check", "beta", "--json", *extra])
    return rc, json.loads(capsys.readouterr().out)


def fails(data):
    return " | ".join(c["label"] for c in data["checklist"] if c["status"] == "fail")


def test_an_eligible_package_passes(lp, env, gh_repo, capsys):
    ready(env)
    before = env.registry.read_text()
    rc, data = check(lp, env, capsys)
    assert rc == 0 and data["eligible"] is True, fails(data)
    labels = " | ".join(c["label"] for c in data["checklist"])
    assert "not listed on Reservoir yet" in labels and "lakefile.lean sets description" in labels
    assert data["record"] is None
    assert env.registry.read_text() == before and read_events(env) == []


def test_private_fork_with_one_star_is_not_eligible(lp, env, gh_repo, capsys):
    ready(env)
    gh_repo.update(visibility="PRIVATE", isFork=True, stargazerCount=1)
    rc, data = check(lp, env, capsys)
    f = fails(data)
    assert rc == 1 and "is private" in f and "is a fork" in f and "1 star(s); Reservoir needs at least 2" in f


def test_missing_lakefile_metadata_and_opt_out(lp, env, gh_repo, capsys):
    ready(env)
    (env.repo_b / "lakefile.lean").write_text("package beta where\n  reservoir := false\n")
    rc, data = check(lp, env, capsys)
    assert rc == 1 and "opted out of Reservoir" in fails(data)
    assert any(c["status"] == "warn" and "no description, keywords, versionTags" in c["label"]
               for c in data["checklist"])


def test_unrecorded_listing_prints_the_record_command(lp, env, gh_repo, monkeypatch, capsys):
    ready(env)
    monkeypatch.setattr(lp, "reservoir_listing_status", lambda owner, name: 200)
    rc, data = check(lp, env, capsys)
    assert rc == 0
    assert data["record"] == ("lean-pkg record beta reservoir-listed "
                              "--field url=https://reservoir.lean-lang.org/@example/beta")


def test_offline_skips_the_api_and_no_manifest_fails(lp, env, gh_repo, monkeypatch, capsys):
    monkeypatch.setattr(lp, "reservoir_listing_status", lambda *a: pytest.fail("queried"))
    rc, data = check(lp, env, capsys, "--offline")
    assert rc == 1                                              # the fixture repo has no lakefile
    assert "no lakefile.lean or lakefile.toml" in fails(data)
    assert any(c["label"] == "Reservoir listing not queried (--offline)" for c in data["checklist"])


def test_bare_reservoir_is_a_usage_error(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "reservoir"]) == 2
    assert "reservoir check NAME" in capsys.readouterr().err


def set_status(env, status, **extra):
    reg = read_reg(env)
    reg["packages"]["beta"]["status"] = status
    reg["packages"]["beta"].update(extra)
    write_reg(env, reg)


def test_pre_promote_private_repo_is_pending_promote_and_still_fails(lp, env, gh_repo, capsys):
    ready(env)                                   # beta is verified-private: promote has not run
    gh_repo.update(visibility="PRIVATE")
    rc, data = check(lp, env, capsys)
    assert rc == 1 and data["eligible"] is False and data["pre_promote"] is True
    pending = data["pending_promote"]
    assert any("example/beta is private; `lean-pkg promote beta --yes` makes it public" in p for p in pending)
    assert any("not listed on Reservoir" in p for p in pending)        # the 404 is pending too
    assert all(c["status"] != "pass" for c in data["checklist"] if c["label"] in pending)
    rc = lp.main(["--registry", str(env.registry), "reservoir", "check", "beta"])
    out = capsys.readouterr().out
    assert rc == 1 and "✗  pending promote: example/beta is private" in out
    assert out.strip().splitlines()[-1].startswith("pre-promote: beta is verified-private; 2 GitHub-dependent")
    assert out.strip().splitlines()[-2] == ("not eligible yet (pre-promote): promote --yes will satisfy the ✗ "
                                            "items above")
    assert "fix every ✗" not in out


def test_pre_promote_summary_counts_other_failures(lp, env, gh_repo, capsys):
    ready(env)
    gh_repo.update(visibility="PRIVATE", stargazerCount=0)
    rc = lp.main(["--registry", str(env.registry), "reservoir", "check", "beta"])
    out = capsys.readouterr().out
    assert rc == 1 and "; 1 other ✗ to fix first" in out
    assert "not eligible: fix every ✗ above" in out and "not eligible yet" not in out


def test_pre_promote_repo_not_on_github_says_what_to_do(lp, env, gh_repo, capsys):
    ready(env)
    set_status(env, "verified-private", repo="/srv/git/beta.git")
    rc, data = check(lp, env, capsys)
    assert rc == 1 and any("is not a GitHub URL yet" in p and "promote beta --yes" in p
                           for p in data["pending_promote"])


def test_public_package_private_repo_is_a_plain_failure(lp, env, gh_repo, capsys):
    ready(env)
    set_status(env, "public", visibility="public")
    gh_repo.update(visibility="PRIVATE")
    rc, data = check(lp, env, capsys)
    assert rc == 1 and data["pre_promote"] is False and data["pending_promote"] == []
    assert "example/beta is private (Reservoir lists public repositories" in fails(data)


@pytest.mark.parametrize("tc,kind", [("leanprover/lean4:v4.35.0-rc2", "a release candidate"),
                                     ("leanprover/lean4:nightly-2026-10-01", "a nightly")])
def test_rc_or_nightly_toolchain_warns(lp, env, gh_repo, capsys, tc, kind):
    ready(env)
    (env.repo_b / "lean-toolchain").write_text(tc + "\n")
    rc, data = check(lp, env, capsys)
    warn = [c["label"] for c in data["checklist"] if c["status"] == "warn" and "lean-toolchain" in c["label"]]
    assert warn == [f"lean-toolchain {tc} is {kind}; Reservoir may display it poorly "
                    f"(prefer a release toolchain for the listed tag)"]
    assert rc == 0                               # a WARN, never a refusal


def test_release_toolchain_passes(lp, env, gh_repo, capsys):
    ready(env)
    rc, data = check(lp, env, capsys)
    assert any(c["status"] == "pass" and c["label"].endswith("is a release") for c in data["checklist"])
