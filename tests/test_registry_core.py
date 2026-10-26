#!/usr/bin/env python3
"""Registry core: --ttl bound, event log checked before the save, committed mode with quoted paths,
rebase recovery over unrelated dirty files and validation of the rebased registry, and the conftest's
isolation fixtures surviving a star import."""

from __future__ import annotations

import ast
import importlib
import json
import os
from pathlib import Path

import pytest

from .conftest import _git, patch_core, read_events, read_reg, set_lease, write_reg
from .test_commit import CLAIM, EV, REG, _clean, _head, _race, _subject, run, shared  # noqa: F401 (fixture)

HUGE = "100000000"


def _claim(lp, env, *extra):
    return lp.main(["--registry", str(env.registry), "claim", "alpha", "--workspace", "w", "--agent", "vera",
                    *extra])


# --------------------------------------------------------------------------
# 1. --ttl upper bound; the audit pre-check reports an overflowing lease


def test_claim_huge_ttl_is_a_usage_error_with_nothing_written(lp, env, capsys):
    before_reg = env.registry.read_bytes()
    before_ev = env.events.read_bytes() if env.events.exists() else b""
    assert _claim(lp, env, "--ttl", HUGE) == 2
    err = capsys.readouterr().err
    assert f"--ttl {HUGE} exceeds the maximum of 8760 hours" in err and "internal error" not in err
    assert env.registry.read_bytes() == before_reg
    assert (env.events.read_bytes() if env.events.exists() else b"") == before_ev


def test_renew_huge_ttl_is_a_usage_error_and_the_lease_is_kept(lp, env, capsys):
    assert _claim(lp, env) == 0
    lease = read_reg(env)["packages"]["alpha"]["lease"]
    n0 = len(read_events(env))
    rc = lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera", "--ttl", HUGE])
    assert rc == 2
    assert "exceeds the maximum of 8760 hours" in capsys.readouterr().err
    assert read_reg(env)["packages"]["alpha"]["lease"] == lease and len(read_events(env)) == n0


def test_ttl_at_the_bound_is_accepted(lp, env, capsys):
    assert _claim(lp, env, "--ttl", "8760") == 0
    assert read_reg(env)["packages"]["alpha"]["lease"]["ttl_hours"] == 8760
    assert _claim(lp, env, "--ttl", "8761") == 2
    with pytest.raises(lp.Fail, match="at most 8760"):
        lp.parse_ttl_hours("8761")
    assert lp.parse_ttl_hours("8760") == 8760


def test_commit_mode_huge_ttl_leaves_the_clone_clean(lp, env, shared, capsys):
    a = shared.a
    rc, cap = run(lp, a, capsys, *CLAIM, "--ttl", HUGE, "--commit")
    assert rc == 2 and _clean(a.root), cap.err
    rc, cap = run(lp, a, capsys, "claim", "beta", "--workspace", "w", "--agent", "max", "--commit")
    assert rc == 0, cap.err


@pytest.mark.parametrize("fix", [False, True])
def test_audit_reports_an_overflowing_lease_instead_of_crashing(lp, env, fake_gh, capsys, fix):
    set_lease(env, "alpha", agent="vera")
    reg = read_reg(env)
    reg["packages"]["alpha"]["lease"]["ttl_hours"] = int(HUGE)   # written by a release before the bound
    write_reg(env, reg)
    rc = lp.main(["--registry", str(env.registry), "--json", "audit", "--no-gh", *(["--fix"] if fix else [])])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    bad = [f for f in json.loads(cap.out)["findings"] if f["check"] == "lease-invalid"]
    assert [f["name"] for f in bad] == ["alpha"] and "OverflowError" in bad[0]["detail"]
    assert read_reg(env)["packages"]["alpha"]["lease"]["ttl_hours"] == int(HUGE)   # not swept


# --------------------------------------------------------------------------
# 2. the event log is checked before the registry is replaced


def test_event_log_that_is_a_directory_refuses_with_nothing_written(lp, env, tmp_path, capsys):
    evdir = tmp_path / "evdir"
    evdir.mkdir()
    before = env.registry.read_bytes()
    rc = lp.main(["--registry", str(env.registry), "--events", str(evdir), "claim", "alpha",
                  "--workspace", "w", "--agent", "vera"])
    err = capsys.readouterr().err
    assert rc == 1 and "cannot be appended to (is a directory); nothing written" in err, err
    assert env.registry.read_bytes() == before and list(evdir.iterdir()) == []


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes a read-only file")
def test_read_only_event_log_refuses_and_a_retry_records_the_claim(lp, env, capsys):
    env.events.write_text("")
    os.chmod(env.events, 0o444)
    before = env.registry.read_bytes()
    try:
        assert _claim(lp, env) == 1
        assert "cannot be appended to" in capsys.readouterr().err
        assert env.registry.read_bytes() == before
    finally:
        os.chmod(env.events, 0o644)
    assert _claim(lp, env) == 0
    assert [e["event"] for e in read_events(env)] == ["claimed"]


def test_event_log_in_a_missing_directory_is_created(lp, env, tmp_path):
    ev = tmp_path / "new" / "dir" / "events.jsonl"
    assert lp.main(["--registry", str(env.registry), "--events", str(ev), "claim", "alpha",
                    "--workspace", "w", "--agent", "vera"]) == 0
    assert json.loads(ev.read_text().splitlines()[-1])["event"] == "claimed"


