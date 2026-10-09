#!/usr/bin/env python3
"""record: D5 evidence events with required fields and registry effects."""

from __future__ import annotations

import json

from .conftest import read_events, read_reg, repo_head, write_reg

C = "0123456789abcdef0123456789abcdef01234567"


def rec(lp, env, name, event, *fields):
    argv = ["--registry", str(env.registry), "record", name, event]
    for f in fields:
        argv += ["--field", f]
    return lp.main(argv)


def test_record_unknown_event_refused(lp, env, capsys):
    assert rec(lp, env, "beta", "something-else") == 1
    assert "event" in capsys.readouterr().err


def test_record_missing_required_field_refused(lp, env, capsys):
    assert rec(lp, env, "beta", "doi-minted", "doi=10.5281/zenodo.1") == 1
    err = capsys.readouterr().err
    assert "tag" in err and "commit" in err
    assert read_events(env) == []


def test_consent_recorded_appends(lp, env):
    assert rec(lp, env, "beta", "consent-recorded", "who=coauthor A", "role=coauthor",
               "pointer=consent/2026-10-05-a.md") == 0
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["consent"][0]["who"] == "coauthor A" and pkg["consent"][0]["recorded_at"]
    assert read_events(env)[-1]["event"] == "consent-recorded"


def test_palomar_lifecycle_merges_by_commit(lp, env):
    head = repo_head(env.repo_b)
    assert rec(lp, env, "beta", "palomar-preflight-pass", f"commit={head}",
               "run=https://example.invalid/runs/1") == 0
    assert rec(lp, env, "beta", "palomar-submitted", f"commit={head}", "submission_id=abc",
               "comparator_config=comparator/config-main.json") == 0
    assert rec(lp, env, "beta", "palomar-review", f"commit={head}", "outcome=neutral") == 0
    assert rec(lp, env, "beta", "palomar-registered", f"commit={head}",
               "id=PALOMAR-2026-10-05-000099", "version=1",
               "comparator_config=comparator/config-main.json") == 0
    pal = read_reg(env)["packages"]["beta"]["palomar"]
    assert len(pal) == 1
    e = pal[0]
    assert e["commit"] == head and e["id"] == "PALOMAR-2026-10-05-000099" and e["version"] == 1
    assert e["review_outcome"] == "neutral" and e["preflight_run"].endswith("/1")


def test_palomar_registered_requires_40hex_commit(lp, env, capsys):
    assert rec(lp, env, "beta", "palomar-registered", "commit=abc", "id=PALOMAR-1",
               "version=1", "comparator_config=x.json") == 1
    assert "commit" in capsys.readouterr().err


def test_doi_minted_appends_version(lp, env):
    head = repo_head(env.repo_b)
    rec(lp, env, "beta", "palomar-registered", f"commit={head}", "id=PALOMAR-2026-10-05-000099",
        "version=1", "comparator_config=c.json")
    assert rec(lp, env, "beta", "doi-minted", "doi=10.5281/zenodo.123", "tag=v1.0.0",
               f"commit={head}", "concept=10.5281/zenodo.122") == 0
    d = read_reg(env)["packages"]["beta"]["doi"]
    assert d["concept"] == "10.5281/zenodo.122"
    assert d["versions"][0]["doi"] == "10.5281/zenodo.123" and d["versions"][0]["commit"] == head


def test_validate_rejects_doi_commit_not_registered(lp, env, capsys):
    reg = read_reg(env)                 # a legacy entry, written before record validated (#16)
    reg["packages"]["beta"]["doi"] = {"concept": None, "versions": [
        {"doi": "10.5281/zenodo.123", "tag": "v1.0.0", "commit": C, "minted_at": "2026-10-01T00:00:00+00:00"}]}
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 1   # validate flags it
    out = capsys.readouterr().out
    assert C[:12] in out and "palomar" in out


def test_audit_warns_preflight_missing_for_palomar_target(lp, env, fake_gh, capsys):
    reg = read_reg(env); reg["packages"]["beta"]["upstream_target"] = "palomar"
    from .conftest import write_reg; write_reg(env, reg)
    lp.main(["--registry", str(env.registry), "audit"])
    assert "palomar-preflight-missing" in capsys.readouterr().out


def test_record_json_payload(lp, env, capsys, monkeypatch):
    monkeypatch.setattr(lp, "reservoir_listing_status", lambda owner, name: 200)
    assert lp.main(["--registry", str(env.registry), "--json", "record", "beta",
                    "reservoir-listed", "--field", "url=https://reservoir.lean-lang.org/@example/x"]) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["ok"] and p["event"] == "reservoir-listed"
    assert read_reg(env)["packages"]["beta"]["reservoir"]["url"].endswith("/x")


def test_record_refuses_reserved_and_unknown_field_keys(lp, env, capsys):
    for bad in ("reason=x", "event=y", "name=z", "ts=now", "bogus=1"):
        assert rec(lp, env, "beta", "reservoir-listed", "url=https://reservoir.example/x", bad) == 1, bad
        assert "field" in capsys.readouterr().err
    assert read_events(env) == []


# --- reservoir-listed is verified against the Reservoir API when it is reachable ---

def _patch_reservoir(lp, monkeypatch, status):
    calls = []

    def fake(owner, name):
        calls.append((owner, name))
        return status

    monkeypatch.setattr(lp, "reservoir_listing_status", fake)
    return calls


def test_reservoir_listed_verified_when_api_returns_200(lp, env, monkeypatch):
    calls = _patch_reservoir(lp, monkeypatch, 200)
    assert lp.main(["--registry", str(env.registry), "record", "beta", "reservoir-listed",
                    "--field", "url=https://reservoir.lean-lang.org/@example/beta"]) == 0
    assert calls == [("example", "beta")]
    r = read_reg(env)["packages"]["beta"]["reservoir"]
    assert r["verified"] is True and r["verified_at"]


def test_reservoir_listed_refused_when_api_returns_404(lp, env, monkeypatch, capsys):
    _patch_reservoir(lp, monkeypatch, 404)
    assert lp.main(["--registry", str(env.registry), "record", "beta", "reservoir-listed",
                    "--field", "url=https://reservoir.lean-lang.org/@example/beta"]) == 1
    assert "not listed" in capsys.readouterr().err
    assert read_reg(env)["packages"]["beta"]["reservoir"] is None


def test_reservoir_listed_recorded_unverified_when_offline(lp, env, monkeypatch, capsys):
    _patch_reservoir(lp, monkeypatch, None)          # network unreachable
    assert lp.main(["--registry", str(env.registry), "record", "beta", "reservoir-listed",
                    "--field", "url=https://reservoir.lean-lang.org/@example/beta"]) == 0
    assert "unverified" in capsys.readouterr().out
    r = read_reg(env)["packages"]["beta"]["reservoir"]
    assert r["verified"] is False


def test_reservoir_listed_url_shape_refused(lp, env, monkeypatch, capsys):
    _patch_reservoir(lp, monkeypatch, 200)
    assert lp.main(["--registry", str(env.registry), "record", "beta", "reservoir-listed",
                    "--field", "url=https://example.invalid/not-reservoir"]) == 1
    assert "reservoir.lean-lang.org" in capsys.readouterr().err
