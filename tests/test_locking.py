#!/usr/bin/env python3
"""Write path (#14): a file lock across processes, an atomic save, events appended after the save."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time

import pytest

from .conftest import CLI, read_events, read_reg, set_lease, subprocess_env, write_reg


def _spawn(env, *argv):
    return subprocess.Popen([*CLI, "--registry", str(env.registry), *argv],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=subprocess_env())


def _wait_all(procs, timeout=120):
    out = []
    for p in procs:
        o, e = p.communicate(timeout=timeout)
        out.append((p.returncode, o, e))
    return out


def _stray_tmp(env):
    return sorted(p.name for p in env.registry.parent.iterdir() if p.name.endswith(".tmp"))


# --------------------------------------------------------------------------
# real multiprocess races

def test_parallel_claims_have_exactly_one_winner(env):
    n = 8
    procs = [_spawn(env, "claim", "alpha", "--workspace", f"w{i}", "--agent", f"agent{i}")
             for i in range(n)]
    results = _wait_all(procs)
    winners = [r for r in results if r[0] == 0]
    losers = [r for r in results if r[0] != 0]
    assert len(winners) == 1, results
    assert len(losers) == n - 1
    for rc, _out, err in losers:
        assert rc == 1 and "lease held by" in err, err
        assert "Traceback" not in err
    claimed = [e for e in read_events(env) if e["event"] in ("claimed", "claim-forced")]
    assert len(claimed) == 1
    lease = read_reg(env)["packages"]["alpha"]["lease"]
    assert lease["agent"] == claimed[0]["agent"]
    assert f"for {lease['agent']}@" in winners[0][1]
    assert _stray_tmp(env) == []


def test_parallel_updates_lose_nothing(env):
    n = 20
    procs = [_spawn(env, "update", "alpha", "--notes", f"n{i}", "--reason", "race") for i in range(n)]
    results = _wait_all(procs)
    for rc, _out, err in results:
        assert rc == 0, err
        assert "Traceback" not in err and "FileNotFoundError" not in err
    updated = [e for e in read_events(env) if e["event"] == "updated" and e["name"] == "alpha"]
    assert len(updated) == n
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["notes"] in {f"n{i}" for i in range(n)}
    # every update landed in history too: no write was lost
    assert sum(1 for h in pkg["history"] if h["event"] == "updated") == n
    assert _stray_tmp(env) == []


# --------------------------------------------------------------------------
# the lock itself

def test_lock_file_is_created_next_to_the_registry(lp, env, capsys):
    lock = env.registry.with_name(env.registry.name + ".lock")
    assert not lock.exists()
    assert lp.main(["--registry", str(env.registry), "claim", "alpha",
                    "--workspace", "w", "--agent", "a"]) == 0
    assert lock.is_file()
    assert lp.lock_path(env.registry) == lock.resolve()


def test_read_only_commands_take_no_lock(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "list"]) == 0
    assert lp.main(["--registry", str(env.registry), "leases"]) == 0
    assert not env.registry.with_name(env.registry.name + ".lock").exists()


def test_a_writer_waits_for_the_lock(lp, env):
    """A claim started while another process holds the lock blocks until it is released."""
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, sys, time; f = open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX); "
         "print('locked', flush=True); time.sleep(1.5)",
         str(env.registry.with_name(env.registry.name + ".lock"))],
        stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "locked"
    t0 = time.monotonic()
    p = _spawn(env, "claim", "alpha", "--workspace", "w", "--agent", "a")
    rc, _out, err = _wait_all([p])[0]
    holder.wait(timeout=30)
    assert rc == 0, err
    assert time.monotonic() - t0 >= 1.0


def test_registry_lock_is_reentrant_in_process(lp, env):
    with lp.registry_lock(env.registry):
        with lp.registry_lock(env.registry):
            reg = lp.load_registry(env.registry)
            lp.save_registry(env.registry, reg)     # takes the lock again: must not deadlock
    assert lp._LOCKS_HELD == {}


def test_lock_that_cannot_be_opened_is_a_clean_refusal(lp, env, capsys):
    env.registry.with_name(env.registry.name + ".lock").mkdir()   # a directory: os.open fails
    rc = lp.main(["--registry", str(env.registry), "claim", "alpha", "--workspace", "w", "--agent", "a"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "cannot open the registry lock" in err and "Traceback" not in err


# --------------------------------------------------------------------------
# atomic save

def test_save_leaves_no_tmp_files_and_keeps_the_mode(lp, env):
    os.chmod(env.registry, 0o640)
    reg = lp.load_registry(env.registry)
    reg["packages"]["alpha"]["notes"] = "saved"
    lp.save_registry(env.registry, reg)
    assert _stray_tmp(env) == []
    assert stat.S_IMODE(env.registry.stat().st_mode) == 0o640
    assert read_reg(env)["packages"]["alpha"]["notes"] == "saved"


def test_failed_save_removes_its_tmp_and_keeps_the_old_file(lp, env, monkeypatch):
    before = env.registry.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(lp.os, "replace", boom)
    reg = lp.load_registry(env.registry)
    reg["packages"]["alpha"]["notes"] = "never"
    with pytest.raises(OSError):
        lp.save_registry(env.registry, reg)
    assert env.registry.read_bytes() == before
    assert _stray_tmp(env) == []


def test_save_through_a_symlinked_registry_rewrites_the_target(lp, env, tmp_path):
    link = tmp_path / "link.yaml"
    link.symlink_to(env.registry)
    reg = lp.load_registry(link)
    reg["packages"]["alpha"]["notes"] = "via link"
    lp.save_registry(link, reg)
    assert link.is_symlink()
    assert read_reg(env)["packages"]["alpha"]["notes"] == "via link"


# --------------------------------------------------------------------------
# events are appended only after the registry is saved

def test_no_event_line_when_the_save_fails(lp, env, monkeypatch, capsys):
    before_reg = env.registry.read_bytes()
    before_ev = env.events.read_bytes() if env.events.exists() else b""

    def failing_save(path, data):
        raise RuntimeError("simulated crash after record()")
    monkeypatch.setattr(lp, "save_registry", failing_save)
    rc = lp.main(["--registry", str(env.registry), "claim", "alpha", "--workspace", "w", "--agent", "a"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "internal error: RuntimeError: simulated crash after record()" in err
    assert "Traceback" not in err
    assert env.registry.read_bytes() == before_reg
    assert (env.events.read_bytes() if env.events.exists() else b"") == before_ev


def test_record_queues_until_save(lp, env):
    reg = lp.load_registry(env.registry)
    n0 = len(read_events(env))
    lp.record(reg, env.events, reg["packages"]["alpha"], "updated", fields=["notes"])
    lp.record(reg, env.events, reg["packages"]["alpha"], "updated", fields=["notes"])
    assert len(read_events(env)) == n0          # nothing written yet
    lp.save_registry(env.registry, reg)
    events = read_events(env)
    assert len(events) == n0 + 2 and events[-1]["event"] == "updated"
    lp.save_registry(env.registry, reg)          # flushed once: a second save appends nothing
    assert len(read_events(env)) == n0 + 2


def test_audit_fix_reports_an_unreadable_lease_and_sweeps_the_expired_one(lp, env, fake_gh, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=25, ttl=24)        # expired
    set_lease(env, "beta", agent="max", hours_ago=1, ttl=24)
    reg = read_reg(env)
    reg["packages"]["beta"]["lease"]["ttl_hours"] = "x"                # unreadable
    write_reg(env, reg)
    n0 = len(read_events(env))
    rc = lp.main(["--registry", str(env.registry), "--json", "audit", "--fix"])
    assert rc == 0
    out = capsys.readouterr().out
    findings = json.loads(out)["findings"]
    bad = [f for f in findings if f["check"] == "lease-invalid"]
    assert [f["name"] for f in bad] == ["beta"] and bad[0]["fixed"] is False
    assert any(f["check"] == "lease-expired" and f["name"] == "alpha" and f["fixed"] for f in findings)
    new = read_events(env)[n0:]
    assert [(e["event"], e["name"]) for e in new] == [("lease-expired", "alpha")]
    reg = read_reg(env)
    assert reg["packages"]["alpha"]["lease"] is None
    assert reg["packages"]["beta"]["lease"]["ttl_hours"] == "x"        # left for a human


# --------------------------------------------------------------------------
# top-level handler

def test_internal_error_is_one_line_and_json(lp, env, monkeypatch, capsys):
    def crash(*a, **k):
        raise KeyError("boom")
    monkeypatch.setattr(lp, "do_claim", crash)
    rc = lp.main(["--registry", str(env.registry), "--json", "claim", "alpha", "--workspace", "w",
                  "--agent", "a"])
    assert rc == 1
    cap = capsys.readouterr()
    assert json.loads(cap.out) == {"ok": False, "error": "internal error: KeyError: 'boom'"}
    assert cap.err.strip() == "lean-pkg: internal error: KeyError: 'boom'"


def test_internal_error_traceback_with_debug_env(lp, env, monkeypatch, capsys):
    def crash(*a, **k):
        raise ValueError("deep")
    monkeypatch.setattr(lp, "do_claim", crash)
    monkeypatch.setenv("LEAN_PKG_DEBUG", "1")
    rc = lp.main(["--registry", str(env.registry), "claim", "alpha", "--workspace", "w", "--agent", "a"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Traceback" in err and "lean-pkg: internal error: ValueError: deep" in err


def test_internal_error_still_runs_commit_finish(lp, env, monkeypatch, capsys):
    seen = {}

    def fake_preflight(args, mode):
        return {"fake": True}

    def fake_finish(ctx, rc):
        seen["rc"] = rc
        print("lean-pkg: not committing: the command failed", file=sys.stderr)
        return rc

    def crash(*a, **k):
        raise RuntimeError("mid-write")
    monkeypatch.setattr(lp, "commit_preflight", fake_preflight)
    monkeypatch.setattr(lp, "commit_finish", fake_finish)
    monkeypatch.setattr(lp, "do_claim", crash)
    rc = lp.main(["--registry", str(env.registry), "--commit", "claim", "alpha", "--workspace", "w",
                  "--agent", "a"])
    assert rc == 1 and seen == {"rc": 1}
    err = capsys.readouterr().err
    assert err.index("not committing") < err.index("internal error: RuntimeError: mid-write")


# --------------------------------------------------------------------------
# TUI

@pytest.mark.parametrize("text,ttl", [("", None), ("  ", None), ("12", 12), (" 3 ", 3)])
def test_tui_ttl_accepts(lp, text, ttl):
    assert lp.parse_ttl_hours(text) == ttl


@pytest.mark.parametrize("text", ["1.5", "0", "-2", "abc", "24h"])
def test_tui_ttl_refuses(lp, text):
    with pytest.raises(lp.Fail, match="ttl must be"):
        lp.parse_ttl_hours(text)


class _Prompts:
    def __init__(self, answers):
        self.answers = list(answers)

    def __call__(self, label):
        return self.answers.pop(0)


def _tui(lp, env, answers):
    t = lp._Tui(None, env.registry, env.events)
    t.reload()
    t.sel = t.names.index("alpha")
    t.prompt = _Prompts(answers)
    t.show_text = lambda title, text: None
    return t


def test_tui_claim_refuses_a_bad_ttl_without_writing(lp, env):
    before = env.registry.read_bytes()
    t = _tui(lp, env, ["w", "a", "1.5"])
    t.key_claim()
    assert "claim cancelled" in t.msg and "whole number" in t.msg
    assert env.registry.read_bytes() == before


def test_tui_reclaim_with_blank_ttl_keeps_the_lease_ttl(lp, env):
    """#21.5: a blank TTL is the CLI's no --ttl: the holder's re-claim keeps a longer lease's TTL."""
    set_lease(env, "alpha", agent="tui-agent", workspace="w", hours_ago=1, ttl=72)
    t = _tui(lp, env, ["w", "tui-agent", "", "", "y"])
    t.key_claim()
    assert not t.msg.startswith("refused"), t.msg
    assert read_reg(env)["packages"]["alpha"]["lease"]["ttl_hours"] == 72


def test_tui_new_claim_with_blank_ttl_gets_the_default(lp, env):
    t = _tui(lp, env, ["w", "tui-agent", "", "", "y"])
    t.key_claim()
    assert read_reg(env)["packages"]["alpha"]["lease"]["ttl_hours"] == lp.DEFAULT_TTL_HOURS


def test_tui_claim_rereads_the_registry_before_acting(lp, env):
    t = _tui(lp, env, ["w", "tui-agent", "", "", "y"])
    set_lease(env, "alpha", agent="other", hours_ago=0, ttl=24)   # taken after the TUI last drew
    t.key_claim()
    assert t.msg.startswith("refused:") and "lease held by other" in t.msg
    assert read_reg(env)["packages"]["alpha"]["lease"]["agent"] == "other"


def test_tui_release_rereads_the_registry_before_acting(lp, env):
    set_lease(env, "alpha", agent="vera", hours_ago=0, ttl=24)
    t = _tui(lp, env, ["vera", "y"])
    reg = read_reg(env)                                             # someone else updates meanwhile
    reg["packages"]["alpha"]["notes"] = "concurrent"
    write_reg(env, reg)
    t.key_release()
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["lease"] is None
    assert pkg["notes"] == "concurrent"                             # not overwritten by a stale copy