def test_flush_keeps_the_queued_lines_when_the_write_fails(lp, env, monkeypatch):
    reg = lp.load_registry(env.registry)
    lp.record(reg, env.events, reg["packages"]["alpha"], "updated", fields=["notes"])
    real = lp._write_event_lines

    def boom(p, lines):
        raise OSError(28, "No space left on device")
    patch_core(monkeypatch, "_write_event_lines", boom)
    with pytest.raises(OSError):
        lp.flush_events(reg)
    patch_core(monkeypatch, "_write_event_lines", real)
    n0 = len(read_events(env))
    lp.flush_events(reg)                       # the line was kept, so a retry writes it
    assert [e["event"] for e in read_events(env)[n0:]] == ["updated"]
    lp.flush_events(reg)                       # and only once
    assert len(read_events(env)) == n0 + 1


# --------------------------------------------------------------------------
# 3. committed mode with a registry path git C-quotes


@pytest.mark.parametrize("sub", ["my reg", "régistre"])
def test_commit_mode_with_a_quoted_registry_path(lp, env, tmp_path, capsys, sub):
    root = tmp_path / "rr"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "branch", "-M", "main")
    d = root / sub
    d.mkdir()
    (d / REG).write_text(env.registry.read_text())
    (d / EV).write_text("")
    (root / ".gitignore").write_text("*.lock\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "user.email", "t@example.com")
    rc = lp.main(["--registry", str(d / REG), "--commit", "claim", "alpha", "--workspace", "w", "--agent", "vera"])
    err = capsys.readouterr().err
    assert rc == 0, err
    assert _subject(root) == "lean-pkg: claimed alpha" and _clean(root)
    rc = lp.main(["--registry", str(d / REG), "release", "alpha", "--agent", "vera"])   # not committed
    err = capsys.readouterr().err
    assert rc == 0 and f"NOT COMMITTED: {sub}/{EV}, {sub}/{REG} modified in " in err, err


# --------------------------------------------------------------------------
# 4. rebase recovery over unrelated dirty files; the recovery advice after a rejection


def test_rebase_recovery_with_an_unrelated_modified_file(lp, env, shared, monkeypatch, capsys):
    a, b = shared.a, shared.clone_b()
    (a.root / "README.md").write_text("local edit, unrelated to the registry\n")
    _race(lp, monkeypatch, b, "claim", "beta", "--workspace", "w4", "--agent", "otto")
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 0, cap.err
    assert "lean-pkg: pushed origin/main" in cap.err and _head(shared.origin, "main") == _head(a.root)
    assert (a.root / "README.md").read_text() == "local edit, unrelated to the registry\n"
    assert _git(a.root, "stash", "list").stdout == ""
    reg = read_reg(a)["packages"]
    assert reg["alpha"]["lease"]["agent"] == "vera" and reg["beta"]["lease"]["agent"] == "otto"


def test_rejected_push_advises_pull_rebase_then_push(lp, env, shared, monkeypatch, capsys):
    a, b = shared.a, shared.clone_b()
    _race(lp, monkeypatch, b, "claim", "alpha", "--workspace", "w4", "--agent", "otto")
    rc, cap = run(lp, a, capsys, *CLAIM, "--push")
    assert rc == 1
    assert "run: git pull --rebase origin main, then git push origin main" in cap.err, cap.err


# --------------------------------------------------------------------------
# 5. the rebased registry is validated before the push


def test_rebased_registry_that_fails_validation_is_not_pushed(lp, env, shared, monkeypatch, capsys):
    a, b = shared.a, shared.clone_b()
    real = lp.validate_registry

    def validate(data):   # an error only the merge of both writes has (each side alone is valid)
        errs = real(data)
        pkgs = (data or {}).get("packages") or {}
        if all((pkgs.get(n) or {}).get("lease") for n in ("alpha", "beta")):
            errs = errs + ["packages.alpha: synthetic merge-only error"]
        return errs
    _race(lp, monkeypatch, b, "claim", "beta", "--workspace", "w4", "--agent", "otto")
    monkeypatch.setattr(lp, "validate_registry", validate)
    rc, cap = run(lp, a, capsys, "--json", *CLAIM, "--push")
    origin_tip = _head(shared.origin, "main")
    assert rc == 1, cap.err
    assert "rebased registry fails validation (1 new error(s): packages.alpha: synthetic merge-only error)" in cap.err
    assert "not pushed, reset to the local commit" in cap.err
    assert "reconcile lean-packages.yaml with origin/main by hand" in cap.err
    assert _head(b.root) == origin_tip                                        # origin has B's write only
    assert _subject(a.root) == "lean-pkg: claimed alpha" and _clean(a.root)
    assert read_reg(a)["packages"]["beta"]["lease"] is None                   # the pre-rebase commit
    assert _git(a.root, "rev-list", "--count", "HEAD..origin/main").stdout.strip() == "1"
    commit = json.loads(cap.out)["commit"]
    assert commit["pushed"] is False and "rebased registry fails validation" in commit["push_error"]


# --------------------------------------------------------------------------
# 6. the conftest's autouse isolation fixtures survive `from tests.conftest import *`


def test_autouse_fixtures_in_conftest_are_star_exported():
    conftest = importlib.import_module(f"{__package__}.conftest")
    tree = ast.parse(Path(conftest.__file__).read_text())

    def autouse(dec) -> bool:
        return isinstance(dec, ast.Call) and any(
            k.arg == "autouse" and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in dec.keywords)
    names = [f.name for f in tree.body if isinstance(f, ast.FunctionDef) and any(map(autouse, f.decorator_list))]
    assert {"no_user_default_registry", "home_outside_tmp_path", "no_real_io_in_search_adapters"} <= set(names)
    ns: dict = {}
    exec(f"from {__package__}.conftest import *", ns)   # what a scratch conftest does
    assert [n for n in names if n not in ns] == []
