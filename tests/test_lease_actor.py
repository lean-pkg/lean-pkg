#!/usr/bin/env python3
"""#21.3: gate, comparator, record, promote --yes, claims sync and search --record take an optional
--agent: another agent's live lease then refuses and the agent is recorded as the event's `actor`;
without --agent nothing is refused, but a live lease prints a warning naming the holder."""

from __future__ import annotations

import json

from .conftest import make_receipt, read_events, read_reg, set_lease, write_receipt

CONSENT = ["record", "beta", "consent-recorded", "--field", "who=Ada", "--field", "role=owner",
           "--field", "pointer=consent/ada.md"]


def cli(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def test_record_with_the_holder_records_the_actor(lp, env, capsys):
    set_lease(env, "beta", agent="vera")
    assert cli(lp, env, *CONSENT, "--agent", "vera") == 0
    assert read_reg(env)["packages"]["beta"]["history"][-1]["actor"] == "vera"
    assert read_events(env)[-1]["actor"] == "vera"
    assert "warning" not in capsys.readouterr().err


def test_record_with_another_agent_is_refused_and_writes_nothing(lp, env, capsys):
    set_lease(env, "beta", agent="vera")
    before = env.registry.read_text()
    assert cli(lp, env, *CONSENT, "--agent", "max", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and "lease held by vera, not max" in payload["error"]
    assert env.registry.read_text() == before


def test_without_agent_a_live_lease_warns_and_records_no_actor(lp, env, capsys):
    set_lease(env, "beta", agent="vera")
    assert cli(lp, env, *CONSENT) == 0
    err = capsys.readouterr().err
    assert "lease held by vera" in err and "--agent vera" in err
    assert "actor" not in read_reg(env)["packages"]["beta"]["history"][-1]


def test_no_lease_any_agent_is_the_actor(lp, env, capsys):
    assert cli(lp, env, *CONSENT, "--agent", "max") == 0
    assert read_reg(env)["packages"]["beta"]["history"][-1]["actor"] == "max"


def test_expired_lease_does_not_refuse(lp, env, capsys):
    set_lease(env, "beta", agent="vera", hours_ago=30, ttl=24)
    assert cli(lp, env, *CONSENT, "--agent", "max") == 0


def test_gate_refuses_another_agents_lease(lp, env, tmp_path, capsys):
    set_lease(env, "alpha", agent="vera")
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert cli(lp, env, "gate", "alpha", "--receipt", str(p), "--agent", "max") == 1
    assert "lease held by vera, not max" in capsys.readouterr().err
    assert read_reg(env)["packages"]["alpha"]["status"] == "gate-pending"
    assert cli(lp, env, "gate", "alpha", "--receipt", str(p), "--agent", "vera") == 0
    assert read_reg(env)["packages"]["alpha"]["history"][-1]["actor"] == "vera"


def test_comparator_refuses_another_agents_lease(lp, env, tmp_path, capsys):
    set_lease(env, "beta", agent="vera")
    p = tmp_path / "c.json"
    p.write_text(json.dumps(dict(env.comp_b)))
    assert cli(lp, env, "comparator", "beta", "--receipt", str(p), "--agent", "max") == 1
    assert "lease held by vera, not max" in capsys.readouterr().err
    assert cli(lp, env, "comparator", "beta", "--receipt", str(p), "--check", "--agent", "max") == 0  # read-only


def test_promote_yes_refuses_another_agents_lease_before_gh(lp, env, fake_gh, capsys):
    set_lease(env, "beta", agent="vera")
    assert cli(lp, env, "promote", "beta", "--yes", "--agent", "max") == 1
    assert "lease held by vera, not max" in capsys.readouterr().err
    assert read_reg(env)["packages"]["beta"]["status"] == "verified-private"
    assert cli(lp, env, "promote", "beta", "--agent", "max") in (0, 1)       # dry run: never refused for it
    assert "lease held by vera, not max" not in capsys.readouterr().err


def test_claims_sync_refuses_another_agents_lease(lp, env, capsys):
    set_lease(env, "beta", agent="vera")
    assert cli(lp, env, "claims", "sync", "beta", "--agent", "max") == 1
    assert "lease held by vera, not max" in capsys.readouterr().err


def test_search_agent_needs_record(lp, env, capsys):
    assert cli(lp, env, "search", "foo", "--offline", "--agent", "max") == 2
    assert "--agent is only used with --record" in capsys.readouterr().err
