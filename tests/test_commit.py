#!/usr/bin/env python3
"""Committed mode (#7): --commit/--push pull --ff-only, write, commit the two registry files together."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from .conftest import _git, read_events, read_reg, set_lease

REG = "lean-packages.yaml"
EV = "lean-package-events.jsonl"
CLAIM = ["claim", "alpha", "--workspace", "w3", "--agent", "vera"]


def _clone(origin: Path, dest: Path) -> SimpleNamespace:
    _git(origin.parent, "clone", "-q", str(origin), str(dest))
    _git(dest, "config", "user.name", "Test")
    _git(dest, "config", "user.email", "test@example.com")
    return SimpleNamespace(root=dest, registry=dest / REG, events=dest / EV)


@pytest.fixture()
def shared(env, tmp_path):
    """A bare origin holding env's registry and event log, and clone A of it (clone_b() adds B)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q")
    _git(seed, "branch", "-M", "main")
    (seed / REG).write_text(env.registry.read_text())
    (seed / EV).write_text(env.events.read_text() if env.events.exists() else "")
    (seed / "README.md").write_text("registry\n")
    (seed / ".gitignore").write_text(REG + ".lock\n")   # the writers' lock file (docs/how-to.md)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "registry")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    a = _clone(origin, tmp_path / "clone-a")
    return SimpleNamespace(origin=origin, a=a, clone_b=lambda: _clone(origin, tmp_path / "clone-b"))


def _head(repo: Path, ref: str = "HEAD") -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


def _count(repo: Path, ref: str = "HEAD") -> int:
    return int(_git(repo, "rev-list", "--count", ref).stdout.strip())


def _subject(repo: Path) -> str:
    return _git(repo, "log", "-1", "--format=%s").stdout.strip()


def _touched(repo: Path) -> set[str]:
    return set(_git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split())


def _clean(repo: Path) -> bool:
    return _git(repo, "status", "--porcelain").stdout.strip() == ""


def run(lp, clone, capsys, *argv):
    rc = lp.main(["--registry", str(clone.registry), *argv])
    return rc, capsys.readouterr()


def test_claim_commit_makes_one_commit_with_both_files(lp, env, shared, capsys):
    a = shared.a
    before = _count(a.root)
    rc, cap = run(lp, a, capsys, *CLAIM, "--commit")
    assert rc == 0
    assert _count(a.root) == before + 1
    assert _subject(a.root) == "lean-pkg: claimed alpha"
    assert _touched(a.root) == {REG, EV}
    assert _clean(a.root)
    assert "lean-pkg: committed " in cap.err and " on main: lean-pkg: claimed alpha" in cap.err
    assert "committed" not in cap.out
    assert read_reg(a)["packages"]["alpha"]["lease"]["agent"] == "vera"
    # stdout is the same as without --commit
    _git(a.root, "reset", "-q", "--hard", "HEAD~1")
    rc, plain = run(lp, a, capsys, *CLAIM)
    assert rc == 0 and "committed" not in plain.err
    strip = lambda s: [x for x in s.splitlines() if "expires" not in x and "claimed_at" not in x]
    assert strip(plain.out) == strip(cap.out)


def test_subcommand_position_and_read_only_commands(lp, env, shared, capsys):
    a = shared.a
    before = _count(a.root)
    rc, cap = run(lp, a, capsys, "list", "--commit")
    assert rc == 0 and _count(a.root) == before
    assert cap.err == "lean-pkg: --commit ignored: read-only command\n"   # a note, not an error (#18)
    rc, cap = run(lp, a, capsys, "--push", "status", "alpha")
    assert rc == 0 and _count(a.root) == before
    assert cap.err == "lean-pkg: --push ignored: read-only command\n"
    rc, cap = run(lp, a, capsys, *CLAIM, "--commit")
    assert rc == 0 and _count(a.root) == before + 1


def test_dirty_registry_files_refuse_before_writing(lp, env, shared, capsys):
    a = shared.a
    a.registry.write_text(a.registry.read_text() + "# hand edit\n")
    n = len(read_events(a))
    before = _count(a.root)
    rc, cap = run(lp, a, capsys, *CLAIM, "--commit")
    assert rc == 1
    assert "uncommitted registry changes in lean-packages.yaml; commit them first" in cap.err
    assert len(read_events(a)) == n and _count(a.root) == before


def test_outside_a_work_tree_refuses(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), *CLAIM, "--commit"])
    cap = capsys.readouterr()
    assert rc == 1 and "needs the registry inside a git work tree" in cap.err
    assert read_events(env) == []


def test_behind_origin_fast_forwards_then_commits(lp, env, shared, capsys):
    a, b = shared.a, shared.clone_b()
    (b.root / "README.md").write_text("moved on\n")
    _git(b.root, "commit", "-q", "-am", "elsewhere")
    _git(b.root, "push", "-q", "origin", "main")
    tip = _head(b.root)
    rc, cap = run(lp, a, capsys, *CLAIM, "--commit")
    assert rc == 0, cap.err
    assert _head(a.root, "HEAD~1") == tip
    assert _subject(a.root) == "lean-pkg: claimed alpha"


def test_diverged_clone_refuses_without_writing(lp, env, shared, capsys):
    a, b = shared.a, shared.clone_b()
    (b.root / "README.md").write_text("theirs\n")
    _git(b.root, "commit", "-q", "-am", "theirs")
    _git(b.root, "push", "-q", "origin", "main")
    (a.root / "README.md").write_text("ours\n")
    _git(a.root, "commit", "-q", "-am", "ours")
    n = len(read_events(a))
    rc, cap = run(lp, a, capsys, *CLAIM, "--commit")
    assert rc == 1
    assert "registry clone has diverged from origin/main; reconcile by hand before writing" in cap.err
    assert len(read_events(a)) == n and _subject(a.root) == "ours"


