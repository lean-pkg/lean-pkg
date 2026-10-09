#!/usr/bin/env python3
"""#28: `status NAME --fields`, `list/board --only`: read one package without filtering the whole dump."""

from __future__ import annotations

import json

import yaml


def _run(lp, env, capsys, *argv):
    rc = lp.main(["--registry", str(env.registry), *argv])
    return rc, capsys.readouterr()


def _claim(lp, env, capsys):
    rc, cap = _run(lp, env, capsys, "claim", "alpha", "--agent", "a1", "--workspace", "ws1")
    assert rc == 0, cap.err


def test_status_fields_json_keeps_nesting(lp, env, capsys):
    _claim(lp, env, capsys)
    rc, cap = _run(lp, env, capsys, "status", "alpha", "--json", "--fields", "status,gate.receipt,lease.agent")
    assert rc == 0, cap.err
    data = json.loads(cap.out)
    assert data["ok"] is True and data["name"] == "alpha"
    assert data["package"] == {"status": "gate-pending", "gate": {"receipt": None}, "lease": {"agent": "a1"}}
    assert data["fields"] == ["status", "gate.receipt", "lease.agent"]
    assert data["history_count"] >= 1


def test_status_fields_text_prints_yaml_only(lp, env, capsys):
    rc, cap = _run(lp, env, capsys, "status", "beta", "--fields", "visibility,gate.receipt")
    assert rc == 0, cap.err
    assert yaml.safe_load(cap.out) == {"beta": {"visibility": "private",
                                                "gate": {"receipt": ".lean-receipts/seed.json"}}}
    assert "history (last" not in cap.out


def test_status_fields_through_null_lease_is_null(lp, env, capsys):
    rc, cap = _run(lp, env, capsys, "status", "alpha", "--json", "--fields", "lease.agent")
    assert rc == 0, cap.err
    assert json.loads(cap.out)["package"] == {"lease": {"agent": None}}


def test_status_fields_whole_key_wins_over_its_subkey(lp, env, capsys):
    rc, cap = _run(lp, env, capsys, "status", "beta", "--json", "--fields", "gate.receipt,gate")
    full = json.loads(_run(lp, env, capsys, "status", "beta", "--json")[1].out)["package"]
    assert json.loads(cap.out)["package"] == {"gate": full["gate"]}
    rc, cap = _run(lp, env, capsys, "status", "beta", "--json", "--fields", "gate,gate.receipt")
    assert json.loads(cap.out)["package"] == {"gate": full["gate"]}


def test_status_fields_optional_key_absent_is_null(lp, env, capsys):
    rc, cap = _run(lp, env, capsys, "status", "alpha", "--json", "--fields", "paper")
    assert rc == 0 and json.loads(cap.out)["package"] == {"paper": None}


def test_status_unknown_field_exits_2_listing_top_level_keys(lp, env, capsys):
    rc, cap = _run(lp, env, capsys, "status", "alpha", "--fields", "status,bogus")
    assert rc == 2 and cap.out == ""
    assert "unknown field 'bogus'" in cap.err
    for k, _, _ in lp.PACKAGE_FIELDS:
        assert k in cap.err
    rc, cap = _run(lp, env, capsys, "status", "alpha", "--json", "--fields", "bogus")
    assert rc == 2
    data = json.loads(cap.out)
    assert data["ok"] is False and data["usage"] is True and "top-level keys: name, repo" in data["error"]


def test_status_unknown_nested_field_exits_2_listing_that_level(lp, env, capsys):
    rc, cap = _run(lp, env, capsys, "status", "beta", "--fields", "gate.recipt")
    assert rc == 2 and "keys of gate: receipt" in cap.err
    rc, cap = _run(lp, env, capsys, "status", "beta", "--fields", "status.x")
    assert rc == 2 and "status is a str, not a mapping" in cap.err
    for bad in ("", ",", "gate..receipt"):
        rc, cap = _run(lp, env, capsys, "status", "beta", "--fields", bad)
        assert rc == 2, bad


def test_status_without_fields_unchanged(lp, env, capsys):
    rc, cap = _run(lp, env, capsys, "status", "alpha", "--json")
    data = json.loads(cap.out)
    assert set(data) == {"ok", "name", "package", "history_count"}
    assert data["package"]["name"] == "alpha"


def test_list_and_board_only_filter_the_dump(lp, env, capsys):
    full = json.loads(_run(lp, env, capsys, "list", "--json")[1].out)
    for cmd in ("list", "board"):
        rc, cap = _run(lp, env, capsys, cmd, "--json", "--only", "beta")
        assert rc == 0, cap.err
        data = json.loads(cap.out)
        assert set(data) == set(full) and list(data["packages"]) == ["beta"]
        assert data["packages"]["beta"] == full["packages"]["beta"]
        rc, cap = _run(lp, env, capsys, cmd, "--json", "--only", "beta,alpha")
        assert set(json.loads(cap.out)["packages"]) == {"alpha", "beta"}


