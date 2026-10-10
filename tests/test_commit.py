#!/usr/bin/env python3
"""Committed mode (#7): --commit/--push pull --ff-only, write, commit the two registry files together."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from .conftest import _git, make_receipt, read_events, read_reg, set_lease, write_receipt

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


class _TickingClock:
    """A stand-in for cli's `dt` module whose datetime.now() moves one second per read: the
    wall clock of a loaded host, where the --commit claim (fetch, add, commit) and the plain claim
    after it land in different seconds."""

    def __init__(self):
        start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        ticks = [start]

        class Ticking(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                ticks[0] += dt.timedelta(seconds=1)
                return ticks[0] if tz is None else ticks[0].astimezone(tz)

        self.datetime, self.date, self.timedelta, self.timezone = Ticking, dt.date, dt.timedelta, dt.timezone


def _claim_commit_one_commit_both_files(lp, shared, capsys, monkeypatch):
    a = shared.a
    # Both claims below stamp claimed_at with now_iso() (second precision) and print
    # "until <claimed_at + ttl>". Pin it: a loaded host puts the two claims in different seconds.
    stamp = lp.now_iso()
    monkeypatch.setattr(lp, "now_iso", lambda: stamp)
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
    assert plain.out == cap.out


def test_claim_commit_makes_one_commit_with_both_files(lp, env, shared, capsys, monkeypatch):
    _claim_commit_one_commit_both_files(lp, shared, capsys, monkeypatch)


def test_claim_commit_stdout_comparison_across_a_second_boundary(lp, env, shared, capsys, monkeypatch):
    """Regression: the two claims above land in different seconds whenever the host is loaded
    (the full suite under concurrent agents); the stdout comparison must not depend on that."""
    monkeypatch.setattr(lp, "dt", _TickingClock())
    _claim_commit_one_commit_both_files(lp, shared, capsys, monkeypatch)


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
            with contextlib.redirect_stdout(io.StringIO()):   # B's output is not A's (A's --json buffer)
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
    real = lp.commit_preflight

    def preflight(args, mode):   # an unpushed commit lands after A's preflight (which refuses one before it)
        ctx = real(args, mode)
        if ctx["root"] == a.root:
            (a.root / "README.md").write_text("local only\n")
            _git(a.root, "commit", "-q", "-am", "unpushed")
        return ctx
    monkeypatch.setattr(lp, "commit_preflight", preflight)
    _race(lp, monkeypatch, b, "claim", "beta", "--workspace", "w4", "--agent", "otto")
    tip = None
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 1 and "push rejected" not in cap.err
    assert "lean-pkg: push failed: origin/main: " in cap.err and "run: git push origin main" in cap.err
    assert _subject(a.root) == "lean-pkg: claimed alpha"
    assert _git(a.root, "log", "-1", "--format=%s", "HEAD~1").stdout.strip() == "unpushed"
    assert tip is None


def test_env_committed_mode_is_named_and_the_push_shows_its_sha(lp, env, shared, capsys, monkeypatch):
    """Field report 2026-10-10: `renew` committed and pushed with no flag (env LEAN_PKG_PUSH=1). The commit
    line names the env variable that turned committed mode on, the push line carries the pushed sha, and
    the subcommand help says when the variable is set."""
    a = shared.a
    rc, cap = run(lp, a, capsys, *CLAIM)
    assert rc == 0, cap.err
    _git(a.root, "commit", "-q", "-am", "claim")
    _git(a.root, "push", "-q", "origin", "main")
    monkeypatch.setenv("LEAN_PKG_PUSH", "1")
    rc, cap = run(lp, a, capsys, "renew", "alpha", "--agent", "vera")
    assert rc == 0, cap.err
    short = _git(a.root, "rev-parse", "--short", "HEAD").stdout.strip()
    assert f"lean-pkg: committed {short} on main: lean-pkg: claim-refreshed alpha " \
           f"(committed mode: env LEAN_PKG_PUSH=1)" in cap.err
    assert f"lean-pkg: pushed origin/main ({short})" in cap.err
    assert _head(shared.origin, "main") == _head(a.root)
    rc, cap = run(lp, a, capsys, "renew", "alpha", "--agent", "vera", "--json")
    assert rc == 0 and json.loads(cap.out)["commit"]["pushed"] is True
    with pytest.raises(SystemExit):
        lp.main(["renew", "--help"])
    assert "set now: writes commit and push unless --no-commit" in " ".join(capsys.readouterr().out.split())
    monkeypatch.delenv("LEAN_PKG_PUSH")
    rc, cap = run(lp, a, capsys, "renew", "alpha", "--agent", "vera", "--push")
    assert rc == 0 and "(committed mode" not in cap.err     # a flag the caller passed is not re-announced
    with pytest.raises(SystemExit):
        lp.main(["renew", "--help"])
    assert "set now" not in capsys.readouterr().out


def _deny_pushes(origin: Path) -> Path:
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'push denied by policy' >&2\nexit 1\n")
    hook.chmod(0o755)
    return hook


def test_push_failure_is_reported_on_stderr_and_in_json(lp, env, shared, capsys):   # #54
    """Field report (0.7.19): a committed-mode write left its commit unpushed. A push that fails names the
    remote branch and the git error on stderr, keeps the commit, says how to recover, and exits 1; --json
    carries commit.pushed false and commit.push_error."""
    a = shared.a
    _deny_pushes(shared.origin)
    tip = _head(shared.origin, "main")
    rc, cap = run(lp, a, capsys, "update", "alpha", "--notes", "n1", "--push")
    assert rc == 1
    short = _git(a.root, "rev-parse", "--short", "HEAD").stdout.strip()
    assert "lean-pkg: push failed: origin/main: remote: push denied by policy\n" in cap.err
    assert f"lean-pkg: kept commit {short}; the clone is ahead of origin/main; run: git push origin main" in cap.err
    assert "lean-pkg: pushed" not in cap.err
    assert _subject(a.root) == "lean-pkg: updated alpha" and _clean(a.root)
    assert _head(shared.origin, "main") == tip
    _git(a.root, "reset", "-q", "--hard", "origin/main")
    rc, cap = run(lp, a, capsys, "--json", "update", "beta", "--notes", "n2", "--push")
    assert rc == 1
    payload = json.loads(cap.out)
    assert payload["ok"] is False
    assert payload["commit"]["pushed"] is False
    assert payload["commit"]["push_error"] == "remote: push denied by policy"
    assert payload["commit"]["error"] == "push failed: origin/main: remote: push denied by policy"
    assert payload["commit"]["sha"] == _head(a.root)


def test_next_push_write_refuses_while_the_clone_is_ahead(lp, env, shared, capsys):   # #54
    a = shared.a
    hook = _deny_pushes(shared.origin)
    rc, cap = run(lp, a, capsys, "update", "alpha", "--notes", "n1", "--push")
    assert rc == 1
    hook.unlink()   # the remote would accept now; the clone still holds the unpushed commit
    short = _git(a.root, "rev-parse", "--short", "HEAD").stdout.strip()
    head, n = _head(a.root), len(read_events(a))
    rc, cap = run(lp, a, capsys, "update", "beta", "--append-note", "x", "--agent", "a", "--push")
    assert rc == 1
    assert (f"registry clone is 1 commit(s) ahead of origin/main: unpushed {short} lean-pkg: updated alpha; "
            f"push it first: git -C {a.root} push origin main") in cap.err
    assert _head(a.root) == head and len(read_events(a)) == n and _clean(a.root)
    rc, cap = run(lp, a, capsys, "--json", "update", "beta", "--notes", "n2", "--push")
    payload = json.loads(cap.out)
    assert rc == 1 and payload["ok"] is False and "1 commit(s) ahead of origin/main" in payload["error"]
    assert _head(a.root) == head and len(read_events(a)) == n
    # --commit (no push) accumulates local commits by design: not refused
    rc, cap = run(lp, a, capsys, "update", "beta", "--notes", "local", "--commit")
    assert rc == 0, cap.err
    _git(a.root, "push", "-q", "origin", "main")
    rc, cap = run(lp, a, capsys, "update", "beta", "--append-note", "x", "--agent", "a", "--push")
    assert rc == 0, cap.err
    short = _git(a.root, "rev-parse", "--short", "HEAD").stdout.strip()
    assert f"lean-pkg: pushed origin/main ({short})" in cap.err
    assert _head(shared.origin, "main") == _head(a.root)


def test_rejected_push_that_cannot_be_rebased_names_origin_branch(lp, env, shared, monkeypatch, capsys):
    """The non-fast-forward race whose rebase conflicts: the failure line names origin/<branch> and git's
    rejection, not the `To <url>` line git prints first."""
    a, b = shared.a, shared.clone_b()
    _race(lp, monkeypatch, b, "claim", "alpha", "--workspace", "w4", "--agent", "otto")
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 1
    line = next(x for x in cap.err.splitlines() if x.startswith("lean-pkg: push failed: "))
    assert line.startswith("lean-pkg: push failed: origin/main: ") and " To " not in line
    assert "rejected" in line


def test_regate_push_is_one_registry_commit(lp, env, shared, tmp_path, capsys):   # #67
    a = shared.a
    (env.repo_b / "New.lean").write_text("-- more\n")
    _git(env.repo_b, "add", "New.lean")
    _git(env.repo_b, "commit", "-m", "more")
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2"))
    before = _count(a.root)
    rc, cap = run(lp, a, capsys, "gate", "beta", "--receipt", str(p), "--regate", "--push")
    assert rc == 0, cap.err
    assert _count(a.root) == before + 1 and _subject(a.root) == "lean-pkg: regated beta"
    assert _head(shared.origin, "main") == _head(a.root) and _clean(a.root)
    assert [e["event"] for e in read_events(a)][-1:] == ["regated"]
    assert read_reg(a)["packages"]["beta"]["status"] == "verified-private"


# #68: push retry (transient transport errors) and rebase (origin moved on)

def _flaky_remote(origin: Path, failures: int) -> Path:
    """A pre-receive hook that fails the first `failures` pushes with a transport-style error, then accepts."""
    counter = origin / "push-count"
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\n"
                    f"n=$(cat '{counter}' 2>/dev/null || echo 0); n=$((n + 1)); echo $n > '{counter}'\n"
                    f"if [ $n -le {failures} ]; then\n"
                    "  echo 'kex_exchange_identification: read: Connection reset by peer' >&2; exit 1\n"
                    "fi\nexit 0\n")
    hook.chmod(0o755)
    return counter


def test_transient_push_failure_is_retried_then_pushed(lp, env, shared, monkeypatch, capsys):
    a = shared.a
    _flaky_remote(shared.origin, 2)
    waits = []
    monkeypatch.setattr(lp, "_sleep", waits.append)
    rc, cap = run(lp, a, capsys, "--json", *CLAIM, "--push")
    assert rc == 0, cap.err
    assert cap.err.count("lean-pkg: push attempt ") == 2 and "Connection reset" in cap.err
    short = _git(a.root, "rev-parse", "--short", "HEAD").stdout.strip()
    assert cap.err.rstrip().endswith(f"lean-pkg: pushed origin/main ({short})")
    commit = json.loads(cap.out)["commit"]
    assert commit["pushed"] is True and commit["push_attempts"] == 3 and commit["rebased"] is False
    assert _head(shared.origin, "main") == _head(a.root)
    assert waits == [0.0, 0.0]   # LEAN_PKG_PUSH_RETRY_DELAY=0 in the suite


def test_push_backoff_doubles_from_the_configured_delay(lp, env, shared, monkeypatch, capsys):
    a = shared.a
    _flaky_remote(shared.origin, 99)
    waits = []
    monkeypatch.setattr(lp, "_sleep", waits.append)
    monkeypatch.setenv("LEAN_PKG_PUSH_RETRY_DELAY", "5")
    rc, cap = run(lp, a, capsys, "--json", *CLAIM, "--push")
    assert rc == 1
    assert waits == [5.0, 10.0, 20.0, 40.0]   # the default 4 retries
    commit = json.loads(cap.out)["commit"]
    assert commit["pushed"] is False and commit["push_attempts"] == 5
    assert "kex_exchange_identification" in commit["push_error"]
    assert "lean-pkg: kept commit" in cap.err and _head(shared.origin, "main") != _head(a.root)


def test_push_retries_zero_fails_at_once(lp, env, shared, monkeypatch, capsys):
    a = shared.a
    counter = _flaky_remote(shared.origin, 1)
    monkeypatch.setenv("LEAN_PKG_PUSH_RETRIES", "0")
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 1 and "push attempt" not in cap.err
    assert counter.read_text().strip() == "1"


def test_permanent_push_failure_is_not_retried(lp, env, shared, capsys):
    a = shared.a
    _deny_pushes(shared.origin)
    rc, cap = run(lp, a, capsys, "--json", *CLAIM, "--push")
    assert rc == 1 and "push attempt" not in cap.err
    assert json.loads(cap.out)["commit"]["push_attempts"] == 1


def test_racing_push_rebases_cleanly_and_reports_it(lp, env, shared, monkeypatch, capsys):
    a, b = shared.a, shared.clone_b()
    _race(lp, monkeypatch, b, "claim", "beta", "--workspace", "w4", "--agent", "otto")
    rc, cap = run(lp, a, capsys, "--json", *CLAIM, "--push")
    assert rc == 0, cap.err
    commit = json.loads(cap.out)["commit"]
    assert commit["pushed"] is True and commit["rebased"] is True and commit["push_attempts"] == 2
    assert commit["sha"] == _head(a.root) == _head(shared.origin, "main")
    assert "lean-pkg: rebased onto origin/main (" in cap.err
    assert _git(a.root, "rev-list", "--count", "origin/main..HEAD").stdout.strip() == "0"


def test_event_log_conflict_merges_by_union_in_timestamp_order(lp, env, shared, monkeypatch, capsys):
    """Origin's racing event is stamped LATER than ours: the union puts our line first (timestamp order),
    not after origin's (file order)."""
    a, b = shared.a, shared.clone_b()
    stamp = {"now": "2026-01-01T00:00:00+00:00"}
    monkeypatch.setattr(lp, "now_iso", lambda: stamp["now"])
    real, fired = lp.commit_preflight, []

    def preflight(args, mode):
        ctx = real(args, mode)
        if not fired:
            fired.append(True)
            stamp["now"] = "2026-01-01T00:00:09+00:00"
            assert lp.main(["--registry", str(b.registry), "claim", "beta", "--workspace", "w4",
                            "--agent", "otto", "--push"]) == 0
            stamp["now"] = "2026-01-01T00:00:01+00:00"
        return ctx
    monkeypatch.setattr(lp, "commit_preflight", preflight)
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 0, cap.err
    assert "event log conflict resolved by union (1 line(s) from origin, 1 ours, timestamp order)" in cap.err
    claimed = [(e["name"], e["ts"]) for e in read_events(a) if e["event"] == "claimed"]
    assert claimed == [("alpha", "2026-01-01T00:00:01+00:00"), ("beta", "2026-01-01T00:00:09+00:00")]
    assert _head(shared.origin, "main") == _head(a.root) and _clean(a.root)
    reg = read_reg(a)["packages"]
    assert reg["alpha"]["lease"]["agent"] == "vera" and reg["beta"]["lease"]["agent"] == "otto"


