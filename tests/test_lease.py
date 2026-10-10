#!/usr/bin/env python3
"""claim/release: CAS lease semantics, TTL refresh, forced claims, audit sweep."""

from __future__ import annotations

import json

from .conftest import read_events, read_reg, set_lease, write_reg


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
    rc = lp.main(["--registry", str(env.registry), "release", "alpha", "--force",
                  "--reason", "vera went offline"])
    assert rc == 0
    ev = read_events(env)[-1]
    assert ev["event"] == "release-forced"
    assert ev["released_by"] == "force" and ev["reason"] == "vera went offline"   # #17: no --agent


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


def test_list_shows_when_a_lease_expires(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)      # live: ~23h left
    set_lease(env, "beta", agent="max", hours_ago=25, ttl=24)       # lapsed ~1h ago, not swept
    assert lp.main(["--registry", str(env.registry), "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["NAME", "STATUS", "VIS", "LEASE", "EXPIRES", "PIN", "REAUDIT"]
    alpha = next(l for l in lines if l.startswith("alpha"))
    beta = next(l for l in lines if l.startswith("beta"))
    assert "vera@" in alpha and "in 22h" in alpha
    assert "max@" in beta and "EXPIRED" in beta and "1h ago" in beta or "59m ago" in beta


def test_list_json_carries_expires_at(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    assert lp.main(["--registry", str(env.registry), "--json", "list"]) == 0
    data = json.loads(capsys.readouterr().out)
    lease = data["packages"]["alpha"]["lease"]
    assert lease["expires_at"] == lp.lease_expiry(lease).isoformat()
    assert data["packages"]["beta"]["lease"] is None
    # the registry on disk is untouched by the computed field
    assert "expires_at" not in (read_reg(env)["packages"]["alpha"]["lease"] or {})


def test_human_delta(lp):
    assert lp.human_delta(45) == "45s"
    assert lp.human_delta(12 * 60) == "12m"
    assert lp.human_delta(2 * 3600 + 13 * 60) == "2h13m"
    assert lp.human_delta(26 * 3600) == "1d2h"
    assert lp.human_delta(-90) == "1m"


# renew / leases (#10)

def test_renew_restarts_a_live_lease_and_keeps_its_ttl(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=20, ttl=24)
    rc = lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera"])
    assert rc == 0
    assert "lease refreshed for vera" in capsys.readouterr().out
    lease = read_reg(env)["packages"]["alpha"]["lease"]
    assert lease["ttl_hours"] == 24
    assert (lp.lease_expiry(lease) - lp.dt.datetime.now(lp.dt.timezone.utc)).total_seconds() > 23 * 3600
    assert read_events(env)[-1]["event"] == "claim-refreshed"


def test_renew_refuses_a_lapsed_lease_and_changes_nothing(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=25, ttl=24)
    before = env.registry.read_bytes()
    rc = lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "expired at" in err and "not renewable" in err and "claim it again" in err
    assert env.registry.read_bytes() == before
    assert not any(e["event"] == "claim-refreshed" for e in read_events(env))


def test_renew_is_holder_only_and_needs_a_lease(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera"])
    assert rc == 1 and "no lease held" in capsys.readouterr().err
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    rc = lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "max"])
    assert rc == 1
    assert "held by vera, not max" in capsys.readouterr().err
    assert read_reg(env)["packages"]["alpha"]["lease"]["agent"] == "vera"


def test_renew_json_and_new_ttl(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    rc = lp.main(["--registry", str(env.registry), "--json", "renew", "alpha", "--agent", "vera", "--ttl", "6"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["event"] == "claim-refreshed" and data["lease"]["ttl_hours"] == 6
    assert data["expires_at"] == lp.lease_expiry(data["lease"]).isoformat()


def test_leases_lists_soonest_first_with_state_and_filters(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)      # ~23h left
    set_lease(env, "beta", agent="max", hours_ago=25, ttl=24)       # lapsed ~1h ago
    assert lp.main(["--registry", str(env.registry), "leases"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["NAME", "AGENT", "WORKSPACE", "CLAIMED", "EXPIRES", "STATE", "CONTACT"]
    assert lines[1].startswith("beta") and "EXPIRED" in lines[1] and "ago" in lines[1]
    assert lines[2].startswith("alpha") and "live" in lines[2] and "in 22h" in lines[2]
    assert lp.main(["--registry", str(env.registry), "leases", "--agent", "vera"]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" not in out
    assert lp.main(["--registry", str(env.registry), "leases", "--expiring", "2"]) == 0
    out = capsys.readouterr().out
    assert "beta" in out and "alpha" not in out
    assert lp.main(["--registry", str(env.registry), "leases", "--agent", "nobody"]) == 0
    assert "no leases held by nobody" in capsys.readouterr().out


def test_leases_json(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    assert lp.main(["--registry", str(env.registry), "--json", "leases"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and len(data["leases"]) == 1
    row = data["leases"][0]
    assert row["name"] == "alpha" and row["live"] is True and row["remaining_seconds"] > 22 * 3600
    assert row["expires_at"].endswith("+00:00")


def test_doctor_leases_line_warns_on_expiring_and_expired(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=23, ttl=24)     # lapses within 2h
    set_lease(env, "beta", agent="max", hours_ago=25, ttl=24)       # expired, unswept
    assert lp.main(["--registry", str(env.registry), "doctor"]) == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if " leases:" in x)
    assert line.startswith("warn leases:")
    assert "1 live" in line and "1 expiring within 2h: alpha" in line
    assert "1 expired, not swept: beta" in line and "audit --fix" in line


def test_doctor_leases_line_ok_when_nothing_is_due(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    assert lp.main(["--registry", str(env.registry), "doctor"]) == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if " leases:" in x)
    assert line == "ok leases: 1 live"


# lease.contact: the holding session's address (claim/renew --contact)

def test_claim_stores_contact_and_events_carry_it(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "--json", "claim", "alpha", "--workspace", "w3",
                  "--agent", "vera", "--contact", "  lab-desk/n14-paper  "])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["lease"]["contact"] == "lab-desk/n14-paper"   # stripped
    assert read_reg(env)["packages"]["alpha"]["lease"]["contact"] == "lab-desk/n14-paper"
    ev = read_events(env)[-1]
    assert ev["event"] == "claimed" and ev["contact"] == "lab-desk/n14-paper"
    # the holder's re-claim without --contact keeps it, and the refresh event carries it
    assert lp.main(["--registry", str(env.registry), "claim", "alpha", "--workspace", "w3",
                    "--agent", "vera"]) == 0
    assert read_reg(env)["packages"]["alpha"]["lease"]["contact"] == "lab-desk/n14-paper"
    ev = read_events(env)[-1]
    assert ev["event"] == "claim-refreshed" and ev["contact"] == "lab-desk/n14-paper"
    # release drops it with the lease
    assert lp.main(["--registry", str(env.registry), "release", "alpha", "--agent", "vera"]) == 0
    assert read_reg(env)["packages"]["alpha"]["lease"] is None


def test_claim_without_contact_stores_no_key_and_event_has_none(lp, env):
    assert lp.main(["--registry", str(env.registry), "claim", "alpha", "--workspace", "w3",
                    "--agent", "vera"]) == 0
    assert "contact" not in read_reg(env)["packages"]["alpha"]["lease"]
    assert "contact" not in read_events(env)[-1]


def test_forced_claim_takes_its_own_contact_not_the_previous_holders(lp, env):
    assert lp.main(["--registry", str(env.registry), "claim", "alpha", "--workspace", "w3",
                    "--agent", "vera", "--contact", "@vera-desk"]) == 0
    assert lp.main(["--registry", str(env.registry), "claim", "alpha", "--workspace", "w4", "--agent", "max",
                    "--force", "--reason", "vera went offline"]) == 0
    assert "contact" not in read_reg(env)["packages"]["alpha"]["lease"]
    assert lp.main(["--registry", str(env.registry), "claim", "alpha", "--workspace", "w4", "--agent", "max",
                    "--force", "--reason", "again", "--contact", "@lean-ops"]) == 0
    assert read_reg(env)["packages"]["alpha"]["lease"]["contact"] == "@lean-ops"
    ev = read_events(env)[-1]
    assert ev["event"] == "claim-forced" and ev["contact"] == "@lean-ops"


def test_renew_keeps_or_replaces_the_contact(lp, env):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    assert lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera"]) == 0
    assert "contact" not in read_reg(env)["packages"]["alpha"]["lease"]
    assert "contact" not in read_events(env)[-1]
    assert lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera",
                    "--contact", "lab-desk/n14-paper"]) == 0
    assert read_reg(env)["packages"]["alpha"]["lease"]["contact"] == "lab-desk/n14-paper"
    assert read_events(env)[-1]["contact"] == "lab-desk/n14-paper"
    assert lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera"]) == 0
    assert read_reg(env)["packages"]["alpha"]["lease"]["contact"] == "lab-desk/n14-paper"
    assert read_events(env)[-1]["contact"] == "lab-desk/n14-paper"
    assert lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera",
                    "--contact", "@lean-ops"]) == 0
    assert read_reg(env)["packages"]["alpha"]["lease"]["contact"] == "@lean-ops"


def test_bad_contact_is_a_usage_error_and_writes_nothing(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    before = env.registry.read_bytes()
    n = len(read_events(env))
    for bad, why in (("a\nb", "one line"), ("a\tb", "one line"), ("a b", "one line"),
                     ("x" * 201, "at most 200"), ("   ", "must not be empty")):
        for argv in (["claim", "alpha", "--workspace", "w3", "--agent", "vera"],
                     ["renew", "alpha", "--agent", "vera"]):
            rc = lp.main(["--registry", str(env.registry), "--json", *argv, "--contact", bad])
            assert rc == 2, (argv[0], bad)
            payload = json.loads(capsys.readouterr().out)
            assert payload["ok"] is False and payload["usage"] is True and why in payload["error"]
    assert env.registry.read_bytes() == before and len(read_events(env)) == n
    # exactly the cap is accepted
    assert lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera",
                    "--contact", "x" * 200]) == 0


def test_leases_shows_a_contact_column(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    set_lease(env, "beta", agent="max", hours_ago=2, ttl=24)
    assert lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera",
                    "--contact", "lab-desk/n14-paper"]) == 0
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "leases"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[-1] == "CONTACT"
    beta = next(x for x in lines if x.startswith("beta"))
    alpha = next(x for x in lines if x.startswith("alpha"))
    assert beta.endswith("live")                                   # blank contact: nothing trailing
    assert alpha.endswith("lab-desk/n14-paper")
    assert alpha.index("lab-desk/n14-paper") == lines[0].index("CONTACT")
    assert lp.main(["--registry", str(env.registry), "--json", "leases"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)["leases"]}
    assert rows["alpha"]["contact"] == "lab-desk/n14-paper" and rows["beta"]["contact"] is None


def test_status_shows_and_selects_lease_contact(lp, env, capsys):
    # a package without a lease: null through the null lease, as for lease.agent
    assert lp.main(["--registry", str(env.registry), "--json", "status", "beta", "--fields", "lease.contact"]) == 0
    assert json.loads(capsys.readouterr().out)["package"] == {"lease": {"contact": None}}
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    set_lease(env, "beta", agent="max", hours_ago=1, ttl=24)
    assert lp.main(["--registry", str(env.registry), "renew", "alpha", "--agent", "vera",
                    "--contact", "@lean-ops"]) == 0
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "status", "alpha"]) == 0
    assert "contact: '@lean-ops'" in capsys.readouterr().out
    assert lp.main(["--registry", str(env.registry), "--json", "status", "alpha"]) == 0
    assert json.loads(capsys.readouterr().out)["package"]["lease"]["contact"] == "@lean-ops"
    assert lp.main(["--registry", str(env.registry), "status", "alpha", "--fields", "lease.contact"]) == 0
    assert capsys.readouterr().out.strip() == "alpha:\n  lease:\n    contact: '@lean-ops'"
    assert lp.main(["--registry", str(env.registry), "--json", "status", "alpha",
                    "--fields", "lease.agent,lease.contact"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["package"] == {"lease": {"agent": "vera", "contact": "@lean-ops"}}
    # a lease without a contact: null, not a usage error
    assert lp.main(["--registry", str(env.registry), "--json", "status", "beta", "--fields", "lease.contact"]) == 0
    assert json.loads(capsys.readouterr().out)["package"] == {"lease": {"contact": None}}
    # list --fields across packages with and without a contact
    assert lp.main(["--registry", str(env.registry), "--json", "list", "--fields", "lease.contact"]) == 0
    pkgs = json.loads(capsys.readouterr().out)["packages"]
    assert pkgs["alpha"] == {"lease": {"contact": "@lean-ops"}} and pkgs["beta"] == {"lease": {"contact": None}}
    # another unknown lease key is still a usage error
    assert lp.main(["--registry", str(env.registry), "status", "alpha", "--fields", "lease.nope"]) == 2


def test_validate_accepts_leases_with_and_without_contact_rejects_a_non_string(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    set_lease(env, "beta", agent="max", hours_ago=1, ttl=24)
    reg = read_reg(env)
    reg["packages"]["alpha"]["lease"]["contact"] = "lab-desk/n14-paper"
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0
    reg["packages"]["alpha"]["lease"]["contact"] = 42
    write_reg(env, reg)
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "validate"]) == 1
    assert "packages.alpha.lease: contact must be a string, got int" in capsys.readouterr().out
