#!/usr/bin/env python3
"""#16: record validates before writing, pre-existing errors do not block unrelated writes,
append-only retraction, public -> upstreamed / -> archived, unsupported registry version refused."""

from __future__ import annotations

import json

from .conftest import read_events, read_reg, write_reg

C = "0123456789abcdef0123456789abcdef01234567"
T = "2026-10-01T00:00:00+00:00"


def run(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def break_alpha(env):
    """A legacy invalid entry on alpha (as an unvalidated `record` used to write it): doi bound to no palomar commit."""
    reg = read_reg(env)
    a = reg["packages"]["alpha"]
    a["doi"] = {"concept": None, "versions": [{"doi": "notadoi", "tag": "v1", "commit": C, "minted_at": T}]}
    a["history"].append({"ts": T, "event": "doi-minted", "reason": "", "doi": "notadoi", "tag": "v1", "commit": C})
    write_reg(env, reg)


def set_status(env, name, status, visibility="public"):
    reg = read_reg(env)
    reg["packages"][name]["status"] = status
    reg["packages"][name]["visibility"] = visibility
    write_reg(env, reg)


# ---- R3 (a): record validates the would-be registry ------------------------------------------

def test_record_refuses_doi_without_palomar_and_names_the_rule(lp, env, capsys):
    before = env.registry.read_text()
    assert run(lp, env, "--json", "record", "alpha", "doi-minted", "--field", "doi=10.5281/zenodo.1",
               "--field", "tag=v1", "--field", f"commit={C}") == 1
    cap = capsys.readouterr()
    assert json.loads(cap.out)["ok"] is False
    assert "rule doi-in-palomar" in cap.err and "nothing written" in cap.err
    assert env.registry.read_text() == before and read_events(env) == []
    assert run(lp, env, "validate") == 0


def test_record_doi_after_palomar_event_is_accepted(lp, env):
    assert run(lp, env, "record", "alpha", "palomar-preflight-pass", "--field", f"commit={C}",
               "--field", "run=https://example.invalid/r/1") == 0
    assert run(lp, env, "record", "alpha", "doi-minted", "--field", "doi=10.5281/zenodo.1",
               "--field", "tag=v1", "--field", f"commit={C}") == 0
    assert run(lp, env, "validate") == 0


# ---- R3 (b): pre-existing errors elsewhere do not block; own new errors do --------------------

def test_update_proceeds_past_foreign_preexisting_error(lp, env, capsys):
    break_alpha(env)
    assert run(lp, env, "--json", "update", "beta", "--notes", "hello") == 0
    cap = capsys.readouterr()
    payload = json.loads(cap.out)
    assert payload["ok"] and any("packages.alpha" in e for e in payload["preexisting_errors"])
    assert "pre-existing (not caused by this change): packages.alpha" in cap.err
    assert read_reg(env)["packages"]["beta"]["notes"] == "hello"


def test_update_refuses_its_own_new_error_even_with_foreign_errors(lp, env, capsys):
    break_alpha(env)
    before = env.registry.read_text()
    assert run(lp, env, "update", "beta", "--reaudit-after", "not-a-date") == 1
    err = capsys.readouterr().err
    assert "update would break the registry schema" in err and "packages.beta: reaudit_after" in err
    assert "packages.alpha" not in err
    assert env.registry.read_text() == before


def test_record_proceeds_past_foreign_preexisting_error(lp, env, capsys):
    break_alpha(env)
    assert run(lp, env, "record", "beta", "mathlib-pr-opened", "--field", "url=https://github.com/x/y/pull/1") == 0
    assert "pre-existing (not caused by this change)" in capsys.readouterr().err


def test_register_proceeds_past_foreign_preexisting_error(lp, env, tmp_path, capsys):
    break_alpha(env)
    assert run(lp, env, "register", "gamma", "--repo", "git@github.com:example/gamma.git",
               "--path", str(tmp_path / "gamma")) == 0
    assert "pre-existing (not caused by this change): packages.alpha" in capsys.readouterr().err
    assert "gamma" in read_reg(env)["packages"]


def test_register_refuses_its_own_new_error(lp, env, tmp_path):
    break_alpha(env)
    reg = read_reg(env)
    try:
        lp.do_register(env.registry, reg, env.events, "gamma", "git@github.com:example/gamma.git",
                       str(tmp_path / "gamma"), None, "secret", "active", "none", "", "", None, None, False)
    except lp.Fail as e:
        assert "bad visibility" in str(e) and "packages.alpha" not in str(e)
    else:
        raise AssertionError("register wrote an invalid package")
    assert "gamma" not in read_reg(env)["packages"]


# ---- R3 (c): append-only retraction ---------------------------------------------------------

def test_retract_clears_a_legacy_invalid_entry_append_only(lp, env, capsys):
    break_alpha(env)
    assert run(lp, env, "validate") == 1
    capsys.readouterr()
    assert run(lp, env, "--json", "record", "alpha", "retract", "--field", "event=doi-minted",
               "--field", f"ts={T}", "--reason", "commit was never registered on palomar") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "evidence-retracted" and payload["retracted"] == {"event": "doi-minted", "ts": T}
    hist = read_reg(env)["packages"]["alpha"]["history"]
    assert hist[0]["event"] == "doi-minted"             # the original entry is kept
    assert hist[-1]["event"] == "evidence-retracted" and hist[-1]["retracted_ts"] == T
    assert read_events(env)[-1]["event"] == "evidence-retracted"
    assert run(lp, env, "validate") == 0
    assert run(lp, env, "update", "beta", "--notes", "x") == 0


def test_retract_refusals(lp, env, capsys):
    break_alpha(env)
    base = ["record", "alpha", "retract", "--field", "event=doi-minted"]
    assert run(lp, env, *base, "--field", f"ts={T}") == 1                         # no --reason
    assert "--reason" in capsys.readouterr().err
    assert run(lp, env, *base, "--field", "ts=2020-01-01T00:00:00+00:00", "--reason", "r") == 1
    assert f"recorded at: {T}" in capsys.readouterr().err
    assert run(lp, env, "record", "alpha", "retract", "--field", "event=updated", "--field", f"ts={T}",
               "--reason", "r") == 1
    assert "not an evidence event" in capsys.readouterr().err
    assert run(lp, env, *base, "--reason", "r") == 1                                # no ts
    assert "ts" in capsys.readouterr().err
    assert run(lp, env, *base, "--field", f"ts={T}", "--field", "x=1", "--reason", "r") == 1
    assert "unknown --field" in capsys.readouterr().err
    assert run(lp, env, *base, "--field", f"ts={T}", "--reason", "r") == 0
    assert run(lp, env, *base, "--field", f"ts={T}", "--reason", "r") == 1
    assert "already retracted" in capsys.readouterr().err


def test_retract_refuses_entries_it_cannot_tell_apart(lp, env, capsys):
    break_alpha(env)
    reg = read_reg(env)
    reg["packages"]["alpha"]["history"].append(
        {"ts": T, "event": "doi-minted", "reason": "", "doi": "other", "tag": "v2", "commit": C})
    write_reg(env, reg)
    assert run(lp, env, "record", "alpha", "retract", "--field", "event=doi-minted", "--field", f"ts={T}",
               "--reason", "r") == 1
    assert "cannot tell them apart" in capsys.readouterr().err


# ---- R5: public -> upstreamed, -> archived, refusals list the allowed moves -------------------

def test_upstreamed_refused_without_evidence_or_url(lp, env, capsys):
    set_status(env, "beta", "public")
    assert run(lp, env, "update", "beta", "--status", "upstreamed", "--reason", "merged") == 1
    err = capsys.readouterr().err
    assert "needs evidence" in err and "mathlib-pr-merged" in err
    assert read_reg(env)["packages"]["beta"]["status"] == "public"


def test_upstreamed_with_url_reason(lp, env):
    set_status(env, "beta", "public")
    assert run(lp, env, "update", "beta", "--status", "upstreamed", "--reason",
               "merged in https://github.com/leanprover-community/mathlib4/pull/1") == 0
    assert read_reg(env)["packages"]["beta"]["status"] == "upstreamed"


def test_upstreamed_with_recorded_mathlib_pr_merged(lp, env):
    set_status(env, "beta", "public")
    assert run(lp, env, "record", "beta", "mathlib-pr-merged", "--field",
               "url=https://github.com/leanprover-community/mathlib4/pull/1") == 0
    assert run(lp, env, "update", "beta", "--status", "upstreamed", "--reason", "merged") == 0


def test_upstreamed_with_palomar_registered_but_not_once_retracted(lp, env, capsys):
    set_status(env, "beta", "public")
    assert run(lp, env, "record", "beta", "palomar-registered", "--field", f"commit={C}", "--field", "id=P-1",
               "--field", "version=1", "--field", "comparator_config=comparator.json") == 0
    ts = read_reg(env)["packages"]["beta"]["history"][-1]["ts"]
    assert run(lp, env, "record", "beta", "retract", "--field", "event=palomar-registered",
               "--field", f"ts={ts}", "--reason", "wrong package") == 0
    assert run(lp, env, "update", "beta", "--status", "upstreamed", "--reason", "registered") == 1
    assert "needs evidence" in capsys.readouterr().err
    assert run(lp, env, "record", "beta", "palomar-registered", "--field", f"commit={C}", "--field", "id=P-2",
               "--field", "version=1", "--field", "comparator_config=comparator.json") == 0
    assert run(lp, env, "update", "beta", "--status", "upstreamed", "--reason", "registered") == 0


def test_archived_from_public_and_upstreamed(lp, env):
    set_status(env, "beta", "public")
    assert run(lp, env, "update", "beta", "--status", "archived", "--reason", "superseded") == 0
    set_status(env, "alpha", "upstreamed")
    assert run(lp, env, "update", "alpha", "--status", "archived", "--reason", "in mathlib now") == 0
    reg = read_reg(env)["packages"]
    assert reg["alpha"]["status"] == reg["beta"]["status"] == "archived"


def test_public_stays_one_way_and_refusal_lists_allowed_moves(lp, env, capsys):
    set_status(env, "beta", "public")
    assert run(lp, env, "update", "beta", "--status", "active", "--reason", "oops") == 1
    assert "allowed from public: archived, upstreamed" in capsys.readouterr().err
    assert run(lp, env, "update", "alpha", "--status", "public", "--reason", "x") == 1     # alpha: gate-pending
    err = capsys.readouterr().err
    assert "use lean-pkg publish --yes" in err
    assert "allowed from gate-pending: active, archived, verified-private (via lean-pkg gate --receipt R)" in err


# ---- R25: a registry newer than this lean-pkg is refused --------------------------------------

def test_unsupported_registry_version_refused(lp, env, capsys):
    reg = read_reg(env)
    reg["version"] = 9
    write_reg(env, reg)
    for argv in (["list"], ["board"], ["--json", "list"], ["status", "alpha"], ["migrate"]):
        assert run(lp, env, *argv) == 1, argv
        assert "registry schema version 9 is not supported (supported: 1, 2, 3)" in capsys.readouterr().err


def test_doctor_reports_unsupported_version_without_crashing(lp, env, capsys):
    reg = read_reg(env)
    reg["version"] = 9
    write_reg(env, reg)
    assert run(lp, env, "--json", "doctor") == 1
    data = json.loads(capsys.readouterr().out)
    line = next(c for c in data["checks"] if c["item"] == "registry")
    assert line["level"] == "warn" and "schema version 9" in line["detail"]
