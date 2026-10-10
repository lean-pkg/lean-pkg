#!/usr/bin/env python3
"""Input hygiene (#17): one local_path normaliser for register / update --path, empty values refused,
missing --reason is a refusal with JSON, UsageError JSON, re-claim keeps the TTL, release --force needs
a reason, init's event carries no host path, SPDX expressions."""

from __future__ import annotations

import json

import pytest

from .conftest import make_repo, read_events, read_reg, set_lease, write_reg


@pytest.fixture()
def home(lp, monkeypatch, tmp_path):
    """$HOME is tmp_path, so env's checkouts (tmp_path/alpha-repo, ...) lie under it."""
    monkeypatch.setattr(lp, "HOME", tmp_path)
    return tmp_path


def cli(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def register(lp, env, name, path, *extra):
    return cli(lp, env, "register", name, "--repo", f"git@github.com:o/{name}.git", "--path", path, *extra)


def refused_json(capsys) -> dict:
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["error"]
    return payload


def unchanged(env, before: str, n_events: int = 0) -> None:
    assert env.registry.read_text() == before and len(read_events(env)) == n_events


# --------------------------------------------------------------------------
# R8: one normaliser, dedupe on the resolved path

def test_register_dedupes_absolute_against_stored_relative(lp, env, home, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["local_path"] = "alpha-repo"      # the $HOME-relative form
    write_reg(env, reg)
    before = env.registry.read_text()
    for form in (str(env.repo_a), str(env.repo_a) + "/", "~/alpha-repo", "./alpha-repo//"):
        assert register(lp, env, "other", form, "--json") == 1, form
        assert "'alpha'" in refused_json(capsys)["error"]
    unchanged(env, before)


def test_register_dedupes_relative_against_stored_absolute(lp, env, home, capsys):
    before = env.registry.read_text()                          # alpha stored absolute (conftest)
    assert register(lp, env, "other", "alpha-repo") == 1
    assert "'alpha'" in capsys.readouterr().err
    unchanged(env, before)


def test_register_expands_tilde_and_stores_home_relative(lp, env, home):
    make_repo(home / "pkgs" / "gamma")
    assert register(lp, env, "gamma", "~/pkgs/gamma/") == 0
    pkg = read_reg(env)["packages"]["gamma"]
    assert pkg["local_path"] == "pkgs/gamma"
    assert pkg["lean_toolchain"]                               # detected at $HOME/pkgs/gamma
    assert read_events(env)[-1]["local_path"] == "pkgs/gamma"
    assert not (home / "~").exists()


def test_register_absolute_under_home_is_stored_relative(lp, env, home):
    make_repo(home / "delta")
    assert register(lp, env, "delta", str(home / "delta") + "/") == 0
    assert read_reg(env)["packages"]["delta"]["local_path"] == "delta"


def test_register_outside_home_stays_absolute(lp, env, home, tmp_path_factory):
    outside = make_repo(tmp_path_factory.mktemp("elsewhere") / "eps")
    assert register(lp, env, "eps", str(outside) + "/") == 0
    assert read_reg(env)["packages"]["eps"]["local_path"] == str(outside)


@pytest.mark.parametrize("path, needle", [("", "must not be empty"), ("   ", "must not be empty"),
                                          ("~", "$HOME itself"), (".", "$HOME itself"),
                                          ("~vera/x", "only a leading ~/")])
def test_register_refuses_empty_home_and_user_tilde_paths(lp, env, home, capsys, path, needle):
    (home / "lean-toolchain").write_text("leanprover/lean4:v4.0.0\n")   # never auto-detected
    before = env.registry.read_text()
    assert register(lp, env, "zeta", path, "--json") == 1
    assert needle in refused_json(capsys)["error"]
    unchanged(env, before)


@pytest.mark.parametrize("argv, flag", [(["--repo", ""], "--repo"), (["--repo", "  "], "--repo"),
                                        (["--repo", "u", "--owner-project", ""], "--owner-project")])
def test_register_refuses_empty_repo_and_owner_project(lp, env, home, capsys, argv, flag):
    before = env.registry.read_text()
    assert cli(lp, env, "register", "zeta", "--path", "zeta", *argv, "--json") == 1
    assert f"{flag} must not be empty" in refused_json(capsys)["error"]
    unchanged(env, before)


def test_update_path_expands_tilde(lp, env, home):
    make_repo(home / "moved" / "alpha")
    assert cli(lp, env, "update", "alpha", "--path", "~/moved/alpha", "--reason", "split") == 0
    assert read_reg(env)["packages"]["alpha"]["local_path"] == "moved/alpha"
    assert read_events(env)[-1]["new_path"] == "moved/alpha"


@pytest.mark.parametrize("path", ["", "  ", "~"])
def test_update_path_refuses_empty_or_home(lp, env, home, capsys, path):
    before = env.registry.read_text()
    assert cli(lp, env, "update", "alpha", "--path", path, "--reason", "r", "--json") == 1
    refused_json(capsys)
    unchanged(env, before)


def test_update_path_onto_another_package_in_another_form_refused(lp, env, home, capsys):
    before = env.registry.read_text()                          # beta stored absolute (conftest)
    for form in ("~/beta-repo/", "beta-repo"):
        assert cli(lp, env, "update", "alpha", "--path", form, "--reason", "r", "--json") == 1, form
        assert "'beta'" in refused_json(capsys)["error"]
    unchanged(env, before)


def test_update_path_same_checkout_is_a_noop_exit_0(lp, env, home, capsys):
    before = env.registry.read_text()
    rel = str(env.repo_a.relative_to(home))
    assert cli(lp, env, "update", "alpha", "--path", rel + "/", "--reason", "r", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["event"] == "update-noop" and payload["changes"] == {}
    assert payload["local_path"] == str(env.repo_a)
    assert cli(lp, env, "update", "alpha", "--path", "~/" + rel, "--reason", "r") == 0   # vs stored absolute
    assert f"already at {env.repo_a}; nothing written" in capsys.readouterr().out
    unchanged(env, before)


# --------------------------------------------------------------------------
# R24: empty update values, SPDX expressions

@pytest.mark.parametrize("flag", ["--repo", "--owner-project"])
def test_update_refuses_empty_repo_and_owner_project(lp, env, capsys, flag):
    before = env.registry.read_text()
    assert cli(lp, env, "update", "beta", flag, "", "--json") == 1
    assert f"{flag} must not be empty" in refused_json(capsys)["error"]
    unchanged(env, before)


def test_update_empty_notes_clears(lp, env):
    assert cli(lp, env, "update", "beta", "--notes", "something") == 0
    assert cli(lp, env, "update", "beta", "--notes", "") == 0
    assert read_reg(env)["packages"]["beta"]["notes"] == ""


@pytest.mark.parametrize("expr", ["MIT OR Apache-2.0", "(MIT OR Apache-2.0) AND BSD-3-Clause",
                                  "GPL-2.0-or-later WITH Classpath-exception-2.0",
                                  "((MIT))", "LicenseRef-custom AND (Apache-2.0 OR GPL-3.0+)"])
def test_update_accepts_spdx_expressions(lp, env, expr):
    assert cli(lp, env, "update", "beta", "--license-spdx", expr) == 0
    assert read_reg(env)["packages"]["beta"]["license_spdx"] == expr


@pytest.mark.parametrize("expr", ["", "MIT OR", "OR MIT", "(MIT", "MIT)", "MIT Apache-2.0", "MIT WITH",
                                  "MIT WITH (X)", "MIT License", "AND", "MIT OR $x", "()"])
def test_update_refuses_malformed_spdx(lp, env, capsys, expr):
    before = env.registry.read_text()
    assert cli(lp, env, "update", "beta", "--license-spdx", expr, "--json") == 1
    assert "license_spdx" in refused_json(capsys)["error"]
    unchanged(env, before)


# --------------------------------------------------------------------------
# R14: missing --reason is a refusal with JSON; UsageError JSON; release --force; llms --json

def test_update_force_without_reason_is_a_refusal_with_json(lp, env, capsys):
    set_lease(env, "beta", agent="vera")
    before = env.registry.read_text()
    assert cli(lp, env, "update", "beta", "--notes", "n", "--force", "--json") == 1
    assert "--force requires --reason" in refused_json(capsys)["error"]
    unchanged(env, before)


def test_update_status_without_reason_is_a_refusal_with_json(lp, env, capsys):
    before = env.registry.read_text()
    assert cli(lp, env, "update", "beta", "--status", "archived", "--json") == 1
    assert "--status requires --reason" in refused_json(capsys)["error"]
    unchanged(env, before)


def test_usage_error_prints_json_under_json_and_exits_2(lp, env, capsys):
    assert cli(lp, env, "update", "alpha", "--path", "x", "--notes", "n", "--json") == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": False, "error": payload["error"], "usage": True}
    assert "relocation on its own" in payload["error"]
    assert cli(lp, env, "update", "alpha", "--notes", "n", "--dry-run") == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "--dry-run" in cap.err                 # no JSON without --json


def test_release_force_requires_reason(lp, env, capsys):
    set_lease(env, "alpha", agent="vera")
    before = env.registry.read_text()
    assert cli(lp, env, "release", "alpha", "--force", "--json") == 1
    assert "--force requires --reason" in refused_json(capsys)["error"]
    unchanged(env, before)
    assert read_reg(env)["packages"]["alpha"]["lease"]["agent"] == "vera"


def test_release_force_records_the_acting_agent_and_reason(lp, env):
    set_lease(env, "alpha", agent="vera")
    assert cli(lp, env, "release", "alpha", "--force", "--agent", "max", "--reason", "vera offline") == 0
    ev = read_events(env)[-1]
    assert ev["event"] == "release-forced" and ev["agent"] == "vera"
    assert ev["released_by"] == "max" and ev["reason"] == "vera offline"


def test_release_by_holder_records_an_optional_reason(lp, env):
    set_lease(env, "alpha", agent="vera")
    assert cli(lp, env, "release", "alpha", "--agent", "vera", "--reason", "done") == 0
    ev = read_events(env)[-1]
    assert ev["event"] == "released" and ev["released_by"] == "vera" and ev["reason"] == "done"


def _tui_release(lp, env, answers):
    t = lp._Tui(None, env.registry, env.events)
    t.reload()
    t.sel = t.names.index("alpha")
    t.prompt = lambda label, _a=list(answers): _a.pop(0)
    t.key_release()
    return t


def test_tui_force_release_without_reason_is_cancelled(lp, env):
    set_lease(env, "alpha", agent="vera")
    before = env.registry.read_text()
    t = _tui_release(lp, env, ["", ""])                             # blank agent = FORCE, blank reason
    assert "FORCE needs a reason" in t.msg
    unchanged(env, before)


def test_tui_force_release_records_the_reason(lp, env):
    set_lease(env, "alpha", agent="vera")
    _tui_release(lp, env, ["", "stale lease", "y"])
    ev = read_events(env)[-1]
    assert ev["event"] == "release-forced" and ev["reason"] == "stale lease"


def test_llms_json_wraps_the_text(lp, capsys):
    assert lp.main(["llms", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "text": lp.LLMS_TEXT}


# --------------------------------------------------------------------------
# R15: a holder's re-claim keeps the TTL

def claim(lp, env, *extra):
    return cli(lp, env, "claim", "alpha", "--workspace", "w3", "--agent", "vera", *extra)


def test_reclaim_without_ttl_keeps_the_lease_ttl_and_records_reason(lp, env):
    set_lease(env, "alpha", agent="vera", ttl=72)
    assert claim(lp, env, "--reason", "still on chapter 2") == 0
    assert read_reg(env)["packages"]["alpha"]["lease"]["ttl_hours"] == 72
    ev = read_events(env)[-1]
    assert ev["event"] == "claim-refreshed" and ev["ttl_hours"] == 72
    assert ev["reason"] == "still on chapter 2"


def test_reclaim_with_ttl_sets_it(lp, env):
    set_lease(env, "alpha", agent="vera", ttl=72)
    assert claim(lp, env, "--ttl", "5") == 0
    assert read_reg(env)["packages"]["alpha"]["lease"]["ttl_hours"] == 5
    assert "reason" not in read_events(env)[-1]                     # none given


def test_fresh_claim_defaults_to_24h(lp, env):
    assert claim(lp, env) == 0
    assert read_reg(env)["packages"]["alpha"]["lease"]["ttl_hours"] == lp.DEFAULT_TTL_HOURS == 24


def test_renew_records_reason_too(lp, env):
    set_lease(env, "alpha", agent="vera", ttl=48)
    assert cli(lp, env, "renew", "alpha", "--agent", "vera", "--reason", "more time") == 0
    ev = read_events(env)[-1]
    assert ev["event"] == "claim-refreshed" and ev["ttl_hours"] == 48 and ev["reason"] == "more time"


def test_claim_nonpositive_ttl_still_refused(lp, env, capsys):
    assert claim(lp, env, "--ttl", "0", "--json") == 1
    assert "--ttl" in refused_json(capsys)["error"]


# --------------------------------------------------------------------------
# R22: init's shared event carries no host path; --force is marked

def test_init_event_records_file_name_and_version_only(lp, tmp_path, capsys):
    reg = tmp_path / "r" / "lean-packages.yaml"
    assert lp.main(["--registry", str(reg), "init", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["forced"] is False
    log = reg.parent / "lean-package-events.jsonl"
    ev = json.loads(log.read_text().splitlines()[0])
    assert ev["registry"] == "lean-packages.yaml" and ev["version"] == lp.REGISTRY_VERSION
    assert "forced" not in ev
    assert str(tmp_path) not in log.read_text()


def test_init_force_marks_the_second_event(lp, tmp_path, capsys):
    reg = tmp_path / "r" / "lean-packages.yaml"
    assert lp.main(["--registry", str(reg), "init"]) == 0
    capsys.readouterr()
    assert lp.main(["--registry", str(reg), "init", "--force", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["forced"] is True
    events = [json.loads(x) for x in (reg.parent / "lean-package-events.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["registry-created", "registry-created"]
    assert events[1]["forced"] is True and "forced" not in events[0]


def test_argparse_errors_print_usage_json_under_json(lp, env, capsys):
    """#21.7: an argparse-level error under --json also prints the UsageError JSON; exit 2 unchanged."""
    with pytest.raises(SystemExit) as e:
        cli(lp, env, "status", "alpha", "--no-such-flag", "--json")
    assert e.value.code == 2
    cap = capsys.readouterr()
    payload = json.loads(cap.out)
    assert payload["ok"] is False and payload["usage"] is True and "--no-such-flag" in payload["error"]
    assert "usage:" in cap.err
    with pytest.raises(SystemExit) as e:
        cli(lp, env, "--json", "register", "x", "--repo", "r", "--path", "p", "--status", "upstreamed")
    assert e.value.code == 2 and "invalid choice" in json.loads(capsys.readouterr().out)["error"]
    with pytest.raises(SystemExit):                                   # no --json: nothing on stdout
        cli(lp, env, "status", "alpha", "--no-such-flag")
    assert capsys.readouterr().out == ""
