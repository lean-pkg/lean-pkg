#!/usr/bin/env python3
"""#23.1: consent re-recorded with the same fields is a no-op; consent is shown once per (who, role)."""

from __future__ import annotations

import json

from .conftest import read_events, read_reg, write_reg

FIELDS = ("who=Ada", "role=owner", "pointer=consent/ada.md")


def rec(lp, env, *fields, json_out=False):
    argv = ["--registry", str(env.registry), "record", "beta", "consent-recorded"]
    for f in fields:
        argv += ["--field", f]
    return lp.main(argv + (["--json"] if json_out else []))


def test_exact_duplicate_is_already_recorded_and_writes_nothing(lp, env, capsys):
    assert rec(lp, env, *FIELDS) == 0
    capsys.readouterr()
    before_reg, before_ev = env.registry.read_text(), read_events(env)
    assert rec(lp, env, *FIELDS, json_out=True) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["already_recorded"] is True
    assert env.registry.read_text() == before_reg and read_events(env) == before_ev
    assert len(read_reg(env)["packages"]["beta"]["consent"]) == 1


def test_human_output_says_already_recorded(lp, env, capsys):
    rec(lp, env, *FIELDS)
    capsys.readouterr()
    assert rec(lp, env, *FIELDS) == 0
    assert "already recorded for Ada (owner, same pointer); nothing to do" in capsys.readouterr().out


def test_a_changed_pointer_is_a_new_record(lp, env, capsys):
    assert rec(lp, env, *FIELDS) == 0
    capsys.readouterr()
    assert rec(lp, env, "who=Ada", "role=owner", "pointer=consent/ada-v2.md", json_out=True) == 0
    assert json.loads(capsys.readouterr().out)["already_recorded"] is False
    assert [c["pointer"] for c in read_reg(env)["packages"]["beta"]["consent"]] == \
        ["consent/ada.md", "consent/ada-v2.md"]


def seed_duplicates(env):
    reg = read_reg(env)
    reg["packages"]["beta"]["consent"] = [
        {"who": "Ada", "role": "owner", "pointer": "c/a", "recorded_at": "2026-10-01T00:00:00+00:00"},
        {"who": "Ada", "role": "owner", "pointer": "c/a", "recorded_at": "2026-10-02T00:00:00+00:00"},
        {"who": "Ada", "role": "owner", "pointer": "c/b", "recorded_at": "2026-10-03T00:00:00+00:00"},
        {"who": "Bo", "role": "coauthor", "pointer": "c/c", "recorded_at": "2026-10-03T00:00:00+00:00"},
    ]
    write_reg(env, reg)


def test_publish_shows_each_person_once(lp, env, fake_gh, capsys):
    seed_duplicates(env)
    lp.main(["--registry", str(env.registry), "publish", "beta", "--dry-run"])
    out = capsys.readouterr().out
    assert "consent recorded (Ada, Bo)" in out


def test_status_human_view_dedupes_and_json_keeps_every_record(lp, env, capsys):
    seed_duplicates(env)
    assert lp.main(["--registry", str(env.registry), "status", "beta"]) == 0
    assert capsys.readouterr().out.count("who: Ada") == 1
    assert lp.main(["--registry", str(env.registry), "status", "beta", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["package"]["consent"]) == 4


def test_audit_reports_consent_duplicate_informationally(lp, env, capsys):
    seed_duplicates(env)
    before = env.registry.read_text()
    rc = lp.main(["--registry", str(env.registry), "audit", "--json"])
    found = [f for f in json.loads(capsys.readouterr().out)["findings"] if f["check"] == "consent-duplicate"]
    assert rc == 0 and len(found) == 1 and found[0]["name"] == "beta" and found[0]["fixed"] is False
    assert "Ada (owner) x3" in found[0]["detail"] and "Bo" not in found[0]["detail"]
    assert env.registry.read_text() == before


def test_audit_is_silent_without_duplicates(lp, env, capsys):
    rec(lp, env, *FIELDS)
    capsys.readouterr()
    lp.main(["--registry", str(env.registry), "audit", "--json"])
    assert not [f for f in json.loads(capsys.readouterr().out)["findings"] if f["check"] == "consent-duplicate"]
