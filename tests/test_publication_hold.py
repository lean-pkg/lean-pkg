#!/usr/bin/env python3
"""update --withdraw-publication / --release-publication-hold (#5 field report): the owner made a promoted
repository private again. The record says what is true now (visibility private, status back to the gated
private status, a publication hold), promotion history stays append-only, promote refuses while held, and
gate/regate/audit --fix never lift the hold. gh is faked; nothing leaves the machine."""

from __future__ import annotations

import json

from .conftest import publish_evidence, read_events, read_reg, write_reg


def run(lp, env, capsys, *args) -> tuple[int, str, str]:
    rc = lp.main(["--registry", str(env.registry), *args])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def promoted(lp, env, fake_gh, capsys) -> dict:
    """beta promoted for real (fake gh), then made private again by its owner (on GitHub only)."""
    publish_evidence(env.repo_b)
    rc, _o, err = run(lp, env, capsys, "promote", "beta", "--yes")
    assert rc == 0, err
    fake_gh.visibility = "PRIVATE"            # the owner's own step, outside lean-pkg
    return read_reg(env)["packages"]["beta"]


def withdraw(lp, env, capsys, *extra) -> tuple[int, dict | str]:
    rc, out, err = run(lp, env, capsys, "update", "beta", "--withdraw-publication",
                       "--reason", "owner made the repository private", *extra)
    return rc, (json.loads(out) if "--json" in extra and rc == 0 else out + err)


def test_withdraw_records_current_privacy_and_keeps_promotion_history(lp, env, fake_gh, fake_notify, capsys):
    before = promoted(lp, env, fake_gh, capsys)
    assert before["status"] == "public" and before["visibility"] == "public"
    rc, data = withdraw(lp, env, capsys, "--agent", "vera", "--json")
    assert rc == 0
    assert data["status"] == {"from": "public", "to": "verified-private"}
    assert data["visibility"] == {"from": "public", "to": "private"}
    assert data["github_visibility"] == "private"
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["status"] == "verified-private" and pkg["visibility"] == "private"
    hold = pkg["publication_hold"]
    assert set(hold) == {"reason", "at", "by"} and hold["by"] == "vera"
    assert pkg["promoted_at"] == before["promoted_at"] and pkg["promoted_commit"] == before["promoted_commit"]
    events = [h["event"] for h in pkg["history"]]
    assert events[-2:] == ["promoted", "publication-withdrawn"]
    assert [e["event"] for e in read_events(env)][-1] == "publication-withdrawn"
    rc, _o, err = run(lp, env, capsys, "validate")
    assert rc == 0, err


def test_withdraw_refused_while_github_still_public(lp, env, fake_gh, fake_notify, capsys):
    promoted(lp, env, fake_gh, capsys)
    fake_gh.visibility = "PUBLIC"
    before = env.registry.read_text()
    rc, text = withdraw(lp, env, capsys)
    assert rc == 1 and "make the repository private first" in text
    assert env.registry.read_text() == before


def test_withdraw_needs_reason_and_public_status(lp, env, fake_gh, capsys):
    rc, out, err = run(lp, env, capsys, "update", "beta", "--withdraw-publication")
    assert rc == 1 and "requires --reason" in err
    rc, out, err = run(lp, env, capsys, "update", "beta", "--withdraw-publication", "--reason", "r")
    assert rc == 1 and "withdraws a public package, got status 'verified-private'" in err
    assert read_events(env) == []


def test_withdraw_is_on_its_own(lp, env, fake_gh, capsys):
    rc, _o, err = run(lp, env, capsys, "update", "beta", "--withdraw-publication", "--reason", "r",
                      "--status", "archived")
    assert rc == 2 and "on its own" in err
    try:
        rc = lp.main(["--registry", str(env.registry), "update", "beta", "--withdraw-publication",
                      "--release-publication-hold", "--reason", "r"])
    except SystemExit as e:
        rc = e.code
    assert rc == 2


def test_withdraw_dry_run_writes_nothing(lp, env, fake_gh, fake_notify, capsys):
    promoted(lp, env, fake_gh, capsys)
    before = env.registry.read_text()
    rc, data = withdraw(lp, env, capsys, "--dry-run", "--json")
    assert rc == 0 and data["dry_run"] is True
    assert env.registry.read_text() == before


