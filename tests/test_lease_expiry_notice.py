#!/usr/bin/env python3
"""A lease that lapses mid-workflow is said out loud (field report: a release lease lapsed overnight while its
holder waited on a decision, and nothing told it), and `lease` is accepted as the name of `claim`."""

from __future__ import annotations

import json

from .conftest import read_events, read_reg, set_lease


def run(lp, env, capsys, *argv):
    rc = lp.main(["--registry", str(env.registry), *argv])
    return rc, capsys.readouterr()


def test_status_marks_an_expired_lease(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=30, ttl=24)
    rc, cap = run(lp, env, capsys, "status", "alpha")
    assert rc == 0
    line = cap.out.splitlines()[-1]
    assert line.startswith("lease: EXPIRED 6h") and "(vera); renew no longer works: lean-pkg lease claim alpha" in line
    rc, cap = run(lp, env, capsys, "status", "alpha", "--json")
    st = json.loads(cap.out)["lease_state"]
    assert st["state"] == "expired" and st["seconds_left"] < 0


def test_status_marks_a_lease_about_to_lapse_and_is_quiet_for_a_live_one(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=23, ttl=24)
    rc, cap = run(lp, env, capsys, "status", "alpha")
    assert rc == 0 and "lease: expires in " in cap.out and "keep it: lean-pkg renew alpha --agent vera" in cap.out
    assert json.loads(run(lp, env, capsys, "status", "alpha", "--json")[1].out)["lease_state"]["state"] == "expiring"
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    rc, cap = run(lp, env, capsys, "status", "alpha")
    assert rc == 0 and "\nlease: " not in cap.out
    assert json.loads(run(lp, env, capsys, "status", "alpha", "--json")[1].out)["lease_state"]["state"] == "live"
    rc, cap = run(lp, env, capsys, "status", "beta", "--json")
    assert json.loads(cap.out)["lease_state"] == {"state": "none", "seconds_left": None}


def test_the_holders_next_command_warns_about_a_lapsed_lease(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=30, ttl=24)
    rc, cap = run(lp, env, capsys, "update", "alpha", "--notes", "x", "--dry-run")
    assert "lean-pkg: WARN: alpha lease: EXPIRED 6h" in cap.err


def test_the_holders_next_command_warns_before_a_lease_lapses(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=23, ttl=24)
    rc, cap = run(lp, env, capsys, "update", "alpha", "--notes", "x", "--dry-run")
    assert "lean-pkg: WARN: alpha lease: expires in " in cap.err and "lean-pkg renew alpha --agent vera" in cap.err


def test_no_warning_for_a_live_lease_or_from_the_lease_commands_themselves(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    assert "WARN" not in run(lp, env, capsys, "update", "alpha", "--notes", "x", "--dry-run")[1].err
    set_lease(env, "alpha", agent="vera", hours_ago=23, ttl=24)
    rc, cap = run(lp, env, capsys, "renew", "alpha", "--agent", "vera")
    assert rc == 0 and "WARN" not in cap.err


def test_lease_is_claim(lp, env, capsys):
    rc, cap = run(lp, env, capsys, "lease", "alpha", "--workspace", "w3", "--agent", "vera")
    assert rc == 0
    assert read_reg(env)["packages"]["alpha"]["lease"]["agent"] == "vera"
    assert read_events(env)[-1]["event"] == "claimed"


def test_lease_is_a_write_like_claim(lp, env, capsys, monkeypatch):
    """The alias must reach committed mode and the registry lock as claim does: both refuse the same way when
    committed mode is on and the registry is not in git."""
    monkeypatch.setenv("LEAN_PKG_COMMIT", "1")
    rc, cap = run(lp, env, capsys, "lease", "alpha", "--workspace", "w3", "--agent", "vera")
    assert rc == 1 and "--commit needs the registry inside a git work tree" in cap.err
    assert read_reg(env)["packages"]["alpha"].get("lease") is None


def test_help_names_the_alias(lp, env, capsys):
    try:
        lp.main(["--help"])
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "lease" in out and "lean-pkg help aliases" in out   # `claim` is `lease claim`; old spellings listed there
