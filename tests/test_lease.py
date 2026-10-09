#!/usr/bin/env python3
"""claim/release: CAS lease semantics, TTL refresh, forced claims, audit sweep."""

from __future__ import annotations

from .conftest import read_events, read_reg, set_lease


def test_claim_cas_refusal_names_holder_and_expiry(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", workspace="w3")
    rc = lp.main(["--registry", str(env.registry), "claim", "alpha",
                  "--workspace", "w4", "--agent", "max"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "vera" in err and "until" in err
    # refused claim must not clobber the lease or log anything
    assert read_reg(env)["packages"]["alpha"]["lease"]["agent"] == "vera"
    assert read_events(env) == []


def test_claim_same_agent_refreshes_ttl(lp, env):
    set_lease(env, "alpha", agent="vera", workspace="w3", hours_ago=2)
    before = read_reg(env)["packages"]["alpha"]["lease"]["claimed_at"]
    rc = lp.main(["--registry", str(env.registry), "claim", "alpha",
                  "--workspace", "w3", "--agent", "vera"])
    assert rc == 0
    lease = read_reg(env)["packages"]["alpha"]["lease"]
    assert lease["agent"] == "vera"
    assert lease["claimed_at"] > before
    assert read_events(env)[-1]["event"] == "claim-refreshed"


def test_claim_expired_lease_reclaim(lp, env):
    set_lease(env, "alpha", agent="vera", hours_ago=25, ttl=24)
    rc = lp.main(["--registry", str(env.registry), "claim", "alpha",
                  "--workspace", "w4", "--agent", "max"])
    assert rc == 0
    lease = read_reg(env)["packages"]["alpha"]["lease"]
    assert lease["agent"] == "max" and lease["workspace"] == "w4"
    assert read_events(env)[-1]["event"] == "claimed"


def test_claim_force_requires_reason(lp, env, capsys):
    set_lease(env, "alpha", agent="vera")
    rc = lp.main(["--registry", str(env.registry), "claim", "alpha",
                  "--workspace", "w4", "--agent", "max", "--force"])
    assert rc == 1
    assert "--force requires --reason" in capsys.readouterr().err


def test_claim_forced_logs_event(lp, env):
    set_lease(env, "alpha", agent="vera")
    rc = lp.main(["--registry", str(env.registry), "claim", "alpha",
                  "--workspace", "w4", "--agent", "max", "--force",
                  "--reason", "vera went offline"])
    assert rc == 0
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["lease"]["agent"] == "max"
    events = read_events(env)
    assert events[-1]["event"] == "claim-forced"
    assert events[-1]["reason"] == "vera went offline"
    assert pkg["history"][-1]["event"] == "claim-forced"


def test_release_holder_only(lp, env, capsys):
    set_lease(env, "alpha", agent="vera")
    rc = lp.main(["--registry", str(env.registry), "release", "alpha",
                  "--agent", "max"])
    assert rc == 1
    assert "holder-only" in capsys.readouterr().err
    assert read_reg(env)["packages"]["alpha"]["lease"]["agent"] == "vera"


def test_release_by_holder(lp, env):
    set_lease(env, "alpha", agent="vera")
    rc = lp.main(["--registry", str(env.registry), "release", "alpha",
                  "--agent", "vera"])
    assert rc == 0
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["lease"] is None and pkg["agent"] is None and pkg["workspace"] is None
    assert read_events(env)[-1]["event"] == "released"


def test_release_force(lp, env):
    set_lease(env, "alpha", agent="vera")
    rc = lp.main(["--registry", str(env.registry), "release", "alpha", "--force"])
    assert rc == 0
    assert read_events(env)[-1]["event"] == "release-forced"


def test_release_without_lease_refused(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "release", "alpha", "--force"])
    assert rc == 1
    assert "no lease held" in capsys.readouterr().err


def test_audit_without_fix_reports_an_expired_lease_but_writes_nothing(lp, env, fake_gh, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=25, ttl=24)
    snapshot = lambda: (env.registry.read_bytes(), env.events.read_bytes() if env.events.exists() else None)
    before = snapshot()
    rc = lp.main(["--registry", str(env.registry), "audit"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "lease-expired" in captured.out and "alpha" in captured.out
    assert "audit --fix" in captured.out                 # tells the operator how to clear it
    assert "wrote" not in captured.err                   # nothing to commit
    # read-only: the lease stands, no event, both files byte-identical
    assert read_reg(env)["packages"]["alpha"]["lease"]["agent"] == "vera"
    assert not any(e["event"] == "lease-expired" for e in read_events(env))
    assert snapshot() == before


def test_audit_fix_sweeps_expired_lease(lp, env, fake_gh, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=25, ttl=24)
    set_lease(env, "beta", agent="max", hours_ago=1, ttl=24)
    rc = lp.main(["--registry", str(env.registry), "audit", "--fix"])
    assert rc == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "lease-expired" in out and "alpha" in out and "cleared" in out
    assert "audit --fix wrote 1 change(s)" in captured.err
    assert "lean-packages.yaml" in captured.err and "lean-package-events.jsonl" in captured.err
    reg = read_reg(env)
    assert reg["packages"]["alpha"]["lease"] is None
    # a live lease survives the sweep
    assert reg["packages"]["beta"]["lease"]["agent"] == "max"
    events = read_events(env)
    assert any(e["event"] == "lease-expired" and e["name"] == "alpha"
               for e in events)
    assert any(h["event"] == "lease-expired"
               for h in reg["packages"]["alpha"]["history"])