def test_never_gated_public_package_withdraws_to_active(lp, env, fake_gh, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"].update(status="public", visibility="public")
    write_reg(env, reg)
    rc, out, err = run(lp, env, capsys, "update", "alpha", "--withdraw-publication", "--reason", "owner")
    assert rc == 0, err
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["status"] == "active" and pkg["publication_hold"]


def test_promote_refuses_while_held_and_after_release_the_hold_item_is_gone(lp, env, fake_gh, fake_notify,
                                                                            capsys):
    promoted(lp, env, fake_gh, capsys)
    assert withdraw(lp, env, capsys)[0] == 0
    edits = len([c for c in fake_gh.calls if "edit" in c])
    rc, out, _e = run(lp, env, capsys, "promote", "beta", "--yes")
    assert rc == 1 and "publication hold since" in out and "refusing" in out
    assert len([c for c in fake_gh.calls if "edit" in c]) == edits        # nothing flipped
    rc, out, _e = run(lp, env, capsys, "publish", "beta", "--dry-run")
    assert "publication hold since" in out
    rc, out, err = run(lp, env, capsys, "update", "beta", "--release-publication-hold", "--reason",
                       "owner approves publishing again")
    assert rc == 0, err
    pkg = read_reg(env)["packages"]["beta"]
    assert "publication_hold" not in pkg and pkg["status"] == "verified-private"
    assert pkg["history"][-1]["event"] == "publication-hold-released"
    assert pkg["history"][-1]["hold"]["reason"] == "owner made the repository private"
    rc, out, _e = run(lp, env, capsys, "promote", "beta")
    assert "publication hold since" not in out


def test_release_without_hold_refused(lp, env, fake_gh, capsys):
    rc, _o, err = run(lp, env, capsys, "update", "beta", "--release-publication-hold", "--reason", "r")
    assert rc == 1 and "no publication hold" in err


def test_regate_keeps_the_hold(lp, env, fake_gh, fake_notify, capsys):
    promoted(lp, env, fake_gh, capsys)
    assert withdraw(lp, env, capsys)[0] == 0
    seed = env.repo_b / ".lean-receipts" / "seed.json"
    run(lp, env, capsys, "gate", "beta", "--receipt", str(seed), "--regate", "--reason", "r")
    run(lp, env, capsys, "gate", "beta", "--receipt", str(seed))
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["publication_hold"] and pkg["status"] == "verified-private" and pkg["visibility"] == "private"


def test_next_never_recommends_publishing_a_held_package(lp, env, fake_gh, fake_notify, capsys):
    promoted(lp, env, fake_gh, capsys)
    assert withdraw(lp, env, capsys)[0] == 0
    rc, out, _e = run(lp, env, capsys, "next", "beta", "--json")
    data = json.loads(out)
    assert data["state"]["publication_hold"]["reason"] == "owner made the repository private"
    assert "publish" not in str(data["command"]) and "promote" not in str(data["command"])


def test_audit_fix_never_flips_a_held_record_public(lp, env, fake_gh, fake_notify, capsys):
    promoted(lp, env, fake_gh, capsys)
    assert withdraw(lp, env, capsys)[0] == 0
    fake_gh.visibility = "PUBLIC"                 # someone flipped it outside promote
    rc, out, _e = run(lp, env, capsys, "audit", "--fix", "--json")
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["visibility"] == "private" and pkg["publication_hold"]
    assert "release-publication-hold" in out


def test_audit_names_the_withdrawal_for_a_public_record_gone_private(lp, env, fake_gh, fake_notify, capsys):
    promoted(lp, env, fake_gh, capsys)            # gh now private, registry still public
    rc, out, _e = run(lp, env, capsys, "audit", "--json")
    assert "--withdraw-publication" in out


def test_validate_refuses_a_hold_on_a_public_record(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["beta"].update(status="public", visibility="public",
                                   publication_hold={"reason": "x", "at": "2026-10-17T00:00:00+00:00", "by": "a"})
    write_reg(env, reg)
    rc, out, err = run(lp, env, capsys, "validate")
    assert rc == 1 and "publication_hold is set" in out + err
    reg["packages"]["beta"]["publication_hold"] = {"reason": "x"}
    reg["packages"]["beta"].update(status="verified-private", visibility="private")
    write_reg(env, reg)
    rc, out, err = run(lp, env, capsys, "validate")
    assert rc == 1 and "exactly the keys" in out + err


def test_hold_is_not_settable_through_update_fields(lp, env, capsys):
    assert "publication_hold" in lp.OWNED_ELSEWHERE