def test_rebase_conflict_aborts_and_leaves_the_clone_as_it_was(lp, env, shared, monkeypatch, capsys):
    a, b = shared.a, shared.clone_b()
    _race(lp, monkeypatch, b, "claim", "alpha", "--workspace", "w4", "--agent", "otto")
    real_finish, seen = lp.commit_finish, {}

    def finish(ctx, rc):   # the commit's sha just before the push
        real_commit = lp.registry_git

        def spy(root, *args):
            if args[:1] == ("push",) and "head" not in seen and Path(root) == a.root:   # A's, not B's
                seen["head"] = real_commit(root, "rev-parse", "HEAD").stdout.strip()
            return real_commit(root, *args)
        monkeypatch.setattr(lp, "registry_git", spy)
        try:
            return real_finish(ctx, rc)
        finally:
            monkeypatch.setattr(lp, "registry_git", real_commit)
    monkeypatch.setattr(lp, "commit_finish", finish)
    rc, cap = run(lp, a, capsys, "--json", *CLAIM, "--push")
    assert rc == 1
    assert "rebase conflict in lean-package-events.jsonl, lean-packages.yaml" in cap.err, cap.err
    assert "lean-pkg: push failed: origin/main: " in cap.err and "; rebase conflict in lean-package-events.jsonl, lean-packages.yaml" in cap.err
    assert "run: git push origin main" in cap.err
    assert _head(a.root) == seen["head"] and _clean(a.root)
    assert not (a.root / ".git" / "rebase-merge").exists() and not (a.root / ".git" / "rebase-apply").exists()
    assert _git(a.root, "rev-list", "--count", "origin/main..HEAD").stdout.strip() == "1"
    commit = json.loads(cap.out)["commit"]
    assert commit["pushed"] is False and commit["rebased"] is False and commit["push_attempts"] == 1
    assert commit["push_error"].endswith("; rebase conflict in lean-package-events.jsonl, lean-packages.yaml")