def test_push_advances_origin(lp, env, shared, capsys):
    a = shared.a
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 0, cap.err
    assert _head(shared.origin, "main") == _head(a.root)
    assert "lean-pkg: pushed origin/main" in cap.err


def test_no_origin_warns_and_commits_locally(lp, env, shared, capsys):
    a = shared.a
    _git(a.root, "remote", "remove", "origin")
    rc, cap = run(lp, a, capsys, *CLAIM, "--commit")
    assert rc == 0
    assert "no origin remote" in cap.err
    assert _subject(a.root) == "lean-pkg: claimed alpha"


def test_push_failure_keeps_commit_and_names_manual_push(lp, env, shared, tmp_path, capsys):
    a = shared.a
    _git(a.root, "remote", "set-url", "--push", "origin", str(tmp_path / "missing.git"))
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 1
    assert "lean-pkg: push failed:" in cap.err and "run: git push origin main" in cap.err
    assert _subject(a.root) == "lean-pkg: claimed alpha" and _clean(a.root)
    assert _head(shared.origin, "main") != _head(a.root)


def test_env_turns_commit_on_and_no_commit_overrides(lp, env, shared, monkeypatch, capsys):
    a = shared.a
    monkeypatch.setenv("LEAN_PKG_COMMIT", "1")
    before = _count(a.root)
    rc, cap = run(lp, a, capsys, *CLAIM)
    assert rc == 0 and _count(a.root) == before + 1
    rc, cap = run(lp, a, capsys, "--no-commit", "release", "alpha", "--agent", "vera")
    assert rc == 0 and _count(a.root) == before + 1 and not _clean(a.root)


def test_push_env(lp, env, shared, monkeypatch, capsys):
    a = shared.a
    monkeypatch.setenv("LEAN_PKG_PUSH", "1")
    rc, cap = run(lp, a, capsys, *CLAIM)
    assert rc == 0 and _head(shared.origin, "main") == _head(a.root)


def test_json_stdout_is_still_one_object(lp, env, shared, capsys):
    a = shared.a
    rc, cap = run(lp, a, capsys, "--json", *CLAIM, "--commit")
    assert rc == 0
    payload = json.loads(cap.out)
    assert isinstance(payload, dict)
    assert "committed" in cap.err


def test_failed_command_commits_nothing(lp, env, shared, capsys):
    a = shared.a
    before = _count(a.root)
    rc, cap = run(lp, a, capsys, "release", "alpha", "--agent", "nobody", "--commit")
    assert rc == 1 and _count(a.root) == before and _clean(a.root)
    assert "committed" not in cap.err


def test_audit_fix_commit_commits_the_sweep(lp, env, shared, fake_gh, capsys):
    a = shared.a
    set_lease(a, "alpha", hours_ago=30)
    _git(a.root, "commit", "-q", "-am", "expired lease")
    rc, cap = run(lp, a, capsys, "audit", "--fix", "--commit")
    assert _subject(a.root) == "lean-pkg: lease-expired alpha", cap.err
    assert _clean(a.root)
    assert read_reg(a)["packages"]["alpha"]["lease"] is None


def _race(lp, monkeypatch, b, *b_argv):
    """Origin moves on between A's preflight and A's push: B writes and pushes right after A's preflight."""
    real, fired = lp.commit_preflight, []

    def preflight(args, mode):
        ctx = real(args, mode)
        if not fired:
            fired.append(True)
            assert lp.main(["--registry", str(b.registry), *b_argv, "--push"]) == 0
        return ctx
    monkeypatch.setattr(lp, "commit_preflight", preflight)


def test_rejected_push_rebases_once_and_pushes(lp, env, shared, monkeypatch, capsys):   # #22.9
    a, b = shared.a, shared.clone_b()
    _race(lp, monkeypatch, b, "claim", "beta", "--workspace", "w4", "--agent", "otto")
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 0, cap.err
    assert "push rejected" in cap.err and "lean-pkg: pushed origin/main" in cap.err
    assert _head(shared.origin, "main") == _head(a.root) and _clean(a.root)
    assert _subject(a.root) == "lean-pkg: claimed alpha"
    reg = read_reg(a)["packages"]
    assert reg["alpha"]["lease"]["agent"] == "vera" and reg["beta"]["lease"]["agent"] == "otto"
    assert [e["name"] for e in read_events(a) if e["event"] == "claimed"] == ["beta", "alpha"]


def test_rejected_push_with_conflict_aborts_and_keeps_commit(lp, env, shared, monkeypatch, capsys):
    a, b = shared.a, shared.clone_b()
    _race(lp, monkeypatch, b, "claim", "alpha", "--workspace", "w4", "--agent", "otto")
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 1
    assert "kept the local commit" in cap.err and "run: git push origin main" in cap.err
    assert _subject(a.root) == "lean-pkg: claimed alpha" and _clean(a.root)
    assert not (a.root / ".git" / "rebase-merge").exists()
    assert read_reg(a)["packages"]["alpha"]["lease"]["agent"] == "vera"


def test_rejected_push_with_other_local_commits_is_not_rebased(lp, env, shared, monkeypatch, capsys):
    a, b = shared.a, shared.clone_b()
    (a.root / "README.md").write_text("local only\n")
    _git(a.root, "commit", "-q", "-am", "unpushed")
    _race(lp, monkeypatch, b, "claim", "beta", "--workspace", "w4", "--agent", "otto")
    tip = _head(a.root)
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 1 and "push rejected" not in cap.err
    assert "run: git push origin main" in cap.err
    assert _head(a.root, "HEAD~1") == tip
