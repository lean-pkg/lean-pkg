#!/usr/bin/env python3
"""promote: the public-safety scan covers what the flip publishes. A package in a subdirectory is
scanned with its whole repository; `promote --yes` first fetches every branch, tag and GitHub
pull-request head of origin (a local bare repository here; gh is faked; nothing leaves the machine),
and refuses when the fetch fails."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from lean_pkg import public_safety as ps

from .conftest import _git, publish_evidence, read_reg

TOKEN = "zorkhost"


def plant_patterns(repo: Path) -> None:
    (repo / ps.PATTERNS_FILE).write_text(TOKEN + "\n")
    with open(repo / ".git" / "info" / "exclude", "a") as f:
        f.write(ps.PATTERNS_FILE + "\n")


def promote(lp, env, capsys, *args) -> tuple[int, dict]:
    rc = lp.main(["--registry", str(env.registry), "promote", "beta", *args, "--json"])
    return rc, json.loads(capsys.readouterr().out)


def remote_clone(env, tmp_path: Path) -> Path:
    other = tmp_path / "elsewhere"
    subprocess.run(["git", "clone", "-q", str(env.repo_b.parent / f"{env.repo_b.name}-origin.git"), str(other)],
                   check=True, capture_output=True)
    return other


def has_ref(repo: Path, ref: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", ref],
                          capture_output=True).returncode == 0


def edits(fake_gh) -> list:
    return [c for c in fake_gh.calls if "edit" in c]


def test_control_promote_yes_passes_and_fetches(lp, env, fake_gh, fake_notify, capsys):
    plant_patterns(env.repo_b)
    publish_evidence(env.repo_b)
    rc, data = promote(lp, env, capsys, "--yes")
    assert rc == 0 and data["checklist"][0]["label"].startswith("fetched origin")
    assert len(edits(fake_gh)) == 1


def test_branch_pushed_elsewhere_is_fetched_and_scanned(lp, env, fake_gh, capsys, tmp_path):
    plant_patterns(env.repo_b)
    publish_evidence(env.repo_b)
    other = remote_clone(env, tmp_path)
    (other / "hosts.txt").write_text(f"ssh {TOKEN}\n")
    _git(other, "add", "hosts.txt")
    _git(other, "commit", "-qm", "ops")
    _git(other, "push", "-q", "origin", "HEAD:refs/heads/ops")
    rc, data = promote(lp, env, capsys)                  # the dry run is offline: local refs only
    assert rc == 0 and data["public_safety"]["block_hits"] == 0
    rc, data = promote(lp, env, capsys, "--yes")
    assert rc == 1 and data["refused"] is True and edits(fake_gh) == []
    assert data["public_safety"]["block_hits"] >= 1
    assert has_ref(env.repo_b, "refs/remotes/origin/ops")
    assert read_reg(env)["packages"]["beta"]["status"] == "verified-private"


def test_pull_request_heads_are_fetched_and_scanned(lp, env, fake_gh, capsys, tmp_path):
    plant_patterns(env.repo_b)
    publish_evidence(env.repo_b)
    other = remote_clone(env, tmp_path)
    (other / "pr.txt").write_text(f"{TOKEN}\n")
    _git(other, "add", "pr.txt")
    _git(other, "commit", "-qm", "pr")
    _git(other, "push", "-q", "origin", "HEAD:refs/pull/7/head")
    rc, data = promote(lp, env, capsys, "--yes")
    assert rc == 1 and edits(fake_gh) == [] and data["public_safety"]["block_hits"] >= 1
    assert has_ref(env.repo_b, "refs/remotes/origin/pr/7")


def test_fetch_failure_refuses(lp, env, fake_gh, capsys, tmp_path):
    publish_evidence(env.repo_b)
    _git(env.repo_b, "remote", "set-url", "origin", str(tmp_path / "no-such-origin.git"))
    rc, data = promote(lp, env, capsys, "--yes")
    assert rc == 1 and edits(fake_gh) == []
    first = data["checklist"][0]
    assert first["status"] == "fail" and first["label"].startswith("git fetch origin failed")


def test_subdirectory_package_promote_scans_the_whole_repository(lp, env, fake_gh, capsys):
    # beta's repository becomes a monorepo: the package moves under lean/, ops/ holds the private name
    plant_patterns(env.repo_b)
    publish_evidence(env.repo_b)
    reg = read_reg(env)
    (env.repo_b / "ops").mkdir()
    (env.repo_b / "ops" / "hosts.txt").write_text(f"ssh {TOKEN}\n")
    _git(env.repo_b, "add", "ops")
    _git(env.repo_b, "commit", "-qm", "ops")
    sub = env.repo_b / "lean"
    sub.mkdir()
    (sub / ps.PATTERNS_FILE).write_text(TOKEN + "\n")
    with open(env.repo_b / ".git" / "info" / "exclude", "a") as f:
        f.write(f"lean/{ps.PATTERNS_FILE}\n")
    reg["packages"]["beta"]["local_path"] = str(sub)
    env.registry.write_text(yaml.safe_dump(reg, sort_keys=False))
    rc, data = promote(lp, env, capsys)
    labels = [c["label"] for c in data["checklist"] if c["label"].startswith("public-safety")]
    assert rc == 1 and data["public_safety"]["scope"] == "repository"
    assert data["public_safety"]["block_hits"] >= 1
    assert labels and "(whole repository: the package is a subdirectory)" in labels[0]
    assert TOKEN not in json.dumps(data)