def test_union_event_lines_unit(lp):
    base = ['{"ts": "2026-01-01T00:00:00+00:00", "event": "a"}']
    o = base + ['{"ts": "2026-01-01T00:00:05+00:00", "event": "o"}']
    t = base + ['{"ts": "2026-01-01T00:00:02+00:00", "event": "t"}']
    assert lp.union_event_lines(base, o, t) == [base[0], t[1], o[1]]
    same = base + ['{"ts": "2026-01-01T00:00:05+00:00", "event": "t"}']
    assert lp.union_event_lines(base, o, same) == [base[0], o[1], same[1]]   # ties: origin first
    assert lp.union_event_lines(base, ["rewritten"], t) is None              # not append-only
    assert lp.union_event_lines(base, o, base + ["not json"]) is None


# #71: GitHub reports a lost ref race as `[remote rejected] … (cannot lock ref …)`, not `[rejected]`

_GITHUB_LOCK_RACE = (
    "To github.com:OWNER/REPO.git\n"
    " ! [remote rejected] main -> main (cannot lock ref 'refs/heads/main': is at "
    "106850d0000000000000000000000000000000000 but expected 057310f0000000000000000000000000000000000)\n"
    "error: failed to push some refs to 'github.com:OWNER/REPO.git'\n")


def _push_result(stderr: str):
    import subprocess
    return subprocess.CompletedProcess(["git", "push"], 1, stdout="", stderr=stderr)


