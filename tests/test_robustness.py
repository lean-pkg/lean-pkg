#!/usr/bin/env python3
"""#18: no tracebacks on bad leases or missing tools; committed mode covers every write and dry runs skip
the preflight; LEAN_PKG_EVENTS precedence; formalization checks before writing; index root resolution."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from .conftest import (_git, commit_claims, index_add, make_index_package, read_events, read_reg,
                       set_lease, write_reg)
from .test_commit import CLAIM, _clone, _count, _head, _subject, shared  # noqa: F401 -- fixture

REG = "lean-packages.yaml"


def main(lp, env, capsys, *argv):
    rc = lp.main(["--registry", str(env.registry), *argv])
    return rc, capsys.readouterr()


# --------------------------------------------------------------------------
# R9: a hand-broken lease

BAD_LEASES = [{"claimed_at": "zzz", "ttl_hours": 24}, {"ttl_hours": "x"}]


@pytest.fixture(params=BAD_LEASES, ids=["claimed_at-zzz", "ttl_hours-x"])
def bad_lease(request, env):
    set_lease(env, "alpha", agent="vera")
    reg = read_reg(env)
    reg["packages"]["alpha"]["lease"].update(request.param)
    write_reg(env, reg)
    return env


def no_internal_error(cap):
    assert "internal error" not in cap.err and "Traceback" not in cap.err, cap.err


@pytest.mark.parametrize("cmd", [["list"], ["board"]])
def test_tables_render_an_invalid_lease(lp, bad_lease, capsys, cmd):
    rc, cap = main(lp, bad_lease, capsys, *cmd)
    assert rc == 0
    no_internal_error(cap)
    row = next(line for line in cap.out.splitlines() if line.startswith("alpha"))
    assert "? INVALID" in row and " ? " in row


def test_list_json_carries_null_expiry(lp, bad_lease, capsys):
    rc, cap = main(lp, bad_lease, capsys, "--json", "list")
    assert rc == 0
    assert json.loads(cap.out)["packages"]["alpha"]["lease"]["expires_at"] is None


def test_leases_lists_it_as_invalid(lp, bad_lease, capsys):
    rc, cap = main(lp, bad_lease, capsys, "leases")
    assert rc == 0
    no_internal_error(cap)
    row = next(line for line in cap.out.splitlines() if line.startswith("alpha"))
    assert "INVALID" in row and " ? " in row
    rc, cap = main(lp, bad_lease, capsys, "--json", "leases", "--expiring", "1")
    rows = json.loads(cap.out)["leases"]
    assert rows[0]["name"] == "alpha" and rows[0]["invalid"] is True
    assert rows[0]["expires_at"] is None and rows[0]["live"] is False


def test_audit_reports_lease_invalid(lp, bad_lease, fake_gh, capsys):
    rc, cap = main(lp, bad_lease, capsys, "--json", "audit")
    assert rc == 0
    no_internal_error(cap)
    assert any(f["check"] == "lease-invalid" and f["name"] == "alpha" for f in json.loads(cap.out)["findings"])


@pytest.mark.parametrize("argv", [
    ["claim", "alpha", "--workspace", "w", "--agent", "zz"],
    ["claim", "alpha", "--workspace", "w", "--agent", "zz", "--force", "--reason", "take over"],
    ["release", "alpha", "--agent", "vera"],
    ["renew", "alpha", "--agent", "vera"],
    ["update", "alpha", "--notes", "x", "--agent", "vera"],
    ["update", "alpha", "--path", "elsewhere", "--reason", "move", "--agent", "vera"],
])
def test_writers_refuse_an_invalid_lease(lp, bad_lease, capsys, argv):
    before = bad_lease.registry.read_text()
    rc, cap = main(lp, bad_lease, capsys, *argv)
    assert rc == 1
    no_internal_error(cap)
    assert "alpha: lease fields unparseable (claimed_at=" in cap.err
    assert "ttl_hours=" in cap.err and "clear it with `lean-pkg release alpha --force --reason TEXT`" in cap.err
    assert bad_lease.registry.read_text() == before


def test_release_force_clears_an_invalid_lease(lp, bad_lease, capsys):
    rc, cap = main(lp, bad_lease, capsys, "release", "alpha", "--force", "--reason", "broken lease")
    assert rc == 0, cap.err
    assert read_reg(bad_lease)["packages"]["alpha"]["lease"] is None
    assert read_events(bad_lease)[-1]["event"] == "release-forced"
    rc, cap = main(lp, bad_lease, capsys, "claim", "alpha", "--workspace", "w", "--agent", "zz")
    assert rc == 0, cap.err


def test_release_force_clears_a_non_mapping_lease(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["lease"] = "garbage"
    write_reg(env, reg)
    rc, cap = main(lp, env, capsys, "list")
    assert rc == 0 and "? INVALID" in cap.out
    rc, cap = main(lp, env, capsys, "release", "alpha", "--force", "--reason", "broken lease")
    assert rc == 0, cap.err
    assert read_reg(env)["packages"]["alpha"]["lease"] is None


def test_doctor_reports_an_invalid_lease(lp, bad_lease, capsys):
    rc, cap = main(lp, bad_lease, capsys, "doctor")
    assert rc == 0
    no_internal_error(cap)
    line = next(x for x in cap.out.splitlines() if " leases:" in x)
    assert line.startswith("warn") and "1 invalid" in line and "alpha" in line


def test_doctor_wraps_each_check(lp, env, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(lp, "registry_repo_check", boom)
    rc, cap = main(lp, env, capsys, "doctor")
    assert rc == 0
    lines = cap.out.splitlines()
    assert "warn registry-repo: internal error RuntimeError: probe exploded" in cap.out
    assert any(x.startswith("ok python:") for x in lines)        # later checks still print
    assert any(" index:" in x for x in lines) and any("tool git:" in x for x in lines)


def test_run_returns_127_for_a_missing_binary(lp):
    p = lp.run(["lean-pkg-no-such-binary"])
    assert p.returncode == 127 and p.stderr == "lean-pkg-no-such-binary not on PATH"


def test_promote_without_gh_reports_it(lp, env, tmp_path, monkeypatch, capsys):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(bindir))
    rc, cap = main(lp, env, capsys, "promote", "beta")
    no_internal_error(cap)
    assert "gh visibility query failed: gh not on PATH" in cap.out
    assert rc in (0, 1)


def test_wiki_unwritable_out_is_a_clean_failure(lp, env, tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    out = blocker / "sub" / "wiki.md"
    rc, cap = main(lp, env, capsys, "wiki", "--out", str(out))
    assert rc == 1
    no_internal_error(cap)
    assert f"cannot write {out}: " in cap.err


def test_main_maps_oserror_to_a_clean_failure(lp, env, monkeypatch, capsys):
    def denied(_args):
        raise PermissionError(13, "Permission denied", "/somewhere/x")
    monkeypatch.setattr(lp, "cmd_list", denied)
    rc, cap = main(lp, env, capsys, "--json", "list")
    assert rc == 1
    no_internal_error(cap)
    assert "cannot access /somewhere/x: Permission denied" in cap.err
    assert json.loads(cap.out)["ok"] is False


# --------------------------------------------------------------------------
# R10: committed mode

def test_is_write_invocation_from_parsed_args(lp, monkeypatch):
    W = lambda **kw: lp.is_write_invocation(SimpleNamespace(**kw))   # noqa: E731
    assert W(cmd="claim") and not W(cmd="register", dry_run=True) and not W(cmd="update", dry_run=True)
    assert not W(cmd="migrate", dry_run=True) and W(cmd="migrate", dry_run=False)
    assert not W(cmd="promote", yes=False) and W(cmd="promote", yes=True)
    assert not W(cmd="audit", fix=False) and W(cmd="audit", fix=True)
    assert not W(cmd="comparator", check=True) and W(cmd="comparator", check=False)
    assert W(cmd="formalization", write=True) and not W(cmd="formalization", write=False)
    idx = __import__("lean_pkg.index", fromlist=["x"])
    cl = __import__("lean_pkg.claims", fromlist=["x"])
    assert W(cmd="index", index_fn=idx.run_add) and not W(cmd="index", index_fn=idx.run_check)
    assert W(cmd="claims", claims_fn=cl.run_sync) and not W(cmd="claims", claims_fn=cl.run_list)
    assert W(cmd="search", record=True) and not W(cmd="search", record=False)
    assert not W(cmd="list") and not W(cmd="doctor")
    monkeypatch.setattr(lp, "board_interactive", lambda: True)
    assert W(cmd="board", fn=lp.cmd_board, json_out=False) and not W(cmd="board", fn=lp.cmd_board, json_out=True)
    assert W(cmd=None, fn=lp.cmd_board, json_out=False)
    # one predicate behind the lock too: the append-only writes and the TUI lock per write
    assert lp.locks_registry(SimpleNamespace(cmd="claim"))
    assert not lp.locks_registry(SimpleNamespace(cmd="index", index_fn=idx.run_add))
    assert not lp.locks_registry(SimpleNamespace(cmd="board", fn=lp.cmd_board, json_out=False))
    monkeypatch.setattr(lp, "board_interactive", lambda: False)
    assert not W(cmd="board", fn=lp.cmd_board, json_out=False)


def test_index_add_is_committed(lp, env, shared, tmp_path, monkeypatch, capsys):
    a = shared.a
    monkeypatch.setenv("LEAN_PKG_COMMIT", "1")
    pkg = make_index_package(tmp_path / "pkgs")
    before = _count(a.root)
    assert index_add(lp, tmp_path / "idx", pkg, registry=a.registry) == 0, capsys.readouterr().err
    assert _count(a.root) == before + 1 and _subject(a.root) == "lean-pkg: index-added demo"
    # the next committed write is not refused for "uncommitted registry changes"
    rc = lp.main(["--registry", str(a.registry), *CLAIM])
    assert rc == 0, capsys.readouterr().err


def test_claims_sync_is_committed(lp, env, shared, monkeypatch, capsys):
    a = shared.a
    commit_claims(env.repo_b)
    before = _count(a.root)
    rc = lp.main(["--registry", str(a.registry), "claims", "sync", "beta", "--commit"])
    assert rc == 0, capsys.readouterr().err
    assert _count(a.root) == before + 1 and _subject(a.root).startswith("lean-pkg: claims-synced beta")


def test_formalization_write_is_committed(lp, env, shared, monkeypatch, capsys):
    a = shared.a
    monkeypatch.setenv("LEAN_PKG_COMMIT", "1")
    before = _count(a.root)
    rc = lp.main(["--registry", str(a.registry), "formalization", "beta", "--write"])
    assert rc == 0, capsys.readouterr().err
    assert _count(a.root) == before + 1 and _subject(a.root) == "lean-pkg: updated beta"


def test_tui_commits_after_each_write(lp, env, shared, monkeypatch, capsys):
    a = shared.a
    args = SimpleNamespace(registry=str(a.registry), events=None)
    ctx = lp.commit_preflight(args, "commit")
    tui = lp._Tui(None, a.registry, a.events, commit_ctx=ctx)
    tui.reload()
    answers = iter(["w3", "vera", "", "", "y"])
    monkeypatch.setattr(tui, "prompt", lambda _label: next(answers))
    before = _count(a.root)
    tui.key_claim()
    assert _count(a.root) == before + 1 and _subject(a.root) == "lean-pkg: claimed alpha"
    assert "committed" in tui.msg
    answers = iter(["vera", "y"])
    tui.key_release()
    assert _count(a.root) == before + 2 and _subject(a.root) == "lean-pkg: released alpha"


def test_no_commit_after_the_subcommand(lp, env, shared, monkeypatch, capsys):
    a = shared.a
    monkeypatch.setenv("LEAN_PKG_COMMIT", "1")
    before = _count(a.root)
    rc = lp.main(["--registry", str(a.registry), *CLAIM, "--no-commit"])
    assert rc == 0, capsys.readouterr().err
    assert _count(a.root) == before


@pytest.mark.parametrize("argv", [
    ["register", "gamma", "--repo", "git@github.com:example/gamma.git", "--path", "gamma", "--dry-run"],
    ["update", "alpha", "--path", "elsewhere", "--reason", "move", "--dry-run"],
    ["promote", "beta"],
    ["audit"],
    ["migrate", "--dry-run"],
    ["list"],
])
def test_dry_runs_skip_the_preflight(lp, env, shared, fake_gh, monkeypatch, capsys, argv):
    a = shared.a
    calls = []
    real_git = lp.git
    monkeypatch.setattr(lp, "git", lambda r, *g: (calls.append(g), real_git(r, *g))[1])
    monkeypatch.setenv("LEAN_PKG_COMMIT", "1")
    lp.main(["--registry", str(a.registry), *argv])
    cap = capsys.readouterr()
    assert not any("fetch" in g or "pull" in g for g in calls), calls
    assert "ignored" not in cap.err      # the env turned committed mode on, not a flag: no note
    # positive control: a real write fetches
    lp.main(["--registry", str(a.registry), *CLAIM])
    assert any("fetch" in g for g in calls)


def test_flag_on_a_dry_run_is_noted(lp, env, shared, capsys):
    a = shared.a
    rc = lp.main(["--registry", str(a.registry), "migrate", "--dry-run", "--commit"])
    assert rc == 0
    assert "lean-pkg: --commit ignored: read-only command" in capsys.readouterr().err


def test_json_payload_carries_the_commit(lp, env, shared, capsys):
    a = shared.a
    rc = lp.main(["--registry", str(a.registry), "--json", *CLAIM, "--commit"])
    cap = capsys.readouterr()
    assert rc == 0
    payload = json.loads(cap.out)
    assert payload["ok"] is True and payload["event"] == "claimed"
    assert payload["commit"] == {"sha": _head(a.root), "pushed": False, "error": None}


def test_json_push_failure_is_not_ok(lp, env, shared, tmp_path, capsys):
    a = shared.a
    _git(a.root, "remote", "set-url", "--push", "origin", str(tmp_path / "missing.git"))
    rc = lp.main(["--registry", str(a.registry), "--json", *CLAIM, "--push"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1 and payload["ok"] is False
    assert payload["commit"]["sha"] == _head(a.root) and payload["commit"]["pushed"] is False
    assert payload["commit"]["error"].startswith("push failed:")


def test_json_push_success(lp, env, shared, capsys):
    a = shared.a
    rc = lp.main(["--registry", str(a.registry), "--json", *CLAIM, "--push"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload["commit"]["pushed"] is True and payload["commit"]["error"] is None


def test_fallback_identity_is_reported(lp, env, shared, monkeypatch, capsys):
    a = shared.a
    _git(a.root, "config", "--unset", "user.name")
    _git(a.root, "config", "--unset", "user.email")
    _git(a.root, "config", "user.useConfigOnly", "true")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(k, raising=False)
    rc = lp.main(["--registry", str(a.registry), *CLAIM, "--commit"])
    err = capsys.readouterr().err
    assert rc == 0, err
    assert "committed as lean-pkg <lean-pkg@localhost>; set git user.name/email" in err


# --------------------------------------------------------------------------
# R11: event log precedence

def test_events_source_precedence(lp, tmp_path, monkeypatch):
    S = lambda **kw: lp.events_source(SimpleNamespace(**{"registry": None, "events": None, **kw}))  # noqa: E731
    reg = tmp_path / "other" / REG
    monkeypatch.setenv("LEAN_PKG_EVENTS", str(tmp_path / "env.jsonl"))
    assert S(events=str(tmp_path / "f.jsonl"), registry=str(reg)) == (tmp_path / "f.jsonl", "flag")
    assert S(registry=str(reg)) == (reg.parent / "lean-package-events.jsonl", "registry-sibling")
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(tmp_path / "envreg" / REG))
    assert S() == (tmp_path / "env.jsonl", "env")
    monkeypatch.delenv("LEAN_PKG_EVENTS")
    assert S() == (tmp_path / "envreg" / "lean-package-events.jsonl", "sibling")


def test_explicit_registry_never_writes_into_the_env_log(lp, env, tmp_path, monkeypatch, capsys):
    shared_log = tmp_path / "shared" / "events.jsonl"
    monkeypatch.setenv("LEAN_PKG_EVENTS", str(shared_log))
    rc, cap = main(lp, env, capsys, *CLAIM)
    assert rc == 0, cap.err
    assert not shared_log.exists() and read_events(env)[-1]["event"] == "claimed"
    rc, cap = main(lp, env, capsys, "doctor")
    line = next(x for x in cap.out.splitlines() if " events:" in x)
    assert str(env.events) in line and line.endswith("[source: registry-sibling]")


# --------------------------------------------------------------------------
# R13: formalization --write checks before it writes

def test_formalization_refuses_before_writing(lp, env, capsys):
    set_lease(env, "beta", agent="vera")
    target = env.repo_b / "formalization.yaml"
    n = len(read_events(env))
    rc, cap = main(lp, env, capsys, "formalization", "beta", "--write", "--agent", "zz")
    assert rc == 1
    assert "lease held by vera" in cap.err and "--force --reason" in cap.err
    assert not target.exists() and len(read_events(env)) == n
    # --force alone (the file-overwrite flag) does not override the lease
    rc, cap = main(lp, env, capsys, "formalization", "beta", "--write", "--agent", "zz", "--force")
    assert rc == 1 and not target.exists()
    rc, cap = main(lp, env, capsys, "formalization", "beta", "--write", "--agent", "zz", "--force",
                   "--reason", "vera is away")
    assert rc == 0, cap.err
    assert target.is_file()
    ev = read_events(env)[-1]
    assert ev["event"] == "updated" and ev["forced"] is True and ev["reason"] == "vera is away"


def test_formalization_refuses_on_an_update_error_before_writing(lp, env, monkeypatch, capsys):
    monkeypatch.setattr(lp, "split_new_errors", lambda _b, _a: (["packages.beta: injected"], []))
    rc, cap = main(lp, env, capsys, "formalization", "beta", "--write")
    assert rc == 1 and "update would break the registry schema" in cap.err
    assert not (env.repo_b / "formalization.yaml").exists()
    assert read_reg(env)["packages"]["beta"]["formalization_yaml"] is None


def test_formalization_invalid_lease_refuses_before_writing(lp, env, capsys):
    set_lease(env, "beta", agent="vera")
    reg = read_reg(env)
    reg["packages"]["beta"]["lease"]["claimed_at"] = "zzz"
    write_reg(env, reg)
    rc, cap = main(lp, env, capsys, "formalization", "beta", "--write", "--agent", "vera")
    assert rc == 1 and "lease fields unparseable" in cap.err
    assert not (env.repo_b / "formalization.yaml").exists()


# --------------------------------------------------------------------------
# R20: index root resolution

def test_index_source_resolution(lp, idx, env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path / "alpha-repo")
    args = SimpleNamespace(registry=str(env.registry), index=None)
    assert idx.index_source(args) == (Path.cwd() / "index", "cwd")
    (tmp_path / "index").mkdir()
    assert idx.index_source(args) == (tmp_path / "index", "registry-sibling")
    monkeypatch.setenv("LEAN_PKG_INDEX", str(tmp_path / "envidx"))
    assert idx.index_source(args) == (tmp_path / "envidx", "env")
    args.index = str(tmp_path / "flagidx")
    assert idx.index_source(args) == (tmp_path / "flagidx", "flag")


def test_doctor_index_line_names_its_source_and_ignores_cwd(lp, env, tmp_path, monkeypatch, capsys):
    (tmp_path / "index").mkdir()
    lines = []
    for cwd in (tmp_path / "alpha-repo", tmp_path / "beta-repo"):
        monkeypatch.chdir(cwd)
        rc, cap = main(lp, env, capsys, "doctor")
        lines.append(next(x for x in cap.out.splitlines() if " index:" in x))
    assert lines[0] == lines[1] and lines[0].endswith("[source: registry-sibling]")
    assert str(tmp_path / "index") in lines[0]


@pytest.mark.parametrize("argv", [["index", "check"], ["index", "render"], ["index", "migrate", "--check"],
                                  ["index", "migrate"]])
def test_missing_index_exits_1(lp, env, tmp_path, monkeypatch, capsys, argv):
    monkeypatch.chdir(tmp_path / "alpha-repo")
    rc, cap = main(lp, env, capsys, *argv)
    assert rc == 1
    assert f"no index at {tmp_path / 'alpha-repo' / 'index'} (pass --index or set LEAN_PKG_INDEX)" in cap.err
    assert not (tmp_path / "alpha-repo" / "index").exists()


def test_render_creates_an_explicit_index(lp, env, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path / "alpha-repo")
    root = tmp_path / "new-index"
    rc, cap = main(lp, env, capsys, "index", "render", "--index", str(root))
    assert rc == 0, cap.err
    assert (root / "README.md").is_file()


def test_migrate_check_on_an_empty_index_says_so(lp, env, tmp_path, capsys):
    root = tmp_path / "empty-index"
    root.mkdir()
    rc, cap = main(lp, env, capsys, "index", "migrate", "--check", "--index", str(root))
    assert rc == 0 and cap.out.strip() == f"no entries under {root}"
