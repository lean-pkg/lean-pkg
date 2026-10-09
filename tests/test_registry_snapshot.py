#!/usr/bin/env python3
"""#34: --registry-snapshot COMMIT (env LEAN_PKG_REGISTRY_SNAPSHOT) reads a historical registry, read-only."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from .conftest import _git, commit_claims, repo_head

REG = "lean-packages.yaml"
EV = "lean-package-events.jsonl"
LEAN2 = "theorem foo_le_bar : True := trivial\ntheorem extra : True := trivial\n"
CLAIMS = {"schema": "claims/1", "package": "beta", "claims": [
    {"id": "beta/baz", "decl": "Demo.baz", "kind": "lemma", "summary": "baz", "file": "Demo/Main.lean",
     "status": "reusable", "superseded_by": None}]}
LEAN1 = "theorem foo_le_bar : True := trivial\nlemma baz : True := trivial\n"


def _commit(root: Path, msg: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)
    return _git(root, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def hist(lp, env, tmp_path, capsys):
    """A registry git repository with two commits: c1 (alpha unleased, beta's claims synced at its first
    claims commit, event log present) and c2 (alpha claimed, beta re-synced at a later source commit)."""
    root = tmp_path / "registry-repo"
    root.mkdir()
    _git(root, "init", "-q")
    reg = root / REG
    reg.write_text(env.registry.read_text())
    (root / ".gitignore").write_text(REG + ".lock\n")
    old_src = commit_claims(env.repo_b, yaml.safe_dump(CLAIMS, sort_keys=False), lean=LEAN1)
    assert lp.main(["--registry", str(reg), "claims", "sync", "beta"]) == 0
    c1 = _commit(root, "c1")
    assert lp.main(["--registry", str(reg), "claim", "alpha", "--workspace", "w3", "--agent", "vera"]) == 0
    (env.repo_b / "Demo" / "Main.lean").write_text(LEAN1 + LEAN2)   # a source change: not evidence-only
    _commit(env.repo_b, "more")
    assert lp.main(["--registry", str(reg), "claims", "sync", "beta"]) == 0
    c2 = _commit(root, "c2")
    capsys.readouterr()
    return SimpleNamespace(root=root, reg=reg, events=root / EV, c1=c1, c2=c2, old_src=old_src)


def run(lp, capsys, *argv):
    rc = lp.main(list(argv))
    return rc, capsys.readouterr()


def test_status_json_reads_the_older_commit_and_names_it(lp, hist, capsys):
    rc, cap = run(lp, capsys, "--registry", str(hist.reg), "--json", "status", "alpha")
    assert rc == 0 and json.loads(cap.out)["package"]["lease"] is not None
    assert "registry_snapshot" not in json.loads(cap.out)
    rc, cap = run(lp, capsys, "--registry", str(hist.reg), "--registry-snapshot", hist.c1[:10],
                  "--json", "status", "alpha")
    assert rc == 0, cap.err
    data = json.loads(cap.out)
    assert data["package"]["lease"] is None
    assert data["registry_snapshot"] == hist.c1
    assert f"registry snapshot {hist.c1[:12]} (read-only)" in cap.err


def test_env_variable_and_list_board_audit(lp, hist, capsys, monkeypatch):
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(hist.reg))
    monkeypatch.setenv("LEAN_PKG_REGISTRY_SNAPSHOT", "HEAD~1")
    for argv in (["list"], ["board"]):
        rc, cap = run(lp, capsys, "--json", *argv)
        assert rc == 0, cap.err
        data = json.loads(cap.out)
        assert data["registry_snapshot"] == hist.c1
        assert data["packages"]["alpha"]["lease"] is None
    rc, cap = run(lp, capsys, "list")
    assert rc == 0 and "alpha" in cap.out
    rc, cap = run(lp, capsys, "--json", "audit")
    assert json.loads(cap.out)["registry_snapshot"] == hist.c1
    # the flag wins over the env
    rc, cap = run(lp, capsys, "--registry-snapshot", hist.c2, "--json", "status", "alpha")
    assert rc == 0 and json.loads(cap.out)["registry_snapshot"] == hist.c2


def test_snapshot_copies_the_commits_registry_and_event_log(lp, hist, tmp_path):
    """The temp copies are byte-for-byte the commit's files; the live files are never read."""
    out = tmp_path / "snap"
    out.mkdir()
    args = SimpleNamespace(registry=str(hist.reg), events=None, registry_snapshot=None)
    sha = lp.materialize_snapshot(args, hist.c1, out)
    assert sha == hist.c1 and args.registry_snapshot_sha == hist.c1 and args.no_commit is True
    assert args.registry == str(out / REG) and args.events == str(out / EV)
    assert (out / REG).read_bytes() == _git(hist.root, "show", f"{hist.c1}:{REG}").stdout.encode()
    assert (out / EV).read_bytes() == _git(hist.root, "show", f"{hist.c1}:{EV}").stdout.encode()
    assert (out / EV).read_bytes() != hist.events.read_bytes()
    assert lp.registry_home_dir(args) == hist.root