def test_only_text_table_and_board_never_opens_tui(lp, env, capsys, monkeypatch):
    monkeypatch.setattr(lp, "board_interactive", lambda: True)
    monkeypatch.setattr(lp, "_Tui", lambda *a, **k: (_ for _ in ()).throw(AssertionError("TUI opened")))
    for cmd in ("list", "board"):
        rc, cap = _run(lp, env, capsys, cmd, "--only", "alpha")
        assert rc == 0, cap.err
        lines = cap.out.splitlines()
        assert lines[0].startswith("NAME") and len(lines) == 2 and lines[1].startswith("alpha")
    assert not lp.is_write_invocation(type("A", (), {"cmd": "board", "fn": lp.cmd_board, "json_out": False,
                                                     "only": "alpha"})())


def test_only_unknown_package_exits_1(lp, env, capsys):
    for cmd in ("list", "board"):
        rc, cap = _run(lp, env, capsys, cmd, "--json", "--only", "alpha,nope")
        assert rc == 1
        assert "unknown package: 'nope'" in json.loads(cap.out)["error"]
        rc, cap = _run(lp, env, capsys, cmd, "--only", ",")
        assert rc == 2


# --- #29: --fields on list/board (shared with status), --only stays a package filter

def test_list_and_board_fields_trim_each_record_like_status(lp, env, capsys):
    _claim(lp, env, capsys)
    for cmd in ("list", "board"):
        rc, cap = _run(lp, env, capsys, cmd, "--json", "--only", "alpha", "--fields", "status,lease.agent")
        assert rc == 0, cap.err
        data = json.loads(cap.out)
        assert data["packages"] == {"alpha": {"status": "gate-pending", "lease": {"agent": "a1"}}}
        assert data["fields"] == ["status", "lease.agent"]
        status = json.loads(_run(lp, env, capsys, "status", "alpha", "--json", "--fields",
                                 "status,lease.agent")[1].out)["package"]
        assert data["packages"]["alpha"] == status
        rc, cap = _run(lp, env, capsys, cmd, "--json", "--fields", "visibility")
        full = json.loads(_run(lp, env, capsys, "list", "--json")[1].out)
        data = json.loads(cap.out)
        assert set(data) == set(full) | {"fields"}
        assert data["packages"] == {k: {"visibility": v["visibility"]} for k, v in full["packages"].items()}


def test_list_fields_dump_keeps_lease_expiry(lp, env, capsys):
    _claim(lp, env, capsys)
    rc, cap = _run(lp, env, capsys, "list", "--json", "--only", "alpha", "--fields", "lease.expires_at")
    assert rc == 0, cap.err
    assert json.loads(cap.out)["packages"]["alpha"]["lease"]["expires_at"]


def test_list_and_board_fields_text_is_yaml_and_never_opens_tui(lp, env, capsys, monkeypatch):
    monkeypatch.setattr(lp, "board_interactive", lambda: True)
    monkeypatch.setattr(lp, "_Tui", lambda *a, **k: (_ for _ in ()).throw(AssertionError("TUI opened")))
    for cmd in ("list", "board"):
        rc, cap = _run(lp, env, capsys, cmd, "--only", "beta", "--fields", "visibility,gate.receipt")
        assert rc == 0, cap.err
        assert yaml.safe_load(cap.out) == {"beta": {"visibility": "private",
                                                    "gate": {"receipt": ".lean-receipts/seed.json"}}}
    assert not lp.is_write_invocation(type("A", (), {"cmd": "board", "fn": lp.cmd_board, "json_out": False,
                                                     "only": None, "fields": "status"})())


def test_list_and_board_fields_validation_is_status_validation(lp, env, capsys):
    for cmd in ("list", "board"):
        rc, cap = _run(lp, env, capsys, cmd, "--fields", "status,bogus")
        assert rc == 2 and cap.out == "" and "unknown field 'bogus'" in cap.err
        assert "top-level keys: name, repo" in cap.err
        rc, cap = _run(lp, env, capsys, cmd, "--json", "--only", "beta", "--fields", "gate.recipt")
        assert rc == 2 and "keys of gate: receipt" in json.loads(cap.out)["error"]
        for bad in ("", ",", "gate..receipt"):
            rc, cap = _run(lp, env, capsys, cmd, "--fields", bad)
            assert rc == 2, (cmd, bad)


def test_list_fields_on_an_empty_registry_still_validates(lp, tmp_path, capsys):
    reg = tmp_path / "r" / "registry.yaml"
    assert lp.main(["--registry", str(reg), "init"]) == 0
    capsys.readouterr()
    assert lp.main(["--registry", str(reg), "list", "--json", "--fields", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["packages"] == {}
    assert lp.main(["--registry", str(reg), "list", "--fields", "bogus"]) == 2


def test_only_alone_is_a_package_filter_with_full_records(lp, env, capsys):
    full = json.loads(_run(lp, env, capsys, "list", "--json")[1].out)
    data = json.loads(_run(lp, env, capsys, "list", "--json", "--only", "alpha")[1].out)
    assert "fields" not in data and data["packages"]["alpha"] == full["packages"]["alpha"]


def test_list_and_board_help_say_only_filters_packages_and_fields_trims_fields(lp, capsys):
    import pytest
    for cmd in ("list", "board", "status"):
        with pytest.raises(SystemExit):
            lp.main([cmd, "--help"])
        out = " ".join(capsys.readouterr().out.split())
        assert "trims FIELDS" in out
        if cmd != "status":
            assert "filters PACKAGES, never fields" in out and "trims FIELDS, never packages" in out