def test_remote_rejected_cannot_lock_ref_is_a_rejection(lp):
    assert lp._push_rejected(_push_result(_GITHUB_LOCK_RACE))
    assert not lp._push_transient(_push_result(_GITHUB_LOCK_RACE))
    assert lp._push_rejected(_push_result(
        " ! [remote rejected] main -> main (ref is at 106850d but expected 057310f)\n"))
    assert lp._push_rejected(_push_result(" ! [rejected]        main -> main (stale info)\n"))


def test_remote_rejected_hook_decline_stays_permanent(lp):
    for reason in ("pre-receive hook declined", "protected branch hook declined", "shallow update not allowed"):
        p = _push_result("To github.com:OWNER/REPO.git\n"
                         f" ! [remote rejected] main -> main ({reason})\n"
                         "error: failed to push some refs to 'github.com:OWNER/REPO.git'\n")
        assert not lp._push_rejected(p), reason
        assert not lp._push_transient(p), reason


def test_cannot_lock_ref_race_fetches_rebases_and_pushes(lp, env, shared, monkeypatch, capsys):
    """Origin moves on while A's push is in flight (the hook lands B's commit, then refuses A's push the way
    GitHub does): A fetches, rebases its one commit cleanly and pushes again."""
    a, b = shared.a, shared.clone_b()
    with contextlib.redirect_stdout(io.StringIO()):
        assert lp.main(["--registry", str(b.registry), "claim", "beta", "--workspace", "w4",
                        "--agent", "otto", "--commit"]) == 0
    _git(b.root, "push", "-q", "origin", "main:refs/race/b")   # B's objects on origin, main not moved yet
    tip = _head(shared.origin, "main")
    counter = shared.origin / "push-count"
    hook = shared.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\n"
                    f"n=$(cat '{counter}' 2>/dev/null || echo 0); n=$((n + 1)); echo $n > '{counter}'\n"
                    "if [ $n -eq 1 ]; then\n"
                    "  race=$(git rev-parse refs/race/b)\n"
                    "  env -u GIT_QUARANTINE_PATH git update-ref refs/heads/main \"$race\"\n"
                    f"  echo \"cannot lock ref 'refs/heads/main': is at $race but expected {tip}\" >&2\n"
                    "  exit 1\n"
                    "fi\nexit 0\n")
    hook.chmod(0o755)
    rc, cap = run(lp, a, capsys, "--json", *CLAIM, "--push")
    assert rc == 0, cap.err
    assert "push rejected (origin/main moved on)" in cap.err
    assert counter.read_text().strip() == "2"   # the hook's refusal, then the rebased push
    commit = json.loads(cap.out)["commit"]
    assert commit["pushed"] is True and commit["rebased"] is True and commit["push_attempts"] == 2
    assert commit["sha"] == _head(a.root) == _head(shared.origin, "main")
    assert _head(shared.origin, "main~1") == _head(shared.origin, "refs/race/b")
    assert _subject(a.root) == "lean-pkg: claimed alpha" and _clean(a.root)
    reg = read_reg(a)["packages"]
    assert reg["alpha"]["lease"]["agent"] == "vera" and reg["beta"]["lease"]["agent"] == "otto"