WRITES = [
    ["claim", "beta", "--workspace", "w9", "--agent", "max"],
    ["release", "alpha", "--agent", "vera"],
    ["update", "alpha", "--notes", "x"],
    ["record", "alpha", "doi-minted", "--field", "doi=10.1/x"],
    ["gate", "alpha", "--receipt", "r.json"],
    ["claims", "sync", "beta"],
    ["audit", "--fix"],
    ["promote", "beta", "--yes"],
    ["init"],
    ["--commit", "migrate"],
]


@pytest.mark.parametrize("argv", WRITES, ids=lambda a: " ".join(a[:2]))
def test_every_write_is_refused_with_exit_2_and_nothing_changes(lp, hist, capsys, argv):
    before = (hist.reg.read_text(), hist.events.read_text(), repo_head(hist.root))
    rc, cap = run(lp, capsys, "--registry", str(hist.reg), "--registry-snapshot", hist.c1, *argv)
    assert rc == 2, (argv, cap.err)
    assert "lean-pkg: read-only: --registry-snapshot: `" in cap.err
    assert (hist.reg.read_text(), hist.events.read_text(), repo_head(hist.root)) == before
    assert _git(hist.root, "status", "--porcelain").stdout.strip() == ""


def test_write_refusal_json(lp, hist, capsys):
    rc, cap = run(lp, capsys, "--registry", str(hist.reg), "--registry-snapshot", hist.c1, "--json",
                  "claim", "beta", "--workspace", "w9", "--agent", "max")
    assert rc == 2
    data = json.loads(cap.out)
    assert data["ok"] is False and data["usage"] is True
    assert data["error"].startswith("read-only: --registry-snapshot: `claim` would write the registry")


def test_dry_runs_are_allowed(lp, hist, capsys):
    rc, cap = run(lp, capsys, "--registry", str(hist.reg), "--registry-snapshot", hist.c1,
                  "register", "gamma", "--repo", "x.git", "--path", "gamma", "--dry-run")
    assert rc == 0, cap.err
    assert "gamma" in cap.out
    assert _git(hist.root, "status", "--porcelain").stdout.strip() == ""


def test_comparator_config_checkout_of_a_historical_copy(lp, env, hist, capsys, tmp_path):
    """The field report: a copy at the earlier synced commit is refused against the current registry and
    accepted against the snapshot that was synced at it."""
    old = tmp_path / "beta-old"
    shutil.copytree(env.repo_b, old)
    _git(old, "checkout", "-q", hist.old_src)
    base = ["comparator", "config", "--from-claims", "beta", "--checkout", str(old), "--dry-run"]
    rc, cap = run(lp, capsys, "--registry", str(hist.reg), *base)
    assert rc == 1 and "is not the claims-synced commit" in cap.err
    rc, cap = run(lp, capsys, "--registry", str(hist.reg), "--registry-snapshot", hist.c1, "--json", *base)
    assert rc == 0, cap.err
    assert json.loads(cap.out)["registry_snapshot"] == hist.c1


def test_refusals_not_a_repo_bad_commit_missing_file(lp, env, hist, capsys, tmp_path):
    rc, cap = run(lp, capsys, "--registry", str(env.registry), "--registry-snapshot", "HEAD", "list")
    assert rc == 1 and "is not inside a git work tree" in cap.err
    rc, cap = run(lp, capsys, "--registry", str(hist.reg), "--registry-snapshot", "nope", "--json", "list")
    assert rc == 1
    assert json.loads(cap.out)["error"].startswith("--registry-snapshot: 'nope' is not a commit in ")
    first = _git(hist.root, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    sub = hist.root / "sub"
    sub.mkdir()
    (sub / REG).write_text(hist.reg.read_text())
    _commit(hist.root, "sub")
    rc, cap = run(lp, capsys, "--registry", str(sub / REG), "--registry-snapshot", first, "list")
    assert rc == 1 and f"sub/{REG} does not exist at {first[:12]}" in cap.err
